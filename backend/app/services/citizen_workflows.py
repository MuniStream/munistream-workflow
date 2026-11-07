"""
Which workflows a citizen may see and start through `/public/...`.

Administrative workflows (`workflow_type=admin`, e.g. a technical opinion a
team issues inside another procedure) are started by the platform for staff,
never by a citizen. The public listing already hid them, but search, the
detail, the pre-check and the start endpoints served them to anyone who knew
or searched the id.
"""
from fastapi import HTTPException

from ..models.workflow import WorkflowDefinition, WorkflowType

CITIZEN_WORKFLOW_TYPES = {WorkflowType.PROCESS.value, WorkflowType.DOCUMENT_PROCESSING.value}


def is_citizen_workflow(definition: WorkflowDefinition) -> bool:
    workflow_type = getattr(definition.workflow_type, "value", definition.workflow_type) or WorkflowType.PROCESS.value
    return workflow_type in CITIZEN_WORKFLOW_TYPES


async def citizen_workflow_or_404(workflow_id: str) -> WorkflowDefinition:
    """The definition when a citizen may use it; 404 otherwise (the same answer as a workflow that does not exist)."""
    from .workflow_service import workflow_service

    definition = await workflow_service.get_workflow_definition(workflow_id)
    if not definition or not is_citizen_workflow(definition):
        raise HTTPException(status_code=404, detail="Workflow not found")
    return definition
