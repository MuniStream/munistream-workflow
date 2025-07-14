from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Query, BackgroundTasks, Depends
from datetime import datetime
import uuid
from beanie import PydanticObjectId

from ...schemas.workflow import (
    WorkflowExecuteRequest,
    InstanceResponse,
    InstanceListResponse,
    InstanceStatus,
    InstanceUpdateRequest,
    ApprovalRequest,
    InstanceProgressResponse,
    ActiveInstancesResponse,
    BottleneckAnalysisResponse,
    InstanceHistoryResponse
)
from ...models.workflow import (
    WorkflowInstance,
    StepExecution,
    WorkflowDefinition,
    WorkflowStep,
    ApprovalRequest as ApprovalModel
)
from ...core.database import get_database
from ...workflows.registry import step_registry
from ...workflows.executor import step_executor, StepExecutionContext
from ...services.workflow_service import WorkflowService

router = APIRouter()


def convert_instance_to_response(instance: WorkflowInstance) -> InstanceResponse:
    """Convert internal WorkflowInstance to API response"""
    return InstanceResponse(
        instance_id=instance.instance_id,
        workflow_id=instance.workflow_id,
        user_id=instance.user_id,
        status=InstanceStatus(instance.status),
        current_step=instance.current_step,
        context=instance.context,
        step_results={},  # Will be populated from StepExecution collection
        created_at=instance.created_at,
        updated_at=instance.updated_at,
        completed_at=instance.completed_at
    )


async def execute_workflow_instance(instance_id: str):
    """Background task to execute workflow instance"""
    instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
    if not instance:
        return
    
    try:
        # Get workflow from registry
        workflow = step_registry.get_workflow(instance.workflow_id)
        if not workflow:
            raise Exception(f"Workflow {instance.workflow_id} not found in registry")
        
        # Set current step to start step if not already set
        if not instance.current_step and workflow.start_step:
            instance.current_step = workflow.start_step.step_id
            instance.status = "running"
            instance.started_at = datetime.utcnow()
            instance.updated_at = datetime.utcnow()
            await instance.save()
            
            # Create step execution record
            execution_context = StepExecutionContext(
                instance_id=instance.instance_id,
                user_id=instance.user_id,
                execution_environment="production"
            )
            execution_context.mark_queued()
            
            # Check if the first step requires citizen input
            start_step = workflow.start_step
            
            # If step requires citizen input, pause execution and wait for input
            if hasattr(start_step, 'requires_citizen_input') and start_step.requires_citizen_input:
                # Don't execute the step yet - wait for citizen input
                instance.status = "awaiting_input"
                instance.updated_at = datetime.utcnow()
                await instance.save()
                return  # Exit early - citizen needs to provide input first
            
            # Execute the step normally (non-citizen-input step)
            step_result = await step_executor.execute_step(
                step=start_step,
                inputs=instance.context,
                context=instance.context,
                execution_context=execution_context
            )
            
            # Record step execution in database
            step_execution = StepExecution(
                execution_id=str(uuid.uuid4()),
                instance_id=instance.instance_id,
                step_id=start_step.step_id,
                workflow_id=instance.workflow_id,
                status=step_result.status.value if hasattr(step_result.status, 'value') else str(step_result.status),
                inputs=instance.context,
                outputs=step_result.outputs if isinstance(step_result.outputs, dict) else {},
                started_at=step_result.started_at,
                completed_at=step_result.completed_at,
                duration_seconds=step_result.execution_duration_ms / 1000 if step_result.execution_duration_ms else None,
                error_message=step_result.error
            )
            await step_execution.create()
            
            # Update instance with step completion
            if step_result.status.value == "completed":
                if start_step.step_id not in instance.completed_steps:
                    instance.completed_steps.append(start_step.step_id)
                
                # Update context with step outputs
                if step_result.outputs:
                    instance.context.update(step_result.outputs)
                
                # Move to next step if available
                if start_step.next_steps:
                    # For simplicity, take the first next step
                    next_step = start_step.next_steps[0]
                    instance.current_step = next_step.step_id
                else:
                    # No next steps, workflow complete
                    instance.status = "completed"
                    instance.current_step = None
                    instance.completed_at = datetime.utcnow()
            elif step_result.status.value == "failed":
                instance.status = "failed"
                if start_step.step_id not in instance.failed_steps:
                    instance.failed_steps.append(start_step.step_id)
                instance.context["error"] = step_result.error
            
            instance.updated_at = datetime.utcnow()
            await instance.save()
            
    except Exception as e:
        instance.status = "failed"
        instance.context["error"] = str(e)
        instance.updated_at = datetime.utcnow()
        await instance.save()


