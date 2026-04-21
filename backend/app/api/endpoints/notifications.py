"""Admin + internal API for the notifications subsystem."""
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field

from ...auth.provider import require_admin
from ...core.config import settings
from ...core.logging_config import get_workflow_logger
from ...notifier.encryption import decrypt_credentials, encrypt_credentials, mask_credentials
from ...notifier.handlers import get_handler
from ...notifier.handlers.base import (
    OutboundMessage,
    PermanentDeliveryError,
    TransientDeliveryError,
)
from ...notifier.models import (
    DeliveryStatus,
    NotificationChannel,
    NotificationChannelConfig,
    NotificationDelivery,
    NotificationTemplate,
    NotificationTrigger,
)
from ...notifier.rendering import TemplateRenderError, render, sample_context

logger = get_workflow_logger(__name__)

router = APIRouter()


def _tenant_id() -> str:
    if not settings.TENANT_ID:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="TENANT_ID no configurado en el backend",
        )
    return settings.TENANT_ID


# ---------- Schemas ----------


class ChannelConfigIn(BaseModel):
    enabled: bool = False
    credentials: Dict[str, Any] = Field(default_factory=dict)
    from_address: Optional[str] = None
    test_recipient: Optional[str] = None


class ChannelConfigOut(BaseModel):
    channel: NotificationChannel
    enabled: bool
    credentials: Dict[str, Any]
    from_address: Optional[str]
    test_recipient: Optional[str]
    updated_at: Optional[datetime]
    updated_by: Optional[str]


class TestChannelIn(BaseModel):
    recipient: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None


class TemplateIn(BaseModel):
    key: str
    locale: str = "es"
    channel: NotificationChannel
    subject: Optional[str] = None
    body: str
    variables_doc: Optional[str] = None
    active: bool = True


class TemplateOut(TemplateIn):
    id: str
    updated_at: datetime


class TemplatePreviewIn(BaseModel):
    subject: Optional[str] = None
    body: str
    channel: NotificationChannel = NotificationChannel.EMAIL
    context: Optional[Dict[str, Any]] = None


class TemplatePreviewOut(BaseModel):
    subject: Optional[str]
    body: str


class TriggerIn(BaseModel):
    workflow_id: str
    step_id: Optional[str] = None
    event_type: str
    template_key: str
    channels: List[NotificationChannel]
    active: bool = True


class TriggerOut(TriggerIn):
    id: str
    updated_at: datetime


class DeliveryOut(BaseModel):
    id: str
    channel: NotificationChannel
    recipient: str
    template_key: str
    status: DeliveryStatus
    attempts: int
    workflow_id: Optional[str]
    step_id: Optional[str]
    instance_id: Optional[str]
    rendered_preview: Optional[str]
    last_error: Optional[str]
    created_at: datetime
    sent_at: Optional[datetime]


# ---------- Channels ----------


def _serialize_channel(cfg: NotificationChannelConfig) -> ChannelConfigOut:
    raw_creds = decrypt_credentials(cfg.credentials_encrypted) if cfg.credentials_encrypted else {}
    return ChannelConfigOut(
        channel=cfg.channel,
        enabled=cfg.enabled,
        credentials=mask_credentials(raw_creds),
        from_address=cfg.from_address,
        test_recipient=cfg.test_recipient,
        updated_at=cfg.updated_at,
        updated_by=cfg.updated_by,
    )


@router.get("/channels", response_model=List[ChannelConfigOut])
async def list_channels(current_user: dict = Depends(require_admin)):
    tenant_id = _tenant_id()
    configs = await NotificationChannelConfig.find(
        NotificationChannelConfig.tenant_id == tenant_id
    ).to_list()
    return [_serialize_channel(cfg) for cfg in configs]


@router.get("/channels/{channel}", response_model=ChannelConfigOut)
async def get_channel(channel: NotificationChannel, current_user: dict = Depends(require_admin)):
    tenant_id = _tenant_id()
    cfg = await NotificationChannelConfig.find_one(
        NotificationChannelConfig.tenant_id == tenant_id,
        NotificationChannelConfig.channel == channel,
    )
    if not cfg:
        raise HTTPException(status_code=404, detail="Canal no configurado")
    return _serialize_channel(cfg)


