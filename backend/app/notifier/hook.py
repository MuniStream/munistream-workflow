"""Bridges workflow events into notification deliveries.

A `NotificationDispatcher` is subscribed to every `EventType` on the running
`WorkflowEventManager`. It consults `NotificationTrigger`s for the current
tenant, resolves the target citizen, enforces opt-out and rate limits, and
enqueues jobs on the arq `notifications` queue for the worker to send.
"""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from ..core.config import settings
from ..models.customer import Customer
from ..models.workflow import EventType, WorkflowEvent
from .models import (
    DeliveryStatus,
    NotificationChannel,
    NotificationChannelConfig,
    NotificationDelivery,
    NotificationTrigger,
)
from .rate_limit import rate_limiter

logger = logging.getLogger(__name__)

STEP_ID_EVENT_KEYS = ("step_id", "failed_step", "approval_step", "current_step")


def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(settings.REDIS_URL)


class NotificationDispatcher:
    def __init__(self):
        self._arq_pool: Optional[ArqRedis] = None

    async def _pool(self) -> ArqRedis:
        if self._arq_pool is None:
            self._arq_pool = await create_pool(_redis_settings())
        return self._arq_pool

    async def handle_event(self, event: WorkflowEvent) -> None:
        tenant_id = settings.TENANT_ID
        if not tenant_id:
            return
        try:
            await self._dispatch(tenant_id, event)
        except Exception:
            logger.exception("NotificationDispatcher failed for event %s", event.event_id)

    async def _dispatch(self, tenant_id: str, event: WorkflowEvent) -> None:
        step_id = self._extract_step_id(event)
        event_type_value = (
            event.event_type.value if isinstance(event.event_type, EventType) else str(event.event_type)
        )

        triggers = await NotificationTrigger.find(
            {
                "tenant_id": tenant_id,
                "workflow_id": event.workflow_id,
                "event_type": event_type_value,
                "active": True,
            }
        ).to_list()

        applicable = [t for t in triggers if t.step_id is None or t.step_id == step_id]
        if not applicable:
            return

        customer = await self._resolve_customer(event)
        channels_enabled = await self._enabled_channels(tenant_id)
        rendering_context = self._build_context(event, customer)

        for trigger in applicable:
            for channel in trigger.channels:
                if channel not in channels_enabled:
                    continue
                recipient = self._recipient_for(customer, channel)
                if not recipient:
                    logger.info(
                        "Skipping %s for event %s: customer lacks %s",
                        channel.value,
                        event.event_id,
                        channel.value,
                    )
                    continue

                opted_in = self._citizen_opted_in(customer, channel)
                if not opted_in:
                    await self._persist_delivery(
                        tenant_id=tenant_id,
                        trigger=trigger,
                        event=event,
                        step_id=step_id,
                        channel=channel,
                        recipient=recipient,
                        customer=customer,
                        context=rendering_context,
                        status=DeliveryStatus.SKIPPED_OPT_OUT,
                    )
                    continue

                if not await rate_limiter.allow(channel.value, recipient):
                    await self._persist_delivery(
                        tenant_id=tenant_id,
                        trigger=trigger,
                        event=event,
                        step_id=step_id,
                        channel=channel,
                        recipient=recipient,
                        customer=customer,
                        context=rendering_context,
                        status=DeliveryStatus.RATE_LIMITED,
                        error="rate_limited",
                    )
                    continue

                delivery = await self._persist_delivery(
                    tenant_id=tenant_id,
                    trigger=trigger,
                    event=event,
                    step_id=step_id,
                    channel=channel,
                    recipient=recipient,
                    customer=customer,
                    context=rendering_context,
                    status=DeliveryStatus.QUEUED,
                )
                try:
                    pool = await self._pool()
                    await pool.enqueue_job(
                        "send_notification",
                        str(delivery.id),
                        _queue_name="notifications",
                    )
                    delivery.queued_at = datetime.utcnow()
                    await delivery.save()
                except Exception as exc:
                    logger.exception("Failed to enqueue delivery %s", delivery.id)
                    delivery.status = DeliveryStatus.FAILED
                    delivery.last_error = f"enqueue_failed: {exc}"
                    await delivery.save()

    @staticmethod
    def _extract_step_id(event: WorkflowEvent) -> Optional[str]:
        for key in STEP_ID_EVENT_KEYS:
            value = event.event_data.get(key) if event.event_data else None
            if value:
                return str(value)
        return None

    @staticmethod
    async def _resolve_customer(event: WorkflowEvent) -> Optional[Customer]:
        if not event.user_id:
            return None
        return await Customer.find_one(Customer.keycloak_id == event.user_id)

    @staticmethod
    async def _enabled_channels(tenant_id: str) -> List[NotificationChannel]:
        configs = await NotificationChannelConfig.find(
            {"tenant_id": tenant_id, "enabled": True}
        ).to_list()
        return [cfg.channel for cfg in configs]

    @staticmethod
    def _recipient_for(customer: Optional[Customer], channel: NotificationChannel) -> Optional[str]:
        if not customer:
            return None
        if channel == NotificationChannel.EMAIL:
            return customer.email
        if channel == NotificationChannel.WHATSAPP:
            return customer.phone
        return None

    @staticmethod
    def _citizen_opted_in(customer: Optional[Customer], channel: NotificationChannel) -> bool:
        if not customer:
            return False
        prefs = customer.notification_preferences
        if channel == NotificationChannel.EMAIL:
            return prefs.email_enabled
        if channel == NotificationChannel.WHATSAPP:
            return prefs.whatsapp_enabled
        return False

    @staticmethod
    def _build_context(event: WorkflowEvent, customer: Optional[Customer]) -> Dict[str, Any]:
        ciudadano: Dict[str, Any] = {}
        if customer:
            ciudadano = {
                "nombre": customer.full_name,
                "email": customer.email,
                "telefono": customer.phone,
            }
        return {
            "ciudadano": ciudadano,
            "workflow": {"id": event.workflow_id},
            "instancia": {"id": event.instance_id},
            "paso": {"id": NotificationDispatcher._extract_step_id(event)},
            "evento": {
                "id": event.event_id,
                "tipo": event.event_type.value if isinstance(event.event_type, EventType) else str(event.event_type),
                "datos": event.event_data or {},
            },
        }

    @staticmethod
    async def _persist_delivery(
        *,
        tenant_id: str,
        trigger: NotificationTrigger,
        event: WorkflowEvent,
        step_id: Optional[str],
        channel: NotificationChannel,
        recipient: str,
        customer: Optional[Customer],
        context: Dict[str, Any],
        status: DeliveryStatus,
        error: Optional[str] = None,
    ) -> NotificationDelivery:
        locale = customer.preferred_language if customer else "es"
        delivery = NotificationDelivery(
            tenant_id=tenant_id,
            instance_id=event.instance_id,
            workflow_id=event.workflow_id,
            step_id=step_id,
            event_id=event.event_id,
            trigger_id=str(trigger.id) if trigger.id else None,
            channel=channel,
            recipient=recipient,
            template_key=trigger.template_key,
            locale=locale,
            status=status,
            last_error=error,
            context_snapshot=context,
        )
        await delivery.insert()
        return delivery


dispatcher = NotificationDispatcher()


async def register_notification_dispatcher(event_manager) -> None:
    """Subscribe the singleton dispatcher to every workflow event type."""
    for event_type in EventType:
        event_manager.subscribe(event_type, dispatcher.handle_event)
    logger.info("NotificationDispatcher subscribed to %d event types", len(list(EventType)))