@router.post("/", response_model=InstanceResponse)
async def create_instance(
    request: WorkflowExecuteRequest,
    background_tasks: BackgroundTasks
):
    """Create and start a new workflow instance"""
    # Check if workflow exists
    workflow = await WorkflowDefinition.find_one(WorkflowDefinition.workflow_id == request.workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    # Create instance
    instance = WorkflowInstance(
        instance_id=str(uuid.uuid4()),
        workflow_id=request.workflow_id,
        workflow_version=workflow.version,
        user_id=request.user_id,
        context=request.initial_context,
        status="running"
    )
    
    # Save to database
    await instance.create()
    
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
    # Build query
    query = {}
    if workflow_id:
        query["workflow_id"] = workflow_id
    if user_id:
        query["user_id"] = user_id
    if status:
        query["status"] = status
    
    # Get total count
    total = await WorkflowInstance.find(query).count()
    
    # Get paginated results
    skip = (page - 1) * page_size
    instances = await WorkflowInstance.find(query).sort(-WorkflowInstance.created_at).skip(skip).limit(page_size).to_list()
    
    # Convert to response format
    instance_responses = [convert_instance_to_response(instance) for instance in instances]
    
    return InstanceListResponse(
        instances=instance_responses,
        total=total,
        page=page,
        page_size=page_size
    )


@router.get("/active", response_model=ActiveInstancesResponse)
async def get_active_instances():
    """Get all currently active workflow instances"""
    active_instances = await WorkflowInstance.find(
        {"status": {"$in": ["running", "paused"]}}
    ).sort(-WorkflowInstance.updated_at).to_list()
    
    result = []
    for instance in active_instances:
        # Get basic progress info
        workflow = await WorkflowDefinition.find_one(WorkflowDefinition.workflow_id == instance.workflow_id)
        total_steps = await WorkflowStep.find(WorkflowStep.workflow_id == instance.workflow_id).count() if workflow else 0
        
        progress_percentage = (len(instance.completed_steps) / total_steps * 100) if total_steps > 0 else 0
        
        result.append({
            "instance_id": instance.instance_id,
            "workflow_id": instance.workflow_id,
            "workflow_name": workflow.name if workflow else "Unknown",
            "user_id": instance.user_id,
            "status": instance.status,
            "current_step": instance.current_step,
            "progress_percentage": round(progress_percentage, 2),
            "started_at": instance.started_at,
            "updated_at": instance.updated_at,
            "pending_approvals": len(instance.pending_approvals)
        })
    
    return {
        "active_instances": result,
        "total_active": len(result)
    }


@router.get("/analytics/bottlenecks", response_model=BottleneckAnalysisResponse)
async def get_bottleneck_analysis():
    """Analyze workflow bottlenecks across all instances"""
    # Get all step executions from last 30 days
    from datetime import timedelta
    cutoff_date = datetime.utcnow() - timedelta(days=30)
    
    recent_executions = await StepExecution.find(
        StepExecution.started_at >= cutoff_date
    ).to_list()
    
    # Group by step_id and calculate average duration
    step_stats = {}
    for execution in recent_executions:
        step_id = execution.step_id
        if step_id not in step_stats:
            step_stats[step_id] = {
                "step_id": step_id,
                "total_executions": 0,
                "total_duration": 0,
                "failed_executions": 0,
                "avg_duration": 0
            }
        
        step_stats[step_id]["total_executions"] += 1
        if execution.duration_seconds:
            step_stats[step_id]["total_duration"] += execution.duration_seconds
        if execution.status == "failed":
            step_stats[step_id]["failed_executions"] += 1
    
    # Calculate averages and sort by duration
    bottlenecks = []
    for stats in step_stats.values():
        if stats["total_executions"] > 0:
            stats["avg_duration"] = stats["total_duration"] / stats["total_executions"]
            stats["failure_rate"] = stats["failed_executions"] / stats["total_executions"]
            bottlenecks.append(stats)
    
    # Sort by average duration (descending)
    bottlenecks.sort(key=lambda x: x["avg_duration"], reverse=True)
    
    # Get instances currently stuck at bottleneck steps
    stuck_instances = []
    if bottlenecks:
        top_bottleneck_steps = [b["step_id"] for b in bottlenecks[:5]]
        stuck = await WorkflowInstance.find({
            "current_step": {"$in": top_bottleneck_steps},
            "status": {"$in": ["running", "paused"]}
        }).to_list()
        
        for instance in stuck:
            workflow = await WorkflowDefinition.find_one(WorkflowDefinition.workflow_id == instance.workflow_id)
            stuck_instances.append({
                "instance_id": instance.instance_id,
                "workflow_name": workflow.name if workflow else "Unknown",
                "current_step": instance.current_step,
                "stuck_duration": (datetime.utcnow() - instance.updated_at).total_seconds(),
                "user_id": instance.user_id
            })
    
    return {
        "bottlenecks": bottlenecks[:10],  # Top 10 bottlenecks
        "stuck_instances": stuck_instances,
        "analysis_period_days": 30,
        "total_executions_analyzed": len(recent_executions)
    }


@router.get("/{instance_id}", response_model=InstanceResponse)
async def get_instance(instance_id: str):
    """Get a specific workflow instance"""
    instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    return convert_instance_to_response(instance)


@router.put("/{instance_id}", response_model=InstanceResponse)
async def update_instance(instance_id: str, update_data: InstanceUpdateRequest):
    """Update instance status or context"""
    instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    if update_data.status is not None:
        instance.status = update_data.status
    
    if update_data.context_updates is not None:
        instance.context.update(update_data.context_updates)
    
    instance.updated_at = datetime.utcnow()
    await instance.save()
    
    return convert_instance_to_response(instance)


@router.post("/{instance_id}/cancel")
async def cancel_instance(instance_id: str):
    """Cancel a running workflow instance"""
    instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    if instance.status not in ["running", "paused"]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel instance in {instance.status} status"
        )
    
    instance.status = "cancelled"
    instance.updated_at = datetime.utcnow()
    await instance.save()
    
    return {"message": "Instance cancelled successfully"}


