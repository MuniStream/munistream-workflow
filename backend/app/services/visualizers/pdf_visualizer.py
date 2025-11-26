"""
Basic PDF Visualizer

Generates PDF reports for entities using the existing PDF generation service.
"""
from typing import Dict, Any, Optional
import logging

from .base import EntityVisualizer
from ..pdf_generation import EntityReportGenerator
from ...models.legal_entity import LegalEntity

logger = logging.getLogger(__name__)


class PDFVisualizer(EntityVisualizer):
    """
    Basic PDF visualizer that generates standard PDF reports for entities.

    Uses the existing EntityReportGenerator to create PDF documents.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize PDF visualizer.

        Args:
            config: Configuration options including:
                - template: Template name to use
                - include_qr: Whether to include QR codes
                - page_size: PDF page size
                - style: Style options
        """
        super().__init__(config)
        self.report_generator = EntityReportGenerator()

    async def generate_pdf(self, entity: LegalEntity) -> bytes:
        """
        Generate PDF report for the entity.

        Args:
            entity: Legal entity to generate PDF for

        Returns:
            PDF bytes
        """
        self._log_generation_start(entity)

        try:
            # Validate entity first
            validation = await self.validate_entity(entity)
            if not validation["valid"]:
                raise ValueError(f"Entity validation failed: {validation['errors']}")

            # Get configuration options
            template_name = self.config.get("template", "default")
            include_qr = self.config.get("include_qr", True)
            include_signatures = self.config.get("include_signatures", False)

            # Get base_url from config if available
            base_url = self.config.get("base_url")

            # Generate PDF using the report generator
            pdf_data = await self.report_generator.generate_entity_report(
                entity=entity,
                template_name=template_name,
                include_qr=include_qr,
                include_signatures=include_signatures,
                format="pdf",
                base_url=base_url
            )

            if not pdf_data:
                raise RuntimeError("PDF generation returned empty data")

            self._log_generation_success(entity, len(pdf_data))
            return pdf_data

        except Exception as e:
            self._log_generation_error(entity, e)
            raise

    async def generate_html(self, entity: LegalEntity) -> str:
        """
        Generate HTML representation of the entity using templates.

        Args:
            entity: Legal entity to generate HTML for

        Returns:
            HTML string
        """
        try:
            # Validate entity first
            validation = await self.validate_entity(entity)
            if not validation["valid"]:
                raise ValueError(f"Entity validation failed: {validation['errors']}")

            # Get configuration options
            template_name = self.config.get("template", "default")
            include_qr = self.config.get("include_qr", True)
            include_signatures = self.config.get("include_signatures", False)

            # Get base_url from config if available
            base_url = self.config.get("base_url")

            # Generate HTML using the report generator
            html_data = await self.report_generator.generate_entity_report(
                entity=entity,
                template_name=template_name,
                include_qr=include_qr,
                include_signatures=include_signatures,
                format="html",
                base_url=base_url
            )

            if not html_data:
                raise RuntimeError("HTML generation returned empty data")

            # Convert bytes to string if needed
            if isinstance(html_data, bytes):
                return html_data.decode('utf-8')
            return html_data

        except Exception as e:
            self.logger.error(f"Failed to generate HTML for entity {entity.entity_id}: {e}")
            raise

    async def get_download_info(self, entity: LegalEntity) -> Dict[str, Any]:
        """
        Get download information for PDF.

        Args:
            entity: Entity to get download info for

        Returns:
            Download information dictionary
        """
        base_info = await super().get_download_info(entity)

        # Customize filename based on entity data
        if entity.name:
            safe_name = "".join(c for c in entity.name if c.isalnum() or c in (' ', '-', '_')).strip()
            base_info["filename"] = f"{safe_name}.pdf"
            base_info["suggested_name"] = f"{safe_name}.pdf"

        return base_info

    def supports_format(self, format_type: str) -> bool:
        """Check if format is supported"""
        return format_type.lower() in ["pdf", "html"]

    def get_visualizer_info(self) -> Dict[str, Any]:
        """Get visualizer information"""
        info = super().get_visualizer_info()
        info.update({
            "name": "PDFVisualizer",
            "description": "Basic PDF report generator for entities",
            "features": [
                "PDF generation",
                "QR code inclusion",
                "Template-based rendering",
                "Custom styling"
            ],
            "config_options": [
                {
                    "name": "template",
                    "type": "string",
                    "description": "Template name to use for PDF generation",
                    "default": "default"
                },
                {
                    "name": "include_qr",
                    "type": "boolean",
                    "description": "Whether to include QR codes in the PDF",
                    "default": True
                },
                {
                    "name": "include_signatures",
                    "type": "boolean",
                    "description": "Whether to include signature information",
                    "default": False
                }
            ]
        })
        return info

    async def validate_entity(self, entity: LegalEntity) -> Dict[str, Any]:
        """
        Validate entity for PDF generation.

        Args:
            entity: Entity to validate

        Returns:
            Validation results
        """
        result = await super().validate_entity(entity)

        # Additional validation for PDF generation
        if entity and entity.data:
            # Check if required fields are present for template rendering
            template_name = self.config.get("template", "default")

            # Template-specific validation could go here
            # For now, just ensure we have some data to render
            if not entity.data or len(entity.data) == 0:
                result["warnings"].append("Entity has no data fields to display")

        return result

    async def get_preview_info(self, entity: LegalEntity) -> Dict[str, Any]:
        """
        Get preview information for the entity.

        Args:
            entity: Entity to get preview info for

        Returns:
            Preview information including field summary
        """
        try:
            preview_info = {
                "entity_id": entity.entity_id,
                "entity_type": entity.entity_type,
                "entity_name": entity.name,
                "data_fields": list(entity.data.keys()) if entity.data else [],
                "field_count": len(entity.data) if entity.data else 0,
                "has_signature": "signature" in (entity.data or {}),
                "template": self.config.get("template", "default"),
                "qr_enabled": self.config.get("include_qr", True)
            }

            # Add signature info if present
            if entity.data and "signature" in entity.data:
                signature = entity.data["signature"]
                preview_info["signature_info"] = {
                    "algorithm": signature.get("algorithm", "unknown"),
                    "signer": signature.get("certificate_info", {}).get("subject", "unknown"),
                    "timestamp": signature.get("timestamp", signature.get("received_at"))
                }

            return preview_info

        except Exception as e:
            logger.error(f"Failed to get preview info for entity {entity.entity_id}: {e}")
            return {
                "entity_id": entity.entity_id,
                "error": str(e)
            }