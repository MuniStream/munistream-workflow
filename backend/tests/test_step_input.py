"""
`submit-data` stores the body under `context["<task>_input"]`; a body wrapped
in that same key (as the portals send assertion reviews and catalog
selections) must reach the operator unwrapped. No database is needed.
"""
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.services.step_input import normalize_step_input  # noqa: E402

DECISIONS = {"decisions": {"curp_match": {"decision": "override", "comment": "CURP corregida en ventanilla"}}}


def test_body_wrapped_in_the_task_key_is_unwrapped():
    assert normalize_step_input("revision", {"revision_input": DECISIONS}) == DECISIONS


def test_plain_bodies_are_unchanged():
    assert normalize_step_input("revision", DECISIONS) == DECISIONS
    selections = {"revision_selections": {"embarcacion": ["e1"]}}
    assert normalize_step_input("revision", selections) == selections
    mixed = {"revision_input": DECISIONS, "otro": 1}
    assert normalize_step_input("revision", mixed) == mixed
    assert normalize_step_input("revision", "texto") == "texto"


def test_another_steps_key_is_not_unwrapped():
    wrapped = {"otra_tarea_input": DECISIONS}
    assert normalize_step_input("revision", wrapped) == wrapped


def test_assertion_override_survives_the_admin_portal_envelope():
    from app.workflows.operators.assertion_operator import AssertionOperator

    op = AssertionOperator(
        task_id="revision",
        assertions=[{"id": "curp_match", "label": "CURP", "left_path": "a", "right_path": "b", "operator": "=="}],
    )
    sent_by_portal = {"revision_input": DECISIONS}

    # Stored as sent, the override and its comment were silently dropped.
    raw = op._process_user_decisions({"a": "X", "b": "Y", "revision_input": sent_by_portal})
    assert raw.data["revision_assertions_result"]["assertions"][0]["user_decision"] == "confirm"

    context = {"a": "X", "b": "Y", "revision_input": normalize_step_input("revision", sent_by_portal)}
    result = op._process_user_decisions(context).data["revision_assertions_result"]
    assertion = result["assertions"][0]
    assert assertion["user_decision"] == "override"
    assert assertion["user_comment"] == "CURP corregida en ventanilla"
    assert result["all_passed"] is True


def test_catalog_selection_survives_the_admin_portal_envelope():
    rows = [{"clave": "01", "nombre": "Sinaloa"}]
    stored = normalize_step_input("estado", {"estado_input": {"selected_items": rows}})
    assert stored == {"selected_items": rows}
