"""
Jinja2 Template Engine for PDF Generation
"""

import os
from typing import Any, Dict, Optional
from pathlib import Path
import jinja2
from jinja2 import ChoiceLoader, Environment, FileSystemLoader, select_autoescape, Template

from .plugins import TemplateDirRegistry


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

        # Componer loader con directorios adicionales registrados por tenants.
        # El default va primero para que los templates shared ganen a homónimos.
        loaders = [FileSystemLoader(str(self.template_dir))]
        for extra in TemplateDirRegistry.dirs():
            if extra.exists():
                loaders.append(FileSystemLoader(str(extra)))

        self.env = Environment(
            loader=ChoiceLoader(loaders) if len(loaders) > 1 else loaders[0],
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
        self.env.filters['format_date_es'] = self._format_date_es

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

    def _format_date(self, date_value) -> str:
        """Format date for display"""
        from datetime import datetime, date

        if isinstance(date_value, datetime):
            return date_value.strftime('%Y-%m-%d')
        elif isinstance(date_value, date):
            return date_value.strftime('%Y-%m-%d')
        elif isinstance(date_value, str):
            return date_value.split("T")[0] if "T" in date_value else date_value
        return str(date_value)

    _MESES_ES = [
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
    ]

    def _format_date_es(self, date_value) -> str:
        """Formato institucional en español: '23 de abril de 2026'."""
        from datetime import datetime, date

        dt = None
        if isinstance(date_value, datetime):
            dt = date_value
        elif isinstance(date_value, date):
            dt = datetime(date_value.year, date_value.month, date_value.day)
        elif isinstance(date_value, str) and date_value:
            try:
                dt = datetime.fromisoformat(date_value.replace("Z", "+00:00"))
            except ValueError:
                try:
                    dt = datetime.strptime(date_value[:10], "%Y-%m-%d")
                except ValueError:
                    return date_value
        if dt is None:
            return str(date_value)
        return f"{dt.day} de {self._MESES_ES[dt.month - 1]} de {dt.year}"

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
        # Determine template file - no fallbacks, fail if not found
        template_file = f"{template_name}.html"

        # Get template - will raise TemplateNotFound if doesn't exist
        template = self.env.get_template(template_file)

        # Merge context
        full_context = {
            **context,
            "entity": entity,
        }

        # Render
        return template.render(**full_context)