"""
Base class for image capture operators with shared validation and extraction logic.

This base class provides common functionality for all image capture operations:
- FormData extraction from frontend submissions
- Base64 to bytes conversion
- Image quality assessment
- Timestamp validation
- Browser fingerprint validation
- Live capture validation
- Error handling and retry logic
- Provenance building

Used by SelfieOperator, IDCaptureOperator, and other image capture operators.
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

from .base import BaseOperator, TaskResult

logger = logging.getLogger(__name__)


class ImageCaptureOperator(BaseOperator):
    """Base class for all image capture operators with shared functionality"""

    def __init__(
        self,
        task_id: str,
        min_quality_score: int = 65,
        max_attempts: int = 3,
        timeout_minutes: int = 10,
        max_timestamp_age_seconds: int = 60,
        allow_retake: bool = True,
        **kwargs
    ):
        super().__init__(task_id, **kwargs)
        self.min_quality_score = min_quality_score
        self.max_attempts = max_attempts
        self.timeout_minutes = timeout_minutes
        self.max_timestamp_age_seconds = max_timestamp_age_seconds
        self.allow_retake = allow_retake

    def extract_image_from_formdata(self, image_data_raw: Any) -> tuple[Union[str, bytes], Dict[str, Any]]:
        """
        Extract image data from FormData format.

        Handles the format sent by frontend: {'base64': '...', 'filename': '...', 'content_type': '...'}
        Returns: (image_data, file_metadata)
        """
        if isinstance(image_data_raw, dict) and 'base64' in image_data_raw:
            image_data = image_data_raw['base64']
            file_metadata = {
                'filename': image_data_raw.get('filename'),
                'content_type': image_data_raw.get('content_type'),
                'file_size': image_data_raw.get('size')
            }
            return image_data, file_metadata
        else:
            # Direct string/bytes data
            return image_data_raw, {}

    def convert_to_bytes(self, image_data: Union[str, bytes]) -> Optional[bytes]:
        """Convert base64 string or bytes to bytes"""
        if isinstance(image_data, str):
            try:
                return base64.b64decode(image_data)
            except Exception as e:
                logger.error(f"Base64 decode error: {e}")
                return None
        elif isinstance(image_data, bytes):
            return image_data
        else:
            logger.error(f"Invalid image data type: {type(image_data)}")
            return None

    def parse_metadata(self, metadata_raw: Union[str, dict]) -> Dict[str, Any]:
        """Parse metadata from JSON string or dict"""
        if isinstance(metadata_raw, str):
            try:
                return json.loads(metadata_raw)
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to parse metadata JSON: {e}")
                return {}
        elif isinstance(metadata_raw, dict):
            return metadata_raw
        else:
            return {}

    async def validate_capture(
        self,
        image_data: Union[str, bytes],
        metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Comprehensive validation of image capture - used by all operators"""
        errors = []
        validation_details = {}

        try:
            # Convert to bytes if base64
            image_bytes = self.convert_to_bytes(image_data)
            if image_bytes is None:
                return {"valid": False, "errors": ["Invalid image data format"], "reason": "invalid_format"}

            # 1. Timestamp validation
            timestamp_valid = self.validate_timestamp(metadata)
            if not timestamp_valid['valid']:
                errors.extend(timestamp_valid['errors'])
            validation_details['timestamp_verified'] = timestamp_valid['valid']

            # 2. Browser fingerprint validation
            fingerprint_valid = self.validate_browser_fingerprint(metadata)
            if not fingerprint_valid['valid']:
                errors.extend(fingerprint_valid['errors'])
            validation_details['browser_fingerprint_verified'] = fingerprint_valid['valid']

            # 3. Live capture validation
            live_capture_valid = self.validate_live_capture(image_bytes, metadata)
            if not live_capture_valid['valid']:
                errors.extend(live_capture_valid['errors'])
            validation_details['live_capture_verified'] = live_capture_valid['valid']

            # 4. Image quality assessment
            quality_result = self.assess_image_quality(image_bytes)
            validation_details['quality_score'] = quality_result['score']
            if quality_result['score'] < self.min_quality_score:
                errors.append(f"Image quality too low: {quality_result['score']} (minimum: {self.min_quality_score})")

            # Overall validation result
            validation_details.update({
                'valid': len(errors) == 0,
                'errors': errors,
                'reason': errors[0] if errors else None,
                'validation_timestamp': datetime.utcnow().isoformat()
            })

            return validation_details

        except Exception as e:
            logger.error(f"Validation error: {e}")
            return {
                'valid': False,
                'errors': [f"Validation failed: {str(e)}"],
                'reason': 'validation_exception'
            }

    def validate_timestamp(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Validate capture timestamp - must be recent"""
        capture_timestamp = metadata.get('captured_at')
        if not capture_timestamp:
            return {
                'valid': False,
                'errors': ["Missing capture timestamp"]
            }

        try:
            if isinstance(capture_timestamp, str):
                capture_time = datetime.fromisoformat(capture_timestamp.replace('Z', '+00:00'))
            elif isinstance(capture_timestamp, (int, float)):
                capture_time = datetime.fromtimestamp(capture_timestamp)
            else:
                return {
                    'valid': False,
                    'errors': ["Invalid timestamp format"]
                }

            age_seconds = (datetime.utcnow() - capture_time.replace(tzinfo=None)).total_seconds()

            if age_seconds > self.max_timestamp_age_seconds:
                return {
                    'valid': False,
                    'errors': [f"Capture too old: {age_seconds:.1f}s (max: {self.max_timestamp_age_seconds}s)"]
                }

            return {'valid': True, 'age_seconds': age_seconds}

        except Exception as e:
            return {
                'valid': False,
                'errors': [f"Invalid timestamp format: {str(e)}"]
            }

    def validate_browser_fingerprint(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Validate browser fingerprint for authenticity"""
        fingerprint = metadata.get('browser_fingerprint', {})

        required_fields = ['language', 'timezone', 'color_depth', 'pixel_ratio']
        missing_fields = [field for field in required_fields if field not in fingerprint]

        if missing_fields:
            return {
                'valid': False,
                'errors': [f"Missing browser fingerprint fields: {missing_fields}"]
            }

        return {'valid': True}

    def validate_live_capture(self, image_bytes: bytes, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Validate that image was captured live, not uploaded"""
        errors = []

        # Check capture method
        capture_method = metadata.get('capture_method')
        if capture_method != 'getUserMedia':
            errors.append(f"Invalid capture method: {capture_method} (expected: getUserMedia)")

        # Check capture source
        capture_source = metadata.get('capture_source')
        if capture_source != 'canvas':
            errors.append(f"Invalid capture source: {capture_source} (expected: canvas)")

        # Check if media devices were available
        media_devices_available = metadata.get('media_devices_available')
        if not media_devices_available:
            errors.append("Media devices not available during capture")

        # Check live capture flag
        live_capture = metadata.get('live_capture')
        if not live_capture:
            errors.append("Live capture flag not set")

        return {
            'valid': len(errors) == 0,
            'errors': errors
        }

    def assess_image_quality(self, image_bytes: bytes) -> Dict[str, Any]:
        """Assess image quality metrics - exactly like SelfieOperator"""
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

    def build_provenance(
        self,
        image_data: Union[str, bytes, List],
        metadata: Dict[str, Any],
        validation_result: Dict[str, Any],
        context: Dict[str, Any],
        additional_info: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Build comprehensive provenance record"""

        # Handle multiple images for ID capture
        if isinstance(image_data, list):
            total_size = sum(len(base64.b64decode(img) if isinstance(img, str) else img) for img in image_data)
            image_count = len(image_data)
        else:
            image_bytes = self.convert_to_bytes(image_data)
            total_size = len(image_bytes) if image_bytes else 0
            image_count = 1

        provenance = {
            # Core capture information
            'capture_timestamp': metadata.get('captured_at', datetime.utcnow().isoformat()),
            'validation_timestamp': validation_result.get('validation_timestamp'),
            'operator_type': self.__class__.__name__,
            'task_id': self.task_id,

            # Technical details
            'image_count': image_count,
            'total_size_bytes': total_size,
            'capture_method': metadata.get('capture_method'),
            'capture_source': metadata.get('capture_source'),

            # Browser environment
            'user_agent': metadata.get('user_agent'),
            'platform': metadata.get('platform'),
            'browser_fingerprint': metadata.get('browser_fingerprint', {}),

            # Validation results
            'quality_score': validation_result.get('quality_score', 0),
            'validation_checks_passed': len([k for k, v in validation_result.items() if k.endswith('_verified') and v]),
            'all_validations_passed': validation_result.get('valid', False),

            # Context
            'workflow_instance_id': context.get('instance_id'),
            'step_sequence': context.get('current_step_sequence', 0)
        }

        # Add any additional information from specific operators
        if additional_info:
            provenance.update(additional_info)

        return provenance

    def handle_validation_error(
        self,
        context: Dict[str, Any],
        error_message: str,
        error_reason: str,
        validation_result: Optional[Dict] = None
    ) -> TaskResult:
        """Handle validation errors with retry logic - shared by all operators"""

        attempts_key = f"{self.task_id}_attempts"
        current_attempts = context.get(attempts_key, 0) + 1
        context[attempts_key] = current_attempts

        logger.warning(f"📄 {self.__class__.__name__} validation failed (attempt {current_attempts}): {error_message}")

        if not self.allow_retake or current_attempts >= self.max_attempts:
            return TaskResult(
                status="failed",
                error=f"Maximum capture attempts exceeded: {error_message}",
                data={
                    "validation_failed": True,
                    "attempts": current_attempts,
                    "reason": error_reason,
                    "validation_details": validation_result or {}
                }
            )

        # Allow retry
        waiting_for_key = f"{self.task_id}_input"
        if waiting_for_key in context:
            del context[waiting_for_key]

        return TaskResult(
            status="waiting",
            data={
                "waiting_for": self.get_waiting_for_key(),
                "validation_error": error_message,
                "attempts": current_attempts,
                "max_attempts": self.max_attempts,
                "can_retry": True,
                "validation_details": validation_result or {}
            },
            retry_delay=5
        )

    def get_waiting_for_key(self) -> str:
        """Override in subclasses to specify waiting_for key"""
        return "capture"

    def has_timed_out(self, context: Dict[str, Any]) -> bool:
        """Check if capture has timed out"""
        start_key = f"{self.task_id}_start_time"
        if start_key not in context:
            context[start_key] = datetime.utcnow().isoformat()
            return False

        start_time = datetime.fromisoformat(context[start_key])
        elapsed = datetime.utcnow() - start_time
        return elapsed.total_seconds() > (self.timeout_minutes * 60)

    def get_remaining_time(self, context: Dict[str, Any]) -> Optional[int]:
        """Get remaining time in seconds"""
        start_key = f"{self.task_id}_start_time"
        if start_key not in context:
            return self.timeout_minutes * 60

        start_time = datetime.fromisoformat(context[start_key])
        elapsed = datetime.utcnow() - start_time
        remaining = (self.timeout_minutes * 60) - elapsed.total_seconds()
        return max(0, int(remaining))

    def generate_filename(self, context: Dict[str, Any], suffix: str = "") -> str:
        """Generate filename for captured images"""
        instance_id = context.get('instance_id', 'unknown')
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')

        if suffix:
            return f"{self.task_id}_{suffix}_{instance_id}_{timestamp}.jpg"
        else:
            return f"{self.task_id}_{instance_id}_{timestamp}.jpg"