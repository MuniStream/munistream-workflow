"""Idempotent seeding of system notifications for the current tenant.

Runs on backend startup (see `workflows/startup.py`). For each entry in
`SYSTEM_NOTIFICATIONS`:

1. Insert the localized `NotificationTemplate` rows when missing. A row still
   owned by the system (`updated_by == "system"`, i.e. never edited from the
   admin UI) is refreshed to the current default; admin edits are never
   overwritten.
2. Insert the `NotificationTrigger` with `is_system=True` and
   `workflow_id=None` (wildcard) only if missing. If it exists, leave it
   untouched so admins can toggle `active` without the seed flipping it back;
   only fields added after it was created (`attach_documents`) get their
   catalog default.
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

VARIABLES_DOC = (
    "Variables disponibles: remitente, ciudadano.nombre, ciudadano.email, "
    "ciudadano.telefono, workflow.id, workflow.nombre, instancia.id, "
    "instancia.folio, paso.id, paso.nombre, url_tramite, resultado.codigo, "
    "resultado.rechazado, resultado.mensaje, documento.nombre, documento.tipo, "
    "documento.folio, documentos (lista con nombre, tipo, folio), adjuntos, "
    "evento.tipo, evento.datos"
)


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
    updated = 0
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
                    subject = template.subject or None
                    if existing.updated_by == "system" and (
                        existing.body != template.body or existing.subject != subject
                    ):
                        existing.body = template.body
                        existing.subject = subject
                        existing.variables_doc = VARIABLES_DOC
                        existing.updated_at = datetime.utcnow()
                        await existing.save()
                        updated += 1
                    else:
                        skipped += 1
                    continue
                doc = NotificationTemplate(
                    tenant_id=tenant_id,
                    key=entry.key,
                    locale=locale,
                    channel=channel,
                    subject=template.subject or None,
                    body=template.body,
                    variables_doc=VARIABLES_DOC,
                    active=True,
                    updated_by="system",
                )
                await doc.insert()
                created += 1
    return {"created": created, "updated": updated, "skipped": skipped}


async def _seed_triggers(tenant_id: str) -> dict:
    created = 0
    updated = 0
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
            # Triggers created before `attach_documents` existed lack the
            # field in Mongo: give them the catalog default once.
            result = await NotificationTrigger.get_motor_collection().update_one(
                {"_id": existing.id, "attach_documents": {"$exists": False}},
                {"$set": {"attach_documents": entry.attach_documents}},
            )
            if result.modified_count:
                updated += 1
            else:
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
            attach_documents=entry.attach_documents,
            created_by="system",
        )
        await trigger.insert()
        created += 1
    return {"created": created, "updated": updated, "skipped": skipped}


__all__ = ["seed_system_notifications"]
