"""TerminateIfOperator: cierra el trámite con un desenlace cuando se cumple una condición."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.workflows.operators.base import TaskStatus
from app.workflows.operators.terminate_if import TerminateIfOperator


def _op():
    return TerminateIfOperator(
        task_id="desenlace",
        conditions=[
            {"path": "resolucion_sentido", "in": ["rejected"]},
            {"path": "_parent_context.dictamen_tecnico_data.sentido|_parent_context.sentido",
             "in": ["no_favorable"]},
        ],
        terminal_status="rejected",
        message_paths=["resolucion_fundamento",
                       "_parent_context.dictamen_tecnico_data.fundamento_tecnico"],
        message_default="La solicitud fue rechazada.",
    )


def test_otorga_sin_imipas_continua():
    r = _op().execute({"resolucion_sentido": "approved"})
    assert r.status == TaskStatus.CONTINUE
    assert r.data["desenlace_terminated"] is False


def test_rechaza_termina_con_motivo():
    r = _op().execute({"resolucion_sentido": "rejected",
                       "resolucion_fundamento": "Documentación insuficiente"})
    assert r.status == TaskStatus.TERMINATE
    assert r.data["terminal_status"] == "rejected"
    assert r.data["terminal_message"] == "Documentación insuficiente"
    assert r.data["desenlace_condicion"] == "resolucion_sentido"


def test_imipas_no_favorable_manda_aunque_otorgue():
    ctx = {"resolucion_sentido": "approved",
           "_parent_context": {"dictamen_tecnico_data": {
               "sentido": "no_favorable", "fundamento_tecnico": "Recurso sobreexplotado"}}}
    r = _op().execute(ctx)
    assert r.status == TaskStatus.TERMINATE
    assert r.data["terminal_message"] == "Recurso sobreexplotado"


def test_imipas_por_ruta_alternativa():
    r = _op().execute({"_parent_context": {"sentido": "no_favorable"}})
    assert r.status == TaskStatus.TERMINATE


def test_sin_motivo_usa_default():
    r = _op().execute({"resolucion_sentido": "rejected"})
    assert r.data["terminal_message"] == "La solicitud fue rechazada."


def test_se_exporta_desde_operators():
    from app.workflows.operators import TerminateIfOperator as Exportado
    assert Exportado is TerminateIfOperator
