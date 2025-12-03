"""
IDCaptureOperator for generic ID document capture with provenance tracking.

This operator forces browser camera capture for both front and back of any documents.
Provides basic detection (photo, text, QR/barcodes) without specific field extraction.

Features:
- Dual capture (front/back) with camera only
- Generic photo detection
- Text presence detection
- QR code and barcode detection and decoding
- Browser fingerprinting and timestamp validation
- Comprehensive provenance tracking for KYC compliance
"""
import asyncio
import base64
import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Union
import io
from PIL import Image
import cv2
import numpy as np
import pytesseract
from pyzbar import pyzbar

from .base import TaskResult
from .image_capture_base import ImageCaptureOperator

logger = logging.getLogger(__name__)


class IDCaptureOperator(ImageCaptureOperator):
    """
    Generic operator for capturing documents via browser camera with basic detection.

    This operator:
    1. Forces camera capture for front and back of documents
    2. Detects basic elements: photo presence, text presence, QR/barcodes
    3. Decodes QR codes and barcodes found in images
    4. Validates live capture vs uploaded files
    5. Creates comprehensive browser fingerprint for provenance
    6. Outputs validated document data to context

    Does NOT extract specific fields - use separate extraction operators for that.
    Does NOT handle storage - use S3UploadOperator for that.
    """

    def __init__(
        self,
        task_id: str,
        output_key: str = "captured_document",
        instructions: Dict[str, str] = None,
        allow_retake: bool = True,
        max_attempts: int = 3,
        timeout_minutes: int = 15,
        min_quality_score: int = 60,
        max_timestamp_age_seconds: int = 120,
        detect_photo: bool = True,
        detect_text: bool = True,
        detect_codes: bool = True,
        validation_config: Optional[Dict] = None,
        **kwargs
    ):
        """
        Initialize IDCaptureOperator.

        Args:
            task_id: Unique task identifier
            output_key: Context key where validated document data will be stored
            instructions: Custom instructions for front/back capture
            allow_retake: Allow user to retake photos if validation fails
            max_attempts: Maximum capture attempts before failing
            timeout_minutes: Timeout for capture process
            min_quality_score: Minimum image quality score (0-100)
            max_timestamp_age_seconds: Max age of capture timestamp
            detect_photo: Whether to detect photo/face presence
            detect_text: Whether to detect text presence
            detect_codes: Whether to detect and decode QR/barcodes
            validation_config: Additional validation parameters
        """
        # Default instructions
        default_instructions = {
            "front": "Captura el FRENTE del documento",
            "back": "Captura el REVERSO del documento"
        }
        self.instructions = instructions or default_instructions

        # Detection settings
        self.detect_photo = detect_photo
        self.detect_text = detect_text
        self.detect_codes = detect_codes

        # Simple form config for waiting_for="id_capture" pattern
        form_config = {
            "title": "Captura de Documento de Identidad",
            "description": "Captura ambos lados del documento usando la cámara de tu dispositivo.",
            "capture_type": "id_document",
            "instructions": [
                "Asegúrate de tener buena iluminación",
                "Mantén el documento plano y centrado",
                "Verifica que todo el texto sea legible",
                "Ambas capturas (frente y reverso) son obligatorias"
            ],
            "camera_settings": {
                "facingMode": "environment",  # Back camera for documents
                "width": {"min": 1280, "ideal": 1920, "max": 3840},
                "height": {"min": 720, "ideal": 1080, "max": 2160},
                "aspectRatio": {"ideal": 1.777},  # 16:9 for documents
                "frameRate": {"ideal": 30}
            },
            "capture_requirements": {
                "show_document_guide": True,
                "auto_capture": False,
                "preview_before_submit": True,
                "allow_retake": allow_retake,
                "max_file_size": 10 * 1024 * 1024,  # 10MB per image
                "require_permissions": ["camera"],
                "validation_feedback": True,
                "detect_edges": True,  # Document edge detection
                "min_document_coverage": 0.7  # 70% of frame should be document
            }
        }

        kwargs['form_config'] = form_config
        kwargs['required_fields'] = ["document_front", "document_back"]

        # Initialize with base class (handles common parameters)
        super().__init__(task_id, **kwargs)

        # Store ID capture specific attributes
        self.form_config = form_config
        self.required_fields = ["document_front", "document_back"]
        self.output_key = output_key
        self.detect_photo = detect_photo
        self.detect_text = detect_text
        self.detect_codes = detect_codes
        self.validation_config = validation_config or {}

    def get_waiting_for_key(self) -> str:
        """Override to specify ID capture waiting key"""
        return "id_capture"

    def execute(self, context: Dict[str, Any]) -> TaskResult:
        """Synchronous wrapper for async execution"""
        return asyncio.run(self.execute_async(context))

    async def execute_async(self, context: Dict[str, Any]) -> TaskResult:
        """Main execution logic for document capture and validation"""
        try:
            logger.info(f"📄 IDCaptureOperator: Starting document capture")

            input_key = f"{self.task_id}_input"
            attempts_key = f"{self.task_id}_attempts"

            # Check for timeout using base class method
            if self.has_timed_out(context):
                return TaskResult(
                    status="failed",
                    error=f"Document capture timed out after {self.timeout_minutes} minutes"
                )

            if input_key not in context:
                # No input yet - wait for ID document capture
                self.state.waiting_for = "id_capture"

                return TaskResult(
                    status="waiting",
                    data={
                        "waiting_for": "id_capture",
                        "form_config": self.form_config,
                        "required_fields": ["document_front", "document_back"],
                        "capture_attempts": context.get(attempts_key, 0),
                        "max_attempts": self.max_attempts,
                        "timeout_remaining": self.get_remaining_time(context)
                    },
                    retry_delay=30
                )

            # Get captured document data
            document_input = context[input_key]
            logger.info(f"📄 IDCaptureOperator: Received document input, validating...")

            # Extract image data and metadata using base class methods - THIS IS THE KEY FIX!
            if not isinstance(document_input, dict) or 'document_front' not in document_input or 'document_back' not in document_input:
                return self.handle_validation_error(
                    context,
                    "Invalid document data format",
                    "missing_image_data"
                )

            front_data_raw = document_input.get('document_front')
            back_data_raw = document_input.get('document_back')
            capture_metadata_raw = document_input.get('metadata', {})

            # Use base class methods for extraction - exactly like SelfieOperator!
            front_data, front_file_metadata = self.extract_image_from_formdata(front_data_raw)
            back_data, back_file_metadata = self.extract_image_from_formdata(back_data_raw)
            capture_metadata = self.parse_metadata(capture_metadata_raw)

            # Merge file metadata into capture metadata
            for key, value in front_file_metadata.items():
                if key not in capture_metadata:
                    capture_metadata[f"front_{key}"] = value
            for key, value in back_file_metadata.items():
                if key not in capture_metadata:
                    capture_metadata[f"back_{key}"] = value

            if not front_data or not back_data:
                return self.handle_validation_error(
                    context,
                    "Both front and back captures are required",
                    "missing_captures"
                )

            # Validate each image using base class validation
            front_validation = await self.validate_capture(front_data, capture_metadata)
            back_validation = await self.validate_capture(back_data, capture_metadata)

            # Combine validation results
            avg_quality = (front_validation['quality_score'] + back_validation['quality_score']) / 2

            # Create combined validation result
            validation_result = {
                'valid': front_validation['valid'] and back_validation['valid'],
                'errors': front_validation.get('errors', []) + back_validation.get('errors', []),
                'quality_score': int(avg_quality),
                'front_quality': front_validation['quality_score'],
                'back_quality': back_validation['quality_score'],
                'front_validation': front_validation,
                'back_validation': back_validation,
                'validation_timestamp': datetime.utcnow().isoformat()
            }

            # Add document-specific validations if enabled
            if validation_result['valid']:
                detected_elements = {}

                # Convert to bytes for detection
                front_bytes = self.convert_to_bytes(front_data)
                back_bytes = self.convert_to_bytes(back_data)

                # Photo detection (if enabled)
                if self.detect_photo:
                    front_has_photo = self._detect_photo_presence(front_bytes)
                    back_has_photo = self._detect_photo_presence(back_bytes)
                    detected_elements['photo'] = {
                        'front': front_has_photo,
                        'back': back_has_photo,
                        'found': front_has_photo or back_has_photo
                    }

                # Text detection (if enabled)
                if self.detect_text:
                    front_has_text = self._detect_text_presence(front_bytes)
                    back_has_text = self._detect_text_presence(back_bytes)
                    detected_elements['text'] = {
                        'front': front_has_text,
                        'back': back_has_text,
                        'found': front_has_text or back_has_text
                    }

                # Code detection (if enabled)
                if self.detect_codes:
                    front_codes = self._detect_and_decode_codes(front_bytes)
                    back_codes = self._detect_and_decode_codes(back_bytes)
                    detected_elements['codes'] = {
                        'front': front_codes,
                        'back': back_codes,
                        'total_found': len(front_codes) + len(back_codes)
                    }

                validation_result['detected_elements'] = detected_elements

            if not validation_result['valid']:
                return self.handle_validation_error(
                    context,
                    f"Document validation failed: {', '.join(validation_result['errors'])}",
                    validation_result.get('reason', 'validation_failed'),
                    validation_result
                )

            # Build comprehensive provenance record using base class
            provenance = self.build_provenance(
                [front_data, back_data],  # List for multiple images
                capture_metadata,
                validation_result,
                context,
                {"operator_specific": "id_document_capture", "document_sides": ["front", "back"]}
            )

            # Prepare output for context
            front_filename = self.generate_filename(context, "front")
            back_filename = self.generate_filename(context, "back")

            output_data = {
                self.output_key: {
                    "front_image": front_data,
                    "back_image": back_data,
                    "front_filename": front_filename,
                    "back_filename": back_filename,
                    "content_type": "image/jpeg",
                    "detected_elements": validation_result.get('detected_elements', {}),
                    "decoded_codes": validation_result.get('decoded_codes', []),
                    "provenance": provenance,
                    "validation": validation_result,
                    "captured_at": provenance['capture_timestamp'],
                    "validated_at": datetime.utcnow().isoformat()
                },
                # Add direct keys for S3UploadOperator compatibility (with _ prefix to exclude from parent context)
                "_id_front_image": {
                    "content": front_data,
                    "filename": front_filename,
                    "content_type": "image/jpeg",
                    "size": len(base64.b64decode(front_data)) if isinstance(front_data, str) else len(front_data),
                },
                "_id_back_image": {
                    "content": back_data,
                    "filename": back_filename,
                    "content_type": "image/jpeg",
                    "size": len(base64.b64decode(back_data)) if isinstance(back_data, str) else len(back_data),
                },
                f"{self.task_id}_validated": True,
                f"{self.task_id}_captured_at": provenance['capture_timestamp'],
                f"{self.task_id}_provenance": provenance,
                f"{self.task_id}_quality_score": validation_result['quality_score']
            }

            # Log successful capture
            await self.log_info(
                f"Document captured and validated successfully",
                details={
                    "quality_score": validation_result['quality_score'],
                    "detected_elements": validation_result.get('detected_elements', {}),
                    "codes_found": len(validation_result.get('decoded_codes', [])),
                    "attempts": context.get(attempts_key, 0) + 1
                }
            )

            return TaskResult(
                status="continue",
                data=output_data
            )

        except Exception as e:
            error_msg = f"Document capture failed: {str(e)}"
            logger.error(f"📄 IDCaptureOperator error: {e}")

            await self.log_error(
                "Document capture operation failed",
                error=e,
                details={
                    "task_id": self.task_id
                }
            )

            return TaskResult(
                status="failed",
                error=error_msg
            )

    def _handle_validation_error(
        self,
        context: Dict[str, Any],
        error_message: str,
        error_reason: str,
        validation_result: Optional[Dict] = None
    ) -> TaskResult:
        """Handle validation errors with retry logic"""
        attempts_key = f"{self.task_id}_attempts"
        attempts = context.get(attempts_key, 0) + 1

        # Store attempt count
        context[attempts_key] = attempts

        logger.warning(f"📄 IDCaptureOperator validation failed (attempt {attempts}): {error_message}")

        if attempts >= self.max_attempts:
            return TaskResult(
                status="failed",
                error=f"Maximum document capture attempts ({self.max_attempts}) exceeded. Last error: {error_message}"
            )

        # Allow retry with feedback
        return TaskResult(
            status="waiting",
            data={
                "waiting_for": "id_capture",
                "form_config": self.form_config,
                "required_fields": ["document_front", "document_back"],
                "validation_errors": validation_result.get('errors', [error_message]) if validation_result else [error_message],
                "capture_attempts": attempts,
                "max_attempts": self.max_attempts,
                "retry_reason": error_reason,
                "user_feedback": self._get_user_feedback(error_reason, validation_result),
                "timeout_remaining": self._get_remaining_time(context)
            }
        )

    async def _validate_captures(
        self,
        front_data: Union[str, bytes],
        back_data: Union[str, bytes],
        front_metadata: Dict[str, Any],
        back_metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Comprehensive validation of document captures"""
        errors = []
        validation_details = {}

        try:
            # Convert to bytes if base64
            logger.info(f"📄 IDCaptureOperator DEBUG - front_data type: {type(front_data)}")
            logger.info(f"📄 IDCaptureOperator DEBUG - back_data type: {type(back_data)}")

            front_bytes = self._convert_to_bytes(front_data)
            back_bytes = self._convert_to_bytes(back_data)

            logger.info(f"📄 IDCaptureOperator DEBUG - front_bytes result: {front_bytes is not None}, length: {len(front_bytes) if front_bytes else 0}")
            logger.info(f"📄 IDCaptureOperator DEBUG - back_bytes result: {back_bytes is not None}, length: {len(back_bytes) if back_bytes else 0}")

            if front_bytes is None or back_bytes is None:
                logger.error(f"📄 IDCaptureOperator DEBUG - Conversion failed! front: {front_bytes is not None}, back: {back_bytes is not None}")
                return {"valid": False, "errors": ["Invalid image data format"], "reason": "invalid_format"}

            # 1. Timestamp validation for both captures
            front_timestamp = self._validate_timestamp(front_metadata)
            back_timestamp = self._validate_timestamp(back_metadata)

            if not front_timestamp['valid']:
                errors.extend([f"Front: {err}" for err in front_timestamp['errors']])
            if not back_timestamp['valid']:
                errors.extend([f"Back: {err}" for err in back_timestamp['errors']])

            validation_details['timestamp_verified'] = front_timestamp['valid'] and back_timestamp['valid']

            # 2. Browser fingerprint validation
            fingerprint_valid = self._validate_browser_fingerprint(front_metadata)
            if not fingerprint_valid['valid']:
                errors.extend(fingerprint_valid['errors'])
            validation_details['browser_fingerprint_verified'] = fingerprint_valid['valid']

            # 3. Live capture validation
            front_live = self._validate_live_capture(front_bytes, front_metadata)
            back_live = self._validate_live_capture(back_bytes, back_metadata)

            if not front_live['valid']:
                errors.extend([f"Front: {err}" for err in front_live['errors']])
            if not back_live['valid']:
                errors.extend([f"Back: {err}" for err in back_live['errors']])

            validation_details['live_capture_verified'] = front_live['valid'] and back_live['valid']

            # 4. Image quality assessment
            front_quality = self._assess_image_quality(front_bytes)
            back_quality = self._assess_image_quality(back_bytes)

            # Debug logging for each image quality
            logger.info(f"📄 IDCaptureOperator DEBUG - front_quality: {front_quality}")
            logger.info(f"📄 IDCaptureOperator DEBUG - back_quality: {back_quality}")
            logger.info(f"📄 IDCaptureOperator DEBUG - front_bytes length: {len(front_bytes) if front_bytes else 0}")
            logger.info(f"📄 IDCaptureOperator DEBUG - back_bytes length: {len(back_bytes) if back_bytes else 0}")

            avg_quality = (front_quality['score'] + back_quality['score']) / 2
            validation_details['quality_score'] = int(avg_quality)
            validation_details['front_quality'] = front_quality['score']
            validation_details['back_quality'] = back_quality['score']

            if avg_quality < self.min_quality_score:
                errors.append(f"Image quality too low: {avg_quality} (minimum: {self.min_quality_score})")

            # 5. Generic element detection
            detected_elements = {}
            decoded_codes = []

            # Detect photo presence (if enabled)
            if self.detect_photo:
                front_has_photo = self._detect_photo_presence(front_bytes)
                back_has_photo = self._detect_photo_presence(back_bytes)
                detected_elements['photo'] = {
                    'front': front_has_photo,
                    'back': back_has_photo,
                    'any': front_has_photo or back_has_photo
                }

            # Detect text presence (if enabled)
            if self.detect_text:
                front_has_text = self._detect_text_presence(front_bytes)
                back_has_text = self._detect_text_presence(back_bytes)
                detected_elements['text'] = {
                    'front': front_has_text,
                    'back': back_has_text,
                    'any': front_has_text or back_has_text
                }

            # Detect and decode QR/barcodes (if enabled)
            if self.detect_codes:
                front_codes = self._detect_and_decode_codes(front_bytes)
                back_codes = self._detect_and_decode_codes(back_bytes)

                all_codes = front_codes + back_codes
                decoded_codes = all_codes

                detected_elements['codes'] = {
                    'front': len(front_codes),
                    'back': len(back_codes),
                    'total': len(all_codes),
                    'types': list(set([code['type'] for code in all_codes]))
                }

            validation_details['detected_elements'] = detected_elements
            validation_details['decoded_codes'] = decoded_codes

            # Overall validation result
            validation_details.update({
                'valid': len(errors) == 0,
                'errors': errors,
                'reason': errors[0] if errors else None,
                'validation_timestamp': datetime.utcnow().isoformat()
            })

            return validation_details

        except Exception as e:
            logger.error(f"Document validation error: {e}")
            return {
                'valid': False,
                'errors': [f"Validation failed: {str(e)}"],
                'reason': 'validation_exception'
            }

    def _convert_to_bytes(self, image_data: Union[str, bytes]) -> Optional[bytes]:
        """Convert base64 string or bytes to bytes"""
        if isinstance(image_data, str):
            try:
                return base64.b64decode(image_data)
            except Exception:
                return None
        return image_data

    def _validate_timestamp(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Validate capture timestamp"""
        capture_timestamp = metadata.get('captured_at')
        if not capture_timestamp:
            return {
                'valid': False,
                'errors': ["Missing capture timestamp"]
            }

        try:
            if isinstance(capture_timestamp, str):
                capture_time = datetime.fromisoformat(capture_timestamp.replace('Z', '+00:00'))
            else:
                capture_time = datetime.utcnow()

            time_diff = (datetime.utcnow() - capture_time.replace(tzinfo=None)).total_seconds()

            if time_diff > self.max_timestamp_age_seconds:
                return {
                    'valid': False,
                    'errors': [f"Capture timestamp too old: {time_diff} seconds (max: {self.max_timestamp_age_seconds})"]
                }

            return {
                'valid': True,
                'age_seconds': time_diff
            }

        except Exception as e:
            return {
                'valid': False,
                'errors': [f"Invalid timestamp format: {str(e)}"]
            }

    def _validate_browser_fingerprint(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Validate browser/device fingerprint for authenticity"""
        required_fields = [
            'user_agent', 'platform', 'screen_resolution',
            'capture_method', 'media_devices_available'
        ]

        missing_fields = []
        for field in required_fields:
            if field not in metadata:
                missing_fields.append(field)

        if missing_fields:
            return {
                'valid': False,
                'errors': [f"Missing browser fingerprint fields: {', '.join(missing_fields)}"]
            }

        # Validate user agent
        user_agent = metadata.get('user_agent', '')
        if not user_agent or 'bot' in user_agent.lower() or len(user_agent) < 20:
            return {
                'valid': False,
                'errors': ["Invalid or suspicious user agent"]
            }

        # Check MediaDevices API support
        if not metadata.get('media_devices_available'):
            return {
                'valid': False,
                'errors': ["MediaDevices API not available - camera access required"]
            }

        # Validate capture method
        if metadata.get('capture_method') != 'getUserMedia':
            return {
                'valid': False,
                'errors': ["Invalid capture method - must use camera"]
            }

        return {'valid': True}

    def _validate_live_capture(self, image_bytes: bytes, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Validate this is a live camera capture, not an uploaded file"""
        # Check capture source
        if metadata.get('capture_source') != 'canvas':
            return {
                'valid': False,
                'errors': ["Not captured from live camera stream"]
            }

        # Check for file upload indicators
        if metadata.get('file_upload_detected'):
            return {
                'valid': False,
                'errors': ["File upload detected - live capture required"]
            }

        # Check for drag-and-drop
        if metadata.get('input_method') == 'file':
            return {
                'valid': False,
                'errors': ["File input detected - camera capture required"]
            }

        return {'valid': True}

    def _assess_image_quality(self, image_bytes: bytes) -> Dict[str, Any]:
        """Assess image quality metrics"""
        try:
            # Convert to OpenCV format
            nparr = np.frombuffer(image_bytes, np.uint8)
            image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

            if image is None:
                return {'score': 0, 'error': 'Invalid image'}

            # Calculate quality metrics
            height, width = image.shape[:2]

            # Resolution score (0-30)
            min_dimension = min(width, height)
            resolution_score = min(30, (min_dimension / 480) * 30)

            # Brightness score (0-25)
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            mean_brightness = np.mean(gray)
            brightness_score = max(0, 25 - abs(mean_brightness - 127) * 0.2)

            # Sharpness score (0-25) - using Laplacian variance
            laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
            sharpness_score = min(25, laplacian_var / 10)

            # Contrast score (0-20)
            contrast = gray.std()
            contrast_score = min(20, contrast / 3)

            total_score = resolution_score + brightness_score + sharpness_score + contrast_score

            return {
                'score': int(total_score),
                'resolution': {'width': width, 'height': height, 'score': resolution_score},
                'brightness': {'value': mean_brightness, 'score': brightness_score},
                'sharpness': {'variance': laplacian_var, 'score': sharpness_score},
                'contrast': {'value': contrast, 'score': contrast_score}
            }

        except Exception as e:
            logger.error(f"Quality assessment error: {e}")
            return {'score': 0, 'error': str(e)}

    def _detect_photo_presence(self, image_bytes: bytes) -> bool:
        """Detect if image contains a person's photo using face detection"""
        try:
            # Convert to OpenCV format
            nparr = np.frombuffer(image_bytes, np.uint8)
            image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

            # Use Haar cascade for face detection
            face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
            faces = face_cascade.detectMultiScale(gray, 1.1, 4)

            return len(faces) > 0
        except Exception as e:
            logger.warning(f"Photo detection error: {e}")
            return False

    def _detect_text_presence(self, image_bytes: bytes) -> bool:
        """Detect if image contains readable text"""
        try:
            # Convert to PIL Image
            image = Image.open(io.BytesIO(image_bytes))

            # Extract text using OCR
            text = pytesseract.image_to_string(image, lang='spa+eng')

            # Basic text validation - at least 10 characters and some letters
            cleaned_text = ''.join(char for char in text if char.isalnum() or char.isspace())
            has_letters = any(char.isalpha() for char in cleaned_text)

            return len(cleaned_text.strip()) >= 10 and has_letters
        except Exception as e:
            logger.warning(f"Text detection error: {e}")
            return False

    def _detect_and_decode_codes(self, image_bytes: bytes) -> List[Dict[str, Any]]:
        """Detect and decode QR codes and barcodes in image"""
        codes = []
        try:
            # Convert to PIL Image
            image = Image.open(io.BytesIO(image_bytes))

            # Detect and decode all codes using pyzbar
            detected_codes = pyzbar.decode(image)

            for code in detected_codes:
                try:
                    # Decode data
                    decoded_data = code.data.decode('utf-8')

                    codes.append({
                        'type': code.type,
                        'data': decoded_data,
                        'rect': {
                            'left': code.rect.left,
                            'top': code.rect.top,
                            'width': code.rect.width,
                            'height': code.rect.height
                        },
                        'polygon': [(point.x, point.y) for point in code.polygon],
                        'quality': code.quality if hasattr(code, 'quality') else None
                    })

                    logger.info(f"Decoded {code.type}: {decoded_data[:50]}...")

                except UnicodeDecodeError:
                    # Handle binary data
                    codes.append({
                        'type': code.type,
                        'data': code.data.hex(),  # Convert to hex string
                        'data_format': 'hex',
                        'rect': {
                            'left': code.rect.left,
                            'top': code.rect.top,
                            'width': code.rect.width,
                            'height': code.rect.height
                        },
                        'polygon': [(point.x, point.y) for point in code.polygon]
                    })

        except Exception as e:
            logger.warning(f"Code detection error: {e}")

        return codes

    def _build_provenance(
        self,
        front_data: Union[str, bytes],
        back_data: Union[str, bytes],
        front_metadata: Dict[str, Any],
        back_metadata: Dict[str, Any],
        validation_result: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Build comprehensive provenance record for KYC compliance"""

        # Calculate image hashes
        front_bytes = self._convert_to_bytes(front_data)
        back_bytes = self._convert_to_bytes(back_data)

        return {
            # Timing information
            "capture_timestamp": front_metadata.get('captured_at'),
            "processing_timestamp": datetime.utcnow().isoformat(),
            "validation_timestamp": validation_result.get('validation_timestamp'),

            # Capture method and source
            "capture_method": "browser_camera_dual",
            "capture_source": "canvas",
            "input_method": "camera_only",
            "document_sides": ["front", "back"],

            # Browser fingerprint (from front metadata - should be same for both)
            "browser_fingerprint": {
                "user_agent": front_metadata.get('user_agent'),
                "platform": front_metadata.get('platform'),
                "browser_name": front_metadata.get('browser_name'),
                "language": front_metadata.get('language'),
                "screen_resolution": front_metadata.get('screen_resolution'),
                "device_memory": front_metadata.get('device_memory'),
                "pixel_ratio": front_metadata.get('device_pixel_ratio')
            },

            # Camera info
            "camera_info": {
                "facing_mode": "environment",
                "front_resolution": front_metadata.get('capture_resolution'),
                "back_resolution": back_metadata.get('capture_resolution'),
                "media_devices_available": front_metadata.get('media_devices_available')
            },

            # Session context
            "session_context": {
                "session_id": context.get('session_id'),
                "instance_id": context.get('instance_id'),
                "workflow_id": context.get('workflow_id'),
                "user_id": context.get('user_id'),
                "operator_task_id": self.task_id,
                "operator_version": "1.0"
            },

            # Security and integrity
            "integrity": {
                "front_image_hash": hashlib.sha256(front_bytes).hexdigest() if front_bytes else None,
                "back_image_hash": hashlib.sha256(back_bytes).hexdigest() if back_bytes else None,
                "front_size_bytes": len(front_bytes) if front_bytes else 0,
                "back_size_bytes": len(back_bytes) if back_bytes else 0,
                "validation_passed": validation_result['valid'],
                "quality_score": validation_result.get('quality_score', 0)
            },

            # Validation results
            "validation": {
                "timestamp_verified": validation_result.get('timestamp_verified', False),
                "browser_fingerprint_verified": validation_result.get('browser_fingerprint_verified', False),
                "live_capture_verified": validation_result.get('live_capture_verified', False),
                "quality_acceptable": validation_result.get('quality_score', 0) >= self.min_quality_score,
                "all_checks_passed": validation_result['valid']
            },

            # Detection results
            "detection": {
                "elements_detected": validation_result.get('detected_elements', {}),
                "codes_found": len(validation_result.get('decoded_codes', [])),
                "code_types": list(set([code['type'] for code in validation_result.get('decoded_codes', [])]))
            },

            # Compliance
            "compliance": {
                "kyc_compliant": validation_result['valid'],
                "gdpr_compliant": True,
                "capture_consent_given": True,
                "purpose_documented": True,
                "audit_trail_complete": True,
                "legal_basis": "identity_verification"
            }
        }

    def _generate_filename(self, context: Dict[str, Any], side: str) -> str:
        """Generate secure filename for document"""
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        instance_id = context.get('instance_id', 'unknown')[:8]

        return f"document_{side}_{instance_id}_{timestamp}.jpg"

    def _has_timed_out(self, context: Dict[str, Any]) -> bool:
        """Check if capture process has timed out"""
        start_time_key = f"{self.task_id}_start_time"
        if start_time_key not in context:
            context[start_time_key] = datetime.utcnow().isoformat()
            return False

        start_time = datetime.fromisoformat(context[start_time_key])
        elapsed = datetime.utcnow() - start_time
        return elapsed > timedelta(minutes=self.timeout_minutes)

    def _get_remaining_time(self, context: Dict[str, Any]) -> int:
        """Get remaining time in seconds"""
        start_time_key = f"{self.task_id}_start_time"
        if start_time_key not in context:
            return self.timeout_minutes * 60

        start_time = datetime.fromisoformat(context[start_time_key])
        elapsed = datetime.utcnow() - start_time
        remaining = timedelta(minutes=self.timeout_minutes) - elapsed
        return max(0, int(remaining.total_seconds()))

    def _get_user_feedback(self, error_reason: str, validation_result: Optional[Dict] = None) -> str:
        """Generate user-friendly feedback for validation errors"""
        feedback_map = {
            'missing_image_data': 'Por favor captura ambos lados del documento.',
            'missing_captures': 'Es necesario capturar tanto el frente como el reverso del documento.',
            'quality_too_low': 'La calidad de las imágenes es muy baja. Asegúrate de tener buena iluminación.',
            'not_live_capture': 'Debes usar la cámara para capturar el documento. No se permite subir archivos.',
            'validation_failed': 'La validación falló. Por favor intenta capturar el documento nuevamente.',
            'timestamp_too_old': 'Las capturas son muy antiguas. Toma nuevas fotos del documento.'
        }

        return feedback_map.get(error_reason, 'Por favor intenta capturar el documento nuevamente.')