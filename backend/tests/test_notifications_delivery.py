"""
Citizen notifications: who receives them, for which events, and with which
documents attached.

Runs against a throwaway MongoDB and Redis given by NOTIFY_TEST_MONGO_URL and
REDIS_URL (each test run uses its own database); skipped when they are absent.
"""
import os
import sys
import uuid
from email import message_from_bytes

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.notifier.contacts import normalize_email, normalize_phone  # noqa: E402

MONGO_URL = os.getenv("NOTIFY_TEST_MONGO_URL")
needs_db = pytest.mark.skipif(not MONGO_URL, reason="NOTIFY_TEST_MONGO_URL not set")

TENANT = "testtenant"


# ---------- pure helpers ----------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("5512345678", "+525512345678"),
        ("55 1234 5678", "+525512345678"),
        ("+52 1 55 1234 5678", "+525512345678"),
        ("5215512345678", "+525512345678"),
        ("+1 (415) 555-0100", "+14155550100"),
        ("0044 20 7946 0958", "+442079460958"),
        ("123", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


def test_normalize_phone_custom_country_code():
    assert normalize_phone("2025550100", "1") == "+12025550100"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Ana@Example.MX ", "ana@example.mx"),
        ("CURP123@llavemx.local", None),
        ("not-an-email", None),
        (None, None),
    ],
)
def test_normalize_email(raw, expected):
    assert normalize_email(raw) == expected


def test_system_templates_render_for_every_event():
    """Every system template renders with the context the dispatcher builds
    (StrictUndefined: a missing key would fail the delivery)."""
    from app.notifier.rendering import render
    from app.notifier.system_notifications import SYSTEM_NOTIFICATIONS

    base = {
        "remitente": "CONAPESCA",
        "ciudadano": {"nombre": "Ana López", "email": "ana@example.mx", "telefono": "+525512345678"},
        "workflow": {"id": "w", "nombre": "Permiso de pesca"},
        "instancia": {"id": "i1", "folio": "i1"},
        "paso": {"id": "confirmacion", "nombre": "Confirmación"},
        "url_tramite": "https://portal.example.mx/instances/i1",
        "resultado": {"codigo": "rejected", "rechazado": True, "mensaje": "Falta documento"},
        "documento": {"id": "e1", "nombre": "Oficio de resolución", "tipo": "oficio", "folio": "OF-2026-000001"},
        "documentos": [
            {"id": "e1", "nombre": "Oficio de resolución", "tipo": "oficio", "folio": "OF-2026-000001"},
            {"id": "e2", "nombre": "Permiso", "tipo": "permiso", "folio": ""},
        ],
        "adjuntos": True,
        "evento": {"id": "ev", "tipo": "COMPLETED", "datos": {}},
    }
    for entry in SYSTEM_NOTIFICATIONS:
        for (locale, channel), template in entry.templates.items():
            out = render(template.body, base, template.subject or None)
            assert "Ana López" in out.body
            assert "{{" not in out.body and "{%" not in out.body

    resolution = next(e for e in SYSTEM_NOTIFICATIONS if e.key == "resolucion_final")
    es_email = resolution.templates[("es", resolution.default_channels[0])]
    rejected = render(es_email.body, base, es_email.subject)
    assert rejected.subject == "Su trámite fue rechazado: Permiso de pesca"
    assert "Motivo: Falta documento" in rejected.body
    assert (
        "Se adjuntan los documentos emitidos:\n"
        "- Oficio de resolución (folio OF-2026-000001)\n- Permiso\n\n"
        "Puede consultar el detalle en: https://portal.example.mx/instances/i1\n\n"
        "Atentamente,\nCONAPESCA"
    ) in rejected.body

    approved = render(
        es_email.body,
        {**base, "resultado": {"codigo": "completed", "rechazado": False, "mensaje": ""}, "documentos": [], "adjuntos": False},
        es_email.subject,
    )
    assert approved.subject == "Su trámite concluyó: Permiso de pesca"
    assert "concluyó." in approved.body and "Se adjunta" not in approved.body


# ---------- against MongoDB / Redis ----------


