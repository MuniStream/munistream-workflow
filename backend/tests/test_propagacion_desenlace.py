"""Un hijo que termina RECHAZADO cierra también al padre."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.workflows.operators.workflow_start_operator import (
    WorkflowStartOperator,
    desenlace_a_propagar,
)


def test_rechazado_se_propaga():
    assert desenlace_a_propagar("rejected", "Motivo", ("rejected",)) == {
        "terminal_status": "rejected", "terminal_message": "Motivo"}


def test_completado_normal_no_propaga():
    assert desenlace_a_propagar(None, None, ("rejected",)) is None


def test_desenlace_no_listado_no_propaga():
    assert desenlace_a_propagar("rejected", "x", ()) is None


def test_por_defecto_propaga_rechazado():
    op = WorkflowStartOperator(task_id="v", workflow_id="w", required_status="any")
    assert op.propagate_terminal == ("rejected",)


def test_el_padre_recibe_el_oficio_que_emitio_el_hijo():
    # Al rechazar, la validación emite el Oficio de Rechazo; el padre termina
    # sin recorrer sus pasos, así que el oficio solo aparece en el trámite del
    # ciudadano si se hereda del hijo.
    from app.workflows.operators.workflow_start_operator import entidades_del_hijo
    ctx = {
        "created_entity_oficio_rechazo": "ent-1",
        "emitir_oficio_rechazo_entity_id": "ent-1",
        "emitir_oficio_rechazo_entity_type": "oficio_rechazo",
        "resolucion_fundamento": "no viaja",
        "_parent_context": {"created_entity_x": "no viaja"},
    }
    assert entidades_del_hijo(ctx) == {
        "created_entity_oficio_rechazo": "ent-1",
        "emitir_oficio_rechazo_entity_id": "ent-1",
        "emitir_oficio_rechazo_entity_type": "oficio_rechazo",
    }


def test_hijo_sin_contexto():
    from app.workflows.operators.workflow_start_operator import entidades_del_hijo
    assert entidades_del_hijo(None) == {}


def test_constantes_para_el_hijo():
    # `context_mapping` solo copia claves que existen en el padre; un valor fijo
    # —p. ej. el modo en que debe correr la validación— necesita su propio canal.
    op = WorkflowStartOperator(task_id="v", workflow_id="w",
                               child_static={"modo_validacion": "admision"})
    hijo = op._prepare_child_context({"instance_id": "p-1", "user_id": "u-1",
                                      "modo_validacion": "otro"})
    assert hijo["modo_validacion"] == "admision"
    assert hijo["parent_user_id"] == "u-1"


def test_sin_constantes_no_agrega_nada():
    op = WorkflowStartOperator(task_id="v", workflow_id="w")
    assert "modo_validacion" not in op._prepare_child_context({"instance_id": "p-1"})
