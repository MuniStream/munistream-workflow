"""Channel-specific delivery handlers."""
from typing import Any, Dict

from ...core.config import settings
from .base import HandlerResult, NotificationHandler, TransientDeliveryError, PermanentDeliveryError
from .email_handler import EmailHandler
from .whatsapp_handler import WhatsAppHandler


def get_handler(channel: str) -> NotificationHandler:
    mapping = {
        "email": EmailHandler(),
        "whatsapp": WhatsAppHandler(),
    }
    if channel not in mapping:
        raise ValueError(f"Canal no soportado: {channel}")
    return mapping[channel]


def with_infra_credentials(channel: str, creds: Dict[str, Any]) -> Dict[str, Any]:
    """WhatsApp goes out through the platform's Baileys service: its URL and
    key are backend infrastructure (env), not tenant configuration. Values
    stored on the channel take precedence."""
    creds = dict(creds or {})
    if channel == "whatsapp":
        if settings.BAILEYS_BASE_URL:
            creds.setdefault("base_url", settings.BAILEYS_BASE_URL)
        if settings.BAILEYS_API_KEY:
            creds.setdefault("api_key", settings.BAILEYS_API_KEY)
    return creds


__all__ = [
    "with_infra_credentials",
    "HandlerResult",
    "NotificationHandler",
    "TransientDeliveryError",
    "PermanentDeliveryError",
    "EmailHandler",
    "WhatsAppHandler",
    "get_handler",
]