@router.post("/{instance_id}/pause")
async def pause_instance(instance_id: str):
    """Pause a running workflow instance"""
    instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    if instance.status != "running":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot pause instance in {instance.status} status"
        )
    
    instance.status = "paused"
    instance.updated_at = datetime.utcnow()
    await instance.save()
    
    return {"message": "Instance paused successfully"}


@router.post("/{instance_id}/resume", response_model=InstanceResponse)
async def resume_instance(instance_id: str, background_tasks: BackgroundTasks):
    """Resume a paused workflow instance"""
    instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    if instance.status != "paused":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot resume instance in {instance.status} status"
        )
    
    instance.status = "running"
    instance.updated_at = datetime.utcnow()
    await instance.save()
    
    # Resume execution in background
    background_tasks.add_task(execute_workflow_instance, instance_id)
    
    return convert_instance_to_response(instance)


@router.post("/{instance_id}/approve")
async def approve_step(approval: ApprovalRequest, background_tasks: BackgroundTasks):
    """Submit approval decision for a workflow step"""
    instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == approval.instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    # Create approval record
    approval_record = ApprovalModel(
        approval_id=str(uuid.uuid4()),
        instance_id=approval.instance_id,
        step_id=approval.step_id,
        workflow_id=instance.workflow_id,
        title=f"Approval for step {approval.step_id}",
        decision=approval.decision,
        decision_reason=approval.comments,
        decided_by=approval.approver_id,
        status="completed",
        responded_at=datetime.utcnow()
    )
    await approval_record.create()
    
    # Update instance context
    instance.context.update({
        "approval_status": approval.decision,
        "approval_comments": approval.comments,
        "approver_id": approval.approver_id,
        "approval_timestamp": datetime.utcnow().isoformat()
    })
    await instance.save()
    
    # Resume workflow execution
    if instance.status == "running":
        background_tasks.add_task(execute_workflow_instance, approval.instance_id)
    
    return {
        "message": f"Approval {approval.decision} recorded",
        "instance_id": approval.instance_id,
        "step_id": approval.step_id
    }


