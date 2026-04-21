"""arq worker: consumes the `notifications` queue and delivers messages.

Start with: `python -m app.notifier.worker`
"""
import logging
from datetime import datetime
from typing import Any, Optional

from arq.connections import RedisSettings
from beanie import PydanticObjectId

from ..core.config import settings
from ..core.database import close_mongo_connection, connect_to_mongo
from ..core.logging_config import setup_gelf_logging
from .encryption import decrypt_credentials
from .handlers import get_handler
from .handlers.base import (
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
from .rendering import TemplateRenderError, render

logger = logging.getLogger(__name__)


def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(settings.REDIS_URL)


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


async def send_notification(ctx: dict, delivery_id: str) -> str:
    delivery = await NotificationDelivery.get(PydanticObjectId(delivery_id))
    if not delivery:
        logger.warning("Delivery %s not found; dropping job", delivery_id)
        return "not_found"

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
    message = OutboundMessage(
        recipient=delivery.recipient,
        subject=rendered.subject,
        body=rendered.body,
        from_address=config.from_address,
        channel_credentials=creds,
        tenant_id=delivery.tenant_id,
    )

    try:
        result = await handler.send(message)
    except PermanentDeliveryError as exc:
        delivery.status = DeliveryStatus.FAILED
        delivery.last_error = f"permanent: {exc}"
        await delivery.save()
        return "failed_permanent"
    except TransientDeliveryError as exc:
        delivery.status = DeliveryStatus.RETRYING
        delivery.last_error = f"transient: {exc}"
        await delivery.save()
        raise  # let arq retry
    except Exception as exc:  # noqa: BLE001 — treat unknown as transient
        delivery.status = DeliveryStatus.RETRYING
        delivery.last_error = f"unknown: {exc}"
        await delivery.save()
        raise

    delivery.status = DeliveryStatus.SENT if result.success else DeliveryStatus.FAILED
    delivery.sent_at = datetime.utcnow()
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
    queue_name = "notifications"
    max_tries = settings.NOTIFICATION_MAX_ATTEMPTS
    job_timeout = 60
    keep_result = 3600
    on_startup = worker_startup
    on_shutdown = worker_shutdown


if __name__ == "__main__":
    from arq.worker import run_worker

    run_worker(WorkerSettings)
