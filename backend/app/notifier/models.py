"""Beanie documents and embedded models for the notifications subsystem."""
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from beanie import Document
from pydantic import BaseModel, Field
from pymongo import IndexModel


class NotificationChannel(str, Enum):
    EMAIL = "email"
    WHATSAPP = "whatsapp"


class DeliveryStatus(str, Enum):
    QUEUED = "queued"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    RETRYING = "retrying"
    RATE_LIMITED = "rate_limited"
    SKIPPED_OPT_OUT = "skipped_opt_out"


class NotificationPreferences(BaseModel):
    """Embedded in Customer. Defaults opt everyone in; the citizen can opt out."""
    email_enabled: bool = True
    whatsapp_enabled: bool = True
    updated_at: datetime = Field(default_factory=datetime.utcnow)


def _tenant_id_field() -> Any:
    return Field(..., description="Tenant identifier this record belongs to")


class NotificationChannelConfig(Document):
    """Per-tenant per-channel configuration (SMTP, Baileys endpoint, etc)."""

    tenant_id: str = _tenant_id_field()
    channel: NotificationChannel
    enabled: bool = False
    credentials_encrypted: Optional[str] = Field(
        default=None,
        description="Fernet-encrypted JSON with channel-specific credentials",
    )
    from_address: Optional[str] = Field(
        default=None,
        description="Email From: address or WhatsApp display name",
    )
    test_recipient: Optional[str] = Field(
        default=None,
        description="Default address/number used by the 'send test' action",
    )

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    updated_by: Optional[str] = None

    class Settings:
        name = "notification_channel_configs"
        indexes = [
            IndexModel([("tenant_id", 1), ("channel", 1)], unique=True),
        ]


class NotificationTemplate(Document):
    """Reusable template keyed by (tenant, key, locale, channel)."""

    tenant_id: str = _tenant_id_field()
    key: str = Field(..., description="Semantic key, e.g. 'step_completed'")
    locale: str = Field(default="es", description="ISO language code: es, en, ...")
    channel: NotificationChannel
    subject: Optional[str] = Field(
        default=None,
        description="Email subject (ignored for WhatsApp)",
    )
    body: str = Field(..., description="Jinja2 template body")
    variables_doc: Optional[str] = Field(
        default=None,
        description="Free-text documentation of available variables, shown in the admin",
    )
    active: bool = True

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    updated_by: Optional[str] = None

    class Settings:
        name = "notification_templates"
        indexes = [
            IndexModel(
                [("tenant_id", 1), ("key", 1), ("locale", 1), ("channel", 1)],
                unique=True,
            ),
            IndexModel([("tenant_id", 1), ("active", 1)]),
        ]


class NotificationTrigger(Document):
    """Binds a workflow event (optionally scoped to a step) to a template + channels."""

    tenant_id: str = _tenant_id_field()
    workflow_id: str
    step_id: Optional[str] = Field(
        default=None,
        description="When None, the trigger fires regardless of step",
    )
    event_type: str = Field(
        ...,
        description="Matches EventType value, e.g. 'STARTED', 'COMPLETED', 'APPROVAL_REQUESTED'",
    )
    template_key: str
    channels: List[NotificationChannel] = Field(default_factory=list)
    active: bool = True

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None

    class Settings:
        name = "notification_triggers"
        indexes = [
            IndexModel([
                ("tenant_id", 1),
                ("workflow_id", 1),
                ("event_type", 1),
                ("step_id", 1),
                ("active", 1),
            ]),
            IndexModel([("tenant_id", 1), ("workflow_id", 1)]),
        ]


class NotificationDelivery(Document):
    """Audit trail of one attempted delivery, updated as the job progresses."""

    tenant_id: str = _tenant_id_field()
    instance_id: Optional[str] = None
    workflow_id: Optional[str] = None
    step_id: Optional[str] = None
    event_id: Optional[str] = None
    trigger_id: Optional[str] = None

    channel: NotificationChannel
    recipient: str
    template_key: str
    locale: str = "es"
    rendered_preview: Optional[str] = Field(
        default=None,
        description="First chars of the rendered body for auditing",
    )

    status: DeliveryStatus = DeliveryStatus.QUEUED
    attempts: int = 0
    last_error: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    queued_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None

    context_snapshot: Dict[str, Any] = Field(
        default_factory=dict,
        description="Rendering context at enqueue time, for retries and debugging",
    )

    class Settings:
        name = "notification_deliveries"
        indexes = [
            IndexModel([("tenant_id", 1), ("created_at", -1)]),
            IndexModel([("instance_id", 1)]),
            IndexModel([("status", 1)]),
            IndexModel([("channel", 1), ("recipient", 1), ("created_at", -1)]),
        ]


__all__ = [
    "NotificationChannel",
    "DeliveryStatus",
    "NotificationPreferences",
    "NotificationChannelConfig",
    "NotificationTemplate",
    "NotificationTrigger",
    "NotificationDelivery",
]
