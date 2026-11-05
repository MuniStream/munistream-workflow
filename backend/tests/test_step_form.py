"""
The form the track endpoints return for a waiting step (`app.services.step_form`).
No database is needed.
"""
import os
import sys
from types import SimpleNamespace

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.services.step_form import (  # noqa: E402
    FORM_OWNER_KEY, context_form_belongs_to, record_form_owner, waiting_step_form,
)

STATIC = {"title": "Datos del recibo", "description": "Texto fijo", "fields": [{"name": "folio"}]}
RECEIPT = {**STATIC, "description": "Pague $1,477.00 con la línea de captura", "validation_errors": {"folio": "No existe"}}
receipt_task = SimpleNamespace(form_config=STATIC)


def test_dynamic_form_wins_over_the_static_one():
    context = {"form_config": RECEIPT}
    record_form_owner(context, "recibo", {"form_config": RECEIPT})
    form = waiting_step_form(context, "recibo", receipt_task, {"status": "waiting"})
    assert form["description"] == "Pague $1,477.00 con la línea de captura"
    assert form["validation_errors"] == {"folio": "No existe"}
    assert form["fields"] == STATIC["fields"]


def test_previous_steps_form_is_not_shown_for_a_step_without_form():
    context = {"form_config": RECEIPT}
    record_form_owner(context, "recibo", {"form_config": RECEIPT})
    record_form_owner(context, "esperar_validacion", {"waiting_for": "child_workflow_completion"})
    assert context[FORM_OWNER_KEY] is None
    assert not context_form_belongs_to(context, "esperar_validacion")
    assert waiting_step_form(context, "esperar_validacion", SimpleNamespace(), {"status": "waiting"}) == {}


def test_in_memory_output_form_is_preferred():
    output = {"form_config": {"description": "De la ejecución"}}
    form = waiting_step_form({}, "recibo", receipt_task, {"output_data": output})
    assert form["description"] == "De la ejecución" and form["title"] == STATIC["title"]


def test_instances_paused_before_the_owner_was_recorded_keep_the_old_order():
    context = {"form_config": RECEIPT}
    form = waiting_step_form(context, "recibo", receipt_task, {"status": "waiting"})
    assert form["description"] == "Texto fijo"
    assert form["validation_errors"] == {"folio": "No existe"}
    assert context_form_belongs_to(context, "cualquier_paso")


def test_static_form_alone_when_there_is_no_dynamic_one():
    context = {}
    record_form_owner(context, "recibo", None)
    assert waiting_step_form(context, "recibo", receipt_task, None) == STATIC
    assert waiting_step_form(context, "recibo", None, None) == {}
