"""Channel-specific delivery handlers."""
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


__all__ = [
    "HandlerResult",
    "NotificationHandler",
    "TransientDeliveryError",
    "PermanentDeliveryError",
    "EmailHandler",
    "WhatsAppHandler",
    "get_handler",
]
