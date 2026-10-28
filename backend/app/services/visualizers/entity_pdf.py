"""
An entity's PDF, exactly as the citizen sees it.

Single source for the download, the in-browser view and the notifications that
carry the document attached: all use the visualizer the entity declares, with
the same configuration, so the attachment matches the download.
"""
from typing import Any, Optional, Tuple

from ...core.config import settings
from ..entity_type_catalog import effective_display_config
from .visualizer_factory import VisualizerFactory


def select_entity_visualizer(entity) -> str:
    """The entity's configured visualizer; ``signed_pdf`` only as a fallback
    for signed entities that declare none."""
    visualizer = effective_display_config(entity).get("visualizer")
    if visualizer:
        return visualizer
    if entity.data and "signature" in entity.data:
        return "signed_pdf"
    return "pdf_report"


def entity_visualizer(entity, **extra: Any):
    """The entity's visualizer instance, or None if it does not exist."""
    config = {
        **effective_display_config(entity),
        "base_url": settings.FRONTEND_BASE_URL,
        **extra,
    }
    return VisualizerFactory.get_visualizer(
        visualizer_type=select_entity_visualizer(entity),
        config=config,
    )


async def render_entity_pdf(entity) -> Optional[Tuple[bytes, str]]:
    """(PDF bytes, filename), or None when the entity has no PDF rendering."""
    visualizer = entity_visualizer(entity)
    if not visualizer:
        return None
    pdf = await visualizer.generate_pdf(entity)
    if not pdf:
        return None
    info = await visualizer.get_download_info(entity)
    filename = info.get("filename") or f"{entity.entity_id}.pdf"
    if not filename.lower().endswith(".pdf"):
        filename = f"{filename}.pdf"
    return pdf, filename
