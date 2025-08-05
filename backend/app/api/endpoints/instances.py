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
from ...services.auth_service import require_permission
from ...models.user import UserModel, Permission

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
        
        # Handle initial workflow start
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
        
        # Handle continued execution from current step
        elif instance.current_step and instance.status == "running":
            current_step = workflow.steps.get(instance.current_step)
            if not current_step:
                raise Exception(f"Current step {instance.current_step} not found in workflow")
            
            # Create execution context
            execution_context = StepExecutionContext(
                instance_id=instance.instance_id,
                user_id=instance.user_id,
                execution_environment="production"
            )
            execution_context.mark_queued()
            
            # Check if step requires citizen input and hasn't been completed
            if hasattr(current_step, 'requires_citizen_input') and current_step.requires_citizen_input:
                # Check if citizen data has been submitted for THIS specific step
                step_citizen_data_key = f"{current_step.step_id}_citizen_data"
                step_validation_key = f"{current_step.step_id}_admin_validation_decision"
                
                has_step_citizen_data = step_citizen_data_key in instance.context
                step_validation_done = instance.context.get(step_validation_key) is not None
                
                if not has_step_citizen_data:
                    # Still waiting for citizen input
                    instance.status = "awaiting_input"
                    instance.updated_at = datetime.utcnow()
                    await instance.save()
                    return
                elif not step_validation_done:
                    # Citizen data submitted but waiting for admin validation
                    instance.status = "pending_validation"
                    instance.updated_at = datetime.utcnow()
                    await instance.save()
                    return
            
            # Execute the current step
            step_result = await step_executor.execute_step(
                step=current_step,
                inputs=instance.context,
                context=instance.context,
                execution_context=execution_context
            )
            
            # Record step execution
            step_execution = StepExecution(
                execution_id=str(uuid.uuid4()),
                instance_id=instance.instance_id,
                step_id=current_step.step_id,
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
                if current_step.step_id not in instance.completed_steps:
                    instance.completed_steps.append(current_step.step_id)
                
                # Update context with step outputs
                if step_result.outputs:
                    instance.context.update(step_result.outputs)
                
                # Move to next step if available
                if hasattr(current_step, 'next_steps') and current_step.next_steps:
                    next_step = current_step.next_steps[0]
                    instance.current_step = next_step.step_id
                    
                    # Continue execution recursively if next step doesn't require citizen input
                    instance.updated_at = datetime.utcnow()
                    await instance.save()
                    
                    # Recursively continue to next step
                    await execute_workflow_instance(instance_id)
                    return
                else:
                    # No next steps, workflow complete
                    instance.status = "completed"
                    instance.current_step = None
                    instance.completed_at = datetime.utcnow()
            elif step_result.status.value == "failed":
                instance.status = "failed"
                if current_step.step_id not in instance.failed_steps:
                    instance.failed_steps.append(current_step.step_id)
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
        {"status": {"$in": ["running", "paused", "awaiting_input"]}}
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


@router.get("/citizen-validations", response_model=List[Dict[str, Any]])
async def get_citizen_validations(
    current_user: UserModel = Depends(require_permission(Permission.MANAGE_INSTANCES)),
    status: Optional[str] = Query(None, description="Filter by status: awaiting_input, pending_validation"),
    limit: int = Query(100, description="Maximum number of results")
):
    """Get citizen instances awaiting validation"""
    from ...models.user import UserModel
    
    # Build query for instances requiring validation
    query_filters = []
    
    # Build MongoDB query to find instances with citizen data that need validation
    if status:
        query = {"status": status}
    else:
        # First get all instances that might need validation
        # We'll filter them properly in Python code below
        query = {
            "$or": [
                {"status": {"$in": ["awaiting_input", "pending_validation"]}},
                {"status": "running"}
            ]
        }
    
    # Find instances that need validation
    instances = await WorkflowInstance.find(
        query
    ).sort(-WorkflowInstance.created_at).limit(limit).to_list()
    
    # Format response with citizen data - filter out instances without citizen data
    validation_items = []
    for instance in instances:
        # Get workflow from registry for metadata
        workflow = step_registry.get_workflow(instance.workflow_id)
        if not workflow:
            continue
            
        # Extract citizen data from context
        citizen_data = {}
        uploaded_files = {}
        has_citizen_data = False
        
        for key, value in instance.context.items():
            if key.endswith('_citizen_data'):
                citizen_data.update(value)
                has_citizen_data = True
            elif key.endswith('_uploaded_files'):
                uploaded_files.update(value)
        
        # Check if admin validation has already been done
        admin_validation_done = instance.context.get("admin_validation_decision") is not None
        
        # Include instances that:
        # 1. Are awaiting input (potential for citizen data), OR
        # 2. Have citizen data but haven't been validated by admin yet
        should_include = False
        
        if instance.status in ["awaiting_input", "pending_validation"]:
            should_include = True
        elif has_citizen_data and not admin_validation_done:
            should_include = True
        
        if not should_include:
            continue
        
        # Find current step requiring validation
        current_step_info = None
        if instance.current_step and instance.current_step in workflow.steps:
            current_step = workflow.steps[instance.current_step]
            current_step_info = {
                "step_id": current_step.step_id,
                "name": current_step.name,
                "description": current_step.description,
                "requires_citizen_input": getattr(current_step, 'requires_citizen_input', False)
            }
        
        validation_items.append({
            "instance_id": instance.instance_id,
            "workflow_id": instance.workflow_id,
            "workflow_name": workflow.name,
            "citizen_id": instance.user_id,
            "status": instance.status,
            "current_step": current_step_info,
            "citizen_data": citizen_data,
            "uploaded_files": uploaded_files,
            "created_at": instance.created_at,
            "updated_at": instance.updated_at,
            "context": instance.context
        })
    
    return validation_items


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


@router.post("/{instance_id}/validate-citizen-data")
async def validate_citizen_data(
    instance_id: str,
    validation_request: Dict[str, Any],
    background_tasks: BackgroundTasks,
    current_user: UserModel = Depends(require_permission(Permission.MANAGE_INSTANCES))
):
    """Validate or reject citizen submitted data"""
    from ...models.user import UserModel
    
    # Find the instance
    instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    # Check if instance can be validated
    # Allow validation if:
    # 1. Status is awaiting_input or pending_validation, OR
    # 2. Status is running and has citizen data but no admin validation yet
    has_citizen_data = any(key.endswith('_citizen_data') for key in instance.context.keys())
    admin_validation_done = instance.context.get("admin_validation_decision") is not None
    
    valid_statuses = ["awaiting_input", "pending_validation"]
    can_validate_running = instance.status == "running" and has_citizen_data and not admin_validation_done
    
    if instance.status not in valid_statuses and not can_validate_running:
        raise HTTPException(
            status_code=400, 
            detail=f"Instance status '{instance.status}' does not allow validation or has already been validated"
        )
    
    # Get validation decision
    decision = validation_request.get("decision")  # "approve" or "reject"
    comments = validation_request.get("comments", "")
    
    if decision not in ["approve", "reject"]:
        raise HTTPException(
            status_code=400,
            detail="Decision must be 'approve' or 'reject'"
        )
    
    # Get workflow from registry
    workflow = step_registry.get_workflow(instance.workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    # Find which step needs validation (the current step requiring citizen input)
    current_step = None
    if instance.current_step and instance.current_step in workflow.steps:
        current_step = workflow.steps[instance.current_step]
    
    # If current step requires citizen input, validate for that step
    if current_step and hasattr(current_step, 'requires_citizen_input') and current_step.requires_citizen_input:
        step_validation_key = f"{current_step.step_id}_admin_validation_decision"
        step_comments_key = f"{current_step.step_id}_admin_validation_comments"
        step_validator_key = f"{current_step.step_id}_validated_by"
        step_timestamp_key = f"{current_step.step_id}_validation_timestamp"
    else:
        # Fall back to global validation for backward compatibility
        step_validation_key = "admin_validation_decision"
        step_comments_key = "admin_validation_comments"
        step_validator_key = "validated_by"
        step_timestamp_key = "validation_timestamp"
    
    # Update instance with validation decision
    instance.context = instance.context or {}
    instance.context.update({
        step_validation_key: decision,
        step_comments_key: comments,
        step_validator_key: str(current_user.id),
        step_timestamp_key: datetime.utcnow().isoformat()
    })
    
    if decision == "approve":
        # Extract and promote citizen data to top-level context for subsequent steps
        if instance.current_step and instance.current_step in workflow.steps:
            current_step = workflow.steps[instance.current_step]
            
            # If this step collected citizen data, promote it to top-level context
            if hasattr(current_step, 'requires_citizen_input') and current_step.requires_citizen_input:
                citizen_data_key = f"{instance.current_step}_citizen_data"
                if citizen_data_key in instance.context:
                    citizen_data = instance.context[citizen_data_key]
                    # Promote each field to top-level context for subsequent steps
                    for field_name, field_value in citizen_data.items():
                        instance.context[field_name] = field_value
            
            # Mark current step as completed
            if instance.current_step not in instance.completed_steps:
                instance.completed_steps.append(instance.current_step)
            
            # Find next step
            next_step = None
            if hasattr(current_step, 'next_steps') and current_step.next_steps:
                next_step = current_step.next_steps[0]
            
            if next_step:
                instance.current_step = next_step.step_id
                instance.status = "running"
            else:
                # No next steps, workflow complete
                instance.status = "completed"
                instance.current_step = None
                instance.completed_at = datetime.utcnow()
        else:
            # No current step, resume normal workflow
            instance.status = "running"
    
    else:  # reject
        # Mark instance as failed with rejection reason
        instance.status = "failed"
        instance.current_step = None
        instance.completed_at = datetime.utcnow()
        if instance.current_step and instance.current_step not in instance.failed_steps:
            instance.failed_steps.append(instance.current_step)
    
    instance.updated_at = datetime.utcnow()
    await instance.save()
    
    # Create audit log for validation decision
    step_execution = StepExecution(
        execution_id=str(uuid.uuid4()),
        instance_id=instance.instance_id,
        step_id=instance.current_step or "admin_validation",
        workflow_id=instance.workflow_id,
        status="completed" if decision == "approve" else "failed",
        inputs={"admin_decision": decision, "comments": comments},
        outputs={"validation_result": decision, "validator": str(current_user.id)},
        started_at=datetime.utcnow(),
        completed_at=datetime.utcnow(),
        duration_seconds=0
    )
    await step_execution.create()
    
    # Continue workflow execution if approved
    if decision == "approve" and instance.status == "running":
        background_tasks.add_task(execute_workflow_instance, instance_id)
    
    return {
        "success": True,
        "message": f"Citizen data {decision}d successfully",
        "instance_id": instance_id,
        "decision": decision,
        "next_status": instance.status,
        "validated_by": str(current_user.id),
        "validation_timestamp": datetime.utcnow().isoformat()
    }


@router.get("/{instance_id}/citizen-data")
async def get_citizen_data(
    instance_id: str,
    current_user: UserModel = Depends(require_permission(Permission.VIEW_INSTANCES))
):
    """Get detailed citizen data for an instance"""
    from ...models.user import UserModel
    
    # Find the instance
    instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    # Get workflow from registry
    workflow = step_registry.get_workflow(instance.workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    # Extract all citizen data from context
    citizen_data = {}
    uploaded_files = {}
    data_submissions = []
    
    for key, value in instance.context.items():
        if key.endswith('_citizen_data'):
            step_id = key.replace('_citizen_data', '')
            citizen_data[step_id] = value
        elif key.endswith('_uploaded_files'):
            step_id = key.replace('_uploaded_files', '')
            uploaded_files[step_id] = value
        elif key.endswith('_data_submitted_at'):
            step_id = key.replace('_data_submitted_at', '')
            data_submissions.append({
                "step_id": step_id,
                "submitted_at": value
            })
    
    # Get step execution history
    step_executions = await StepExecution.find(
        StepExecution.instance_id == instance_id
    ).sort(StepExecution.started_at).to_list()
    
    # Build complete citizen data view
    citizen_info = {
        "instance_id": instance_id,
        "workflow_id": instance.workflow_id,
        "workflow_name": workflow.name,
        "citizen_id": instance.user_id,
        "status": instance.status,
        "current_step": instance.current_step,
        "created_at": instance.created_at,
        "updated_at": instance.updated_at,
        "completed_at": instance.completed_at,
        "citizen_data": citizen_data,
        "uploaded_files": uploaded_files,
        "data_submissions": data_submissions,
        "step_executions": [
            {
                "step_id": ex.step_id,
                "status": ex.status,
                "started_at": ex.started_at,
                "completed_at": ex.completed_at,
                "duration_seconds": ex.duration_seconds,
                "inputs": ex.inputs,
                "outputs": ex.outputs
            }
            for ex in step_executions
        ],
        "validation_history": [
            {
                "decision": instance.context.get("admin_validation_decision"),
                "comments": instance.context.get("admin_validation_comments"),
                "validated_by": instance.context.get("validated_by"),
                "timestamp": instance.context.get("validation_timestamp")
            }
        ] if instance.context.get("admin_validation_decision") else []
    }
    
    return citizen_info