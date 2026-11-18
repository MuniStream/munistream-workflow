"""Un trámite no puede morir JUSTO DESPUÉS de que el revisor lo aprueba.

`WorkflowStartOperator.required_status` valía "approved" por defecto, pero NADA
en la plataforma escribe `terminal_status`: las 81 validaciones completadas en
dev lo tienen en `None`, y el operador lo lee como "completed". El default no se
cumplía nunca.

De los 26 trámites de conapesca, 23 declaran `required_status` explícito y por
eso funcionaban; los tres que confiaban en el default —aviso de siembra, arribo
de menores y pesca deportiva— fallaban en `administrative_validation` después de
que el revisor los aprobaba, y el ciudadano veía "Error" sin más: la causa
viajaba en `data["error"]`, que el ejecutor no lee.

Pruebas puras: no tocan Mongo ni Keycloak.
"""

import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.models.workflow import WorkflowType  # noqa: E402
from app.workflows.operators.workflow_start_operator import WorkflowStartOperator  # noqa: E402


def _operador(**kwargs):
    kwargs.setdefault("task_id", "administrative_validation")
    kwargs.setdefault("workflow_id", "admin_validacion_conapesca")
    kwargs.setdefault("workflow_type", WorkflowType.ADMIN)
    return WorkflowStartOperator(**kwargs)


def test_el_default_es_satisfacible():
    """El hijo siempre termina como "completed"; exigir otra cosa lo mata."""
    assert _operador().required_status == "completed"


def test_lo_declarado_manda_sobre_el_default():
    assert _operador(required_status="any").required_status == "any"


def test_todo_fallo_declara_su_causa():
    """`data["error"]` no lo lee el ejecutor: la causa va en `TaskResult.error`.

    Sin esto el paso quedaba como "fallido sin causa declarada" y el ciudadano
    no podía saber si le tocaba corregir algo.
    """
    import inspect

    fuente = inspect.getsource(WorkflowStartOperator)
    fallos = fuente.count("status=TaskStatus.FAILED")
    con_causa = fuente.count("error=")
    assert fallos > 0
    assert con_causa >= fallos, (
        f"{fallos} retornos FAILED pero solo {con_causa} declaran `error=`"
    )


def test_el_vencimiento_se_explica_al_ciudadano():
    from app.workflows.operators.workflow_start_operator import mensaje_de_vencimiento

    assert mensaje_de_vencimiento("Validación Administrativa", 1440) == (
        "«Validación Administrativa» no se completó en el plazo de 24 horas. El trámite se cerró sin "
        "resolverse; puedes iniciarlo de nuevo.")
    assert "2 días" in mensaje_de_vencimiento("Validación", 2880)
    assert "4 días" in mensaje_de_vencimiento("Validación", 5760)
    assert "1.5 horas" in mensaje_de_vencimiento("Validación", 90)


def test_el_vencimiento_cancela_al_hijo(monkeypatch):
    from app.services import workflow_service as servicio

    cancelados = []

    class Ejecutor:
        def cancel_instance(self, instance_id):
            cancelados.append(instance_id)

    monkeypatch.setattr(servicio.workflow_service, "executor", Ejecutor(), raising=False)
    op = _operador()
    op.child_instance_id = "hijo-1"
    op._cancelar_hijo_vencido()
    assert cancelados == ["hijo-1"]
