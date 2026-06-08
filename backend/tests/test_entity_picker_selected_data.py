"""
Unit tests for EntityPickerOperator persisting selected entities' DATA
(not just their IDs) into the workflow context.

These tests exercise the pure mapping/hydration logic without Mongo:
- _build_selected_entities_data: IDs -> data snapshots keyed by store_as
- _auto_select_entities: CONTINUE result carries selected_entities_data
- _hydrate_selected_entities_data / _attach_selected_entities_data: user
  selection path fetches data by ID (EntityService.get_entity stubbed)

The goal of the feature: downstream steps can resolve requirement-entity
fields via dot-paths like ``selected_entities_data.<store_as>.0.<field>``
instead of re-asking the citizen for data already in a selected entity.
"""

import os
import sys
from types import SimpleNamespace

import pytest

# Ensure the backend `app` package is importable when running pytest from
# the repo root without installing the package.
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.workflows.operators import entity_picker_operator as epo_module
from app.workflows.operators.entity_picker_operator import EntityPickerOperator
from app.workflows.operators.base import TaskResult, TaskStatus


def _entity(entity_id, entity_type, name, data):
    return SimpleNamespace(
        entity_id=entity_id,
        entity_type=entity_type,
        name=name,
        data=data,
    )


def _make_picker():
    return EntityPickerOperator(
        task_id="pick_required_documents",
        requirements=[
            {
                "entity_type": "embarcacion_registrada",
                "min_count": 1,
                "max_count": 1,
                "store_as": "embarcacion_ids",
            }
        ],
    )


def test_build_selected_entities_data_maps_ids_to_data():
    picker = _make_picker()
    entity = _entity(
        "embarcacion_registrada_1",
        "embarcacion_registrada",
        "Mi Embarcación",
        {"nombre": "Mi Embarcación", "matricula": "ABC-123", "eslora_metros": 25.5},
    )
    selected_group = {"embarcacion_ids": ["embarcacion_registrada_1"]}

    result = picker._build_selected_entities_data(
        selected_group, {"embarcacion_registrada_1": entity}
    )

    assert list(result.keys()) == ["embarcacion_ids"]
    snapshot = result["embarcacion_ids"][0]
    # Data fields are present and resolvable via store_as.0.<field>
    assert snapshot["nombre"] == "Mi Embarcación"
    assert snapshot["matricula"] == "ABC-123"
    assert snapshot["eslora_metros"] == 25.5
    # Metadata is underscore-prefixed (auto-skipped by EntityCreationOperator)
    assert snapshot["_entity_id"] == "embarcacion_registrada_1"
    assert snapshot["_entity_type"] == "embarcacion_registrada"
    assert snapshot["_entity_name"] == "Mi Embarcación"


def test_build_selected_entities_data_skips_unknown_ids():
    picker = _make_picker()
    selected_group = {"embarcacion_ids": ["missing_id"]}
    result = picker._build_selected_entities_data(selected_group, {})
    assert result == {"embarcacion_ids": []}


def test_auto_select_includes_entity_data():
    picker = _make_picker()
    entity = _entity(
        "embarcacion_registrada_1",
        "embarcacion_registrada",
        "Mi Embarcación",
        {"nombre": "Mi Embarcación", "matricula": "ABC-123"},
    )
    requirement_entities = {"embarcacion_ids": [entity]}

    result = picker._auto_select_entities(requirement_entities, picker.requirements)

    assert result.status == TaskStatus.CONTINUE
    # IDs preserved (backward compatible)
    assert result.data["selected_entities"] == {
        "embarcacion_ids": ["embarcacion_registrada_1"]
    }
    # New: data is attached too
    snapshot = result.data["_selected_entities_data"]["embarcacion_ids"][0]
    assert snapshot["nombre"] == "Mi Embarcación"
    assert snapshot["_entity_id"] == "embarcacion_registrada_1"


async def test_hydrate_selected_entities_data(monkeypatch):
    picker = _make_picker()
    entity = _entity(
        "embarcacion_registrada_1",
        "embarcacion_registrada",
        "Mi Embarcación",
        {"nombre": "Mi Embarcación", "puerto_base": "Ensenada"},
    )

    async def fake_get_entity(entity_id, owner_user_id=None):
        assert entity_id == "embarcacion_registrada_1"
        return entity

    monkeypatch.setattr(epo_module.EntityService, "get_entity", fake_get_entity)

    selected_group = {"embarcacion_ids": ["embarcacion_registrada_1"]}
    result = await picker._hydrate_selected_entities_data(
        selected_group, {"user_id": "user-1"}
    )

    snapshot = result["embarcacion_ids"][0]
    assert snapshot["puerto_base"] == "Ensenada"
    assert snapshot["_entity_id"] == "embarcacion_registrada_1"


async def test_attach_selected_entities_data_populates_continue_result(monkeypatch):
    picker = _make_picker()
    entity = _entity(
        "embarcacion_registrada_1",
        "embarcacion_registrada",
        "Mi Embarcación",
        {"nombre": "Mi Embarcación"},
    )

    async def fake_get_entity(entity_id, owner_user_id=None):
        return entity

    monkeypatch.setattr(epo_module.EntityService, "get_entity", fake_get_entity)

    result = TaskResult(
        status=TaskStatus.CONTINUE,
        data={"selected_entities": {"embarcacion_ids": ["embarcacion_registrada_1"]}},
    )

    await picker._attach_selected_entities_data(result, {"user_id": "user-1"})

    snapshot = result.data["_selected_entities_data"]["embarcacion_ids"][0]
    assert snapshot["nombre"] == "Mi Embarcación"


async def test_attach_skips_when_already_present(monkeypatch):
    picker = _make_picker()

    async def boom(entity_id, owner_user_id=None):  # pragma: no cover - must not run
        raise AssertionError("get_entity should not be called when data present")

    monkeypatch.setattr(epo_module.EntityService, "get_entity", boom)

    preexisting = {"embarcacion_ids": [{"nombre": "Existente"}]}
    result = TaskResult(
        status=TaskStatus.CONTINUE,
        data={
            "selected_entities": {"embarcacion_ids": ["embarcacion_registrada_1"]},
            "_selected_entities_data": preexisting,
        },
    )

    await picker._attach_selected_entities_data(result, {"user_id": "user-1"})

    assert result.data["_selected_entities_data"] is preexisting
