"""
A review that outlives its deadline is escalated and announced; the citizen's
request keeps waiting instead of failing.

Pure tests: Mongo, Keycloak, the email channel and the event manager are faked.
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.models.workflow import AssignmentStatus, WorkflowInstance, WorkflowType  # noqa: E402
from app.services import review_escalation  # noqa: E402
from app.services.review_escalation import due_escalation_level, period_label  # noqa: E402
from app.workflows.operators import workflow_start_operator as wso  # noqa: E402
from app.workflows.operators.base import TaskStatus  # noqa: E402
from app.workflows.operators.workflow_start_operator import WorkflowStartOperator  # noqa: E402

DAY = 1440


def _child(**kwargs):
    kwargs.setdefault("instance_id", "child-1")
    kwargs.setdefault("workflow_id", "admin_validacion")
    kwargs.setdefault("user_id", "citizen-1")
    kwargs.setdefault("status", "waiting_for_start")
    kwargs.setdefault("assigned_team_id", "validadores")
    kwargs.setdefault("parent_instance_id", "parent-1")
    defaults = {name: field.get_default(call_default_factory=True)
                for name, field in WorkflowInstance.model_fields.items() if not field.is_required()}
    return WorkflowInstance.model_construct(**{**defaults, **kwargs})


def _operator(**kwargs):
    kwargs.setdefault("task_id", "validacion_admin")
    kwargs.setdefault("name", "Validación Administrativa")
    kwargs.setdefault("workflow_id", "admin_validacion")
    kwargs.setdefault("workflow_type", WorkflowType.ADMIN)
    kwargs.setdefault("timeout_minutes", DAY)
    return WorkflowStartOperator(**kwargs)


def _context(op, started_minutes_ago):
    start = datetime.utcnow() - timedelta(minutes=started_minutes_ago)
    return {
        "instance_id": "parent-1",
        "workflow_id": "representacion_legal",
        "user_id": "citizen-1",
        f"workflow_start_{op.task_id}_parent-1_state": {
            "child_instance_id": "child-1",
            "start_time": start.isoformat(),
            "child_failure_count": 0,
        },
    }


@pytest.fixture
def lookup(monkeypatch):
    """Serve `child` from WorkflowInstance.find_one."""
    holder = {}

    async def find_one(*_args, **_kwargs):
        return holder["child"]

    monkeypatch.setattr(WorkflowInstance, "find_one", find_one)
    # Beanie is not initialised: the query expression needs a class attribute.
    monkeypatch.setattr(WorkflowInstance, "instance_id", "instance_id", raising=False)
    return holder


@pytest.fixture
def escalations(monkeypatch):
    calls = []

    async def fake(**kwargs):
        calls.append(kwargs)
        kwargs["child"].escalation_level = kwargs["level"]
        return {"escalation_level": kwargs["level"]}

    monkeypatch.setattr(wso, "escalate_overdue_review", fake)
    return calls


# ---------- levels ----------


def test_nothing_is_due_before_the_deadline():
    assert due_escalation_level(DAY - 1, DAY, 0, 3) is None
    assert due_escalation_level(DAY, DAY, 0, 3) is None


def test_one_level_per_elapsed_period_up_to_the_maximum():
    assert due_escalation_level(DAY + 1, DAY, 0, 3) == 1
    assert due_escalation_level(2 * DAY + 1, DAY, 1, 3) == 2
    assert due_escalation_level(10 * DAY, DAY, 2, 3) == 3
    assert due_escalation_level(10 * DAY, DAY, 3, 3) is None


def test_a_reached_level_is_not_repeated():
    assert due_escalation_level(DAY + 30, DAY, 1, 3) is None


def test_a_late_poll_jumps_straight_to_the_level_due():
    assert due_escalation_level(3 * DAY + 5, DAY, 0, 3) == 3


def test_period_labels():
    assert period_label(1440) == "24 horas"
    assert period_label(2880) == "2 días"
    assert period_label(90) == "1.5 horas"


# ---------- operator ----------


def test_escalate_is_the_default_and_unknown_modes_are_rejected():
    assert _operator().on_timeout == "escalate"
    with pytest.raises(ValueError):
        _operator(on_timeout="ignore")


def test_overdue_review_escalates_and_keeps_the_request_waiting(lookup, escalations):
    asyncio.run(_overdue_review_escalates_and_keeps_the_request_waiting(lookup, escalations))


async def _overdue_review_escalates_and_keeps_the_request_waiting(lookup, escalations):
    op = _operator(escalate_to={"team": "supervisores"})
    lookup["child"] = _child()
    result = await op.execute_async(_context(op, DAY + 10))

    assert result.status == TaskStatus.WAITING
    assert result.data["escalation_level"] == 1
    assert len(escalations) == 1
    call = escalations[0]
    assert call["level"] == 1
    assert call["escalate_to_team"] == "supervisores"
    assert call["step_name"] == "Validación Administrativa"
    assert call["parent_context"]["instance_id"] == "parent-1"


def test_the_next_poll_in_the_same_period_does_not_notify_again(lookup, escalations):
    asyncio.run(_the_next_poll_in_the_same_period_does_not_notify_again(lookup, escalations))


async def _the_next_poll_in_the_same_period_does_not_notify_again(lookup, escalations):
    op = _operator()
    lookup["child"] = _child(escalation_level=1)
    result = await op.execute_async(_context(op, DAY + 60))
    assert result.status == TaskStatus.WAITING
    assert escalations == []


def test_a_failed_escalation_does_not_break_the_wait(lookup, monkeypatch):
    asyncio.run(_a_failed_escalation_does_not_break_the_wait(lookup, monkeypatch))


async def _a_failed_escalation_does_not_break_the_wait(lookup, monkeypatch):
    async def broken(**_kwargs):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(wso, "escalate_overdue_review", broken)
    op = _operator()
    lookup["child"] = _child()
    result = await op.execute_async(_context(op, DAY + 10))
    assert result.status == TaskStatus.WAITING


def test_a_review_resolved_after_the_deadline_still_continues(lookup, escalations):
    asyncio.run(_a_review_resolved_after_the_deadline_still_continues(lookup, escalations))


async def _a_review_resolved_after_the_deadline_still_continues(lookup, escalations):
    op = _operator()
    lookup["child"] = _child(status="completed", context={}, escalation_level=1)
    result = await op.execute_async(_context(op, 3 * DAY))
    assert result.status == TaskStatus.CONTINUE
    assert escalations == []


def test_fail_mode_still_cancels_the_child_and_fails(lookup, escalations, monkeypatch):
    asyncio.run(_fail_mode_still_cancels_the_child_and_fails(lookup, escalations, monkeypatch))


async def _fail_mode_still_cancels_the_child_and_fails(lookup, escalations, monkeypatch):
    cancelled = []
    monkeypatch.setattr(WorkflowStartOperator, "_cancel_overdue_child",
                        lambda self: cancelled.append(self.child_instance_id))
    op = _operator(on_timeout="fail")
    lookup["child"] = _child()
    result = await op.execute_async(_context(op, DAY + 10))
    assert result.status == TaskStatus.FAILED
    assert "24 horas" in result.error
    assert cancelled == ["child-1"]
    assert escalations == []


# ---------- escalation service ----------


def test_mark_overdue_keeps_the_review_state():
    child = _child(assignment_status=AssignmentStatus.UNDER_REVIEW, priority=5, previous_assignments=[])
    child.mark_overdue(level=2, reason="plazo vencido")
    assert child.assignment_status == AssignmentStatus.UNDER_REVIEW
    assert child.escalation_level == 2
    assert child.priority == 10
    assert child.previous_assignments[-1]["escalation_level"] == 2


def test_staff_recipients(monkeypatch):
    asyncio.run(_staff_recipients(monkeypatch))


async def _staff_recipients(monkeypatch):
    members = {
        "validadores": [{"id": "kc-ana", "email": "Ana@Catastro.mx"}, {"id": "kc-luis", "email": "luis@catastro.mx"}],
        "supervisores": [{"id": "kc-jefa", "email": "jefa@catastro.mx"}],
    }

    async def team(team_id):
        return members.get(team_id, [])

    class Cfg:
        alertas = True
        destinatarios = ["direccion@catastro.mx", "ana@catastro.mx"]

    async def cfg():
        return Cfg()

    monkeypatch.setattr(review_escalation, "_team_members", team)
    monkeypatch.setattr("app.services.reportes.configuracion", cfg)
    child = _child(assigned_user_id="kc-ana")
    assert await review_escalation.staff_recipients(child, "supervisores") == [
        "ana@catastro.mx", "luis@catastro.mx", "jefa@catastro.mx", "direccion@catastro.mx",
    ]

    Cfg.alertas = False
    assert "direccion@catastro.mx" not in await review_escalation.staff_recipients(child, None)


def test_first_escalation_emails_staff_and_tells_the_citizen(monkeypatch):
    asyncio.run(_first_escalation_emails_staff_and_tells_the_citizen(monkeypatch))


async def _first_escalation_emails_staff_and_tells_the_citizen(monkeypatch):
    sent, events, saved = [], [], []

    async def recipients(child, team):
        return ["ana@catastro.mx"]

    async def send(to, subject, body, adjuntos=None):
        sent.append((to, subject, body))
        return [{"destinatario": d, "enviado": True} for d in to]

    class Events:
        async def publish_event(self, **kwargs):
            events.append(kwargs)

    class Executor:
        event_manager = Events()

    from app.services import workflow_service as service

    monkeypatch.setattr(review_escalation, "staff_recipients", recipients)
    monkeypatch.setattr("app.services.reportes._enviar_correo", send)
    monkeypatch.setattr("app.services.reportes._admin_base_url", lambda: "https://admin.catastro.mx")
    monkeypatch.setattr(service.workflow_service, "executor", Executor(), raising=False)

    child = _child(previous_assignments=[], priority=5)

    async def save():
        saved.append(child.escalation_level)

    object.__setattr__(child, "save", save)
    kwargs = dict(child=child, parent_context={"instance_id": "parent-1a2b3c4d", "workflow_id": "representacion_legal",
                                               "user_id": "citizen-1"},
                  step_id="validacion_admin", step_name="Validación", max_escalations=3,
                  timeout_minutes=DAY, elapsed_minutes=DAY + 10)

    summary = await review_escalation.escalate_overdue_review(level=1, **kwargs)
    assert saved == [1]
    assert summary["staff_notified"] == ["ana@catastro.mx"]
    assert summary["citizen_notified"] is True
    to, subject, body = sent[0]
    assert "folio parent-1" in subject and "1.º aviso" in subject
    assert "https://admin.catastro.mx/instances/child-1" in body
    assert events[0]["event_type"].value == "review_overdue"
    assert events[0]["instance_id"] == "parent-1a2b3c4d"
    assert events[0]["event_data"]["plazo"] == "24 horas"

    # Later levels remind staff only.
    summary = await review_escalation.escalate_overdue_review(level=3, **kwargs)
    assert summary["citizen_notified"] is False
    assert len(events) == 1
    assert "último aviso" in sent[-1][2]


def test_citizen_notice_renders():
    from app.notifier.models import NotificationChannel
    from app.notifier.rendering import render
    from app.notifier.system_notifications import find_by_key

    entry = find_by_key("revision_demorada")
    assert entry.event_type.value == "review_overdue"
    ctx = {
        "remitente": "Catastro", "ciudadano": {"nombre": "Ana"}, "workflow": {"nombre": "Representación Legal"},
        "instancia": {"folio": "1136441f"}, "paso": {"nombre": "Validación Administrativa"},
        "url_tramite": "https://catastro.mx/instances/x", "evento": {"datos": {"plazo": "24 horas"}},
    }
    for (locale, channel), tpl in entry.templates.items():
        msg = render(tpl.body, ctx, tpl.subject or None)
        assert "1136441f" in msg.body
        if locale == "es" and channel == NotificationChannel.EMAIL:
            assert "plazo de atención de 24 horas." in msg.body and "sigue abierto" in msg.body
        # An edited preview or an older event without `plazo` still renders.
        bare = render(tpl.body, {**ctx, "evento": {"datos": {}}}, tpl.subject or None)
        assert "{{" not in bare.body
