"""
Base EntityVisualizer class

Abstract base class for entity visualization services.
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, Union
import logging

from ...models.legal_entity import LegalEntity

logger = logging.getLogger(__name__)


class EntityVisualizer(ABC):
    """
    Abstract base class for entity visualizers.

    Visualizers generate different representations of entities, such as PDF reports,
    HTML previews, QR codes, etc.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize the visualizer with configuration.

        Args:
            config: Visualizer-specific configuration options
        """
        self.config = config or {}
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    async def generate_pdf(self, entity: LegalEntity) -> bytes:
        """
        Generate a PDF representation of the entity.

        Args:
            entity: The legal entity to visualize

        Returns:
            PDF bytes

        Raises:
            NotImplementedError: If not implemented by subclass
        """
        raise NotImplementedError("Subclasses must implement generate_pdf")

    async def generate_html(self, entity: LegalEntity) -> str:
        """
        Generate HTML representation of the entity.

        Default implementation using EntityReportGenerator.
        Subclasses can override for custom HTML generation.

        Args:
            entity: The legal entity to visualize

        Returns:
            HTML string representation of the entity
        """
        try:
            # Import here to avoid circular imports
            from ..pdf_generation import EntityReportGenerator

            # Create report generator if not exists
            if not hasattr(self, 'report_generator'):
                self.report_generator = EntityReportGenerator()

            # Get configuration options
            template_name = self.config.get("template", "default")
            include_qr = self.config.get("include_qr", True)
            include_signatures = self.config.get("include_signatures", False)
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
            return f"<html><body><h1>Error generating document</h1><p>{str(e)}</p></body></html>"

    async def generate_preview(self, entity: LegalEntity) -> str:
        """
        Generate a base64-encoded preview of the entity.

        This is useful for displaying previews in web interfaces.

        Args:
            entity: The legal entity to visualize

        Returns:
            Base64-encoded representation (typically PDF or image)
        """
        try:
            pdf_data = await self.generate_pdf(entity)
            if pdf_data:
                import base64
                return base64.b64encode(pdf_data).decode('utf-8')
            return ""
        except Exception as e:
            self.logger.error(f"Failed to generate preview for entity {entity.entity_id}: {e}")
            return ""

    async def get_download_info(self, entity: LegalEntity) -> Dict[str, Any]:
        """
        Get information about downloadable content for this entity.

        Args:
            entity: The legal entity

        Returns:
            Dictionary with download information (filename, content_type, etc.)
        """
        return {
            "filename": f"{entity.entity_type}_{entity.entity_id}.pdf",
            "content_type": "application/pdf",
            "suggested_name": f"{entity.name or entity.entity_type}.pdf"
        }

    def supports_format(self, format_type: str) -> bool:
        """
        Check if this visualizer supports a specific format.

        Args:
            format_type: Format type to check (e.g., "pdf", "html", "png")

        Returns:
            True if format is supported
        """
        format_type = format_type.lower()
        if format_type == "pdf":
            return True
        elif format_type == "html":
            # HTML is supported by default since all visualizers use HTML templates
            return True
        return False

    def get_visualizer_info(self) -> Dict[str, Any]:
        """
        Get information about this visualizer.

        Returns:
            Dictionary with visualizer metadata
        """
        return {
            "name": self.__class__.__name__,
            "description": self.__doc__ or "Entity visualizer",
            "supported_formats": ["pdf", "html"],
            "config": self.config
        }

    async def validate_entity(self, entity: LegalEntity) -> Dict[str, Any]:
        """
        Validate that an entity can be visualized by this visualizer.

        Args:
            entity: Entity to validate

        Returns:
            Dictionary with validation results
        """
        result = {
            "valid": True,
            "warnings": [],
            "errors": []
        }

        # Basic validation
        if not entity:
            result["valid"] = False
            result["errors"].append("Entity is None")
            return result

        if not entity.entity_id:
            result["warnings"].append("Entity has no ID")

        if not entity.name:
            result["warnings"].append("Entity has no name")

        if not entity.data:
            result["warnings"].append("Entity has no data")

        return result

    def _prepare_context_data(self, entity: LegalEntity) -> Dict[str, Any]:
        """
        Prepare context data for visualization templates.

        Args:
            entity: Entity to prepare data for

        Returns:
            Dictionary with template context data
        """
        context = {
            "entity": {
                "id": entity.entity_id,
                "type": entity.entity_type,
                "name": entity.name,
                "data": entity.data,
                "status": entity.status,
                "verified": entity.verified,
                "created_at": entity.created_at.isoformat() if entity.created_at else None,
                "updated_at": entity.updated_at.isoformat() if entity.updated_at else None
            },
            "visualization_config": entity.visualization_config or {},
            "display_config": entity.entity_display_config or {},
            "generated_at": self._get_current_timestamp()
        }

        # Add signature information if present
        if entity.data and "signature" in entity.data:
            context["signature"] = entity.data["signature"]

        return context

    def _get_current_timestamp(self) -> str:
        """Get current timestamp in ISO format"""
        from datetime import datetime
        return datetime.utcnow().isoformat()

    def _get_template_path(self, template_name: str) -> str:
        """
        Get the full path for a template file.

        Args:
            template_name: Name of the template file

        Returns:
            Full path to template
        """
        import os
        from pathlib import Path

        # Get the services directory
        services_dir = Path(__file__).parent.parent

        # Look for template in pdf_generation/templates
        template_path = services_dir / "pdf_generation" / "templates" / template_name

        if template_path.exists():
            return str(template_path)

        # Look in visualizers/templates subdirectory
        template_path = Path(__file__).parent / "templates" / template_name

        if template_path.exists():
            return str(template_path)

        # Return default path
        return str(services_dir / "pdf_generation" / "templates" / "default.html")

    def _log_generation_start(self, entity: LegalEntity):
        """Log the start of visualization generation"""
        self.logger.info(
            f"Starting {self.__class__.__name__} generation for entity {entity.entity_id}",
            extra={
                "entity_id": entity.entity_id,
                "entity_type": entity.entity_type,
                "visualizer": self.__class__.__name__
            }
        )

    def _log_generation_success(self, entity: LegalEntity, output_size: int):
        """Log successful visualization generation"""
        self.logger.info(
            f"Successfully generated visualization for entity {entity.entity_id} ({output_size} bytes)",
            extra={
                "entity_id": entity.entity_id,
                "entity_type": entity.entity_type,
                "visualizer": self.__class__.__name__,
                "output_size": output_size
            }
        )

    def _log_generation_error(self, entity: LegalEntity, error: Exception):
        """Log visualization generation error"""
        self.logger.error(
            f"Failed to generate visualization for entity {entity.entity_id}: {error}",
            extra={
                "entity_id": entity.entity_id,
                "entity_type": entity.entity_type,
                "visualizer": self.__class__.__name__,
                "error": str(error)
            },
            exc_info=True
        )