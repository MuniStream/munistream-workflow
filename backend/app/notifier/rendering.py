"""Safe Jinja2 rendering for notification templates.

Uses `SandboxedEnvironment` so templates from the admin UI cannot execute
arbitrary Python. `StrictUndefined` surfaces missing variables as errors so
misconfigured templates fail loudly instead of sending blank messages.
"""
from dataclasses import dataclass
from typing import Any, Dict, Optional

from jinja2 import StrictUndefined
from jinja2.exceptions import TemplateError, UndefinedError
from jinja2.sandbox import SandboxedEnvironment


_env = SandboxedEnvironment(
    autoescape=False,
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)


class TemplateRenderError(ValueError):
    """Raised when a template cannot be rendered with the provided context."""


@dataclass
class RenderedMessage:
    subject: Optional[str]
    body: str

    def preview(self, max_chars: int = 200) -> str:
        return self.body[:max_chars]


def render(
    body_template: str,
    context: Dict[str, Any],
    subject_template: Optional[str] = None,
) -> RenderedMessage:
    try:
        body = _env.from_string(body_template).render(**context)
        subject = (
            _env.from_string(subject_template).render(**context)
            if subject_template
            else None
        )
        return RenderedMessage(subject=subject, body=body)
    except UndefinedError as exc:
        raise TemplateRenderError(f"Variable no definida en plantilla: {exc}") from exc
    except TemplateError as exc:
        raise TemplateRenderError(f"Error de plantilla: {exc}") from exc


def sample_context() -> Dict[str, Any]:
    """Sample data used by the admin 'preview' action."""
    return {
        "ciudadano": {
            "nombre": "Ana López",
            "email": "ana.lopez@example.mx",
            "telefono": "+525555555555",
        },
        "workflow": {
            "id": "tramite_demo",
            "nombre": "Trámite de prueba",
        },
        "paso": {
            "id": "paso_revision",
            "nombre": "Revisión documental",
        },
        "instancia": {
            "id": "inst_123",
            "folio": "F-2026-0001",
            "estado": "en_progreso",
        },
        "url_tramite": "https://portal.example.mx/tramites/inst_123",
    }
