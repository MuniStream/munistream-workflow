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
    WorkflowStatus,
    StepCreate,
    StepUpdate,
    StepResponse
)
from ...models.workflow import WorkflowDefinition, WorkflowStep
from ...models.team import TeamModel
from ...auth.provider import get_current_user, require_roles

router = APIRouter()


async def get_user_accessible_workflows(user: dict) -> List[str]:
    """Get list of workflow IDs that the user can access based on their roles"""
    user_roles = user.get("roles", [])

    # Admin and managers can see all workflows
    if "admin" in user_roles or "manager" in user_roles:
        workflows = await WorkflowDefinition.find().to_list()
        return [w.workflow_id for w in workflows]

    # For other roles, return public workflows or team-based workflows
    # For now, return all workflows for authenticated users
    workflows = await WorkflowDefinition.find({"is_active": True}).to_list()
    return [w.workflow_id for w in workflows]


def step_to_schema(step: WorkflowStep) -> StepSchema:
    """Convert a WorkflowStep DB model to a StepSchema"""
    return StepSchema(
        step_id=step.step_id,
        name=step.name,
        step_type=step.step_type,
        description=step.description,
        required_inputs=step.required_inputs,
        optional_inputs=step.optional_inputs,
        next_steps=step.next_steps,
        operator_class=step.operator_class or step.step_type
    )


async def convert_workflow_to_response(workflow: WorkflowDefinition) -> WorkflowResponse:
    """Convert database WorkflowDefinition to API response"""
    steps = await WorkflowStep.find(WorkflowStep.workflow_id == workflow.workflow_id).to_list()
    step_schemas = [step_to_schema(s) for s in steps]
    
    return WorkflowResponse(
        workflow_id=workflow.workflow_id,
        name=workflow.name,
        description=workflow.description,
        metadata=workflow.metadata,
        version=workflow.version,
        status=WorkflowStatus(workflow.status),
        steps=step_schemas,
        start_step_id=workflow.start_step_id,
        created_at=workflow.created_at,
        updated_at=workflow.updated_at
    )


@router.post("/", response_model=WorkflowResponse)
async def create_workflow(workflow_data: WorkflowCreate):
    """Create a new workflow"""
    # Check if workflow already exists
    existing = await WorkflowDefinition.find_one(WorkflowDefinition.workflow_id == workflow_data.workflow_id)
    if existing:
        raise HTTPException(status_code=400, detail="Workflow ID already exists")
    
    # Create workflow definition
    workflow = WorkflowDefinition(
        workflow_id=workflow_data.workflow_id,
        name=workflow_data.name,
        description=workflow_data.description or "",
        version=workflow_data.version,
        status="draft"
    )
    
    await workflow.create()
    return await convert_workflow_to_response(workflow)


@router.get("/", response_model=WorkflowListResponse)
async def list_workflows(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[WorkflowStatus] = None,
    current_user: dict = Depends(get_current_user)
):
    """List workflows from DAGBag with DB status, with pagination"""
    from ...services.workflow_service import workflow_service

    # Get all workflows from DAGBag (source of truth for available workflows)
    all_workflows = await workflow_service.list_workflow_definitions(
        status=status.value if status else None,
        limit=0  # no limit, we paginate below
    )

    # Filter by accessible workflows for non-admin users
    user_roles = current_user.get("roles", [])
    if "admin" not in user_roles and "manager" not in user_roles:
        accessible_ids = set(await get_user_accessible_workflows(current_user))
        if not accessible_ids:
            return WorkflowListResponse(workflows=[], total=0, page=page, page_size=page_size)
        all_workflows = [w for w in all_workflows if w.workflow_id in accessible_ids]

    total = len(all_workflows)
    skip = (page - 1) * page_size
    page_workflows = all_workflows[skip:skip + page_size]

    # Batch-fetch all steps for this page in one query
    page_wf_ids = [wf.workflow_id for wf in page_workflows]
    all_steps = await WorkflowStep.find({"workflow_id": {"$in": page_wf_ids}}).to_list() if page_wf_ids else []
    steps_by_wf: dict = {}
    for step in all_steps:
        steps_by_wf.setdefault(step.workflow_id, []).append(step_to_schema(step))

    valid_statuses = {s.value for s in WorkflowStatus}
    workflow_responses = []
    for wf in page_workflows:
        wf_status = WorkflowStatus(wf.status) if wf.status in valid_statuses else WorkflowStatus.ACTIVE
        workflow_responses.append(WorkflowResponse(
            workflow_id=wf.workflow_id,
            name=wf.name,
            description=wf.description,
            metadata=wf.metadata,
            version=wf.version,
            status=wf_status,
            steps=steps_by_wf.get(wf.workflow_id, []),
            start_step_id=getattr(wf, 'start_step_id', None),
            created_at=wf.created_at,
            updated_at=wf.updated_at
        ))

    return WorkflowListResponse(
        workflows=workflow_responses,
        total=total,
        page=page,
        page_size=page_size
    )


