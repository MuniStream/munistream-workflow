from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query, Depends
from datetime import datetime

from ...schemas.workflow import (
    WorkflowCreate,
    WorkflowUpdate,
    WorkflowResponse,
    WorkflowListResponse,
    WorkflowDiagram,
    StepSchema,
    StepType,
    WorkflowStatus
)
from ...workflows.workflow import Workflow
from ...workflows.examples.citizen_registration import create_citizen_registration_workflow

router = APIRouter()

# In-memory storage for demo (will be replaced with MongoDB)
workflows_db = {}


def convert_workflow_to_response(workflow: Workflow) -> WorkflowResponse:
    """Convert internal Workflow object to API response"""
    steps = []
    for step_id, step in workflow.steps.items():
        step_type = StepType.ACTION
        if hasattr(step, 'conditions'):
            step_type = StepType.CONDITIONAL
        elif hasattr(step, 'approvers'):
            step_type = StepType.APPROVAL
        elif hasattr(step, 'service_name'):
            step_type = StepType.INTEGRATION
        elif hasattr(step, 'terminal_status'):
            step_type = StepType.TERMINAL
        
        steps.append(StepSchema(
            step_id=step.step_id,
            name=step.name,
            step_type=step_type,
            description=step.description,
            required_inputs=step.required_inputs,
            optional_inputs=step.optional_inputs,
            next_steps=[s.step_id for s in step.next_steps]
        ))
    
    return WorkflowResponse(
        workflow_id=workflow.workflow_id,
        name=workflow.name,
        description=workflow.description,
        version=workflow.version,
        status=workflow.status,
        steps=steps,
        start_step_id=workflow.start_step.step_id if workflow.start_step else None,
        metadata=workflow.metadata,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )


@router.post("/", response_model=WorkflowResponse)
async def create_workflow(workflow_data: WorkflowCreate):
    """Create a new workflow"""
    if workflow_data.workflow_id in workflows_db:
        raise HTTPException(status_code=400, detail="Workflow ID already exists")
    
    # Create basic workflow
    workflow = Workflow(
        workflow_id=workflow_data.workflow_id,
        name=workflow_data.name,
        description=workflow_data.description or ""
    )
    workflow.version = workflow_data.version
    workflow.metadata = workflow_data.metadata
    
    # Store workflow
    workflows_db[workflow.workflow_id] = workflow
    
    return convert_workflow_to_response(workflow)


@router.get("/", response_model=WorkflowListResponse)
async def list_workflows(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[WorkflowStatus] = None
):
    """List all workflows with pagination"""
    # Filter workflows
    filtered_workflows = []
    for workflow in workflows_db.values():
        if status is None or workflow.status == status:
            filtered_workflows.append(convert_workflow_to_response(workflow))
    
    # Pagination
    total = len(filtered_workflows)
    start = (page - 1) * page_size
    end = start + page_size
    
    return WorkflowListResponse(
        workflows=filtered_workflows[start:end],
        total=total,
        page=page,
        page_size=page_size
    )


@router.get("/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(workflow_id: str):
    """Get a specific workflow by ID"""
    if workflow_id not in workflows_db:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    return convert_workflow_to_response(workflows_db[workflow_id])


@router.put("/{workflow_id}", response_model=WorkflowResponse)
async def update_workflow(workflow_id: str, update_data: WorkflowUpdate):
    """Update workflow metadata"""
    if workflow_id not in workflows_db:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    workflow = workflows_db[workflow_id]
    
    if update_data.name is not None:
        workflow.name = update_data.name
    if update_data.description is not None:
        workflow.description = update_data.description
    if update_data.status is not None:
        workflow.status = update_data.status
    if update_data.metadata is not None:
        workflow.metadata.update(update_data.metadata)
    
    return convert_workflow_to_response(workflow)


@router.delete("/{workflow_id}")
async def delete_workflow(workflow_id: str):
    """Delete a workflow (archive it)"""
    if workflow_id not in workflows_db:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    workflow = workflows_db[workflow_id]
    workflow.status = WorkflowStatus.ARCHIVED
    
    return {"message": "Workflow archived successfully"}


@router.get("/{workflow_id}/diagram", response_model=WorkflowDiagram)
async def get_workflow_diagram(workflow_id: str, diagram_type: str = "mermaid"):
    """Get workflow diagram in specified format"""
    if workflow_id not in workflows_db:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    if diagram_type != "mermaid":
        raise HTTPException(status_code=400, detail="Only mermaid diagrams are supported")
    
    workflow = workflows_db[workflow_id]
    
    return WorkflowDiagram(
        workflow_id=workflow_id,
        diagram_type=diagram_type,
        content=workflow.to_mermaid()
    )


@router.post("/{workflow_id}/validate")
async def validate_workflow(workflow_id: str):
    """Validate workflow structure"""
    if workflow_id not in workflows_db:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    workflow = workflows_db[workflow_id]
    
    try:
        workflow.validate()
        return {"valid": True, "message": "Workflow is valid"}
    except ValueError as e:
        return {"valid": False, "message": str(e)}


@router.post("/examples/citizen-registration", response_model=WorkflowResponse)
async def create_example_citizen_registration():
    """Create the example citizen registration workflow"""
    workflow = create_citizen_registration_workflow()
    workflows_db[workflow.workflow_id] = workflow
    
    return convert_workflow_to_response(workflow)