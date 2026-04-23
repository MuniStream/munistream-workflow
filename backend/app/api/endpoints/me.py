"""Citizen-facing endpoints scoped to the authenticated user."""
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ...models.customer import Customer
from ...notifier.models import NotificationChannelToggle, NotificationPreferences
from ...notifier.system_notifications import SYSTEM_NOTIFICATIONS
from .public_auth import get_current_customer

router = APIRouter()


class ChannelToggleSchema(BaseModel):
    email: bool = True
    whatsapp: bool = True


class NotificationPreferencesPayload(BaseModel):
    email_enabled: bool
    whatsapp_enabled: bool
    per_notification: Optional[Dict[str, ChannelToggleSchema]] = None


class NotificationPreferencesResponse(BaseModel):
    email_enabled: bool
    whatsapp_enabled: bool
    per_notification: Dict[str, ChannelToggleSchema]
    updated_at: datetime


class NotificationCatalogEntry(BaseModel):
    key: str
    title: str
    description: str
    default_channels: List[str]


def _serialize_per_notification(
    prefs: NotificationPreferences,
) -> Dict[str, ChannelToggleSchema]:
    return {
        key: ChannelToggleSchema(email=toggle.email, whatsapp=toggle.whatsapp)
        for key, toggle in prefs.per_notification.items()
    }


def _as_response(prefs: NotificationPreferences) -> NotificationPreferencesResponse:
    return NotificationPreferencesResponse(
        email_enabled=prefs.email_enabled,
        whatsapp_enabled=prefs.whatsapp_enabled,
        per_notification=_serialize_per_notification(prefs),
        updated_at=prefs.updated_at,
    )


@router.get("/notification-preferences", response_model=NotificationPreferencesResponse)
async def get_my_notification_preferences(
    current_customer: Customer = Depends(get_current_customer),
):
    return _as_response(current_customer.notification_preferences)


@router.put("/notification-preferences", response_model=NotificationPreferencesResponse)
async def update_my_notification_preferences(
    payload: NotificationPreferencesPayload,
    current_customer: Customer = Depends(get_current_customer),
):
    existing = current_customer.notification_preferences
    if payload.per_notification is not None:
        per_notification = {
            key: NotificationChannelToggle(email=value.email, whatsapp=value.whatsapp)
            for key, value in payload.per_notification.items()
        }
    else:
        per_notification = existing.per_notification

    current_customer.notification_preferences = NotificationPreferences(
        email_enabled=payload.email_enabled,
        whatsapp_enabled=payload.whatsapp_enabled,
        per_notification=per_notification,
        updated_at=datetime.utcnow(),
    )
    current_customer.updated_at = datetime.utcnow()
    await current_customer.save()
    return _as_response(current_customer.notification_preferences)


@router.get("/notification-catalog", response_model=List[NotificationCatalogEntry])
async def get_my_notification_catalog(
    current_customer: Customer = Depends(get_current_customer),
):
    """Return the system-shipped notifications catalog, localized for the citizen."""
    locale = current_customer.preferred_language or "es"
    return [
        NotificationCatalogEntry(
            key=entry.key,
            title=entry.title(locale),
            description=entry.description(locale),
            default_channels=[channel.value for channel in entry.default_channels],
        )
        for entry in SYSTEM_NOTIFICATIONS
    ]
