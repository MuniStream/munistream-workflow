"""
Trámites que aceptan cada tipo de entidad.

El vínculo lo declara cada trámite en los requisitos de su `EntityPicker`
(`entity_type` / `entity_types`), así que se deriva de los DAG registrados en
vez de pedir una lista aparte.

Pruebas puras (sin Mongo, sin HTTP):

    docker exec backend-conapesca python -m pytest tests/test_entity_workflows.py -q
"""
import asyncio
from types import SimpleNamespace

from app.models.workflow import WorkflowType
from app.services.entity_workflows import workflows_for_entity_types


def _task(*requirements):
    return SimpleNamespace(requirements=list(requirements))


def _dag(dag_id, *tasks, metadata=None):
    return SimpleNamespace(
        dag_id=dag_id,
        tasks={f"t{i}": task for i, task in enumerate(tasks)},
        metadata=metadata or {},
    )


def _definition(workflow_id, name, workflow_type=WorkflowType.PROCESS, metadata=None):
    return SimpleNamespace(
        workflow_id=workflow_id,
        name=name,
        description=f"Descripción de {name}",
        category="pesca",
        workflow_type=workflow_type,
        metadata=metadata or {},
    )


class FakeService:
    """El servicio de workflows: definiciones en la base, DAG en memoria."""

    def __init__(self, pairs):
        self._definitions = [definition for definition, _ in pairs]
        self.dag_bag = SimpleNamespace(
            dags={dag.dag_id: dag for _, dag in pairs},
            get_dag=lambda dag_id: self.dag_bag.dags.get(dag_id),
        )
        self.calls = []

    async def list_workflow_definitions(self, status=None, limit=20, **kwargs):
        self.calls.append({"status": status, "limit": limit})
        definitions = [d for d in self._definitions if status is None or status == "active"]
        return definitions[:limit] if limit else definitions


def test_derives_the_types_of_an_entity_picker_requirement():
    service = FakeService([
        (
            _definition("guia_pesca", "Guía de pesca"),
            _dag("guia_pesca", _task({"entity_type": "permiso_pesca_comercial"},
                                     {"entity_types": ["aviso_arribo", "aviso_cosecha"]})),
        ),
    ])

    por_tipo = asyncio.run(workflows_for_entity_types(service))

    assert sorted(por_tipo) == ["aviso_arribo", "aviso_cosecha", "permiso_pesca_comercial"]
    assert por_tipo["aviso_arribo"] == [{
        "workflow_id": "guia_pesca",
        "name": "Guía de pesca",
        "description": "Descripción de Guía de pesca",
        "category": "pesca",
    }]


def test_carries_the_cost_and_the_time_declared_for_the_tramite():
    """El costo y el plazo salen del mismo lugar que en la ficha del trámite:
    el metadata del DAG, y lo que diga la base manda sobre él."""
    service = FakeService([
        (
            _definition("guia_pesca", "Guía de pesca", metadata={"estimatedTime": "20-30 min"}),
            _dag("guia_pesca", _task({"entity_type": "permiso"}),
                 metadata={"cost": "$1,234", "estimatedTime": "10-20 min"}),
        ),
        (
            _definition("aviso", "Aviso de arribo"),
            _dag("aviso", _task({"entity_type": "permiso"})),
        ),
    ])

    tramites = (asyncio.run(workflows_for_entity_types(service)))["permiso"]

    assert [t["workflow_id"] for t in tramites] == ["aviso", "guia_pesca"]
    assert tramites[1]["cost"] == "$1,234"
    assert tramites[1]["estimatedDuration"] == "20-30 min"
    # Sin dato declarado no se inventa una duración.
    assert "estimatedDuration" not in tramites[0] and "cost" not in tramites[0]


def test_leaves_out_the_internal_subflows():
    service = FakeService([
        (
            _definition("validacion", "Validación administrativa", WorkflowType.ADMIN),
            _dag("validacion", _task({"entity_type": "permiso"})),
        ),
        (
            _definition("registro", "Registro RNPA", WorkflowType.DOCUMENT_PROCESSING),
            _dag("registro", _task({"entity_type": "permiso"})),
        ),
    ])

    por_tipo = asyncio.run(workflows_for_entity_types(service))

    assert [t["workflow_id"] for t in por_tipo["permiso"]] == ["registro"]


def test_asks_for_every_definition_not_just_the_first_page():
    pairs = [
        (_definition(f"w{i:02d}", f"Trámite {i:02d}"), _dag(f"w{i:02d}", _task({"entity_type": "permiso"})))
        for i in range(25)
    ]
    service = FakeService(pairs)

    por_tipo = asyncio.run(workflows_for_entity_types(service))

    assert len(por_tipo["permiso"]) == 25
    assert service.calls == [{"status": "active", "limit": 0}]


def test_a_tramite_appears_once_per_type_even_if_two_steps_ask_for_it():
    service = FakeService([
        (
            _definition("guia_pesca", "Guía de pesca"),
            _dag("guia_pesca",
                 _task({"entity_type": "permiso"}),
                 _task({"entity_types": ["permiso"]})),
        ),
    ])

    assert len((asyncio.run(workflows_for_entity_types(service)))["permiso"]) == 1


def test_ignores_steps_without_entity_requirements():
    service = FakeService([
        (
            _definition("sin_entidad", "Trámite sin entidad"),
            _dag("sin_entidad", _task({"min_count": 1}), SimpleNamespace()),
        ),
    ])

    assert asyncio.run(workflows_for_entity_types(service)) == {}


def test_a_definition_without_a_registered_dag_is_skipped():
    service = FakeService([])
    service._definitions = [_definition("fantasma", "Trámite fantasma")]

    assert asyncio.run(workflows_for_entity_types(service)) == {}