@router.put("/channels/{channel}", response_model=ChannelConfigOut)
async def put_channel(
    channel: NotificationChannel,
    payload: ChannelConfigIn,
    current_user: dict = Depends(require_admin),
):
    tenant_id = _tenant_id()
    cfg = await NotificationChannelConfig.find_one(
        NotificationChannelConfig.tenant_id == tenant_id,
        NotificationChannelConfig.channel == channel,
    )

    # Merge credentials: masked entries ("****") mean "keep existing"
    existing_creds = decrypt_credentials(cfg.credentials_encrypted) if cfg and cfg.credentials_encrypted else {}
    merged_creds: Dict[str, Any] = dict(existing_creds)
    for key, value in payload.credentials.items():
        if value == "****":
            continue
        merged_creds[key] = value

    encrypted = encrypt_credentials(merged_creds) if merged_creds else None

    if cfg:
        cfg.enabled = payload.enabled
        cfg.credentials_encrypted = encrypted
        cfg.from_address = payload.from_address
        cfg.test_recipient = payload.test_recipient
        cfg.updated_at = datetime.utcnow()
        cfg.updated_by = current_user.get("sub")
        await cfg.save()
    else:
        cfg = NotificationChannelConfig(
            tenant_id=tenant_id,
            channel=channel,
            enabled=payload.enabled,
            credentials_encrypted=encrypted,
            from_address=payload.from_address,
            test_recipient=payload.test_recipient,
            updated_by=current_user.get("sub"),
        )
        await cfg.insert()

    return _serialize_channel(cfg)