@pytest.fixture
async def db(monkeypatch):
    from beanie import init_beanie
    from motor.motor_asyncio import AsyncIOMotorClient

    from app.core.config import settings
    from app.models.customer import Customer
    from app.models.legal_entity import LegalEntity
    from app.models.profile_field_definition import ProfileFieldDefinition
    from app.models.user_profile import UserProfile
    from app.models.workflow import WorkflowEvent, WorkflowInstance
    from app.notifier.models import (
        NotificationChannelConfig,
        NotificationDelivery,
        NotificationTemplate,
        NotificationTrigger,
    )

    monkeypatch.setattr(settings, "TENANT_ID", TENANT)
    monkeypatch.setattr(settings, "TENANT_NAME", "CONAPESCA")
    monkeypatch.setattr(settings, "FRONTEND_BASE_URL", "https://portal.example.mx")
    client = AsyncIOMotorClient(MONGO_URL)
    name = f"notify_test_{uuid.uuid4().hex[:8]}"
    await init_beanie(
        database=client[name],
        document_models=[
            Customer, LegalEntity, ProfileFieldDefinition, UserProfile,
            WorkflowEvent, WorkflowInstance, NotificationChannelConfig,
            NotificationDelivery, NotificationTemplate, NotificationTrigger,
        ],
    )
    from app.notifier.seed import seed_system_notifications

    await seed_system_notifications(TENANT)
    await NotificationChannelConfig(tenant_id=TENANT, channel="email", enabled=True).insert()
    await NotificationChannelConfig(tenant_id=TENANT, channel="whatsapp", enabled=True).insert()
    yield client[name]
    await client.drop_database(name)
    client.close()


async def _customer(phone="55 1234 5678", email="ana@example.mx"):
    from app.models.customer import Customer

    customer = Customer(
        email=email, full_name="Ana López", phone=phone,
        keycloak_id=f"kc-{uuid.uuid4().hex[:6]}", password_hash="x",
    )
    await customer.insert()
    return customer


async def _instance(customer, parent=None, workflow_id="tramite_demo"):
    from app.models.workflow import WorkflowInstance

    inst = WorkflowInstance(
        instance_id=f"inst_{uuid.uuid4().hex[:8]}",
        workflow_id=workflow_id,
        user_id=str(customer.id),
        parent_instance_id=parent.instance_id if parent else None,
        status="running",
    )
    await inst.insert()
    return inst


async def _event(event_type, instance, customer, data):
    from app.models.workflow import WorkflowEvent

    event = WorkflowEvent(
        event_id=uuid.uuid4().hex,
        workflow_id=instance.workflow_id,
        instance_id=instance.instance_id,
        event_type=event_type,
        event_data=data,
        user_id=str(customer.id),
    )
    await event.insert()
    return event


async def _deliveries():
    from app.notifier.models import NotificationDelivery

    return await NotificationDelivery.find({"tenant_id": TENANT}).to_list()


@needs_db
async def test_customer_resolved_by_id_and_by_keycloak_sub(db):
    from app.notifier.contacts import resolve_customer

    customer = await _customer()
    assert (await resolve_customer(str(customer.id))).id == customer.id
    assert (await resolve_customer(customer.keycloak_id)).id == customer.id
    assert await resolve_customer("nobody") is None


@needs_db
async def test_profile_phone_fields_are_added_to_the_account_phone(db):
    from app.models.profile_field_definition import ProfileFieldDefinition
    from app.models.user_profile import UserProfile
    from app.notifier.contacts import contact_points

    customer = await _customer(email="ana.cuenta@example.mx")
    await ProfileFieldDefinition(field_id="telefono_whatsapp", label="Celular", type="phone").insert()
    await ProfileFieldDefinition(field_id="correo_avisos", label="Correo", type="email").insert()
    await UserProfile(
        customer_id=str(customer.id),
        data={"telefono_whatsapp": "+52 1 55 9999 0000", "correo_avisos": "ana@example.mx"},
    ).insert()
    assert await contact_points(customer) == {
        "email": ["ana.cuenta@example.mx", "ana@example.mx"],
        "whatsapp": ["+525512345678", "+525599990000"],
    }

    # The same number typed differently is one recipient.
    profile = await UserProfile.find_one(UserProfile.customer_id == str(customer.id))
    profile.data["telefono_whatsapp"] = "5512345678"
    await profile.save()
    assert (await contact_points(customer))["whatsapp"] == ["+525512345678"]


