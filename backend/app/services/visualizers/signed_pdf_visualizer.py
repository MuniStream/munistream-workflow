"""
Signed PDF Visualizer

Generates PDF reports for entities that include digital signature information.
This visualizer is designed to work with entities that have signature data
in their data field (typically from SignerOperator).
"""
from typing import Dict, Any, Optional
import logging

from .pdf_visualizer import PDFVisualizer
from ..signature.signature_verifier import SignatureVerifier
from ...models.legal_entity import LegalEntity

logger = logging.getLogger(__name__)


class SignedPDFVisualizer(PDFVisualizer):
    """
    PDF visualizer that handles entities with digital signatures.

    This visualizer extends the basic PDF visualizer to include signature
    verification and display signature information in the generated PDF.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize signed PDF visualizer.

        Args:
            config: Configuration options including:
                - template: Template name (defaults to signed-specific template)
                - include_qr: Whether to include QR codes
                - verify_signatures: Whether to verify signatures before display
                - show_signature_details: Whether to show detailed signature info
                - signature_section: Configuration for signature display section
        """
        # Set default template for signed documents
        if config is None:
            config = {}

        if "template" not in config:
            config["template"] = "signed_entity.html"

        # Enable signature-related features by default
        config.setdefault("include_signatures", True)
        config.setdefault("verify_signatures", True)
        config.setdefault("show_signature_details", True)

        super().__init__(config)
        self.signature_verifier = SignatureVerifier()

    async def generate_pdf(self, entity: LegalEntity) -> bytes:
        """
        Generate PDF with signature information.

        Args:
            entity: Legal entity with signature data

        Returns:
            PDF bytes including signature information
        """
        self._log_generation_start(entity)

        try:
            # Validate entity and signature
            validation = await self.validate_entity(entity)
            if not validation["valid"]:
                raise ValueError(f"Entity validation failed: {validation['errors']}")

            # Check if entity has signature data
            signature_data = entity.data.get("signature") if entity.data else None
            if not signature_data:
                logger.warning(f"Entity {entity.entity_id} has no signature data, using basic PDF generation")
                return await super().generate_pdf(entity)

            # Verify signature if enabled
            signature_verified = False
            verification_report = None

            if self.config.get("verify_signatures", True):
                try:
                    # Prepare data that was originally signed
                    # This should match the data prepared by SignerOperator
                    signable_data = self._reconstruct_signable_data(entity, signature_data)

                    if signable_data:
                        verification_report = await self.signature_verifier.create_verification_report(
                            data=signable_data,
                            signature_data=signature_data
                        )
                        signature_verified = verification_report.get("overall_valid", False)

                        logger.info(f"Signature verification for entity {entity.entity_id}: {signature_verified}")
                    else:
                        logger.warning(f"Could not reconstruct signable data for entity {entity.entity_id}")

                except Exception as e:
                    logger.error(f"Signature verification failed for entity {entity.entity_id}: {e}")
                    verification_report = {
                        "verification_timestamp": self._get_current_timestamp(),
                        "error": str(e),
                        "overall_valid": False
                    }

            # Add signature verification to entity data for template rendering
            enhanced_entity_data = dict(entity.data)
            enhanced_entity_data["signature_verification"] = {
                "verified": signature_verified,
                "report": verification_report,
                "verification_enabled": self.config.get("verify_signatures", True)
            }

            # Create a temporary entity copy with enhanced data
            temp_entity = LegalEntity(
                entity_id=entity.entity_id,
                entity_type=entity.entity_type,
                owner_user_id=entity.owner_user_id,
                name=entity.name,
                data=enhanced_entity_data,
                visualization_config=entity.visualization_config,
                entity_display_config=entity.entity_display_config,
                status=entity.status,
                verified=entity.verified,
                created_at=entity.created_at,
                updated_at=entity.updated_at
            )

            # Generate PDF with signature information
            pdf_data = await self.report_generator.generate_entity_report(
                entity=temp_entity,
                template_name=self.config.get("template", "signed_entity.html"),
                include_qr=self.config.get("include_qr", True),
                include_signatures=True,
                format="pdf"
            )

            if not pdf_data:
                raise RuntimeError("Signed PDF generation returned empty data")

            self._log_generation_success(entity, len(pdf_data))
            return pdf_data

        except Exception as e:
            self._log_generation_error(entity, e)
            raise

    def _reconstruct_signable_data(self, entity: LegalEntity, signature_data: Dict[str, Any]) -> Optional[bytes]:
        """
        Reconstruct the data that was originally signed.

        This needs to match the format used by SignerOperator when preparing data for signing.

        Args:
            entity: Entity containing the signed data
            signature_data: Signature metadata

        Returns:
            Original signable data as bytes, or None if reconstruction fails
        """
        try:
            import json

            # Check if signature_data contains information about what was signed
            signed_fields = signature_data.get("signed_fields", [])

            if not signed_fields:
                logger.warning("No signed_fields information in signature data")
                return None

            # Reconstruct the signable data structure
            signable_data = {}

            # Get the fields that were originally signed from the signature metadata
            # This would typically include fields like entity data, timestamps, etc.
            for field in signed_fields:
                if field == "entity_id":
                    signable_data["entity_id"] = entity.entity_id
                elif field == "entity_type":
                    signable_data["entity_type"] = entity.entity_type
                elif field == "timestamp":
                    # Use the timestamp from signature data
                    signable_data["timestamp"] = signature_data.get("timestamp")
                elif field in entity.data:
                    # Get field from entity data, but exclude the signature itself
                    if field != "signature":
                        signable_data[field] = entity.data[field]

            # Add metadata similar to what SignerOperator would add
            signable_data.update({
                "signature_purpose": "entity_signing",
                "signed_fields": signed_fields
            })

            # Convert to canonical JSON bytes (same as SignerOperator)
            data_json = json.dumps(signable_data, sort_keys=True)
            return data_json.encode('utf-8')

        except Exception as e:
            logger.error(f"Failed to reconstruct signable data: {e}")
            return None

    async def validate_entity(self, entity: LegalEntity) -> Dict[str, Any]:
        """
        Validate entity for signed PDF generation.

        Args:
            entity: Entity to validate

        Returns:
            Validation results
        """
        result = await super().validate_entity(entity)

        # Additional validation for signed entities
        if entity and entity.data:
            signature_data = entity.data.get("signature")

            if not signature_data:
                result["warnings"].append("Entity has no signature data")
            else:
                # Validate signature structure
                required_signature_fields = ["signature", "certificate", "algorithm"]
                missing_fields = [f for f in required_signature_fields if f not in signature_data]

                if missing_fields:
                    result["errors"].append(f"Signature missing required fields: {missing_fields}")

                # Check certificate info
                cert_info = signature_data.get("certificate_info", {})
                if not cert_info:
                    result["warnings"].append("No certificate information available")

        return result

    def get_visualizer_info(self) -> Dict[str, Any]:
        """Get signed PDF visualizer information"""
        info = super().get_visualizer_info()
        info.update({
            "name": "SignedPDFVisualizer",
            "description": "PDF generator for entities with digital signatures",
            "features": [
                "PDF generation with signature info",
                "Signature verification",
                "Certificate information display",
                "QR code with verification data",
                "Signature validity indicators"
            ],
            "config_options": info.get("config_options", []) + [
                {
                    "name": "verify_signatures",
                    "type": "boolean",
                    "description": "Whether to verify signatures before PDF generation",
                    "default": True
                },
                {
                    "name": "show_signature_details",
                    "type": "boolean",
                    "description": "Whether to show detailed signature information",
                    "default": True
                }
            ]
        })
        return info

    async def get_preview_info(self, entity: LegalEntity) -> Dict[str, Any]:
        """
        Get preview information including signature status.

        Args:
            entity: Entity to get preview info for

        Returns:
            Preview information with signature details
        """
        preview_info = await super().get_preview_info(entity)

        # Add signature-specific preview information
        if entity.data and "signature" in entity.data:
            signature = entity.data["signature"]

            # Extract detailed signature info
            signature_preview = {
                "has_signature": True,
                "algorithm": signature.get("algorithm", "unknown"),
                "timestamp": signature.get("timestamp", signature.get("received_at")),
                "certificate_subject": signature.get("certificate_info", {}).get("subject", "unknown"),
                "certificate_issuer": signature.get("certificate_info", {}).get("issuer", "unknown"),
                "signed_fields": signature.get("signed_fields", [])
            }

            # Try to verify signature for preview
            if self.config.get("verify_signatures", True):
                try:
                    signable_data = self._reconstruct_signable_data(entity, signature)
                    if signable_data:
                        verification_report = await self.signature_verifier.create_verification_report(
                            data=signable_data,
                            signature_data=signature
                        )
                        signature_preview["verification_status"] = "verified" if verification_report.get("overall_valid") else "invalid"
                    else:
                        signature_preview["verification_status"] = "cannot_verify"
                except Exception as e:
                    signature_preview["verification_status"] = "verification_error"
                    signature_preview["verification_error"] = str(e)
            else:
                signature_preview["verification_status"] = "not_verified"

            preview_info["signature_preview"] = signature_preview

        return preview_info

    def supports_format(self, format_type: str) -> bool:
        """Check if format is supported"""
        return format_type.lower() in ["pdf"]