@router.post("/channels/{channel}/test")
async def test_channel(
    channel: NotificationChannel,
    payload: TestChannelIn,
    current_user: dict = Depends(require_admin),
):
    tenant_id = _tenant_id()
    cfg = await NotificationChannelConfig.find_one(
        NotificationChannelConfig.tenant_id == tenant_id,
        NotificationChannelConfig.channel == channel,
    )
    if not cfg:
        raise HTTPException(status_code=404, detail="Canal no configurado")

    creds = decrypt_credentials(cfg.credentials_encrypted) if cfg.credentials_encrypted else {}
    recipient = payload.recipient or cfg.test_recipient
    if not recipient:
        raise HTTPException(status_code=400, detail="Recipient de prueba no definido")

    subject = payload.subject or "MuniStream: prueba de notificaciones"
    body = payload.body or (
        "Este es un mensaje de prueba enviado desde la configuración de "
        "notificaciones de MuniStream. Si lo recibes, la integración está funcionando."
    )

    handler = get_handler(channel.value)
    message = OutboundMessage(
        recipient=recipient,
        subject=subject,
        body=body,
        from_address=cfg.from_address,
        channel_credentials=creds,
        tenant_id=tenant_id,
    )

    try:
        result = await handler.send(message)
    except PermanentDeliveryError as exc:
        logger.warning("Prueba permanente rechazada: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TransientDeliveryError as exc:
        logger.warning("Prueba con fallo transitorio: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {"success": result.success, "provider_reference": result.provider_reference}


# ---------- Templates ----------


def _serialize_template(tpl: NotificationTemplate) -> TemplateOut:
    return TemplateOut(
        id=str(tpl.id),
        key=tpl.key,
        locale=tpl.locale,
        channel=tpl.channel,
        subject=tpl.subject,
        body=tpl.body,
        variables_doc=tpl.variables_doc,
        active=tpl.active,
        updated_at=tpl.updated_at,
    )


@router.get("/templates", response_model=List[TemplateOut])
async def list_templates(
    current_user: dict = Depends(require_admin),
    channel: Optional[NotificationChannel] = None,
):
    tenant_id = _tenant_id()
    filters: Dict[str, Any] = {"tenant_id": tenant_id}
    if channel is not None:
        filters["channel"] = channel.value
    templates = await NotificationTemplate.find(filters).sort("key").to_list()
    return [_serialize_template(t) for t in templates]


@router.post("/templates", response_model=TemplateOut, status_code=201)
async def create_template(payload: TemplateIn, current_user: dict = Depends(require_admin)):
    tenant_id = _tenant_id()
    existing = await NotificationTemplate.find_one(
        NotificationTemplate.tenant_id == tenant_id,
        NotificationTemplate.key == payload.key,
        NotificationTemplate.locale == payload.locale,
        NotificationTemplate.channel == payload.channel,
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail="Ya existe una plantilla con ese key/locale/canal",
        )
    tpl = NotificationTemplate(
        tenant_id=tenant_id,
        key=payload.key,
        locale=payload.locale,
        channel=payload.channel,
        subject=payload.subject,
        body=payload.body,
        variables_doc=payload.variables_doc,
        active=payload.active,
        updated_by=current_user.get("sub"),
    )
    await tpl.insert()
    return _serialize_template(tpl)


@router.put("/templates/{template_id}", response_model=TemplateOut)
async def update_template(
    template_id: str,
    payload: TemplateIn,
    current_user: dict = Depends(require_admin),
):
    tenant_id = _tenant_id()
    try:
        obj_id = PydanticObjectId(template_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Identificador inválido") from exc
    tpl = await NotificationTemplate.get(obj_id)
    if not tpl or tpl.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")
    tpl.key = payload.key
    tpl.locale = payload.locale
    tpl.channel = payload.channel
    tpl.subject = payload.subject
    tpl.body = payload.body
    tpl.variables_doc = payload.variables_doc
    tpl.active = payload.active
    tpl.updated_at = datetime.utcnow()
    tpl.updated_by = current_user.get("sub")
    await tpl.save()
    return _serialize_template(tpl)


@router.delete("/templates/{template_id}", status_code=204)
async def delete_template(template_id: str, current_user: dict = Depends(require_admin)):
    tenant_id = _tenant_id()
    try:
        obj_id = PydanticObjectId(template_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Identificador inválido") from exc
    tpl = await NotificationTemplate.get(obj_id)
    if not tpl or tpl.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")
    await tpl.delete()
    return None


@router.post("/templates/preview", response_model=TemplatePreviewOut)
async def preview_template(
    payload: TemplatePreviewIn,
    current_user: dict = Depends(require_admin),
):
    context = payload.context or sample_context()
    try:
        rendered = render(payload.body, context, payload.subject)
    except TemplateRenderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TemplatePreviewOut(subject=rendered.subject, body=rendered.body)


# ---------- Triggers ----------


def _serialize_trigger(trg: NotificationTrigger) -> TriggerOut:
    return TriggerOut(
        id=str(trg.id),
        workflow_id=trg.workflow_id,
        step_id=trg.step_id,
        event_type=trg.event_type,
        template_key=trg.template_key,
        channels=trg.channels,
        active=trg.active,
        updated_at=trg.updated_at,
    )


@router.get("/triggers", response_model=List[TriggerOut])
async def list_triggers(
    current_user: dict = Depends(require_admin),
    workflow_id: Optional[str] = None,
):
    tenant_id = _tenant_id()
    filters: Dict[str, Any] = {"tenant_id": tenant_id}
    if workflow_id:
        filters["workflow_id"] = workflow_id
    triggers = await NotificationTrigger.find(filters).to_list()
    return [_serialize_trigger(t) for t in triggers]


@router.post("/triggers", response_model=TriggerOut, status_code=201)
async def create_trigger(payload: TriggerIn, current_user: dict = Depends(require_admin)):
    tenant_id = _tenant_id()
    trg = NotificationTrigger(
        tenant_id=tenant_id,
        workflow_id=payload.workflow_id,
        step_id=payload.step_id,
        event_type=payload.event_type,
        template_key=payload.template_key,
        channels=payload.channels,
        active=payload.active,
        created_by=current_user.get("sub"),
    )
    await trg.insert()
    return _serialize_trigger(trg)


@router.put("/triggers/{trigger_id}", response_model=TriggerOut)
async def update_trigger(
    trigger_id: str,
    payload: TriggerIn,
    current_user: dict = Depends(require_admin),
):
    tenant_id = _tenant_id()
    try:
        obj_id = PydanticObjectId(trigger_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Identificador inválido") from exc
    trg = await NotificationTrigger.get(obj_id)
    if not trg or trg.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Trigger no encontrado")
    trg.workflow_id = payload.workflow_id
    trg.step_id = payload.step_id
    trg.event_type = payload.event_type
    trg.template_key = payload.template_key
    trg.channels = payload.channels
    trg.active = payload.active
    trg.updated_at = datetime.utcnow()
    await trg.save()
    return _serialize_trigger(trg)


@router.delete("/triggers/{trigger_id}", status_code=204)
async def delete_trigger(trigger_id: str, current_user: dict = Depends(require_admin)):
    tenant_id = _tenant_id()
    try:
        obj_id = PydanticObjectId(trigger_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Identificador inválido") from exc
    trg = await NotificationTrigger.get(obj_id)
    if not trg or trg.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Trigger no encontrado")
    await trg.delete()
    return None


# ---------- Deliveries ----------


def _serialize_delivery(d: NotificationDelivery) -> DeliveryOut:
    return DeliveryOut(
        id=str(d.id),
        channel=d.channel,
        recipient=d.recipient,
        template_key=d.template_key,
        status=d.status,
        attempts=d.attempts,
        workflow_id=d.workflow_id,
        step_id=d.step_id,
        instance_id=d.instance_id,
        rendered_preview=d.rendered_preview,
        last_error=d.last_error,
        created_at=d.created_at,
        sent_at=d.sent_at,
    )


@router.get("/deliveries", response_model=List[DeliveryOut])
async def list_deliveries(
    current_user: dict = Depends(require_admin),
    status_filter: Optional[DeliveryStatus] = Query(default=None, alias="status"),
    channel: Optional[NotificationChannel] = None,
    instance_id: Optional[str] = None,
    limit: int = Query(default=50, le=500),
    skip: int = 0,
):
    tenant_id = _tenant_id()
    filters: Dict[str, Any] = {"tenant_id": tenant_id}
    if status_filter is not None:
        filters["status"] = status_filter.value
    if channel is not None:
        filters["channel"] = channel.value
    if instance_id:
        filters["instance_id"] = instance_id

    deliveries = (
        await NotificationDelivery.find(filters)
        .sort([("created_at", -1)])
        .skip(skip)
        .limit(limit)
        .to_list()
    )
    return [_serialize_delivery(d) for d in deliveries]


# ---------- Baileys proxy ----------


def _baileys_headers() -> Dict[str, str]:
    headers = {}
    if settings.BAILEYS_API_KEY:
        headers["X-API-Key"] = settings.BAILEYS_API_KEY
    return headers


def _require_baileys_base_url() -> str:
    if not settings.BAILEYS_BASE_URL:
        raise HTTPException(status_code=503, detail="BAILEYS_BASE_URL no configurado")
    return settings.BAILEYS_BASE_URL.rstrip("/")


@router.get("/baileys/status")
async def baileys_status(current_user: dict = Depends(require_admin)):
    base_url = _require_baileys_base_url()
    tenant_id = _tenant_id()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{base_url}/session/{tenant_id}/status",
                headers=_baileys_headers(),
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"baileys inalcanzable: {exc}") from exc
    return response.json()


@router.get("/baileys/qr")
async def baileys_qr(current_user: dict = Depends(require_admin)):
    base_url = _require_baileys_base_url()
    tenant_id = _tenant_id()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{base_url}/session/{tenant_id}/qr",
                headers=_baileys_headers(),
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"baileys inalcanzable: {exc}") from exc
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    return response.json()


@router.post("/baileys/connect")
async def baileys_connect(current_user: dict = Depends(require_admin)):
    base_url = _require_baileys_base_url()
    tenant_id = _tenant_id()
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{base_url}/session/{tenant_id}/connect",
                headers=_baileys_headers(),
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"baileys inalcanzable: {exc}") from exc
    return response.json()


@router.post("/baileys/logout")
async def baileys_logout(current_user: dict = Depends(require_admin)):
    base_url = _require_baileys_base_url()
    tenant_id = _tenant_id()
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{base_url}/session/{tenant_id}/logout",
                headers=_baileys_headers(),
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"baileys inalcanzable: {exc}") from exc
    return response.json()
