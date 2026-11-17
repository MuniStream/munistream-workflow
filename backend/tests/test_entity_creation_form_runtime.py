"""
EntityCreationOperator keeps the form machinery out of the entity.

A waiting step leaves `form_config`, `waiting_for`, `initial_values`… in the
context. Flattening them copied the form's `fields`, `title` and `description`
into the entity, and a child workflow's result carried its own `form_config`.
"""

import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.workflows.operators.entity_operators import EntityCreationOperator


def _entity_data(context):
    op = EntityCreationOperator(task_id="create", entity_type="document", name_source="static:Doc")
    op.execute(context)
    return op._entity_params["data"]


def test_form_runtime_keys_are_not_copied():
    data = _entity_data({
        "user_id": "u1",
        "form_config": {
            "title": "Documentos de Acreditación de Propiedad",
            "description": "Suba los documentos…",
            "fields": [{"name": "escritura_files", "type": "file"}],
        },
        "form_config_task_id": "captura_documentos",
        "waiting_for": "user_input",
        "validation_errors": ["Required field: calle"],
        "previous_input": {"calle": ""},
        "initial_values": {"titular": "X"},
        "required_fields": ["calle"],
        "validacion_result": {
            "validation_decision": "approved",
            "form_config": {"title": "Revisión de Información del Ciudadano"},
            "waiting_for": "user_input",
        },
        "captura_documentos_data": {"calle": "Av. Universidad", "numero_exterior": "251"},
    })
    for key in ("form_config", "form_config_task_id", "waiting_for", "validation_errors",
                "previous_input", "initial_values", "required_fields",
                "fields", "title", "description"):
        assert key not in data, key
    assert data["calle"] == "Av. Universidad"
    assert data["numero_exterior"] == "251"
    assert data["validation_decision"] == "approved"


def test_custom_blacklist_still_skips_form_runtime_keys():
    op = EntityCreationOperator(task_id="create", entity_type="document", name_source="static:Doc",
                                field_blacklist=["secret"])
    op.execute({"user_id": "u1", "secret": "x", "form_config": {"fields": []}, "nombre": "Ana"})
    data = op._entity_params["data"]
    assert "secret" not in data and "fields" not in data and "form_config" not in data
    assert data["nombre"] == "Ana"
