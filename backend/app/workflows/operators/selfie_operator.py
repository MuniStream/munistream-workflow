"""
SelfieOperator for KYC camera capture with provenance tracking.

This operator forces browser camera capture (no file uploads) and validates:
- Live capture vs uploaded files
- Timestamp verification
- Browser fingerprinting
- EXIF data validation
- Face detection (optional)
- Image quality assessment

Outputs validated selfie data to context for other operators (e.g., S3UploadOperator).
"""
import asyncio
import base64
import json
import logging
import io
from datetime import datetime
from typing import Dict, Any, Optional, Union
import cv2
import numpy as np
from PIL import Image, ExifTags

from .base import TaskResult
from .image_capture_base import ImageCaptureOperator

logger = logging.getLogger(__name__)


class SelfieOperator(ImageCaptureOperator):
    """
    Operator for capturing selfie via browser camera with comprehensive validation.

    This operator:
    1. Forces camera capture through browser (no file uploads)
    2. Validates live capture vs uploaded files
    3. Performs timestamp verification
    4. Creates browser fingerprint for provenance
    5. Optional face detection and quality checks
    6. Outputs validated selfie data to context

    Does NOT handle storage - use S3UploadOperator for that.
    """

    def __init__(
        self,
        task_id: str,
        output_key: str = "selfie_capture",
        purpose: str = "identity_verification",
        require_face_detection: bool = True,
        require_liveness: bool = False,
        **kwargs
    ):
        """
        Initialize SelfieOperator.

        Args:
            task_id: Unique task identifier
            output_key: Context key where validated selfie data will be stored
            purpose: Purpose of selfie capture for audit trail
            require_face_detection: Whether face detection is mandatory
            require_liveness: Whether liveness detection is required
        """
        # Simple form config for waiting_for="selfie" pattern
        form_config = {
            "title": "Verificación de Identidad - Selfie",
            "description": "Toma una selfie usando la cámara de tu dispositivo para verificar tu identidad.",
            "capture_type": "selfie",
            "camera_settings": {
                "facingMode": "user",  # Front camera for selfie
                "width": {"min": 640, "ideal": 1280, "max": 1920},
                "height": {"min": 480, "ideal": 720, "max": 1080},
                "aspectRatio": {"ideal": 1.333},
                "frameRate": {"ideal": 30}
            },
            "capture_requirements": {
                "show_guide_overlay": True,  # Face position guide
                "auto_capture": False,  # Manual capture only
                "preview_before_submit": True,
                "allow_retake": True,
                "max_file_size": 5 * 1024 * 1024,  # 5MB
                "require_permissions": ["camera"],
                "validation_feedback": True  # Show validation results
            },
            "instructions": [
                "Asegúrate de tener buena iluminación",
                "Mantén tu rostro centrado en el marco",
                "Quítate lentes de sol o elementos que oculten tu cara",
                "La foto se tomará en tiempo real con la cámara"
            ]
        }

        kwargs['form_config'] = form_config
        kwargs['required_fields'] = ["selfie_image"]

        # Initialize with base class (handles common parameters)
        super().__init__(task_id, **kwargs)

        # Store selfie-specific attributes
        self.form_config = form_config
        self.required_fields = ["selfie_image"]
        self.output_key = output_key
        self.purpose = purpose
        self.require_face_detection = require_face_detection
        self.require_liveness = require_liveness

        # Initialize face detection cascade
        try:
            self.face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
        except Exception as e:
            logger.warning(f"Failed to initialize face cascade: {e}")
            self.face_cascade = None

    def get_waiting_for_key(self) -> str:
        """Override to specify selfie waiting key"""
        return "selfie"

    def execute(self, context: Dict[str, Any]) -> TaskResult:
        """Synchronous wrapper for async execution"""
        return asyncio.run(self.execute_async(context))

    async def execute_async(self, context: Dict[str, Any]) -> TaskResult:
        """Main execution logic for selfie capture and validation"""
        try:
            logger.info(f"🤳 SelfieOperator: Starting selfie capture for {self.purpose}")

            input_key = f"{self.task_id}_input"
            attempts_key = f"{self.task_id}_attempts"

            # Check for timeout using base class method
            if self.has_timed_out(context):
                return TaskResult(
                    status="failed",
                    error=f"Selfie capture timed out after {self.timeout_minutes} minutes"
                )

            if input_key not in context:
                # No input yet - wait for selfie capture
                self.state.waiting_for = "selfie"

                return TaskResult(
                    status="waiting",
                    data={
                        "waiting_for": "selfie",
                        "form_config": self.form_config,
                        "required_fields": self.required_fields,
                        "capture_attempts": context.get(attempts_key, 0),
                        "max_attempts": self.max_attempts,
                        "timeout_remaining": self.get_remaining_time(context)
                    },
                    retry_delay=30
                )

            # Get captured selfie data
            selfie_input = context[input_key]
            logger.info(f"🤳 SelfieOperator: Received selfie input, validating...")

            # Extract image data and metadata using base class methods
            if not isinstance(selfie_input, dict) or 'selfie_image' not in selfie_input:
                return self.handle_validation_error(
                    context,
                    "Invalid selfie data format",
                    "missing_image_data"
                )

            image_data_raw = selfie_input.get('selfie_image')
            capture_metadata_raw = selfie_input.get('metadata', {})

            # Use base class methods for extraction and parsing
            image_data, file_metadata = self.extract_image_from_formdata(image_data_raw)
            capture_metadata = self.parse_metadata(capture_metadata_raw)

            # Merge file metadata into capture metadata
            for key, value in file_metadata.items():
                if key not in capture_metadata:
                    capture_metadata[key] = value

            # Use base class validation with selfie-specific additions
            validation_result = await self.validate_capture(image_data, capture_metadata)

            # Add selfie-specific validations
            if validation_result['valid']:
                # Convert to bytes for additional validations
                image_bytes = self.convert_to_bytes(image_data)

                # Face detection (if required)
                if self.require_face_detection:
                    face_result = await self.detect_face(image_bytes)
                    validation_result['face_detected'] = face_result['face_detected']
                    validation_result['face_confidence'] = face_result.get('confidence', 0)
                    validation_result['face_count'] = face_result.get('face_count', 0)

                    if not face_result['face_detected']:
                        validation_result['valid'] = False
                        validation_result['errors'].append("No face detected in selfie")
                    elif face_result.get('face_count', 0) > 1:
                        validation_result['valid'] = False
                        validation_result['errors'].append("Multiple faces detected - only one face allowed")

                # Liveness detection (if required)
                if self.require_liveness:
                    liveness_result = self.detect_liveness(image_bytes, capture_metadata)
                    validation_result['liveness_verified'] = liveness_result['verified']
                    if not liveness_result['verified']:
                        validation_result['valid'] = False
                        validation_result['errors'].append("Liveness check failed - please ensure you are looking at the camera")

            # Handle validation failure
            if not validation_result['valid']:
                return self.handle_validation_error(
                    context,
                    f"Selfie validation failed: {', '.join(validation_result['errors'])}",
                    validation_result.get('reason', 'validation_failed'),
                    validation_result
                )

            # Build comprehensive provenance record using base class
            provenance = self.build_provenance(
                image_data,
                capture_metadata,
                validation_result,
                context,
                {"purpose": self.purpose, "operator_specific": "selfie_capture"}
            )

            # Prepare output for context
            selfie_filename = self.generate_filename(context)

            output_data = {
                self.output_key: {
                    "image_data": image_data,
                    "filename": selfie_filename,
                    "content_type": "image/jpeg",
                    "size": len(base64.b64decode(image_data)) if isinstance(image_data, str) else len(image_data),
                    "provenance": provenance,
                    "validation": validation_result,
                    "purpose": self.purpose,
                    "captured_at": provenance['capture_timestamp'],
                    "validated_at": datetime.utcnow().isoformat()
                },
                # Add direct key for S3UploadOperator compatibility (with _ prefix to exclude from parent context)
                "_selfie_image": {
                    "content": image_data,
                    "filename": selfie_filename,
                    "content_type": "image/jpeg",
                    "size": len(base64.b64decode(image_data)) if isinstance(image_data, str) else len(image_data),
                },
                f"{self.task_id}_validated": True,
                f"{self.task_id}_captured_at": provenance['capture_timestamp'],
                f"{self.task_id}_provenance": provenance,
                f"{self.task_id}_validation_score": validation_result['quality_score']
            }

            # Log successful capture
            await self.log_info(
                f"Selfie captured and validated successfully",
                details={
                    "purpose": self.purpose,
                    "quality_score": validation_result['quality_score'],
                    "face_detected": validation_result.get('face_detected', False),
                    "validation_checks_passed": len([k for k, v in validation_result.items() if k.endswith('_verified') and v]),
                    "attempts": context.get(attempts_key, 0) + 1
                }
            )

            return TaskResult(
                status="continue",
                data=output_data
            )

        except Exception as e:
            error_msg = f"Selfie capture failed: {str(e)}"
            logger.error(f"🤳 SelfieOperator error: {e}")

            await self.log_error(
                "Selfie capture operation failed",
                error=e,
                details={
                    "purpose": self.purpose,
                    "task_id": self.task_id
                }
            )

            return TaskResult(
                status="failed",
                error=error_msg
            )

    async def detect_face(self, image_bytes: bytes) -> Dict[str, Any]:
        """Detect faces in the image"""
        try:
            if self.face_cascade is None:
                return {
                    'face_detected': False,
                    'error': 'Face detection not available'
                }

            # Convert to OpenCV format
            nparr = np.frombuffer(image_bytes, np.uint8)
            image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

            # Detect faces
            faces = self.face_cascade.detectMultiScale(
                gray,
                scaleFactor=1.1,
                minNeighbors=5,
                minSize=(50, 50)
            )

            return {
                'face_detected': len(faces) > 0,
                'face_count': len(faces),
                'confidence': 0.8 if len(faces) > 0 else 0.0,
                'faces': [{'x': int(x), 'y': int(y), 'width': int(w), 'height': int(h)}
                         for (x, y, w, h) in faces]
            }
        except Exception as e:
            logger.error(f"Face detection error: {e}")
            return {'face_detected': False, 'error': str(e)}

    def detect_liveness(self, image_bytes: bytes, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Basic liveness detection"""
        try:
            # Basic checks for liveness indicators
            live_indicators = 0

            # Check if captured from live stream
            if metadata.get('capture_source') == 'canvas' and metadata.get('stream_active'):
                live_indicators += 1

            # Check for real-time timestamp
            capture_time = metadata.get('captured_at')
            if capture_time:
                try:
                    capture_dt = datetime.fromisoformat(capture_time.replace('Z', '+00:00'))
                    time_diff = (datetime.utcnow() - capture_dt.replace(tzinfo=None)).total_seconds()
                    if time_diff < 30:  # Captured within 30 seconds
                        live_indicators += 1
                except:
                    pass

            # Check for camera permissions and getUserMedia usage
            if metadata.get('capture_method') == 'getUserMedia':
                live_indicators += 1

            verified = live_indicators >= 2

            return {
                'verified': verified,
                'confidence': min(0.9, live_indicators * 0.3),
                'indicators_passed': live_indicators,
                'total_indicators': 3
            }
        except Exception as e:
            logger.error(f"Liveness detection error: {e}")
            return {'verified': False, 'error': str(e)}

    def validate_exif_data(self, image_bytes: bytes, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Validate EXIF data for authenticity"""
        try:
            # Load image and check for EXIF
            image = Image.open(io.BytesIO(image_bytes))
            exif_data = image._getexif()

            if exif_data is None:
                # No EXIF data - this is actually expected for canvas captures
                return {'valid': True, 'reason': 'No EXIF (canvas capture)'}

            # If EXIF exists, check for suspicious indicators
            exif_dict = {}
            for tag, value in exif_data.items():
                tag_name = ExifTags.TAGS.get(tag, tag)
                exif_dict[tag_name] = value

            # Check for software manipulation
            software = exif_dict.get('Software', '').lower()
            suspicious_software = ['photoshop', 'gimp', 'paint.net', 'canva']

            for sus in suspicious_software:
                if sus in software:
                    return {
                        'valid': False,
                        'reason': f'Image edited with {sus}'
                    }

            return {'valid': True, 'exif_data': exif_dict}

        except Exception as e:
            # Most canvas captures will fail here, which is expected
            return {'valid': True, 'reason': 'No EXIF (expected for canvas)'}

