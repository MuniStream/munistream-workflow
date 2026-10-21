"""Bridges workflow events into notification deliveries.

A `NotificationDispatcher` is subscribed to every `EventType` on the running
`WorkflowEventManager`. It consults `NotificationTrigger`s for the current
tenant, resolves the target citizen, enforces opt-out and rate limits, and
enqueues jobs on the tenant's arq queue (see `queue_name`) for the worker to send.
"""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from ..core.config import settings
from ..models.customer import Customer
from ..models.legal_entity import LegalEntity
from ..models.workflow import EventType, WorkflowEvent, WorkflowInstance
from ..services.estado_instancia import RECHAZADO, estado_efectivo
from .contacts import DEFAULT_COUNTRY_CODE, contact_points, resolve_customer
from .models import (
    DeliveryStatus,
    NotificationChannel,
    NotificationChannelConfig,
    NotificationDelivery,
    NotificationTrigger,
)
from .rate_limit import rate_limiter

logger = logging.getLogger(__name__)


def _document_view(entity: LegalEntity) -> Dict[str, Any]:
    folio_field = (entity.entity_display_config or {}).get("folio_field")
    data = entity.data or {}
    folio = data.get(folio_field) if folio_field else data.get("folio")
    return {
        "id": entity.entity_id,
        "nombre": entity.name or entity.entity_type,
        "tipo": entity.entity_type,
        "folio": folio or "",
    }

STEP_ID_EVENT_KEYS = ("step_id", "failed_step", "approval_step", "current_step")


def _redis_settings() -> RedisSettings:
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    redis_settings.conn_timeout = 5
    redis_settings.conn_retries = 5
    return redis_settings


def queue_name(tenant_id: Optional[str] = None) -> str:
    """Redis is shared by every tenant backend: each tenant gets its own queue
    so a worker never picks up a delivery stored in another tenant's database."""
    return f"notifications:{tenant_id or settings.TENANT_ID}"


# Notices the citizen must always get: they are never rate limited.
UNTHROTTLED_TEMPLATE_KEYS = {"entidad_emitida", "resolucion_final", "modificaciones_solicitadas"}

# Event payload keys that carry whole contexts: never copied into the
# rendering snapshot stored with each delivery.
_BULKY_EVENT_KEYS = {"final_context", "initial_context", "entity_data", "context_at_failure"}


