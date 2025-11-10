"""
Jinja2 Template Engine for PDF Generation
"""

import os
from typing import Any, Dict, Optional
from pathlib import Path
import jinja2
from jinja2 import Environment, FileSystemLoader, select_autoescape, Template


class TemplateEngine:
    """Manages Jinja2 templates for entity PDF generation"""

    def __init__(self, template_dir: Optional[str] = None):
        """
        Initialize template engine

        Args:
            template_dir: Directory containing templates. If None, uses default.
        """
        if template_dir is None:
            # Default to templates in the same directory
            template_dir = os.path.join(
                os.path.dirname(__file__),
                'templates'
            )

        self.template_dir = Path(template_dir)
        self._ensure_template_dir()

        # Setup Jinja2 environment
        self.env = Environment(
            loader=FileSystemLoader(str(self.template_dir)),
            autoescape=select_autoescape(['html', 'xml']),
            trim_blocks=True,
            lstrip_blocks=True
        )

        # Add custom filters
        self._register_filters()

    def _ensure_template_dir(self):
        """Ensure template directory exists"""
        self.template_dir.mkdir(parents=True, exist_ok=True)

        # Create components directory
        components_dir = self.template_dir / "components"
        components_dir.mkdir(exist_ok=True)

    def _register_filters(self):
        """Register custom Jinja2 filters"""
        self.env.filters['format_field'] = self._format_field_name
        self.env.filters['format_value'] = self._format_value
        self.env.filters['format_date'] = self._format_date

    def _format_field_name(self, field_name: str) -> str:
        """Format field name for display"""
        return field_name.replace("_", " ").title()

    def _format_value(self, value: Any) -> str:
        """Format value for display"""
        if value is None:
            return "-"
        if isinstance(value, bool):
            return "Yes" if value else "No"
        if isinstance(value, (list, tuple)):
            return ", ".join(str(v) for v in value)
        if isinstance(value, dict):
            return str(value)  # Could be enhanced with better formatting
        return str(value)

    def _format_date(self, date_str: str) -> str:
        """Format date for display"""
        # Simple format, could be enhanced
        return date_str.split("T")[0] if "T" in date_str else date_str

    async def render_entity(
        self,
        entity: Any,
        template_name: str,
        context: Dict[str, Any]
    ) -> str:
        """
        Render entity using specified template

        Args:
            entity: The entity object
            template_name: Name of template to use
            context: Additional context for rendering

        Returns:
            Rendered HTML string
        """
        # Determine template file
        template_file = f"{template_name}.html"
        if not (self.template_dir / template_file).exists():
            # Fall back to entity type template
            entity_type_template = f"{entity.entity_type}_entity.html"
            if (self.template_dir / entity_type_template).exists():
                template_file = entity_type_template
            else:
                # Fall back to default
                template_file = "default.html"

        # Get template
        template = self.env.get_template(template_file)

        # Merge context
        full_context = {
            **context,
            "entity": entity,
        }

        # Render
        return template.render(**full_context)