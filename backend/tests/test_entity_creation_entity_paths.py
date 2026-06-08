"""
Unit tests for EntityCreationOperator resolving selected-entity data paths.

After the EntityPickerOperator persists `_selected_entities_data` into context,
a trámite can map an OUTPUT entity field from a selected requirement entity using
a dot-path with a numeric list index, e.g.
    "_selected_entities_data.embarcacion_ids.0.nombre": "embarcacion"

These tests verify EntityCreationOperator.execute() resolves such paths for both
data_mapping and name_source, without touching Mongo (execute() only builds the
pending params and returns PENDING_ASYNC).
"""

import os
import sys

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.workflows.operators.entity_operators import (
    EntityCreationOperator,
    _resolve_context_path,
)


def test_resolve_context_path_supports_list_indices():
    context = {
        "_selected_entities_data": {
            "embarcacion_ids": [
                {"nombre": "Mi Embarcación", "eslora_metros": 25.5},
            ]
        }
    }
    assert (
        _resolve_context_path(context, "_selected_entities_data.embarcacion_ids.0.nombre")
        == "Mi Embarcación"
    )
    assert (
        _resolve_context_path(
            context, "_selected_entities_data.embarcacion_ids.0.eslora_metros"
        )
        == 25.5
    )
    # Out-of-range index and missing key resolve to None (not an error)
    assert (
        _resolve_context_path(context, "_selected_entities_data.embarcacion_ids.1.nombre")
        is None
    )
    assert (
        _resolve_context_path(context, "_selected_entities_data.embarcacion_ids.0.missing")
        is None
    )


def test_resolve_context_path_backward_compatible_dict_paths():
    context = {"collect_data": {"nombre_completo": "Juan Pérez"}}
    assert _resolve_context_path(context, "collect_data.nombre_completo") == "Juan Pérez"
    assert _resolve_context_path(context, "collect_data.missing") is None
    assert _resolve_context_path(context, "missing.x") is None


def test_entity_creation_maps_fields_from_selected_entity():
    op = EntityCreationOperator(
        task_id="generate_fishing_permit",
        entity_type="permiso_pesca_comercial",
        name_source="_selected_entities_data.embarcacion_ids.0.nombre",
        data_mapping={
            "tipo_pesqueria": "pesqueria",
            "_selected_entities_data.embarcacion_ids.0.nombre": "embarcacion",
            "_selected_entities_data.embarcacion_ids.0.eslora_metros": "eslora_metros",
            "_selected_entities_data.embarcacion_ids.0.tonelaje_trb": "arqueo_toneladas",
            "_selected_entities_data.embarcacion_ids.0.puerto_base": "puerto_registro",
        },
    )

    context = {
        "user_id": "user-1",
        "tipo_pesqueria": "camaron",
        "selected_entities": {"embarcacion_ids": ["embarcacion_registrada_1"]},
        "_selected_entities_data": {
            "embarcacion_ids": [
                {
                    "nombre": "Mi Embarcación",
                    "eslora_metros": 25.5,
                    "tonelaje_trb": 40.0,
                    "puerto_base": "Ensenada",
                    "_entity_id": "embarcacion_registrada_1",
                    "_entity_type": "embarcacion_registrada",
                }
            ]
        },
    }

    op.execute(context)
    params = op._entity_params
    data = params["data"]

    # Fields sourced from the selected entity
    assert data["embarcacion"] == "Mi Embarcación"
    assert data["eslora_metros"] == 25.5
    assert data["arqueo_toneladas"] == 40.0
    assert data["puerto_registro"] == "Ensenada"
    # Regular form field still mapped
    assert data["pesqueria"] == "camaron"
    # name_source resolved from the selected entity
    assert params["name"] == "Mi Embarcación"


def test_entity_creation_does_not_absorb_selected_entities_data_blob():
    """The full _selected_entities_data blob must NOT leak into output entity data."""
    op = EntityCreationOperator(
        task_id="generate_fishing_permit",
        entity_type="permiso_pesca_comercial",
        name_source="_selected_entities_data.embarcacion_ids.0.nombre",
        data_mapping={},
    )

    context = {
        "user_id": "user-1",
        "selected_entities": {"embarcacion_ids": ["embarcacion_registrada_1"]},
        "_selected_entities_data": {
            "embarcacion_ids": [{"nombre": "Mi Embarcación", "secreto": "x"}]
        },
    }

    op.execute(context)
    data = op._entity_params["data"]

    # Underscore-prefixed top-level key is skipped by auto-collect, so the full
    # entity payload (and its fields) never leak into the output entity.
    assert "_selected_entities_data" not in data
    assert "secreto" not in data
    # The IDs from `selected_entities` may still be flattened (pre-existing
    # behavior) — but as IDs, never as the data snapshots.
    assert data.get("embarcacion_ids") == ["embarcacion_registrada_1"]
