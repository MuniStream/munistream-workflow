"""
File Conversion Service

Handles conversion of various file types to images for preview purposes.
Supports caching of converted files and provides fallback icons for non-convertible files.
"""
import os
import io
import base64
import hashlib
import mimetypes
import logging
from typing import Dict, List, Optional, Union, Any
from PIL import Image, ImageOps
import redis
import asyncio
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


class FileConversionService:
    """Service for converting files to images and managing conversions"""

    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=2)
        self._redis_client = None

    def _get_redis_client(self) -> Optional[redis.Redis]:
        """Get Redis client for caching (optional)"""
        try:
            if not self._redis_client:
                redis_url = os.getenv("REDIS_URL")
                if redis_url:
                    self._redis_client = redis.from_url(redis_url)
            return self._redis_client
        except Exception as e:
            logger.warning(f"Redis not available for caching: {e}")
            return None

    def _generate_cache_key(
        self,
        file_url: str,
        convert_format: str,
        page: Optional[int] = None,
        max_width: Optional[int] = None,
        thumbnail: bool = False
    ) -> str:
        """Generate cache key for converted file"""
        key_data = f"{file_url}:{convert_format}:{page}:{max_width}:{thumbnail}"
        return f"file_conversion:{hashlib.md5(key_data.encode()).hexdigest()}"

    async def _get_cached_conversion(self, cache_key: str) -> Optional[bytes]:
        """Get cached conversion if available"""
        try:
            redis_client = self._get_redis_client()
            if redis_client:
                loop = asyncio.get_event_loop()
                cached_data = await loop.run_in_executor(
                    self.executor, redis_client.get, cache_key
                )
                return cached_data
        except Exception as e:
            logger.warning(f"Cache retrieval failed: {e}")
        return None

    async def _cache_conversion(
        self,
        cache_key: str,
        data: bytes,
        ttl: int = 3600
    ) -> None:
        """Cache converted file data"""
        try:
            redis_client = self._get_redis_client()
            if redis_client:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    self.executor,
                    lambda: redis_client.setex(cache_key, ttl, data)
                )
        except Exception as e:
            logger.warning(f"Cache storage failed: {e}")

    def _detect_file_type(self, file_bytes: bytes, filename: str) -> str:
        """Detect file type from content and filename"""
        # First try to detect from content
        if file_bytes.startswith(b'\x89PNG'):
            return 'png'
        elif file_bytes.startswith(b'\xFF\xD8\xFF'):
            return 'jpeg'
        elif file_bytes.startswith(b'GIF87a') or file_bytes.startswith(b'GIF89a'):
            return 'gif'
        elif file_bytes.startswith(b'%PDF'):
            return 'pdf'
        elif file_bytes.startswith(b'PK\x03\x04'):
            # ZIP-based formats (docx, xlsx, etc.)
            if filename.lower().endswith(('.docx', '.doc')):
                return 'document'
            elif filename.lower().endswith(('.xlsx', '.xls')):
                return 'spreadsheet'
            else:
                return 'archive'

        # Fallback to mime type detection
        mime_type, _ = mimetypes.guess_type(filename)
        if mime_type:
            if mime_type.startswith('image/'):
                return mime_type.split('/')[-1]
            elif mime_type == 'application/pdf':
                return 'pdf'
            elif mime_type in ['application/msword', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document']:
                return 'document'
            elif mime_type in ['application/vnd.ms-excel', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet']:
                return 'spreadsheet'
            elif mime_type.startswith('text/'):
                return 'text'

        return 'unknown'

    def _resize_image(
        self,
        image: Image.Image,
        max_width: Optional[int] = None,
        max_height: Optional[int] = None
    ) -> Image.Image:
        """Resize image while maintaining aspect ratio"""
        if not max_width and not max_height:
            return image

        width, height = image.size

        if max_width and width > max_width:
            ratio = max_width / width
            new_height = int(height * ratio)
            image = image.resize((max_width, new_height), Image.Resampling.LANCZOS)

        if max_height:
            width, height = image.size
            if height > max_height:
                ratio = max_height / height
                new_width = int(width * ratio)
                image = image.resize((new_width, max_height), Image.Resampling.LANCZOS)

        return image

    async def _convert_image(
        self,
        image_bytes: bytes,
        output_format: str = 'png',
        max_width: Optional[int] = None,
        thumbnail: bool = False
    ) -> bytes:
        """Convert image to specified format and size"""
        def _process(image_data, format_str, width_param, thumb_flag):
            try:
                # Open image
                image = Image.open(io.BytesIO(image_data))

                # Convert to RGB if needed (for PNG output)
                if format_str.lower() == 'png' and image.mode in ('RGBA', 'LA', 'P'):
                    # Keep transparency for PNG
                    pass
                elif image.mode not in ('RGB', 'L'):
                    image = image.convert('RGB')

                # Resize if requested
                resize_width = width_param
                if thumb_flag:
                    resize_width = width_param or 200
                if resize_width:
                    image = self._resize_image(image, max_width=resize_width)

                # Convert to bytes
                output_buffer = io.BytesIO()
                image.save(output_buffer, format=format_str.upper())
                return output_buffer.getvalue()

            except Exception as e:
                logger.error(f"Image conversion failed: {e}")
                raise

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.executor, _process, image_bytes, output_format, max_width, thumbnail)

    async def _convert_pdf_to_images(
        self,
        pdf_bytes: bytes,
        page: Optional[Union[int, str]] = None,
        max_width: Optional[int] = None
    ) -> List[bytes]:
        """Convert PDF pages to images"""
        def _process():
            try:
                # Import here to avoid import issues if not available
                from pdf2image import convert_from_bytes

                # Determine pages to convert
                if page == "all":
                    first_page, last_page = None, None
                elif isinstance(page, int):
                    first_page, last_page = page, page
                else:
                    # Default to first 3 pages
                    first_page, last_page = 1, 3

                # Convert PDF to images
                images = convert_from_bytes(
                    pdf_bytes,
                    dpi=150,  # Balance between quality and performance
                    first_page=first_page,
                    last_page=last_page
                )

                converted_pages = []
                for img in images:
                    # Resize if needed
                    if max_width:
                        img = self._resize_image(img, max_width=max_width)

                    # Convert to bytes
                    img_buffer = io.BytesIO()
                    img.save(img_buffer, format='PNG')
                    converted_pages.append(img_buffer.getvalue())

                return converted_pages

            except ImportError:
                logger.error("pdf2image not available for PDF conversion")
                raise
            except Exception as e:
                logger.error(f"PDF conversion failed: {e}")
                raise

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.executor, _process)

    def _get_generic_file_icon(self, file_type: str) -> bytes:
        """Get generic file icon for non-convertible file types"""
        # For now, return a simple colored square as placeholder
        # In production, you'd load actual icon files
        try:
            # Create a simple colored icon based on file type
            colors = {
                'document': '#4285F4',  # Blue for documents
                'spreadsheet': '#34A853',  # Green for spreadsheets
                'text': '#FBBC05',  # Yellow for text files
                'archive': '#EA4335',  # Red for archives
                'unknown': '#9AA0A6'  # Gray for unknown
            }

            color = colors.get(file_type, colors['unknown'])

            # Create a 64x64 colored square
            image = Image.new('RGB', (64, 64), color)

            # Convert to bytes
            buffer = io.BytesIO()
            image.save(buffer, format='PNG')
            return buffer.getvalue()

        except Exception as e:
            logger.error(f"Failed to generate file icon: {e}")
            # Return minimal PNG data
            return b'\x89PNG\x0D\x0A\x1A\x0A\x00\x00\x00\x0DIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1F\x15\xC4\x89\x00\x00\x00\x0BIDAT\x78\x9Cc\x00\x01\x00\x00\x05\x00\x01\x0D\x0A\x2D\xB4\x00\x00\x00\x00IEND\xAEB`\x82'

    async def _download_file_from_url(self, file_url: str, filename: Optional[str] = None) -> tuple[bytes, str]:
        """
        Download file from URL

        Transparently handles S3/MinIO URLs using boto3 with credentials,
        and regular HTTP URLs using aiohttp.

        Returns:
            Tuple of (file_bytes, filename)
        """
        from urllib.parse import urlparse

        try:
            # Check if this is an S3/MinIO URL
            if self._is_s3_url(file_url):
                return await self._download_from_s3(file_url, filename)

            # Otherwise use HTTP download
            import aiohttp

            async with aiohttp.ClientSession() as session:
                async with session.get(file_url) as response:
                    if response.status == 200:
                        file_bytes = await response.read()

                        # Extract filename if not provided
                        if not filename:
                            # Try to get from Content-Disposition header
                            content_disposition = response.headers.get('Content-Disposition', '')
                            if 'filename=' in content_disposition:
                                filename = content_disposition.split('filename=')[1].strip('"')
                            else:
                                # Fallback to URL path
                                filename = os.path.basename(file_url.split('?')[0])

                        return file_bytes, filename
                    else:
                        raise Exception(f"HTTP {response.status}: Failed to download file from {file_url}")

        except Exception as e:
            logger.error(f"Failed to download file from {file_url}: {e}")
            raise

    def _is_s3_url(self, file_url: str) -> bool:
        """Check if URL is from S3/MinIO"""
        from urllib.parse import urlparse

        parsed_url = urlparse(file_url)

        # Check for common S3/MinIO patterns
        return (
            # MinIO internal hostnames
            'minio:' in file_url or
            # AWS S3 patterns
            's3.amazonaws.com' in file_url or
            's3.' in parsed_url.netloc or
            '.amazonaws.com' in parsed_url.netloc or
            # Custom S3-compatible endpoints
            parsed_url.netloc.endswith('.s3.amazonaws.com') or
            # MinIO bucket patterns (bucket in path)
            any(bucket in file_url for bucket in ['uploads', 'documents', 'files']) and
            ('9000' in parsed_url.netloc or 's3' in parsed_url.netloc.lower())
        )

    async def _download_from_s3(self, file_url: str, filename: Optional[str] = None) -> tuple[bytes, str]:
        """Download file from S3/MinIO using boto3 with environment credentials"""

        def _sync_download(provided_filename):
            import boto3
            from urllib.parse import urlparse

            parsed_url = urlparse(file_url)

            # Extract bucket and key from the URL
            # Handle different S3 URL formats:
            # 1. http://minio:9000/bucket-name/path/to/file
            # 2. https://bucket-name.s3.amazonaws.com/path/to/file
            # 3. https://s3.amazonaws.com/bucket-name/path/to/file

            if parsed_url.netloc.endswith('.s3.amazonaws.com'):
                # Format: bucket-name.s3.amazonaws.com/path/to/file
                bucket_name = parsed_url.netloc.split('.')[0]
                from urllib.parse import unquote
                s3_key = unquote(parsed_url.path.strip('/'))
            elif 's3.amazonaws.com' in parsed_url.netloc:
                # Format: s3.amazonaws.com/bucket-name/path/to/file
                from urllib.parse import unquote
                path_parts = unquote(parsed_url.path.strip('/')).split('/', 1)
                bucket_name = path_parts[0]
                s3_key = path_parts[1] if len(path_parts) > 1 else ''
            else:
                # MinIO format: minio:9000/bucket-name/path/to/file
                from urllib.parse import unquote
                path_parts = unquote(parsed_url.path.strip('/')).split('/', 1)
                bucket_name = path_parts[0]
                s3_key = path_parts[1] if len(path_parts) > 1 else ''

            # Initialize S3 client with environment configuration
            s3_client = boto3.client(
                's3',
                region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
                endpoint_url=os.getenv("S3_ENDPOINT_URL"),  # For MinIO
                aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY")
            )

            try:
                # Download file from S3/MinIO
                response = s3_client.get_object(Bucket=bucket_name, Key=s3_key)
                file_bytes = response['Body'].read()

                # Extract filename if not provided
                final_filename = provided_filename
                if not final_filename:
                    final_filename = os.path.basename(s3_key) or 'downloaded_file'

                logger.info(f"Successfully downloaded {len(file_bytes)} bytes from S3: {bucket_name}/{s3_key}")
                return file_bytes, final_filename

            except Exception as s3_error:
                logger.error(f"S3 download error for {bucket_name}/{s3_key}: {s3_error}")
                raise Exception(f"Failed to download from S3: {s3_error}")

        # Run the sync S3 operation in thread pool
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.executor, _sync_download, filename)

    async def convert_file(
        self,
        file_bytes: Optional[bytes] = None,
        file_url: Optional[str] = None,
        filename: Optional[str] = None,
        convert_format: str = 'png',
        page: Optional[Union[int, str]] = None,
        max_width: Optional[int] = None,
        thumbnail: bool = False,
        cache_ttl: int = 3600
    ) -> Dict[str, Any]:
        """
        Convert file to specified format with caching

        Args:
            file_bytes: File content as bytes (if already downloaded)
            file_url: URL to download file from (alternative to file_bytes)
            filename: Original filename (required if using file_url)
            convert_format: Output format (png, jpg, etc.)
            page: For PDFs, page number or 'all'
            max_width: Maximum width for images
            thumbnail: Generate smaller thumbnail
            cache_ttl: Cache time-to-live in seconds

        Returns:
            Dict with conversion result:
            - For images: {"type": "image", "data": bytes}
            - For multi-page PDFs: {"type": "multi-page", "pages": [bytes, ...]}
            - For non-convertible: {"type": "file", "icon": bytes, "filename": str}
        """
        try:
            # Download file if URL provided
            if file_url and not file_bytes:
                file_bytes, filename = await self._download_file_from_url(file_url, filename)
            elif not file_bytes:
                raise ValueError("Either file_bytes or file_url must be provided")

            if not filename:
                filename = "unknown_file"

            # Check cache first
            cache_key = self._generate_cache_key(
                filename, convert_format, page, max_width, thumbnail
            )

            cached_result = await self._get_cached_conversion(cache_key)
            if cached_result:
                # Cached result is stored as JSON string
                import json
                try:
                    return json.loads(cached_result.decode())
                except:
                    # Cache corruption, continue with conversion
                    pass

            # Detect file type
            file_type = self._detect_file_type(file_bytes, filename)

            result = {}

            if file_type in ['png', 'jpeg', 'jpg', 'gif', 'bmp', 'webp']:
                # Convert image
                converted_bytes = await self._convert_image(
                    file_bytes, convert_format, max_width, thumbnail
                )
                result = {
                    "type": "image",
                    "format": convert_format,
                    "data": base64.b64encode(converted_bytes).decode(),
                    "filename": filename
                }

            elif file_type == 'pdf':
                # Convert PDF to images
                page_images = await self._convert_pdf_to_images(
                    file_bytes, page, max_width
                )
                result = {
                    "type": "multi-page",
                    "total_pages": len(page_images),
                    "pages": [
                        base64.b64encode(img).decode()
                        for img in page_images
                    ],
                    "filename": filename
                }

            else:
                # Non-convertible file - return generic icon
                icon_bytes = self._get_generic_file_icon(file_type)
                result = {
                    "type": "file",
                    "file_type": file_type,
                    "icon": base64.b64encode(icon_bytes).decode(),
                    "filename": filename,
                    "size": len(file_bytes) if file_bytes else 0
                }

            # Cache the result
            try:
                import json
                cache_data = json.dumps(result).encode()
                await self._cache_conversion(cache_key, cache_data, cache_ttl)
            except Exception as cache_error:
                logger.warning(f"Failed to cache conversion result: {cache_error}")

            return result

        except Exception as e:
            logger.error(f"File conversion failed for {filename}: {e}")
            # Return fallback result
            icon_bytes = self._get_generic_file_icon('unknown')
            return {
                "type": "file",
                "file_type": "unknown",
                "icon": base64.b64encode(icon_bytes).decode(),
                "filename": filename,
                "size": len(file_bytes) if file_bytes else 0,
                "error": str(e)
            }