from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from datetime import datetime
import uuid

from ...schemas.workflow import (
    WorkflowExecuteRequest,
    InstanceResponse,
    InstanceListResponse,
    InstanceStatus,
    InstanceUpdateRequest,
    ApprovalRequest
)
from ...workflows.workflow import WorkflowInstance
from .workflows import workflows_db

router = APIRouter()

# In-memory storage for demo (will be replaced with MongoDB)
instances_db: Dict[str, WorkflowInstance] = {}


def convert_instance_to_response(instance: WorkflowInstance) -> InstanceResponse:
    """Convert internal WorkflowInstance to API response"""
    return InstanceResponse(
        instance_id=instance.instance_id,
        workflow_id=instance.workflow_id,
        user_id=instance.user_id,
        status=InstanceStatus(instance.status),
        current_step=instance.current_step,
        context=instance.context,
        step_results={k: v.dict() for k, v in instance.step_results.items()},
        created_at=instance.created_at,
        updated_at=instance.updated_at,
        completed_at=instance.completed_at
    )


async def execute_workflow_instance(instance_id: str):
    """Background task to execute workflow instance"""
    instance = instances_db[instance_id]
    workflow = workflows_db[instance.workflow_id]
    
    try:
        # Execute the workflow
        await workflow.execute_instance(instance)
        instances_db[instance_id] = instance
    except Exception as e:
        instance.status = "failed"
        instance.context["error"] = str(e)
        instance.updated_at = datetime.utcnow()
        instances_db[instance_id] = instance


@router.post("/", response_model=InstanceResponse)
async def create_instance(
    request: WorkflowExecuteRequest,
    background_tasks: BackgroundTasks
):
    """Create and start a new workflow instance"""
    if request.workflow_id not in workflows_db:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    # Create instance
    instance = WorkflowInstance(
        instance_id=str(uuid.uuid4()),
        workflow_id=request.workflow_id,
        user_id=request.user_id,
        context=request.initial_context
    )
    
    # Store instance
    instances_db[instance.instance_id] = instance
    
    # Execute workflow in background
    background_tasks.add_task(execute_workflow_instance, instance.instance_id)
    
    return convert_instance_to_response(instance)


@router.get("/", response_model=InstanceListResponse)
async def list_instances(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    workflow_id: Optional[str] = None,
    user_id: Optional[str] = None,
    status: Optional[InstanceStatus] = None
):
    """List workflow instances with filtering and pagination"""
    # Filter instances
    filtered_instances = []
    for instance in instances_db.values():
        if workflow_id and instance.workflow_id != workflow_id:
            continue
        if user_id and instance.user_id != user_id:
            continue
        if status and instance.status != status:
            continue
        filtered_instances.append(convert_instance_to_response(instance))
    
    # Sort by created_at descending
    filtered_instances.sort(key=lambda x: x.created_at, reverse=True)
    
    # Pagination
    total = len(filtered_instances)
    start = (page - 1) * page_size
    end = start + page_size
    
    return InstanceListResponse(
        instances=filtered_instances[start:end],
        total=total,
        page=page,
        page_size=page_size
    )


@router.get("/{instance_id}", response_model=InstanceResponse)
async def get_instance(instance_id: str):
    """Get a specific workflow instance"""
    if instance_id not in instances_db:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    return convert_instance_to_response(instances_db[instance_id])


@router.put("/{instance_id}", response_model=InstanceResponse)
async def update_instance(instance_id: str, update_data: InstanceUpdateRequest):
    """Update instance status or context"""
    if instance_id not in instances_db:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    instance = instances_db[instance_id]
    
    if update_data.status is not None:
        instance.status = update_data.status
    
    if update_data.context_updates is not None:
        instance.context.update(update_data.context_updates)
    
    instance.updated_at = datetime.utcnow()
    
    return convert_instance_to_response(instance)


@router.post("/{instance_id}/cancel")
async def cancel_instance(instance_id: str):
    """Cancel a running workflow instance"""
    if instance_id not in instances_db:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    instance = instances_db[instance_id]
    
    if instance.status not in ["running", "paused"]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel instance in {instance.status} status"
        )
    
    instance.status = "cancelled"
    instance.updated_at = datetime.utcnow()
    
    return {"message": "Instance cancelled successfully"}


@router.post("/{instance_id}/pause")
async def pause_instance(instance_id: str):
    """Pause a running workflow instance"""
    if instance_id not in instances_db:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    instance = instances_db[instance_id]
    
    if instance.status != "running":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot pause instance in {instance.status} status"
        )
    
    instance.status = "paused"
    instance.updated_at = datetime.utcnow()
    
    return {"message": "Instance paused successfully"}


@router.post("/{instance_id}/resume", response_model=InstanceResponse)
async def resume_instance(instance_id: str, background_tasks: BackgroundTasks):
    """Resume a paused workflow instance"""
    if instance_id not in instances_db:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    instance = instances_db[instance_id]
    
    if instance.status != "paused":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot resume instance in {instance.status} status"
        )
    
    instance.status = "running"
    instance.updated_at = datetime.utcnow()
    
    # Resume execution in background
    background_tasks.add_task(execute_workflow_instance, instance_id)
    
    return convert_instance_to_response(instance)


@router.post("/{instance_id}/approve")
async def approve_step(approval: ApprovalRequest, background_tasks: BackgroundTasks):
    """Submit approval decision for a workflow step"""
    if approval.instance_id not in instances_db:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    instance = instances_db[approval.instance_id]
    
    # Update context with approval decision
    instance.context.update({
        "approval_status": approval.decision,
        "approval_comments": approval.comments,
        "approver_id": approval.approver_id,
        "approval_timestamp": datetime.utcnow().isoformat()
    })
    
    # Resume workflow execution
    if instance.status == "running":
        background_tasks.add_task(execute_workflow_instance, approval.instance_id)
    
    return {
        "message": f"Approval {approval.decision} recorded",
        "instance_id": approval.instance_id,
        "step_id": approval.step_id
    }


@router.get("/{instance_id}/history")
async def get_instance_history(instance_id: str):
    """Get execution history of a workflow instance"""
    if instance_id not in instances_db:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    instance = instances_db[instance_id]
    
    # Format step results as history
    history = []
    for step_id, result in instance.step_results.items():
        history.append({
            "step_id": step_id,
            "status": result.status,
            "started_at": result.started_at,
            "completed_at": result.completed_at,
            "outputs": result.outputs,
            "error": result.error
        })
    
    # Sort by started_at
    history.sort(key=lambda x: x["started_at"] or datetime.min)
    
    return {
        "instance_id": instance_id,
        "workflow_id": instance.workflow_id,
        "history": history,
        "current_step": instance.current_step,
        "overall_status": instance.status
    }