@router.get("/{instance_id}/history", response_model=InstanceHistoryResponse)
async def get_instance_history(instance_id: str):
    """Get execution history of a workflow instance"""
    instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    # Get step executions for this instance
    step_executions = await StepExecution.find(StepExecution.instance_id == instance_id).sort(+StepExecution.started_at).to_list()
    
    # Format step executions as history
    history = []
    for execution in step_executions:
        history.append({
            "step_id": execution.step_id,
            "execution_id": execution.execution_id,
            "status": execution.status,
            "started_at": execution.started_at,
            "completed_at": execution.completed_at,
            "duration_seconds": execution.duration_seconds,
            "inputs": execution.inputs,
            "outputs": execution.outputs,
            "error_message": execution.error_message,
            "retry_count": execution.retry_count
        })
    
    return {
        "instance_id": instance_id,
        "workflow_id": instance.workflow_id,
        "history": history,
        "current_step": instance.current_step,
        "overall_status": instance.status,
        "completed_steps": instance.completed_steps,
        "failed_steps": instance.failed_steps,
        "pending_approvals": instance.pending_approvals
    }


@router.get("/{instance_id}/progress", response_model=InstanceProgressResponse)
async def get_instance_progress(instance_id: str):
    """Get detailed progress information for a workflow instance"""
    instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    # Get workflow definition to calculate progress percentage
    workflow = await WorkflowDefinition.find_one(WorkflowDefinition.workflow_id == instance.workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow definition not found")
    
    # Get all steps for this workflow
    total_steps = await WorkflowStep.find(WorkflowStep.workflow_id == instance.workflow_id).count()
    completed_steps_count = len(instance.completed_steps)
    failed_steps_count = len(instance.failed_steps)
    
    # Calculate progress percentage
    progress_percentage = (completed_steps_count / total_steps * 100) if total_steps > 0 else 0
    
    # Get step executions with timing
    step_executions = await StepExecution.find(StepExecution.instance_id == instance_id).to_list()
    
    # Calculate total processing time
    total_duration = sum(exec.duration_seconds for exec in step_executions if exec.duration_seconds)
    
    # Find current bottleneck (longest running step)
    current_bottleneck = None
    if step_executions:
        longest_step = max(step_executions, key=lambda x: x.duration_seconds or 0)
        if longest_step.duration_seconds and longest_step.duration_seconds > 0:
            current_bottleneck = {
                "step_id": longest_step.step_id,
                "duration_seconds": longest_step.duration_seconds,
                "status": longest_step.status
            }
    
    return {
        "instance_id": instance_id,
        "workflow_id": instance.workflow_id,
        "progress_percentage": round(progress_percentage, 2),
        "total_steps": total_steps,
        "completed_steps": completed_steps_count,
        "failed_steps": failed_steps_count,
        "pending_steps": total_steps - completed_steps_count - failed_steps_count,
        "current_step": instance.current_step,
        "status": instance.status,
        "total_duration_seconds": total_duration,
        "started_at": instance.started_at,
        "updated_at": instance.updated_at,
        "completed_at": instance.completed_at,
        "current_bottleneck": current_bottleneck,
        "pending_approvals_count": len(instance.pending_approvals),
        "estimated_completion": None  # TODO: Implement estimation logic
    }