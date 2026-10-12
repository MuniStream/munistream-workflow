"""El rechazo desde el explorador puede seguir el flujo (para emitir un oficio
de rechazo) en vez de matar la instancia como fallo técnico."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.workflows.operators.base import TaskStatus
from app.workflows.operators.context_explorer_validator import ContextExplorerValidator

RECHAZO = {"explorar_input": {"validation_decision": "rejected",
                              "validation_comments": "Falta el título de propiedad"},
           "user_id": "rev-1"}


def test_por_defecto_el_rechazo_cierra_como_rechazado():
    # Un rechazo del revisor no es un fallo técnico: el trámite termina
    # `rejected` con el motivo, y el padre lo hereda.
    op = ContextExplorerValidator(task_id="explorar")
    r = op._process_validation_decision(dict(RECHAZO))
    assert r.status == TaskStatus.TERMINATE
    assert r.data["terminal_status"] == "rejected"
    assert r.data["terminal_message"] == "Falta el título de propiedad"
    assert r.data["explorar_decision"] == "rejected"


def test_rechazo_sin_comentario_tiene_motivo():
    op = ContextExplorerValidator(task_id="explorar")
    r = op._process_validation_decision(
        {"explorar_input": {"validation_decision": "rejected"}})
    assert r.data["terminal_message"]


def test_on_reject_fail_conserva_el_fallo_con_causa():
    op = ContextExplorerValidator(task_id="explorar", on_reject="fail")
    r = op._process_validation_decision(dict(RECHAZO))
    assert r.status == TaskStatus.FAILED
    assert "Falta el título de propiedad" in (r.error or "")


def test_con_on_reject_continue_el_rechazo_sigue_el_flujo():
    op = ContextExplorerValidator(task_id="explorar", on_reject="continue")
    r = op._process_validation_decision(dict(RECHAZO))
    assert r.status == TaskStatus.CONTINUE
    assert r.data["explorar_decision"] == "rejected"
    assert r.data["explorar_comments"] == "Falta el título de propiedad"


def test_aprobar_publica_la_decision():
    op = ContextExplorerValidator(task_id="explorar", on_reject="continue")
    ctx = {"explorar_input": {"validation_decision": "approved"}}
    r = op._process_validation_decision(ctx)
    assert r.status == TaskStatus.CONTINUE
    assert r.data["explorar_decision"] == "approved"
