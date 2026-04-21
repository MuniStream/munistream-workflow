"""Citizen-facing endpoints scoped to the authenticated user."""
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ...models.customer import Customer
from ...notifier.models import NotificationPreferences
from .public_auth import get_current_customer

router = APIRouter()


class NotificationPreferencesPayload(BaseModel):
    email_enabled: bool
    whatsapp_enabled: bool


class NotificationPreferencesResponse(NotificationPreferencesPayload):
    updated_at: datetime


def _as_response(prefs: NotificationPreferences) -> NotificationPreferencesResponse:
    return NotificationPreferencesResponse(
        email_enabled=prefs.email_enabled,
        whatsapp_enabled=prefs.whatsapp_enabled,
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
    current_customer.notification_preferences = NotificationPreferences(
        email_enabled=payload.email_enabled,
        whatsapp_enabled=payload.whatsapp_enabled,
        updated_at=datetime.utcnow(),
    )
    current_customer.updated_at = datetime.utcnow()
    await current_customer.save()
    return _as_response(current_customer.notification_preferences)
