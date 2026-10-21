"""
A citizen's request is a tree of instances: the root the citizen started and
the sub-workflows it launches (admin validation, technical opinion...), which
inherit the citizen's user_id. Documents can be issued at any node, and
notifications to the citizen always refer to the root.
"""
from typing import List, Optional

from ..models.legal_entity import LegalEntity
from ..models.workflow import WorkflowInstance

# Guards against cycles caused by corrupt parent links.
MAX_DEPTH = 10


async def root_instance(instance_id: str) -> Optional[WorkflowInstance]:
    """The instance the citizen started, walking up parent_instance_id."""
    instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
    seen = set()
    while instance and instance.parent_instance_id and len(seen) < MAX_DEPTH:
        seen.add(instance.instance_id)
        parent = await WorkflowInstance.find_one(
            WorkflowInstance.instance_id == instance.parent_instance_id
        )
        if not parent or parent.instance_id in seen:
            break
        instance = parent
    return instance


async def tree_instance_ids(instance_id: str) -> List[str]:
    """The instance and all of its descendants."""
    ids = [instance_id]
    frontier = [instance_id]
    for _ in range(MAX_DEPTH):
        children = await WorkflowInstance.find(
            {"parent_instance_id": {"$in": frontier}}
        ).to_list()
        frontier = [c.instance_id for c in children if c.instance_id not in ids]
        if not frontier:
            break
        ids.extend(frontier)
    return ids


async def issued_entity_ids(instance_id: str) -> List[str]:
    """IDs of the entities issued by the instance or its sub-workflows, in issue order."""
    ids = await tree_instance_ids(instance_id)
    entities = await LegalEntity.find(
        {"created_by_workflow": {"$in": ids}}
    ).sort("created_at").to_list()
    return [e.entity_id for e in entities]
