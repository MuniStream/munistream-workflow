"""Idempotent seeding of system notifications for the current tenant.

Runs on backend startup (see `workflows/startup.py`). For each entry in
`SYSTEM_NOTIFICATIONS`:

1. Insert the localized `NotificationTemplate` rows only if they are missing.
   Admins can edit template bodies from the admin UI and their edits must not
   be overwritten on subsequent restarts.
2. Insert the `NotificationTrigger` with `is_system=True` and
   `workflow_id=None` (wildcard) only if missing. If it exists, leave it
   untouched so admins can toggle `active` without the seed flipping it back.
"""
import logging
from datetime import datetime
from typing import List, Tuple

from .models import (
    NotificationChannel,
    NotificationTemplate,
    NotificationTrigger,
)
from .system_notifications import (
    SUPPORTED_LOCALES,
    SYSTEM_NOTIFICATIONS,
    SystemNotification,
)

logger = logging.getLogger(__name__)


async def seed_system_notifications(tenant_id: str) -> None:
    if not tenant_id:
        logger.warning("seed_system_notifications skipped: TENANT_ID is empty")
        return

    template_stats = await _seed_templates(tenant_id)
    trigger_stats = await _seed_triggers(tenant_id)

    logger.info(
        "system_notifications seed for tenant %s: templates %s, triggers %s",
        tenant_id,
        template_stats,
        trigger_stats,
    )


async def _seed_templates(tenant_id: str) -> dict:
    created = 0
    skipped = 0
    for entry in SYSTEM_NOTIFICATIONS:
        for locale in SUPPORTED_LOCALES:
            for channel in entry.default_channels:
                template = entry.templates.get((locale, channel))
                if template is None:
                    continue
                existing = await NotificationTemplate.find_one(
                    {
                        "tenant_id": tenant_id,
                        "key": entry.key,
                        "locale": locale,
                        "channel": channel,
                    }
                )
                if existing is not None:
                    skipped += 1
                    continue
                doc = NotificationTemplate(
                    tenant_id=tenant_id,
                    key=entry.key,
                    locale=locale,
                    channel=channel,
                    subject=template.subject or None,
                    body=template.body,
                    variables_doc=(
                        "Variables disponibles: ciudadano.nombre, ciudadano.email, "
                        "ciudadano.telefono, workflow.id, instancia.id, paso.id, "
                        "evento.tipo, evento.datos"
                    ),
                    active=True,
                    updated_by="system",
                )
                await doc.insert()
                created += 1
    return {"created": created, "skipped": skipped}


async def _seed_triggers(tenant_id: str) -> dict:
    created = 0
    skipped = 0
    for entry in SYSTEM_NOTIFICATIONS:
        existing = await NotificationTrigger.find_one(
            {
                "tenant_id": tenant_id,
                "is_system": True,
                "template_key": entry.key,
            }
        )
        if existing is not None:
            skipped += 1
            continue
        trigger = NotificationTrigger(
            tenant_id=tenant_id,
            workflow_id=None,
            step_id=None,
            event_type=entry.event_type.value,
            template_key=entry.key,
            channels=list(entry.default_channels),
            active=True,
            is_system=True,
            created_by="system",
        )
        await trigger.insert()
        created += 1
    return {"created": created, "skipped": skipped}


__all__ = ["seed_system_notifications"]
