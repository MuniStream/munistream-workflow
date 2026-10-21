"""
WorkflowService.describe_operator: lo que el diagrama del trámite necesita de
cada operador —sub-trámite que lanza, entidad que emite, desenlace— sale de la
instancia del operador, y viaja como JSON simple.
"""
import json
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.models.workflow import WorkflowType
from app.services.workflow_service import WorkflowService
from app.workflows.operators.entity_operators import EntityCreationOperator, EntityUpdateOperator
from app.workflows.operators.python import PythonOperator
from app.workflows.operators.terminate_if import TerminateIfOperator
from app.workflows.operators.workflow_start_operator import WorkflowStartOperator

describe = WorkflowService.describe_operator


def test_workflow_start_describe_hijo_y_espera():
    op = WorkflowStartOperator(
        task_id="validacion",
        workflow_id="admin_validacion",
        workflow_type=WorkflowType.ADMIN,
        wait_for_completion=True,
        timeout_minutes=7200,
        on_child_failure="goto_task",
        retry_target_task="captura",
    )
    d = describe(op)
    assert d == {
        "notify": True,
        "child_workflow_id": "admin_validacion",
        "child_workflow_type": "admin",
        "wait_for_completion": True,
        "required_status": "completed",
        "propagate_terminal": ["rejected"],
        "on_child_failure": "goto_task",
        "retry_target_task": "captura",
        "timeout_minutes": 7200,
    }
    json.dumps(d)


def test_entity_creation_describe_la_entidad_emitida():
    op = EntityCreationOperator(
        task_id="emitir",
        entity_type="permiso_pesca",
        entity_subtype="comercial",
        visualizer="documento_oficial",
        folio_config={"field": "folio", "prefix": "PP"},
    )
    assert describe(op) == {
        "notify": True,
        "emits_entity": {
            "entity_type": "permiso_pesca",
            "entity_subtype": "comercial",
            "visualizer": "documento_oficial",
            "folio_prefix": "PP",
        }
    }


def test_entity_update_y_terminate_if():
    assert describe(EntityUpdateOperator(task_id="act", entity_id_source="entidad_id")) == {
        "notify": True,
        "updates_entity": {"entity_id_source": "entidad_id"}
    }
    term = TerminateIfOperator(task_id="desenlace", conditions=[], terminal_status="rejected")
    assert describe(term) == {"notify": True, "terminal_status": "rejected"}


def test_otros_operadores_no_describen_nada():
    assert describe(PythonOperator(task_id="p", python_callable=lambda **c: {})) == {"notify": True}


def test_notify_por_defecto_y_sobreescrito():
    from app.workflows.operators.user_input import UserInputOperator
    from app.workflows.operators.s3_upload import S3UploadOperator
    from app.workflows.operators.confirmation_operator import ConfirmationOperator

    assert describe(UserInputOperator(task_id="f", form_config={}))["notify"] is False
    assert describe(UserInputOperator(task_id="g", form_config={}, notify=True))["notify"] is True
    assert S3UploadOperator(task_id="s").notify is False
    assert ConfirmationOperator(task_id="c", summary_sections=[], declarations=[]).notify is True
    assert PythonOperator(task_id="p2", python_callable=lambda **c: {}, notify=False).notify is False
