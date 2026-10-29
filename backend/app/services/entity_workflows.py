"""
Which trámites accept each entity type.

A trámite declares the entity types it works with in the requirements of its
``EntityPicker`` steps (``entity_type`` / ``entity_types``), which is what the
citizen portal needs in reverse: given one of their entities, what can they do
with it. So the map is derived from the registered DAGs instead of asking every
trámite to repeat the list in its metadata — a second place to declare it is a
second place to forget.
"""
from typing import Any, Dict, List, Optional, Set

from ..models.workflow import WorkflowType

# Workflow types a citizen can start. The rest (ADMIN, INTEGRATION, MONITORING,
# VALIDATION) are internal subflows, registered as definitions just like the
# trámites are, and they must never be offered as something to start.
CITIZEN_WORKFLOW_TYPES = {WorkflowType.PROCESS, WorkflowType.DOCUMENT_PROCESSING}


def _requirement_entity_types(requirement: Dict[str, Any]) -> List[str]:
    """The entity types one requirement accepts.

    ``entity_types`` is the list of accepted types; ``entity_type`` is the
    single-type form (and the default the picker shows when both are present).
    """
    types = requirement.get("entity_types") or []
    if not isinstance(types, list):
        types = [types]
    if not types and requirement.get("entity_type"):
        types = [requirement["entity_type"]]
    return [t for t in types if isinstance(t, str) and t]


def entity_types_of_dag(dag: Any) -> Set[str]:
    """Every entity type the steps of a DAG ask the citizen for."""
    types: Set[str] = set()
    for task in (getattr(dag, "tasks", None) or {}).values():
        for requirement in getattr(task, "requirements", None) or []:
            if isinstance(requirement, dict):
                types.update(_requirement_entity_types(requirement))
    return types


async def workflows_for_entity_types(service: Optional[Any] = None) -> Dict[str, List[Dict[str, Any]]]:
    """Map of entity type -> trámites that accept it, each sorted by name.

    ``service`` is the workflow service; it is a parameter so this can be
    tested without a database. ``limit=0`` is not optional: the default page of
    ``list_workflow_definitions`` is 20 and a tenant has far more than that.
    """
    if service is None:
        from .workflow_service import workflow_service
        service = workflow_service

    by_type: Dict[str, List[Dict[str, Any]]] = {}
    definitions = await service.list_workflow_definitions(status="active", limit=0)

    for definition in definitions:
        if definition.workflow_type not in CITIZEN_WORKFLOW_TYPES:
            continue
        dag = service.dag_bag.get_dag(definition.workflow_id)
        if dag is None:
            continue
        types = entity_types_of_dag(dag)
        if not types:
            continue

        # Same precedence as the trámite's own page: what the DAG declares,
        # overridden by what an admin stored in the database.
        metadata = {**(getattr(dag, "metadata", None) or {}), **(definition.metadata or {})}
        tramite = {
            "workflow_id": definition.workflow_id,
            "name": definition.name,
            "description": definition.description,
            "category": definition.category,
        }
        duration = metadata.get("estimatedTime") or metadata.get("estimated_duration")
        if duration:
            tramite["estimatedDuration"] = duration
        if metadata.get("cost"):
            tramite["cost"] = metadata["cost"]

        for entity_type in types:
            by_type.setdefault(entity_type, []).append(tramite)

    for tramites in by_type.values():
        tramites.sort(key=lambda t: (t["name"] or "").lower())
    return by_type
