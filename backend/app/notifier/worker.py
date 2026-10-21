"""arq worker: consumes the tenant's notifications queue and delivers messages.

Runs inside the backend process by default (`start_in_process_worker`, called
from app startup). It can also run standalone: `python -m app.notifier.worker`.
"""
import asyncio
import logging
from datetime import datetime
from typing import Any, List, Optional, Tuple

from arq import Retry
from arq.connections import RedisSettings
from beanie import PydanticObjectId

from ..core.config import settings
from ..core.database import close_mongo_connection, connect_to_mongo
from ..core.logging_config import setup_gelf_logging
from .encryption import decrypt_credentials
from ..models.legal_entity import LegalEntity
from ..services.visualizers.entity_pdf import render_entity_pdf
from .handlers import get_handler, with_infra_credentials
from .handlers.base import (
    Attachment,
    OutboundMessage,
    PermanentDeliveryError,
    TransientDeliveryError,
)
from .models import (
    DeliveryStatus,
    NotificationChannelConfig,
    NotificationDelivery,
    NotificationTemplate,
)
from .hook import queue_name
from .rendering import TemplateRenderError, render

logger = logging.getLogger(__name__)


def _redis_settings() -> RedisSettings:
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    # arq's 1 s connect timeout is too tight for a Redis shared by every tenant
    # backend: one slow connect raised out of the poll loop and stopped it.
    redis_settings.conn_timeout = 5
    redis_settings.conn_retries = 5
    return redis_settings


async def _load_template(
    tenant_id: str, key: str, locale: str, channel: str
) -> Optional[NotificationTemplate]:
    tpl = await NotificationTemplate.find_one(
        {
            "tenant_id": tenant_id,
            "key": key,
            "locale": locale,
            "channel": channel,
            "active": True,
        }
    )
    if tpl:
        return tpl
    return await NotificationTemplate.find_one(
        {
            "tenant_id": tenant_id,
            "key": key,
            "locale": "es",
            "channel": channel,
            "active": True,
        }
    )


async def _load_channel_config(tenant_id: str, channel: str) -> Optional[NotificationChannelConfig]:
    return await NotificationChannelConfig.find_one(
        {"tenant_id": tenant_id, "channel": channel, "enabled": True}
    )


def _retry_delay_seconds(attempt: int) -> int:
    """1, 2, 4, 8... minutes, capped at one hour."""
    return min(60 * 2 ** max(attempt - 1, 0), 3600)


async def _render_attachments(entity_ids: List[str]) -> Tuple[List[Attachment], List[str]]:
    """PDFs are rendered at send time from the stored entity, so the
    attachment always matches what the citizen downloads. A document that
    cannot be rendered is reported, never blocks the notice."""
    attachments: List[Attachment] = []
    errors: List[str] = []
    for entity_id in entity_ids:
        try:
            entity = await LegalEntity.find_one(LegalEntity.entity_id == entity_id)
            if not entity:
                errors.append(f"{entity_id}: not found")
                continue
            rendered = await render_entity_pdf(entity)
            if not rendered:
                errors.append(f"{entity_id}: no PDF rendering")
                continue
            data, filename = rendered
            attachments.append(Attachment(filename=filename, data=data))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Could not render attachment %s", entity_id)
            errors.append(f"{entity_id}: {exc}")
    return attachments, errors