@needs_db
async def test_entity_created_queues_one_delivery_per_contact_with_attachment(db):
    from app.models.legal_entity import LegalEntity
    from app.models.workflow import EventType
    from app.notifier.hook import dispatcher, queue_name

    customer = await _customer()
    root = await _instance(customer)
    await LegalEntity(
        entity_id="permiso_abc", entity_type="permiso", owner_user_id=str(customer.id),
        name="Permiso de pesca", data={"folio": "PP-2026-000001"},
        entity_display_config={"folio_field": "folio"},
    ).insert()
    event = await _event(EventType.ENTITY_CREATED, root, customer, {
        "entity_id": "permiso_abc", "entity_type": "permiso",
        "entity_name": "Permiso de pesca", "folio": "PP-2026-000001",
    })
    await dispatcher.handle_event(event)

    deliveries = await _deliveries()
    assert sorted((d.channel.value, d.recipient) for d in deliveries) == [
        ("email", "ana@example.mx"), ("whatsapp", "+525512345678"),
    ]
    for d in deliveries:
        assert d.template_key == "entidad_emitida"
        assert d.status.value == "queued"
        assert d.attachments == ["permiso_abc"]
        assert d.context_snapshot["documento"]["folio"] == "PP-2026-000001"
        assert d.context_snapshot["adjuntos"] is True
        assert d.context_snapshot["url_tramite"] == f"https://portal.example.mx/instances/{root.instance_id}"

    pool = await dispatcher._pool()
    queued = await pool.zcard(queue_name(TENANT))
    assert queued >= 2


@needs_db
async def test_completed_of_a_sub_workflow_does_not_notify_the_citizen(db):
    from app.models.workflow import EventType
    from app.notifier.hook import dispatcher

    customer = await _customer()
    root = await _instance(customer)
    child = await _instance(customer, parent=root, workflow_id="admin_validacion")
    event = await _event(EventType.COMPLETED, child, customer, {"entities_created": []})
    await dispatcher.handle_event(event)
    assert await _deliveries() == []


@needs_db
async def test_completed_root_attaches_every_issued_document_and_reports_rejection(db):
    from app.models.legal_entity import LegalEntity
    from app.models.workflow import EventType
    from app.notifier.hook import dispatcher

    customer = await _customer(phone=None)
    root = await _instance(customer)
    root.terminal_status = "rejected"
    root.terminal_message = "Documentación incompleta"
    await root.save()
    await LegalEntity(
        entity_id="oficio_1", entity_type="oficio", owner_user_id=str(customer.id), name="Oficio de rechazo",
    ).insert()
    event = await _event(EventType.COMPLETED, root, customer, {
        "entities_created": ["oficio_1"], "terminal_status": "rejected",
    })
    await dispatcher.handle_event(event)

    (delivery,) = await _deliveries()
    assert delivery.channel.value == "email"
    assert delivery.template_key == "resolucion_final"
    assert delivery.attachments == ["oficio_1"]
    assert delivery.context_snapshot["resultado"] == {
        "codigo": "rejected", "rechazado": True, "mensaje": "Documentación incompleta",
    }


@needs_db
async def test_step_advanced_only_when_the_finished_operator_notifies(db):
    from app.models.workflow import EventType
    from app.notifier.hook import dispatcher

    customer = await _customer()
    root = await _instance(customer)
    quiet = await _event(EventType.STEP_ADVANCED, root, customer,
                         {"previous_step": "formulario", "current_step": "confirmacion", "notify": False})
    await dispatcher.handle_event(quiet)
    assert await _deliveries() == []

    loud = await _event(EventType.STEP_ADVANCED, root, customer,
                        {"previous_step": "confirmacion", "current_step": "validacion", "notify": True})
    await dispatcher.handle_event(loud)
    assert {d.template_key for d in await _deliveries()} == {"paso_avanzado"}


