"""Shared types for notification delivery handlers."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional


class DeliveryError(Exception):
    """Base class for delivery failures."""


class TransientDeliveryError(DeliveryError):
    """Recoverable failure. The worker should retry."""


class PermanentDeliveryError(DeliveryError):
    """Non-recoverable failure. No retry."""


@dataclass
class HandlerResult:
    success: bool
    provider_reference: Optional[str] = None
    error: Optional[str] = None


@dataclass
class OutboundMessage:
    recipient: str
    subject: Optional[str]
    body: str
    from_address: Optional[str] = None
    channel_credentials: Dict[str, Any] = None
    tenant_id: Optional[str] = None

    def __post_init__(self):
        if self.channel_credentials is None:
            self.channel_credentials = {}


class NotificationHandler(ABC):
    """Each channel (email, whatsapp, ...) implements this interface."""

    channel: str = ""

    @abstractmethod
    async def send(self, message: OutboundMessage) -> HandlerResult:
        raise NotImplementedError
