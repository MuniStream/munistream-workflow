"""
FacialVerificationOperator for comparing faces using DeepFace library.

This operator uses the DeepFace library to verify if faces in images match.
It accepts configurable context keys for source and target images, making it
generic and reusable across different workflow scenarios.

Features:
- Generic input configuration via context keys
- Support for multiple target images
- Configurable verification thresholds
- Multiple face recognition models
- Comprehensive verification results
- Uses base64 images directly (no temp files needed)
"""
import asyncio
import base64
import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Union

from .base import BaseOperator, TaskResult, TaskStatus

# Import DeepFace with error handling
try:
    from deepface import DeepFace
    DEEPFACE_AVAILABLE = True
except ImportError:
    DEEPFACE_AVAILABLE = False
    DeepFace = None

logger = logging.getLogger(__name__)


class FacialVerificationOperator(BaseOperator):
    """
    Generic operator for facial verification using DeepFace.

    This operator can compare any source image against multiple target images
    using configurable context keys. It's designed to be reusable across
    different workflow scenarios, not tied to specific operators.
    """

    def __init__(
        self,
        task_id: str,
        source_image_key: str,
        target_image_keys: List[str],
        output_key: str = "facial_verification_results",
        verification_threshold: float = 0.4,
        model_name: str = "VGG-Face",
        require_all_match: bool = False,
        min_confidence: float = 0.6,
        distance_metric: str = "cosine",
        enforce_detection: bool = True,
        fail_on_no_match: bool = True,
        extract_faces: bool = True,
        face_detector_backend: str = "opencv",
        min_face_size: int = 50,
        **kwargs
    ):
        """
        Initialize FacialVerificationOperator.

        Args:
            task_id: Unique task identifier
            source_image_key: Context key for source image (supports dot notation)
            target_image_keys: List of context keys for target images
            output_key: Context key where verification results will be stored
            verification_threshold: Threshold for face verification (lower = stricter)
            model_name: DeepFace model to use (VGG-Face, Facenet, ArcFace, etc.)
            require_all_match: Whether all targets must match (True) or any (False)
            min_confidence: Minimum confidence required for valid verification
            distance_metric: Distance metric (cosine, euclidean, euclidean_l2)
            enforce_detection: Whether to enforce face detection in all images
            fail_on_no_match: Whether to fail the workflow if no face match is found
            extract_faces: Whether to extract/crop faces before verification
            face_detector_backend: DeepFace detector backend (opencv, ssd, dlib, mtcnn, retinaface, mediapipe)
            min_face_size: Minimum face size in pixels for valid detection
        """
        super().__init__(task_id, **kwargs)

        if not DEEPFACE_AVAILABLE:
            raise ImportError("DeepFace library is required but not installed. Run: pip install deepface==0.0.96")

        self.source_image_key = source_image_key
        self.target_image_keys = target_image_keys
        self.output_key = output_key
        self.verification_threshold = verification_threshold
        self.model_name = model_name
        self.require_all_match = require_all_match
        self.min_confidence = min_confidence
        self.distance_metric = distance_metric
        self.enforce_detection = enforce_detection
        self.fail_on_no_match = fail_on_no_match
        self.extract_faces = extract_faces
        self.face_detector_backend = face_detector_backend
        self.min_face_size = min_face_size

        # Validate model name
        self.available_models = ["VGG-Face", "Facenet", "Facenet512", "OpenFace", "DeepFace", "DeepID", "ArcFace", "Dlib", "SFace"]
        if self.model_name not in self.available_models:
            logger.warning(f"Model {self.model_name} not in known models. Available: {self.available_models}")

    def execute(self, context: Dict[str, Any]) -> TaskResult:
        """Synchronous wrapper for async execution"""
        return asyncio.run(self.execute_async(context))

    async def execute_async(self, context: Dict[str, Any]) -> TaskResult:
        """Main execution logic for facial verification"""
        try:
            logger.info(f"[FACIAL_VERIFICATION] Starting facial verification using {self.model_name}")

            # Extract source image
            source_image = self._extract_image_from_context(context, self.source_image_key)
            if source_image is None:
                return TaskResult(
                    status="failed",
                    error=f"[FACIAL_VERIFICATION] Source image not found at key: {self.source_image_key}"
                )

            # Extract target images
            target_images = {}
            for target_key in self.target_image_keys:
                target_image = self._extract_image_from_context(context, target_key)
                if target_image is not None:
                    target_images[target_key] = target_image
                else:
                    logger.warning(f"Target image not found at key: {target_key}")

            if not target_images:
                return TaskResult(
                    status="failed",
                    error=f"[FACIAL_VERIFICATION] No target images found from keys: {self.target_image_keys}"
                )

            # Perform facial verification using base64 strings directly
            verification_results = await self._perform_verification(
                source_image,
                target_images
            )

            # Determine overall verification status
            overall_verified = self._determine_verification_status(verification_results)

            # Build comprehensive results
            results = {
                "verified": overall_verified,
                "verification_threshold": self.verification_threshold,
                "model_used": self.model_name,
                "distance_metric": self.distance_metric,
                "require_all_match": self.require_all_match,
                "min_confidence": self.min_confidence,
                "source_key": self.source_image_key,
                "target_keys": self.target_image_keys,
                "targets_processed": len(target_images),
                "verification_timestamp": datetime.utcnow().isoformat(),
                "best_match": self._find_best_match(verification_results),
                "all_comparisons": verification_results,
                "faces_detected": self._summarize_face_detection(verification_results)
            }

            # Check if facial verification failed and we should fail the workflow
            if not overall_verified and self.fail_on_no_match:
                # Log failure details
                error_details = []
                for comparison in verification_results:
                    if "error" in comparison:
                        error_details.append(f"{comparison['target_key']}: {comparison['error']}")
                    elif not comparison.get('verified', False):
                        error_details.append(f"{comparison['target_key']}: verification failed (confidence: {comparison.get('confidence', 0):.3f})")

                error_msg = f"Facial verification failed - no face match found. Details: {'; '.join(error_details)}"

                # Still store results for audit trail but fail the task
                output_data = {
                    self.output_key: results
                }

                logger.error(f"🔍 {error_msg}")

                return TaskResult(
                    status="failed",
                    error=error_msg,
                    data=output_data
                )

            # Store results in context
            output_data = {
                self.output_key: results
            }

            # Log results
            await self._log_verification_results(results)

            return TaskResult(
                status="continue",
                data=output_data
            )

        except Exception as e:
            error_msg = f"Facial verification failed: {str(e)}"
            logger.error(f"🔍 FacialVerificationOperator error: {e}")

            return TaskResult(
                status="failed",
                error=error_msg
            )

    def _extract_image_from_context(self, context: Dict[str, Any], key: str) -> Optional[str]:
        """Extract base64 image from context using dot notation"""
        try:
            # Support dot notation for nested keys
            current_value = context
            key_parts = key.split('.')

            for part in key_parts:
                if isinstance(current_value, dict) and part in current_value:
                    current_value = current_value[part]
                else:
                    logger.error(f"[FACIAL_VERIFICATION] Part '{part}' not found in context path {key}")
                    return None

            # Handle different possible image data formats
            if isinstance(current_value, str):
                # Clean base64 string
                cleaned_base64 = self._clean_base64_string(current_value)
                if cleaned_base64 and len(cleaned_base64) > 100:
                    return cleaned_base64
                else:
                    logger.warning(f"[FACIAL_VERIFICATION] Invalid base64 image data")
                    return None
            elif isinstance(current_value, dict):
                # Look for common image data keys
                for image_key in ['image_data', 'content', 'data', 'front_image', 'back_image']:
                    if image_key in current_value:
                        image_data = current_value[image_key]
                        if isinstance(image_data, str):
                            cleaned_base64 = self._clean_base64_string(image_data)
                            if cleaned_base64 and len(cleaned_base64) > 100:
                                return cleaned_base64
                logger.warning(f"[FACIAL_VERIFICATION] No valid image data found in dict with keys: {list(current_value.keys())}")

            return None

        except Exception as e:
            logger.error(f"[FACIAL_VERIFICATION] Error extracting image from context key {key}: {e}")
            return None

    def _clean_base64_string(self, base64_string: str) -> Optional[str]:
        """Clean and format base64 string for DeepFace compatibility"""
        try:
            if not base64_string:
                return None

            # Extract pure base64 data
            pure_base64 = base64_string

            # Remove data URL prefix if present (e.g., "data:image/jpeg;base64,")
            if base64_string.startswith('data:'):
                # Find the comma that separates the header from the base64 data
                comma_index = base64_string.find(',')
                if comma_index != -1:
                    pure_base64 = base64_string[comma_index + 1:]

            # Remove any whitespace
            pure_base64 = pure_base64.strip()

            # Basic validation - check if it looks like base64
            import base64
            try:
                # Test decode a small portion to validate
                base64.b64decode(pure_base64[:100])

                # DeepFace expects format: "data:image/jpeg," + base64_data
                formatted_string = "data:image/jpeg," + pure_base64
                logger.info(f"[FACIAL_VERIFICATION] Formatted base64 string for DeepFace, length: {len(formatted_string)}")
                return formatted_string
            except Exception:
                logger.error(f"[FACIAL_VERIFICATION] Invalid base64 format after cleaning")
                return None

        except Exception as e:
            logger.error(f"[FACIAL_VERIFICATION] Error cleaning base64 string: {e}")
            return None

    def _extract_face_from_image(self, base64_image: str) -> Optional[str]:
        """Extract and crop face from image using DeepFace"""
        try:
            if not self.extract_faces:
                # Return original image if face extraction is disabled
                return base64_image

            logger.info(f"[FACIAL_VERIFICATION] Extracting face using {self.face_detector_backend} detector")

            # Use DeepFace to extract faces
            faces = DeepFace.extract_faces(
                img_path=base64_image,
                detector_backend=self.face_detector_backend,
                enforce_detection=False,
                align=True
            )

            if not faces:
                logger.warning(f"[FACIAL_VERIFICATION] No faces detected in image")
                return None

            logger.info(f"[FACIAL_VERIFICATION] Detected {len(faces)} faces, type: {type(faces[0])}")

            # Take the first (largest) face detected
            face_data = faces[0]

            # Convert numpy array back to base64
            import numpy as np
            from PIL import Image
            import io

            # Handle different return formats from DeepFace.extract_faces
            if isinstance(face_data, dict):
                # Some versions return dict with 'face' key
                if 'face' in face_data:
                    face_array = face_data['face']
                else:
                    logger.warning(f"[FACIAL_VERIFICATION] Unexpected face data format: {list(face_data.keys())}")
                    return base64_image  # Fallback
            else:
                # Direct numpy array
                face_array = face_data

            # DeepFace.extract_faces returns normalized arrays [0,1], convert to [0,255]
            if hasattr(face_array, 'max') and face_array.max() <= 1.0:
                face_image = (face_array * 255).astype(np.uint8)
            elif hasattr(face_array, 'astype'):
                face_image = face_array.astype(np.uint8)
            else:
                logger.warning(f"[FACIAL_VERIFICATION] Invalid face array type: {type(face_array)}")
                return base64_image  # Fallback

            # Create PIL Image
            pil_image = Image.fromarray(face_image)

            # Mejora #3: Normalizar iluminación y contraste
            pil_image = self._enhance_image_quality(pil_image)

            # Resize to larger size for better detail (Mejora #5)
            pil_image = pil_image.resize((512, 512), Image.Resampling.LANCZOS)

            # Convert to base64
            buffer = io.BytesIO()
            pil_image.save(buffer, format='JPEG', quality=95)
            face_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')

            # Format for DeepFace
            formatted_face = f"data:image/jpeg,{face_base64}"

            logger.info(f"[FACIAL_VERIFICATION] Successfully extracted face, size: {len(formatted_face)}")
            return formatted_face

        except ImportError as e:
            logger.error(f"[FACIAL_VERIFICATION] Missing dependencies for face extraction: {e}")
            return base64_image  # Fallback to original image
        except Exception as e:
            logger.warning(f"[FACIAL_VERIFICATION] Face extraction failed: {e}, using original image")
            return base64_image  # Fallback to original image

    def _enhance_image_quality(self, pil_image):
        """Mejora #3: Normalizar iluminación y contraste"""
        try:
            from PIL import ImageEnhance, ImageOps

            # Ecualizar histograma para mejorar contraste
            pil_image = ImageOps.equalize(pil_image)

            # Ajustar contraste ligeramente
            enhancer = ImageEnhance.Contrast(pil_image)
            pil_image = enhancer.enhance(1.1)

            # Ajustar nitidez
            enhancer = ImageEnhance.Sharpness(pil_image)
            pil_image = enhancer.enhance(1.2)

            logger.info(f"[FACIAL_VERIFICATION] Applied image quality enhancements")
            return pil_image

        except Exception as e:
            logger.warning(f"[FACIAL_VERIFICATION] Image enhancement failed: {e}")
            return pil_image  # Return original if enhancement fails

    async def _perform_verification(
        self,
        source_base64: str,
        target_base64_dict: Dict[str, str]
    ) -> List[Dict[str, Any]]:
        """Perform facial verification using DeepFace with base64 strings"""
        results = []

        # Extract face from source image if enabled
        source_face = self._extract_face_from_image(source_base64)
        if source_face is None:
            logger.error(f"[FACIAL_VERIFICATION] Could not extract face from source image")
            return [{
                "target_key": "source_extraction",
                "verified": False,
                "error": "No face detected in source image",
                "confidence": 0.0,
                "distance": 1.0,
                "passes_threshold": False,
                "passes_confidence": False,
                "error_type": "face_extraction_error"
            }]

        for target_key, target_base64 in target_base64_dict.items():
            try:
                logger.info(f"[FACIAL_VERIFICATION] Comparing source with {target_key} using {self.model_name}")

                # Add input validation
                if not source_face or not target_base64:
                    raise ValueError("Empty base64 image data")

                if len(source_face) < 100 or len(target_base64) < 100:
                    raise ValueError("Base64 data too short to be valid image")

                # Extract face from target image if enabled
                target_face = self._extract_face_from_image(target_base64)
                if target_face is None:
                    logger.warning(f"[FACIAL_VERIFICATION] Could not extract face from {target_key}, skipping")
                    results.append({
                        "target_key": target_key,
                        "verified": False,
                        "error": f"No face detected in {target_key}",
                        "confidence": 0.0,
                        "distance": 1.0,
                        "passes_threshold": False,
                        "passes_confidence": False,
                        "error_type": "face_extraction_error"
                    })
                    continue

                # Use DeepFace.verify with face crops or original images
                verification_mode = "face crops" if self.extract_faces else "full images"
                logger.info(f"[FACIAL_VERIFICATION] Calling DeepFace.verify using {verification_mode}, enforce_detection={self.enforce_detection}")

                result = DeepFace.verify(
                    img1_path=source_face,
                    img2_path=target_face,
                    model_name=self.model_name,
                    distance_metric=self.distance_metric,
                    enforce_detection=self.enforce_detection
                )

                logger.info(f"[FACIAL_VERIFICATION] DeepFace result keys: {list(result.keys())}")

                # Calculate confidence from distance
                distance = result.get('distance', 1.0)
                confidence = max(0, 1.0 - distance) if distance <= 1.0 else 0

                # Determine if verification passes threshold
                verified = result.get('verified', False)
                passes_threshold = distance <= self.verification_threshold
                passes_confidence = confidence >= self.min_confidence

                verification_result = {
                    "target_key": target_key,
                    "verified": verified and passes_threshold and passes_confidence,
                    "deepface_verified": result.get('verified', False),
                    "distance": distance,
                    "confidence": confidence,
                    "threshold": result.get('threshold', self.verification_threshold),
                    "passes_threshold": passes_threshold,
                    "passes_confidence": passes_confidence,
                    "model": result.get('model', self.model_name),
                    "distance_metric": result.get('distance_metric', self.distance_metric),
                    "facial_areas": result.get('facial_areas', {}),
                    "time": result.get('time', 0),
                    "face_extraction_enabled": self.extract_faces,
                    "face_detector_backend": self.face_detector_backend if self.extract_faces else None
                }

                results.append(verification_result)

                logger.info(
                    f"[FACIAL_VERIFICATION] Verification complete for {target_key}: "
                    f"verified={verification_result['verified']}, "
                    f"confidence={confidence:.3f}, "
                    f"distance={distance:.3f}"
                )

            except ImportError as e:
                logger.error(f"[FACIAL_VERIFICATION] DeepFace import error: {e}")
                results.append({
                    "target_key": target_key,
                    "verified": False,
                    "error": f"DeepFace library error: {str(e)}",
                    "confidence": 0.0,
                    "distance": 1.0,
                    "passes_threshold": False,
                    "passes_confidence": False,
                    "error_type": "import_error"
                })
            except ValueError as e:
                logger.error(f"[FACIAL_VERIFICATION] Input validation error for {target_key}: {e}")
                results.append({
                    "target_key": target_key,
                    "verified": False,
                    "error": f"Input validation error: {str(e)}",
                    "confidence": 0.0,
                    "distance": 1.0,
                    "passes_threshold": False,
                    "passes_confidence": False,
                    "error_type": "validation_error"
                })
            except Exception as e:
                logger.error(f"[FACIAL_VERIFICATION] Unexpected error verifying {target_key}: {e}")
                logger.error(f"[FACIAL_VERIFICATION] Error type: {type(e).__name__}")
                results.append({
                    "target_key": target_key,
                    "verified": False,
                    "error": f"Verification error: {str(e)}",
                    "confidence": 0.0,
                    "distance": 1.0,
                    "passes_threshold": False,
                    "passes_confidence": False,
                    "error_type": type(e).__name__
                })

        return results

    def _determine_verification_status(self, results: List[Dict[str, Any]]) -> bool:
        """Determine overall verification status based on results"""
        if not results:
            return False

        verified_results = [r for r in results if r.get('verified', False)]

        if self.require_all_match:
            # All targets must match
            return len(verified_results) == len(results)
        else:
            # At least one target must match
            return len(verified_results) > 0

    def _find_best_match(self, results: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Find the best matching target"""
        verified_results = [r for r in results if r.get('verified', False)]

        if not verified_results:
            return None

        # Sort by highest confidence, then lowest distance
        best_match = max(
            verified_results,
            key=lambda r: (r.get('confidence', 0), -r.get('distance', 1))
        )

        return {
            "target_key": best_match["target_key"],
            "confidence": best_match.get("confidence", 0),
            "distance": best_match.get("distance", 1),
            "model": best_match.get("model", self.model_name)
        }

    def _summarize_face_detection(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Summarize face detection results"""
        targets_with_faces = []
        targets_without_faces = []

        for result in results:
            target_key = result["target_key"]

            # Check if faces were detected (no error and distance is reasonable)
            if "error" not in result and result.get("distance", 1.0) < 1.0:
                targets_with_faces.append(target_key)
            else:
                targets_without_faces.append(target_key)

        return {
            "source": True,  # Assume source has face if we got this far
            "targets_with_faces": targets_with_faces,
            "targets_without_faces": targets_without_faces,
            "total_targets": len(results)
        }

    async def _log_verification_results(self, results: Dict[str, Any]) -> None:
        """Log verification results for audit trail"""
        logger.info(
            f"🔍 Facial verification completed: "
            f"verified={results['verified']}, "
            f"targets_processed={results['targets_processed']}, "
            f"model={results['model_used']}"
        )

        if results.get('best_match'):
            best = results['best_match']
            logger.info(
                f"🔍 Best match: {best['target_key']} "
                f"(confidence={best['confidence']:.3f}, distance={best['distance']:.3f})"
            )