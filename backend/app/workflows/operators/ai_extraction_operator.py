"""
AI Extraction Operator for intelligent document data extraction.
Uses LLM/AI models to extract structured data from documents, images, and text.
"""
from typing import Dict, Any, List, Optional, Union
import asyncio
import base64
import json
import io
import logging
import os
import boto3
import aiohttp
from datetime import datetime
from PIL import Image
import pytesseract
import pdf2image
import cv2
import numpy as np

from .base import BaseOperator, TaskResult
from ...core.config import settings

logger = logging.getLogger(__name__)


class AIExtractionOperator(BaseOperator):
    """
    AI-powered operator for extracting structured data from documents.
    Supports images, PDFs, and text using OCR + LLM processing.
    """

    def __init__(
        self,
        task_id: str,
        extraction_schema: Dict[str, Any],
        file_context_key: str = "uploaded_files",
        ai_model: Optional[str] = None,
        use_ocr: bool = True,
        preprocessing_config: Optional[Dict[str, Any]] = None,
        extraction_prompt: Optional[str] = None,
        **kwargs
    ):
        """
        Initialize AI extraction operator.

        Args:
            task_id: Unique task identifier
            extraction_schema: JSON schema defining expected extracted data structure
            file_context_key: Context key containing file data to process
            ai_model: Specific AI model to use (overrides config default)
            use_ocr: Whether to apply OCR preprocessing
            preprocessing_config: Image preprocessing settings
            extraction_prompt: Custom prompt for AI extraction
        """
        super().__init__(task_id, **kwargs)
        self.extraction_schema = extraction_schema
        self.file_context_key = file_context_key
        self.ai_model = ai_model or settings.AI_MODEL_NAME
        self.use_ocr = use_ocr
        self.preprocessing_config = preprocessing_config or {}
        self.extraction_prompt = extraction_prompt or self._build_default_prompt()

    def _build_default_prompt(self) -> str:
        """Build default extraction prompt based on schema."""
        schema_description = json.dumps(self.extraction_schema, indent=2)

        return f"""
You are an expert document data extraction assistant. Your task is to extract structured information from the provided document content and return it in JSON format.

EXTRACTION SCHEMA:
{schema_description}

INSTRUCTIONS:
1. Carefully analyze the document content provided
2. Extract data that matches the schema fields
3. Use null for missing or unclear information
4. Ensure extracted dates are in ISO format (YYYY-MM-DD)
5. For numerical values, extract numbers only (no currency symbols)
6. Return valid JSON that matches the schema structure

VALIDATION RULES:
- All required fields must be present (use null if not found)
- Text fields should be cleaned and properly formatted
- Dates must be valid and in ISO format
- Numbers should be numeric types, not strings

Please extract the information and return only the JSON result, no additional text.
"""

    def execute(self, context: Dict[str, Any]) -> TaskResult:
        """Synchronous wrapper for async execution."""
        return asyncio.run(self.execute_async(context))

    async def execute_async(self, context: Dict[str, Any]) -> TaskResult:
        """Execute AI extraction on document files."""
        try:
            logger.info(f"Starting AI extraction for task {self.task_id}")

            # Get files from context
            files_data = context.get(self.file_context_key, [])
            if not files_data:
                return TaskResult.failure(f"No files found in context key: {self.file_context_key}")

            extracted_results = []

            for file_data in files_data:
                try:
                    result = await self._process_single_file(file_data)
                    extracted_results.append({
                        'filename': file_data.get('filename', 'unknown'),
                        'extraction_result': result,
                        'extraction_timestamp': datetime.utcnow().isoformat()
                    })
                except Exception as e:
                    logger.error(f"Failed to process file {file_data.get('filename', 'unknown')}: {str(e)}")
                    extracted_results.append({
                        'filename': file_data.get('filename', 'unknown'),
                        'extraction_result': None,
                        'error': str(e),
                        'extraction_timestamp': datetime.utcnow().isoformat()
                    })

            # Store results in context
            context.update({
                f'{self.task_id}_extractions': extracted_results,
                f'{self.task_id}_schema': self.extraction_schema,
                f'{self.task_id}_success_count': len([r for r in extracted_results if r.get('extraction_result')]),
                f'{self.task_id}_total_count': len(extracted_results)
            })

            success_rate = len([r for r in extracted_results if r.get('extraction_result')]) / len(extracted_results)

            if success_rate >= 0.5:  # At least 50% success rate
                logger.info(f"AI extraction completed with {success_rate:.1%} success rate")
                return TaskResult.success(f"Extracted data from {len(extracted_results)} files")
            else:
                return TaskResult.failure(f"Low extraction success rate: {success_rate:.1%}")

        except Exception as e:
            logger.error(f"AI extraction failed: {str(e)}")
            return TaskResult.failure(f"AI extraction error: {str(e)}")

    async def _process_single_file(self, file_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Process a single file for data extraction."""
        filename = file_data.get('filename', '')
        content_type = file_data.get('content_type', '')

        # Check if we have direct file content (base64 or bytes)
        file_content = file_data.get('base64', '') or file_data.get('content', '')

        # If no direct content, try to download from S3
        if not file_content:
            s3_url = file_data.get('url')
            s3_key = file_data.get('s3_key')
            bucket = file_data.get('bucket')

            if s3_url or (s3_key and bucket):
                file_content = await self._download_from_s3(file_data)

            if not file_content:
                raise ValueError("No file content found")

        # Decode base64 content
        try:
            if isinstance(file_content, str):
                file_bytes = base64.b64decode(file_content)
            else:
                file_bytes = file_content
        except Exception:
            raise ValueError("Invalid file content encoding")

        # Extract text based on file type
        if content_type.startswith('image/') or filename.lower().endswith(('.png', '.jpg', '.jpeg', '.tiff', '.bmp')):
            text_content = await self._extract_text_from_image(file_bytes)
        elif content_type == 'application/pdf' or filename.lower().endswith('.pdf'):
            text_content = await self._extract_text_from_pdf(file_bytes)
        elif content_type.startswith('text/') or filename.lower().endswith('.txt'):
            text_content = file_bytes.decode('utf-8', errors='ignore')
        else:
            raise ValueError(f"Unsupported file type: {content_type}")

        if not text_content.strip():
            raise ValueError("No text content extracted from file")

        # Use AI to extract structured data
        extracted_data = await self._ai_extract_data(text_content)
        return extracted_data

    async def _extract_text_from_image(self, image_bytes: bytes) -> str:
        """Extract text from image using OCR."""
        if not self.use_ocr:
            return ""

        try:
            # Load image
            image = Image.open(io.BytesIO(image_bytes))

            # Convert to OpenCV format for preprocessing
            cv_image = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)

            # Apply preprocessing
            if self.preprocessing_config.get('grayscale', True):
                cv_image = cv2.cvtColor(cv_image, cv2.COLOR_BGR2GRAY)

            if self.preprocessing_config.get('denoise', True):
                cv_image = cv2.medianBlur(cv_image, 5)

            if self.preprocessing_config.get('enhance_contrast', True):
                cv_image = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8)).apply(cv_image)

            # Convert back to PIL Image
            processed_image = Image.fromarray(cv_image)

            # Apply OCR
            ocr_config = f'--oem 3 --psm 6 -l {settings.OCR_LANGUAGES}'
            text = pytesseract.image_to_string(processed_image, config=ocr_config)

            return text.strip()

        except Exception as e:
            logger.error(f"OCR extraction failed: {str(e)}")
            return ""

    async def _extract_text_from_pdf(self, pdf_bytes: bytes) -> str:
        """Extract text from PDF using pdf2image + OCR."""
        try:
            # Convert PDF to images
            images = pdf2image.convert_from_bytes(
                pdf_bytes,
                dpi=300,
                first_page=1,
                last_page=10  # Limit to first 10 pages for performance
            )

            all_text = []
            for i, image in enumerate(images):
                try:
                    # Convert PIL image to bytes for OCR processing
                    img_buffer = io.BytesIO()
                    image.save(img_buffer, format='PNG')
                    img_bytes = img_buffer.getvalue()

                    page_text = await self._extract_text_from_image(img_bytes)
                    if page_text:
                        all_text.append(f"--- Page {i+1} ---\n{page_text}")
                except Exception as e:
                    logger.warning(f"Failed to extract text from PDF page {i+1}: {str(e)}")
                    continue

            return "\n\n".join(all_text)

        except Exception as e:
            logger.error(f"PDF text extraction failed: {str(e)}")
            return ""

    async def _ai_extract_data(self, text_content: str) -> Dict[str, Any]:
        """Use AI to extract structured data from text content."""
        try:
            if settings.AI_MODEL_PROVIDER == "openai":
                return await self._openai_extract(text_content)
            elif settings.AI_MODEL_PROVIDER == "anthropic":
                return await self._anthropic_extract(text_content)
            else:
                raise ValueError(f"Unsupported AI provider: {settings.AI_MODEL_PROVIDER}")

        except Exception as e:
            logger.error(f"AI extraction failed: {str(e)}")
            raise

    async def _openai_extract(self, text_content: str) -> Dict[str, Any]:
        """Extract data using OpenAI API."""
        try:
            import openai

            if not settings.OPENAI_API_KEY:
                raise ValueError("OpenAI API key not configured")

            client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

            messages = [
                {"role": "system", "content": self.extraction_prompt},
                {"role": "user", "content": f"Document content:\n\n{text_content}"}
            ]

            response = await client.chat.completions.create(
                model=self.ai_model,
                messages=messages,
                max_tokens=settings.AI_MAX_TOKENS,
                temperature=settings.AI_TEMPERATURE,
                timeout=settings.AI_REQUEST_TIMEOUT
            )

            result_text = response.choices[0].message.content.strip()

            # Parse JSON response
            try:
                return json.loads(result_text)
            except json.JSONDecodeError:
                # Try to extract JSON from response if wrapped in other text
                start = result_text.find('{')
                end = result_text.rfind('}') + 1
                if start >= 0 and end > start:
                    return json.loads(result_text[start:end])
                else:
                    raise ValueError("No valid JSON found in AI response")

        except Exception as e:
            logger.error(f"OpenAI extraction failed: {str(e)}")
            raise

    async def _anthropic_extract(self, text_content: str) -> Dict[str, Any]:
        """Extract data using Anthropic Claude API."""
        try:
            import anthropic

            if not settings.ANTHROPIC_API_KEY:
                raise ValueError("Anthropic API key not configured")

            client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

            message = await client.messages.create(
                model=self.ai_model,
                max_tokens=settings.AI_MAX_TOKENS,
                temperature=settings.AI_TEMPERATURE,
                messages=[
                    {"role": "user", "content": f"{self.extraction_prompt}\n\nDocument content:\n\n{text_content}"}
                ]
            )

            result_text = message.content[0].text.strip()

            # Parse JSON response
            try:
                return json.loads(result_text)
            except json.JSONDecodeError:
                # Try to extract JSON from response if wrapped in other text
                start = result_text.find('{')
                end = result_text.rfind('}') + 1
                if start >= 0 and end > start:
                    return json.loads(result_text[start:end])
                else:
                    raise ValueError("No valid JSON found in AI response")

        except Exception as e:
            logger.error(f"Anthropic extraction failed: {str(e)}")
            raise

    async def _download_from_s3(self, file_data: Dict[str, Any]) -> Optional[bytes]:
        """Download file content from S3/MinIO."""
        try:
            s3_url = file_data.get('url')
            s3_key = file_data.get('s3_key')
            bucket = file_data.get('bucket')

            # Try direct URL download first (for presigned URLs)
            if s3_url:
                logger.info(f"Downloading file from URL: {s3_url}")
                async with aiohttp.ClientSession() as session:
                    async with session.get(s3_url) as response:
                        if response.status == 200:
                            return await response.read()
                        else:
                            logger.warning(f"URL download failed with status {response.status}")

            # Fallback to boto3 S3 client
            if s3_key and bucket:
                logger.info(f"Downloading file from S3: {bucket}/{s3_key}")

                # Initialize S3 client with same config as S3UploadOperator
                aws_region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
                endpoint_url = os.getenv("S3_ENDPOINT_URL")

                s3_client = boto3.client(
                    's3',
                    region_name=aws_region,
                    endpoint_url=endpoint_url,
                    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
                    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY")
                )

                response = s3_client.get_object(Bucket=bucket, Key=s3_key)
                return response['Body'].read()

            logger.error("No valid S3 URL or key/bucket provided")
            return None

        except Exception as e:
            logger.error(f"S3 download failed: {str(e)}")
            return None