@needs_db
async def test_opt_out_is_recorded_and_not_queued(db):
    from app.models.workflow import EventType
    from app.notifier.hook import dispatcher

    customer = await _customer()
    customer.notification_preferences.whatsapp_enabled = False
    await customer.save()
    root = await _instance(customer)
    await dispatcher.handle_event(await _event(EventType.COMPLETED, root, customer, {"entities_created": []}))
    by_channel = {d.channel.value: d.status.value for d in await _deliveries()}
    assert by_channel == {"email": "queued", "whatsapp": "skipped_opt_out"}


@needs_db
async def test_seed_refreshes_system_templates_but_keeps_admin_edits(db):
    from app.notifier.models import NotificationTemplate, NotificationTrigger
    from app.notifier.seed import seed_system_notifications

    edited = await NotificationTemplate.find_one({"tenant_id": TENANT, "key": "paso_avanzado", "locale": "es", "channel": "email"})
    edited.body = "Texto del admin"
    edited.updated_by = "admin-sub"
    await edited.save()
    stale = await NotificationTemplate.find_one({"tenant_id": TENANT, "key": "tramite_recibido", "locale": "es", "channel": "email"})
    stale.body = "Cuerpo viejo del sistema"
    await stale.save()
    await NotificationTrigger.get_motor_collection().update_many(
        {"tenant_id": TENANT}, {"$unset": {"attach_documents": ""}}
    )

    await seed_system_notifications(TENANT)

    assert (await NotificationTemplate.get(edited.id)).body == "Texto del admin"
    assert (await NotificationTemplate.get(stale.id)).body != "Cuerpo viejo del sistema"
    triggers = {t.template_key: t.attach_documents for t in await NotificationTrigger.find({"tenant_id": TENANT}).to_list()}
    assert triggers["entidad_emitida"] is True
    assert triggers["resolucion_final"] is True
    assert triggers["paso_avanzado"] is False


@needs_db
async def test_worker_sends_email_with_the_entity_pdf(db, monkeypatch):
    import aiosmtplib

    from app.models.legal_entity import LegalEntity
    from app.notifier.models import NotificationChannelConfig, NotificationDelivery
    from app.notifier.encryption import encrypt_credentials, _fernet
    from app.notifier.worker import send_notification
    from app.core.config import settings
    from cryptography.fernet import Fernet

    monkeypatch.setattr(settings, "NOTIFICATION_SECRET_KEY", Fernet.generate_key().decode())
    _fernet.cache_clear()
    cfg = await NotificationChannelConfig.find_one({"tenant_id": TENANT, "channel": "email"})
    cfg.credentials_encrypted = encrypt_credentials({"host": "smtp.test", "port": 587})
    cfg.from_address = "avisos@example.mx"
    await cfg.save()

    customer = await _customer()
    await LegalEntity(
        entity_id="permiso_pdf", entity_type="permiso", owner_user_id=str(customer.id),
        name="Permiso de pesca", data={"titular": "Ana López"},
    ).insert()
    delivery = NotificationDelivery(
        tenant_id=TENANT, channel="email", recipient="ana@example.mx",
        template_key="entidad_emitida", attachments=["permiso_pdf"],
        context_snapshot={
            "remitente": "CONAPESCA", "ciudadano": {"nombre": "Ana"},
            "workflow": {"id": "w", "nombre": "Permiso"}, "instancia": {"id": "i", "folio": "i"},
            "documento": {"nombre": "Permiso de pesca", "folio": ""}, "url_tramite": "", "adjuntos": True,
        },
    )
    await delivery.insert()

    sent = []

    async def capture(message, **kwargs):
        sent.append((message, kwargs))
        return ({}, "250 OK")

    monkeypatch.setattr(aiosmtplib, "send", capture)
    assert await send_notification({}, str(delivery.id)) == "sent"
    _fernet.cache_clear()

    (message, kwargs), = sent
    assert kwargs["hostname"] == "smtp.test"
    parsed = message_from_bytes(bytes(message))
    pdfs = [p for p in parsed.walk() if p.get_content_type() == "application/pdf"]
    assert len(pdfs) == 1
    assert pdfs[0].get_payload(decode=True).startswith(b"%PDF")
    stored = await NotificationDelivery.get(delivery.id)
    assert stored.status.value == "sent"
    assert stored.attachments_sent == [pdfs[0].get_filename()]
