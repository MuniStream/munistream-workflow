"""«Datos generales» del expediente muestra datos del trámite, no la maquinaria
con que corre (formularios en pantalla, esperas, asignaciones, referencias a las
validaciones hijas)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.instance_dossier import curate_context


def test_datos_generales_sin_maquinaria():
    padre = {
        "customer_email": "ana@ejemplo.mx", "customer_name": "Ana", "user_id": "u1", "_started_via": "portal",
        "form_config": {"title": "Revisión"}, "waiting_for": "child_workflow_completion", "message": "…",
        "required_fields": ["x"], "signable_data": {"a": 1}, "assigned_to": "TECNICA",
        "assignment_status": "pending_review", "elapsed_minutes": 1, "completed_at": "2026-11-17",
        "child_instance_id": "c1", "child_status": "completed", "parent_instance_id": "p0",
        "validation_decision": "approved", "firmas": [{"task_id": "firma"}],
    }
    general = curate_context({"parent_instance_id": "p1"}, padre)["origin"]["general"]
    assert set(general) == {"customer_email", "customer_name", "user_id", "validation_decision", "firmas"}