@router.get("/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(workflow_id: str):
    """Get a specific workflow with steps from DB or DAGBag fallback"""
    workflow = await WorkflowDefinition.find_one(WorkflowDefinition.workflow_id == workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    response = await convert_workflow_to_response(workflow)

    # If no steps in DB, try to build them from DAGBag
    if not response.steps:
        from ...services.workflow_service import workflow_service
        dag = await workflow_service.get_dag(workflow_id)
        if dag:
            for task_id, task in dag.tasks.items():
                step_type = workflow_service.get_step_type_from_operator(task)
                response.steps.append(StepSchema(
                    step_id=task_id,
                    name=task.name,
                    step_type=step_type,
                    description=f"{task.__class__.__name__} operation",
                    required_inputs=[],
                    optional_inputs=[],
                    next_steps=[t.task_id for t in task.downstream_tasks],
                    operator_class=task.__class__.__name__
                ))

    return response


@router.put("/{workflow_id}", response_model=WorkflowResponse)
async def update_workflow(workflow_id: str, update_data: WorkflowUpdate):
    """Update a workflow"""
    workflow = await WorkflowDefinition.find_one(WorkflowDefinition.workflow_id == workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    # Update fields
    if update_data.name is not None:
        workflow.name = update_data.name
    if update_data.description is not None:
        workflow.description = update_data.description
    if update_data.status is not None:
        workflow.status = update_data.status
    if update_data.metadata is not None:
        workflow.metadata = update_data.metadata
    
    workflow.updated_at = datetime.utcnow()
    await workflow.save()
    
    return await convert_workflow_to_response(workflow)


@router.delete("/{workflow_id}")
async def delete_workflow(workflow_id: str):
    """Delete a workflow"""
    workflow = await WorkflowDefinition.find_one(WorkflowDefinition.workflow_id == workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    # Delete associated steps
    await WorkflowStep.find(WorkflowStep.workflow_id == workflow_id).delete()
    
    # Delete workflow
    await workflow.delete()
    
    return {"message": "Workflow deleted successfully"}


@router.get("/{workflow_id}/diagram")
async def get_workflow_diagram(workflow_id: str):
    """Get workflow diagram in Mermaid format"""
    workflow = await WorkflowDefinition.find_one(WorkflowDefinition.workflow_id == workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    # Get steps
    steps = await WorkflowStep.find(WorkflowStep.workflow_id == workflow_id).to_list()
    
    # Generate basic Mermaid diagram
    mermaid_lines = ["graph TD"]
    
    for step in steps:
        # Add step node
        mermaid_lines.append(f'    {step.step_id}["{step.name}"]')
        
        # Add connections to next steps
        for next_step_id in step.next_steps:
            mermaid_lines.append(f'    {step.step_id} --> {next_step_id}')
    
    if not mermaid_lines[1:]:  # No steps
        mermaid_lines.append(f'    start["{workflow.name}"]')
    
    return WorkflowDiagram(
        workflow_id=workflow_id,
        diagram_type="mermaid",
        content="\n".join(mermaid_lines)
    )


@router.post("/{workflow_id}/validate")
async def validate_workflow(workflow_id: str):
    """Validate workflow configuration"""
    workflow = await WorkflowDefinition.find_one(WorkflowDefinition.workflow_id == workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    steps = await WorkflowStep.find(WorkflowStep.workflow_id == workflow_id).to_list()
    
    errors = []
    warnings = []
    
    # Check if workflow has steps
    if not steps:
        errors.append("Workflow has no steps defined")
    
    # Check if start step exists
    if workflow.start_step_id:
        start_step_exists = any(step.step_id == workflow.start_step_id for step in steps)
        if not start_step_exists:
            errors.append(f"Start step '{workflow.start_step_id}' not found")
    else:
        warnings.append("No start step defined")
    
    # Check for orphaned steps
    step_ids = {step.step_id for step in steps}
    for step in steps:
        for next_step_id in step.next_steps:
            if next_step_id not in step_ids:
                errors.append(f"Step '{step.step_id}' references non-existent step '{next_step_id}'")
    
    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings
    }


# Step Management Endpoints

@router.post("/{workflow_id}/steps", response_model=StepResponse)
async def create_step(workflow_id: str, step_data: StepCreate):
    """Create a new step in a workflow"""
    # Check if workflow exists
    workflow = await WorkflowDefinition.find_one(WorkflowDefinition.workflow_id == workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    # Check if step already exists
    existing_step = await WorkflowStep.find_one(
        (WorkflowStep.workflow_id == workflow_id) & 
        (WorkflowStep.step_id == step_data.step_id)
    )
    if existing_step:
        raise HTTPException(status_code=400, detail="Step ID already exists in this workflow")
    
    # Create step
    step = WorkflowStep(
        workflow_id=workflow_id,
        step_id=step_data.step_id,
        name=step_data.name,
        step_type=step_data.step_type,
        description=step_data.description,
        required_inputs=step_data.required_inputs,
        optional_inputs=step_data.optional_inputs,
        next_steps=step_data.next_steps
    )
    
    await step.create()
    
    return StepResponse(
        step_id=step.step_id,
        workflow_id=step.workflow_id,
        name=step.name,
        step_type=StepType(step.step_type),
        description=step.description,
        required_inputs=step.required_inputs,
        optional_inputs=step.optional_inputs,
        next_steps=step.next_steps,
        created_at=step.created_at,
        updated_at=step.updated_at
    )


@router.get("/{workflow_id}/steps", response_model=List[StepResponse])
async def list_workflow_steps(workflow_id: str):
    """List all steps in a workflow"""
    # Check if workflow exists
    workflow = await WorkflowDefinition.find_one(WorkflowDefinition.workflow_id == workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    steps = await WorkflowStep.find(WorkflowStep.workflow_id == workflow_id).to_list()
    
    return [
        StepResponse(
            step_id=step.step_id,
            workflow_id=step.workflow_id,
            name=step.name,
            step_type=StepType(step.step_type),
            description=step.description,
            required_inputs=step.required_inputs,
            optional_inputs=step.optional_inputs,
            next_steps=step.next_steps,
            created_at=step.created_at,
            updated_at=step.updated_at
        )
        for step in steps
    ]


@router.get("/{workflow_id}/steps/{step_id}", response_model=StepResponse)
async def get_step(workflow_id: str, step_id: str):
    """Get a specific step in a workflow"""
    step = await WorkflowStep.find_one(
        (WorkflowStep.workflow_id == workflow_id) & 
        (WorkflowStep.step_id == step_id)
    )
    if not step:
        raise HTTPException(status_code=404, detail="Step not found")
    
    return StepResponse(
        step_id=step.step_id,
        workflow_id=step.workflow_id,
        name=step.name,
        step_type=StepType(step.step_type),
        description=step.description,
        required_inputs=step.required_inputs,
        optional_inputs=step.optional_inputs,
        next_steps=step.next_steps,
        created_at=step.created_at,
        updated_at=step.updated_at
    )


@router.put("/{workflow_id}/steps/{step_id}", response_model=StepResponse)
async def update_step(workflow_id: str, step_id: str, update_data: StepUpdate):
    """Update a step in a workflow"""
    step = await WorkflowStep.find_one(
        (WorkflowStep.workflow_id == workflow_id) & 
        (WorkflowStep.step_id == step_id)
    )
    if not step:
        raise HTTPException(status_code=404, detail="Step not found")
    
    # Update fields
    if update_data.name is not None:
        step.name = update_data.name
    if update_data.step_type is not None:
        step.step_type = update_data.step_type
    if update_data.description is not None:
        step.description = update_data.description
    if update_data.required_inputs is not None:
        step.required_inputs = update_data.required_inputs
    if update_data.optional_inputs is not None:
        step.optional_inputs = update_data.optional_inputs
    if update_data.next_steps is not None:
        step.next_steps = update_data.next_steps
    
    step.updated_at = datetime.utcnow()
    await step.save()
    
    return StepResponse(
        step_id=step.step_id,
        workflow_id=step.workflow_id,
        name=step.name,
        step_type=StepType(step.step_type),
        description=step.description,
        required_inputs=step.required_inputs,
        optional_inputs=step.optional_inputs,
        next_steps=step.next_steps,
        created_at=step.created_at,
        updated_at=step.updated_at
    )


@router.delete("/{workflow_id}/steps/{step_id}")
async def delete_step(workflow_id: str, step_id: str):
    """Delete a step from a workflow"""
    step = await WorkflowStep.find_one(
        (WorkflowStep.workflow_id == workflow_id) & 
        (WorkflowStep.step_id == step_id)
    )
    if not step:
        raise HTTPException(status_code=404, detail="Step not found")
    
    await step.delete()
    
    return {"message": "Step deleted successfully"}