def _event_type_value(event: WorkflowEvent) -> str:
    return event.event_type.value if isinstance(event.event_type, EventType) else str(event.event_type)


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
        event_type_value = _event_type_value(event)
        event_data = event.event_data or {}

        # A step only notifies when its operator says so (BaseOperator.notify).
        if event_type_value == EventType.STEP_ADVANCED.value and not event_data.get("notify", True):
            return

        triggers = await NotificationTrigger.find(
            {
                "tenant_id": tenant_id,
                "event_type": event_type_value,
                "active": True,
                "$or": [
                    {"workflow_id": event.workflow_id},
                    {"workflow_id": None},
                ],
            }
        ).to_list()

        applicable = [t for t in triggers if t.step_id is None or t.step_id == step_id]
        if not applicable:
            return

        instance = await self._load_instance(event.instance_id)
        # Sub-workflows (admin validation, technical opinion) carry the
        # citizen's user_id, but their lifecycle is not the citizen's request.
        # ENTITY_CREATED is already re-pointed to the root instance.
        if instance is not None and instance.parent_instance_id:
            return

        customer = await resolve_customer(event.user_id)
        if not customer:
            logger.info("Skipping event %s: no citizen for user %s", event.event_id, event.user_id)
            return

        channel_configs = await self._enabled_channels(tenant_id)
        whatsapp_cfg = channel_configs.get(NotificationChannel.WHATSAPP)
        country_code = (
            (whatsapp_cfg.options or {}).get("default_country_code") if whatsapp_cfg else None
        ) or DEFAULT_COUNTRY_CODE
        contacts = await contact_points(customer, country_code)

        entity_ids = self._event_entity_ids(event)
        base_context = await self._build_context(event, customer, instance, entity_ids)

        for trigger in applicable:
            attachments = entity_ids if trigger.attach_documents else []
            rendering_context = {**base_context, "adjuntos": bool(attachments)}
            for channel in trigger.channels:
                if channel not in channel_configs:
                    continue
                recipients = contacts.get(channel.value, [])
                if not recipients:
                    logger.info(
                        "Skipping %s for event %s: citizen has no %s contact",
                        channel.value,
                        event.event_id,
                        channel.value,
                    )
                    continue
                for recipient in recipients:
                    await self._deliver(
                        tenant_id=tenant_id,
                        trigger=trigger,
                        event=event,
                        step_id=step_id,
                        channel=channel,
                        recipient=recipient,
                        customer=customer,
                        context=rendering_context,
                        attachments=attachments,
                    )

    async def _deliver(
        self,
        *,
        tenant_id: str,
        trigger: NotificationTrigger,
        event: WorkflowEvent,
        step_id: Optional[str],
        channel: NotificationChannel,
        recipient: str,
        customer: Customer,
        context: Dict[str, Any],
        attachments: List[str],
    ) -> None:
        common = dict(
            tenant_id=tenant_id,
            trigger=trigger,
            event=event,
            step_id=step_id,
            channel=channel,
            recipient=recipient,
            customer=customer,
            context=context,
            attachments=attachments,
        )
        if not self._citizen_opted_in(customer, channel, trigger.template_key):
            await self._persist_delivery(**common, status=DeliveryStatus.SKIPPED_OPT_OUT)
            return

        if trigger.template_key not in UNTHROTTLED_TEMPLATE_KEYS and not await rate_limiter.allow(
            f"{tenant_id}:{channel.value}", recipient
        ):
            await self._persist_delivery(**common, status=DeliveryStatus.RATE_LIMITED, error="rate_limited")
            return

        delivery = await self._persist_delivery(**common, status=DeliveryStatus.QUEUED)
        try:
            pool = await self._pool()
            await pool.enqueue_job(
                "send_notification",
                str(delivery.id),
                _queue_name=queue_name(tenant_id),
            )
            delivery.queued_at = datetime.utcnow()
            await delivery.save()
        except Exception as exc:
            logger.exception("Failed to enqueue delivery %s", delivery.id)
            delivery.status = DeliveryStatus.FAILED
            delivery.last_error = f"enqueue_failed: {exc}"
            await delivery.save()

    @staticmethod
    def _event_entity_ids(event: WorkflowEvent) -> List[str]:
        data = event.event_data or {}
        if data.get("entity_id"):
            return [data["entity_id"]]
        return [e for e in (data.get("entities_created") or []) if e]

    @staticmethod
    async def _load_instance(instance_id: Optional[str]) -> Optional[WorkflowInstance]:
        if not instance_id:
            return None
        return await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)

    @staticmethod
    def _extract_step_id(event: WorkflowEvent) -> Optional[str]:
        for key in STEP_ID_EVENT_KEYS:
            value = event.event_data.get(key) if event.event_data else None
            if value:
                return str(value)
        return None

    @staticmethod
    async def _enabled_channels(tenant_id: str) -> Dict[NotificationChannel, NotificationChannelConfig]:
        configs = await NotificationChannelConfig.find(
            {"tenant_id": tenant_id, "enabled": True}
        ).to_list()
        return {cfg.channel: cfg for cfg in configs}

    @staticmethod
    def _citizen_opted_in(
        customer: Optional[Customer],
        channel: NotificationChannel,
        template_key: str,
    ) -> bool:
        """Master switch gates the channel; then per-notification override, then default opt-in."""
        if not customer:
            return False
        prefs = customer.notification_preferences
        if channel == NotificationChannel.EMAIL and not prefs.email_enabled:
            return False
        if channel == NotificationChannel.WHATSAPP and not prefs.whatsapp_enabled:
            return False
        override = prefs.per_notification.get(template_key)
        if override is None:
            return True
        if channel == NotificationChannel.EMAIL:
            return override.email
        if channel == NotificationChannel.WHATSAPP:
            return override.whatsapp
        return True

    @staticmethod
    async def _build_context(
        event: WorkflowEvent,
        customer: Optional[Customer],
        instance: Optional[WorkflowInstance],
        entity_ids: List[str],
    ) -> Dict[str, Any]:
        """Rendering context. Templates render with StrictUndefined, so every
        key documented in system_notifications is always present."""
        from ..services.workflow_service import workflow_service

        data = event.event_data or {}
        dag = workflow_service.dag_bag.get_dag(event.workflow_id) if event.workflow_id else None
        step_id = NotificationDispatcher._extract_step_id(event)
        if event.event_type == EventType.STEP_ADVANCED:
            # The notice is about the stage that just finished.
            step_id = data.get("previous_step") or step_id
        step = dag.tasks.get(step_id) if dag and step_id else None

        terminal_status = data.get("terminal_status") or (instance.terminal_status if instance else None)
        terminal_message = instance.terminal_message if instance else None
        outcome = estado_efectivo("completed", terminal_status) if event.event_type == EventType.COMPLETED else ""

        documents = []
        if entity_ids:
            entities = await LegalEntity.find({"entity_id": {"$in": entity_ids}}).to_list()
            by_id = {e.entity_id: e for e in entities}
            documents = [_document_view(by_id[i]) for i in entity_ids if i in by_id]
        document = documents[0] if documents else {"id": "", "nombre": "", "tipo": "", "folio": ""}
        if event.event_type == EventType.ENTITY_CREATED:
            document = {
                **document,
                "nombre": data.get("entity_name") or document["nombre"],
                "folio": data.get("folio") or document["folio"],
            }

        base_url = (settings.FRONTEND_BASE_URL or "").rstrip("/")
        return {
            "remitente": settings.TENANT_NAME or settings.PROJECT_NAME,
            "ciudadano": {
                "nombre": customer.full_name if customer else "",
                "email": customer.email if customer else "",
                "telefono": (customer.phone or "") if customer else "",
            },
            "workflow": {
                "id": event.workflow_id,
                "nombre": (dag.name if dag else None) or event.workflow_id,
            },
            # Same short reference the citizen portal shows in "My requests".
            "instancia": {"id": event.instance_id or "", "folio": (event.instance_id or "")[:8]},
            "paso": {
                "id": step_id or "",
                "nombre": (step.name if step else None) or step_id or "",
            },
            "url_tramite": f"{base_url}/instances/{event.instance_id}" if base_url and event.instance_id else "",
            "resultado": {
                "codigo": outcome,
                "rechazado": outcome == RECHAZADO,
                "mensaje": terminal_message or "",
            },
            "documento": document,
            "documentos": documents,
            "adjuntos": False,
            "evento": {
                "id": event.event_id,
                "tipo": _event_type_value(event),
                "datos": {k: v for k, v in data.items() if k not in _BULKY_EVENT_KEYS},
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
        attachments: Optional[List[str]] = None,
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
            attachments=list(attachments or []),
        )
        await delivery.insert()
        return delivery


dispatcher = NotificationDispatcher()


async def register_notification_dispatcher(event_manager) -> None:
    """Subscribe the singleton dispatcher to every workflow event type."""
    for event_type in EventType:
        event_manager.subscribe(event_type, dispatcher.handle_event)
    logger.info("NotificationDispatcher subscribed to %d event types", len(list(EventType)))