async def send_notification(ctx: dict, delivery_id: str) -> str:
    delivery = await NotificationDelivery.get(PydanticObjectId(delivery_id))
    if not delivery:
        logger.warning("Delivery %s not found; dropping job", delivery_id)
        return "not_found"
    if delivery.status not in (DeliveryStatus.QUEUED, DeliveryStatus.RETRYING):
        # Cancelled (or already handled) while waiting in the queue.
        logger.info("Delivery %s is %s; dropping job", delivery_id, delivery.status.value)
        return "skipped"

    delivery.attempts += 1
    delivery.status = DeliveryStatus.SENDING
    await delivery.save()

    template = await _load_template(
        delivery.tenant_id,
        delivery.template_key,
        delivery.locale,
        delivery.channel.value,
    )
    if not template:
        delivery.status = DeliveryStatus.FAILED
        delivery.last_error = f"template_not_found: {delivery.template_key}/{delivery.locale}/{delivery.channel.value}"
        await delivery.save()
        return "template_missing"

    config = await _load_channel_config(delivery.tenant_id, delivery.channel.value)
    if not config:
        delivery.status = DeliveryStatus.FAILED
        delivery.last_error = "channel_not_configured"
        await delivery.save()
        return "channel_missing"

    try:
        rendered = render(template.body, delivery.context_snapshot, template.subject)
    except TemplateRenderError as exc:
        delivery.status = DeliveryStatus.FAILED
        delivery.last_error = f"render_error: {exc}"
        await delivery.save()
        return "render_error"

    delivery.rendered_preview = rendered.preview(200)

    handler = get_handler(delivery.channel.value)
    creds = decrypt_credentials(config.credentials_encrypted) if config.credentials_encrypted else {}
    creds = with_infra_credentials(delivery.channel.value, creds)
    attachments, attachment_errors = await _render_attachments(delivery.attachments or [])
    message = OutboundMessage(
        recipient=delivery.recipient,
        subject=rendered.subject,
        body=rendered.body,
        from_address=config.from_address,
        channel_credentials=creds,
        tenant_id=delivery.tenant_id,
        attachments=attachments,
    )

    try:
        result = await handler.send(message)
    except PermanentDeliveryError as exc:
        delivery.status = DeliveryStatus.FAILED
        delivery.last_error = f"permanent: {exc}"
        await delivery.save()
        return "failed_permanent"
    except Exception as exc:  # noqa: BLE001 — unknown errors are treated as transient
        prefix = "transient" if isinstance(exc, TransientDeliveryError) else "unknown"
        delivery.last_error = f"{prefix}: {exc}"
        if delivery.attempts >= settings.NOTIFICATION_MAX_ATTEMPTS:
            delivery.status = DeliveryStatus.FAILED
            await delivery.save()
            return "failed_transient"
        delivery.status = DeliveryStatus.RETRYING
        await delivery.save()
        # arq only re-runs a job that raises Retry; any other exception fails it.
        raise Retry(defer=_retry_delay_seconds(delivery.attempts)) from exc

    delivery.status = DeliveryStatus.SENT if result.success else DeliveryStatus.FAILED
    delivery.sent_at = datetime.utcnow()
    delivery.attachments_sent = [a.filename for a in attachments]
    delivery.last_error = None
    if attachment_errors:
        delivery.last_error = "attachments: " + "; ".join(attachment_errors)
    if not result.success:
        delivery.last_error = result.error or "handler_returned_failure"
    await delivery.save()
    return "sent" if result.success else "failed"


async def worker_startup(ctx: dict) -> None:
    import os

    container_name = os.getenv("CONTAINER_NAME", "notifier-worker")
    graylog_host = os.getenv("GRAYLOG_HOST", "graylog")
    graylog_port = int(os.getenv("GRAYLOG_PORT", "12201"))
    setup_gelf_logging(
        graylog_host=graylog_host, graylog_port=graylog_port, container_name=container_name
    )
    await connect_to_mongo()
    logger.info("notifier worker started; connected to Mongo")


async def worker_shutdown(ctx: dict) -> None:
    await close_mongo_connection()


class WorkerSettings:
    functions = [send_notification]
    redis_settings = _redis_settings()
    queue_name = queue_name()
    max_tries = settings.NOTIFICATION_MAX_ATTEMPTS + 1
    job_timeout = 60
    keep_result = 3600
    on_startup = worker_startup
    on_shutdown = worker_shutdown


_in_process_task: Optional[asyncio.Task] = None

# Pause before restarting a crashed in-process worker.
_RESTART_DELAY_SECONDS = 5


def _build_worker():
    from arq.worker import Worker

    return Worker(
        functions=[send_notification],
        redis_settings=_redis_settings(),
        queue_name=queue_name(),
        max_tries=settings.NOTIFICATION_MAX_ATTEMPTS + 1,
        job_timeout=120,
        keep_result=3600,
        handle_signals=False,
    )


async def _supervise() -> None:
    """arq's poll loop re-raises any error from a finished job task, which ends
    the loop. Inside the backend nobody would notice, so the loop is restarted
    and the error logged."""
    while True:
        worker = _build_worker()
        try:
            await worker.main()
        except asyncio.CancelledError:
            try:
                await worker.close()
            except Exception:
                logger.warning("could not close the notifier worker on shutdown", exc_info=True)
            raise
        except Exception:
            logger.exception("notifier worker stopped unexpectedly; restarting")
            try:
                await worker.close()
            except Exception:
                logger.warning("could not close the stopped notifier worker", exc_info=True)
            await asyncio.sleep(_RESTART_DELAY_SECONDS)


async def start_in_process_worker() -> None:
    """Consume the tenant's queue from the backend's own event loop. Mongo is
    already connected and plugins (tenant visualizers) are loaded, which the
    PDF attachments need."""
    global _in_process_task
    if _in_process_task is not None or not settings.TENANT_ID:
        return
    _in_process_task = asyncio.create_task(_supervise())
    logger.info("notifier worker running in-process on queue %s", queue_name())


async def stop_in_process_worker() -> None:
    global _in_process_task
    if _in_process_task is not None:
        _in_process_task.cancel()
    _in_process_task = None


if __name__ == "__main__":
    from arq.worker import run_worker

    run_worker(WorkerSettings)
