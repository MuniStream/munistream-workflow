from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Query, BackgroundTasks, Depends
from datetime import datetime
import uuid
import logging
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
    ApprovalRequest as ApprovalModel,
    AssignmentStatus,
    AssignmentType
)
from ...core.database import get_database
from ...workflows.dag import DAGInstance, InstanceStatus
from ...services.workflow_service import workflow_service
from ...auth.provider import require_permission, get_current_user
from ...services.assignment_service import assignment_service
from ...models.team import TeamModel

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/", response_model=InstanceResponse)
async def create_workflow_instance(
    request: WorkflowExecuteRequest,
    current_user: dict = Depends(get_current_user)
):
    """Create and execute a new workflow instance using DAG architecture"""
    try:
        # Always use authenticated user's ID for security
        user_id = str(current_user.get("sub"))
        
        # Create DAG instance with authenticated user
        dag_instance = await workflow_service.create_instance(
            workflow_id=request.workflow_id,
            user_id=user_id,
            initial_data=request.initial_context or {}
        )
        
        # Start execution
        await workflow_service.execute_instance(dag_instance.instance_id)
        
        # Return response
        return InstanceResponse(
            instance_id=dag_instance.instance_id,
            workflow_id=dag_instance.dag.dag_id,
            status=dag_instance.status.value,
            user_id=dag_instance.user_id,
            context=dag_instance.context,
            step_results={},  # Initialize empty step results
            current_step=dag_instance.current_task,
            created_at=dag_instance.created_at,
            updated_at=dag_instance.updated_at,
            completed_at=dag_instance.completed_at if dag_instance.status.value == "completed" else None
        )
        
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{instance_id}", response_model=InstanceResponse)
async def get_instance(
    instance_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get DAG instance details"""
    try:
        dag_instance = await workflow_service.get_instance(instance_id)
    except Exception as e:
        logger.error(f"Error getting instance {instance_id}: {e}")
        logger.error(f"Exception type: {type(e)}")
        logger.error(f"Exception details: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

    if not dag_instance:
        raise HTTPException(status_code=404, detail="Instance not found")

    # Check permissions - user can only see their own instances unless admin
    if dag_instance.user_id != str(current_user.get("sub")) and "admin" not in current_user.get("roles", []):
        raise HTTPException(status_code=403, detail="Access denied")
    
    return InstanceResponse(
        instance_id=dag_instance.instance_id,
        workflow_id=dag_instance.dag.dag_id,
        status=dag_instance.status if isinstance(dag_instance.status, str) else dag_instance.status.value,
        user_id=dag_instance.user_id,
        context=dag_instance.context,
        step_results={},  # Initialize empty step results
        current_step=dag_instance.current_task,
        created_at=dag_instance.created_at,
        updated_at=dag_instance.updated_at,
        completed_at=dag_instance.completed_at if dag_instance.status == "completed" else None
    )


@router.get("/my-assignments", response_model=InstanceListResponse)
async def get_my_assigned_instances(
    current_user: dict = Depends(get_current_user),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    assignment_status: Optional[str] = Query(None, description="Filter by assignment status"),
    workflow_id: Optional[str] = Query(None, description="Filter by workflow ID"),
    team_id: Optional[str] = Query(None, description="Filter by specific team (admin only)")
):
    """Get instances assigned to the current user or their teams"""
    
    # Build query based on user role and permissions
    query = {}
    
    # Admin and managers can see all assignments or filter by specific team
    if "admin" in current_user.get("roles", []):
        if team_id:
            # Filter by specific team if requested
            query["assigned_team_id"] = team_id
        else:
            # Show all assignments if no specific filter
            query["$or"] = [
                {"assigned_user_id": {"$exists": True}},
                {"assigned_team_id": {"$exists": True}}
            ]
    else:
        # Reviewers and other roles see only their assignments and team assignments
        user_teams = [str(team_id) for team_id in current_user.get("team_ids", [])] if current_user.get("team_ids", []) else []
        
        or_conditions = [
            {"assigned_user_id": str(current_user.get("sub"))}
        ]
        
        # Add team assignments if user belongs to teams
        if user_teams:
            or_conditions.append({"assigned_team_id": {"$in": user_teams}})
        
        query["$or"] = or_conditions
    
    # Apply additional filters
    if assignment_status:
        query["assignment_status"] = assignment_status
    
    if workflow_id:
        query["workflow_id"] = workflow_id
    
    # Execute query with pagination
    skip = (page - 1) * page_size
    
    instances = await WorkflowInstance.find(query).skip(skip).limit(page_size).to_list()
    total = await WorkflowInstance.find(query).count()
    
    # Convert to response format
    instance_responses = [convert_instance_to_response(instance) for instance in instances]
    
    return InstanceListResponse(
        instances=instance_responses,
        total=total,
        page=page,
        page_size=page_size
    )


@router.get("/assignment-statistics")
async def get_assignment_statistics_early(
    current_user: dict = Depends(get_current_user)
):
    """Get statistics about automatic vs manual assignments"""
    
    # Require admin or manager permission
    if "admin" not in current_user.get("roles", []):
        raise HTTPException(
            status_code=403, 
            detail="Only administrators and managers can view assignment statistics"
        )
    
    stats = await assignment_service.get_assignment_statistics()
    return stats


@router.get("/unassigned-instances")
async def get_unassigned_instances_early(
    workflow_id: Optional[str] = Query(None, description="Filter by workflow ID"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user)
):
    """Get list of unassigned instances that can be auto-assigned"""
    
    # Require admin or manager permission
    if "admin" not in current_user.get("roles", []):
        raise HTTPException(
            status_code=403, 
            detail="Only administrators and managers can view unassigned instances"
        )
    
    # Build query
    query = {
        "$or": [
            {"assignment_status": AssignmentStatus.UNASSIGNED},
            {"assignment_status": None}
        ]
    }
    
    if workflow_id:
        query["workflow_id"] = workflow_id
    
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


@router.post("/bulk-auto-assign")
async def bulk_auto_assign_instances_early(
    workflow_id: Optional[str] = None,
    limit: int = Query(10, ge=1, le=100, description="Maximum number of instances to assign"),
    current_user: dict = Depends(get_current_user)
):
    """Automatically assign multiple unassigned instances"""
    
    # Require admin or manager permission
    if "admin" not in current_user.get("roles", []):
        raise HTTPException(
            status_code=403, 
            detail="Only administrators and managers can trigger bulk auto-assignment"
        )
    
    # Build query for unassigned instances
    query = {
        "$or": [
            {"assignment_status": AssignmentStatus.UNASSIGNED},
            {"assignment_status": None}
        ]
    }
    
    if workflow_id:
        query["workflow_id"] = workflow_id
    
    # Get unassigned instances
    unassigned_instances = await WorkflowInstance.find(query).limit(limit).to_list()
    
    results = {
        "total_processed": 0,
        "successful_assignments": 0,
        "failed_assignments": 0,
        "assignments": []
    }
    
    for instance in unassigned_instances:
        results["total_processed"] += 1
        
        try:
            # Get workflow definition
            workflow_def = await WorkflowDefinition.find_one(
                WorkflowDefinition.workflow_id == instance.workflow_id
            )
            
            # Attempt auto-assignment
            success = await assignment_service.auto_assign_instance(instance, workflow_def)
            
            if success:
                results["successful_assignments"] += 1
                
                # Get updated assignment info
                updated_instance = await WorkflowInstance.find_one(
                    WorkflowInstance.instance_id == instance.instance_id
                )
                
                results["assignments"].append({
                    "instance_id": instance.instance_id,
                    "workflow_id": instance.workflow_id,
                    "success": True,
                    "assigned_to": {
                        "team_id": updated_instance.assigned_team_id,
                        "user_id": updated_instance.assigned_user_id
                    }
                })
            else:
                results["failed_assignments"] += 1
                results["assignments"].append({
                    "instance_id": instance.instance_id,
                    "workflow_id": instance.workflow_id,
                    "success": False,
                    "error": "No suitable assignment found"
                })
                
        except Exception as e:
            results["failed_assignments"] += 1
            results["assignments"].append({
                "instance_id": instance.instance_id,
                "workflow_id": instance.workflow_id,
                "success": False,
                "error": str(e)
            })
    
    return results


def convert_instance_to_response(instance: WorkflowInstance) -> InstanceResponse:
    """Convert internal WorkflowInstance to API response"""
    # Handle invalid status gracefully
    try:
        status = InstanceStatus(instance.status)
    except ValueError:
        # If status is invalid, default to FAILED 
        print(f"Warning: Invalid status '{instance.status}' for instance {instance.instance_id}, defaulting to FAILED")
        status = InstanceStatus.FAILED
    
    return InstanceResponse(
        instance_id=instance.instance_id,
        workflow_id=instance.workflow_id,
        user_id=instance.user_id,
        status=status,
        current_step=instance.current_step,
        context=instance.context,
        step_results=getattr(instance, 'step_results', {}),  # Default to empty dict if missing
        created_at=instance.created_at,
        updated_at=getattr(instance, 'updated_at', instance.created_at),  # Default to created_at if missing
        completed_at=instance.completed_at,
        # Assignment information
        assigned_user_id=instance.assigned_user_id,
        assigned_team_id=instance.assigned_team_id,
        assignment_status=instance.assignment_status.value if instance.assignment_status else None,
        assignment_type=instance.assignment_type.value if instance.assignment_type else None,
        assigned_at=instance.assigned_at,
        assigned_by=instance.assigned_by,
        assignment_notes=instance.assignment_notes
    )


# Removed execute_workflow_instance - using DAG executor directly now

@router.post("/{instance_id}/cancel")
async def cancel_instance(
    instance_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Cancel a running instance"""
    # Cancel through workflow service
    workflow_service.executor.cancel_instance(instance_id)
    return {"success": True, "message": "Instance cancelled"}


# Background task for auto-assignment
async def auto_assign_new_instance(instance_id: str, workflow_def: WorkflowDefinition):
    """Background task to automatically assign newly created instances"""
    try:
        # Small delay to ensure instance is fully created
        import asyncio
        await asyncio.sleep(2)
        
        # Get the instance
        instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
        if not instance:
            print(f"Instance {instance_id} not found for auto-assignment")
            return
        
        # Check if already assigned (manual assignment might have happened)
        if instance.assignment_status and instance.assignment_status != AssignmentStatus.UNASSIGNED:
            print(f"Instance {instance_id} already assigned, skipping auto-assignment")
            return
        
        # Attempt automatic assignment
        success = await assignment_service.auto_assign_instance(instance, workflow_def)
        
        if success:
            print(f"Successfully auto-assigned instance {instance_id}")
        else:
            print(f"Could not auto-assign instance {instance_id} - no suitable assignment found")
            
    except Exception as e:
        print(f"Error in auto_assign_new_instance for {instance_id}: {e}")


# Removed duplicate create_instance endpoint - using authenticated one above


@router.get("/", response_model=InstanceListResponse)
@router.get("", response_model=InstanceListResponse)
async def list_instances(
    current_user: dict = Depends(get_current_user),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    workflow_id: Optional[str] = None,
    user_id: Optional[str] = None,
    status: Optional[InstanceStatus] = None,
    instance_id: Optional[str] = Query(None, description="Search by instance ID (partial match)")
):
    """List workflow instances with filtering and pagination"""
    # Build query
    query = {}
    if workflow_id:
        query["workflow_id"] = workflow_id
    if user_id:
        query["user_id"] = {"$regex": user_id, "$options": "i"}
    if status:
        query["status"] = status
    if instance_id:
        query["instance_id"] = {"$regex": instance_id, "$options": "i"}
    
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
async def get_active_instances(
    current_user: dict = Depends(require_permission("VIEW_INSTANCES")),
    status: Optional[str] = Query(None, description="Filter by status"),
    user_id: Optional[str] = Query(None, description="Filter by user/citizen ID"),
    workflow_id: Optional[str] = Query(None, description="Filter by workflow ID"),
    instance_id: Optional[str] = Query(None, description="Search by instance ID (partial match)"),
    limit: int = Query(50, description="Number of instances to return"),
    offset: int = Query(0, description="Number of instances to skip")
):
    """Get workflow instances with optional filtering"""
    
    # Build query filters
    query_filters = {}
    
    if status:
        query_filters["status"] = status
    
    if user_id:
        query_filters["user_id"] = {"$regex": user_id, "$options": "i"}
    
    if workflow_id:
        query_filters["workflow_id"] = workflow_id
    
    if instance_id:
        query_filters["instance_id"] = {"$regex": instance_id, "$options": "i"}
    
    # Get all instances (not just active ones) with filters
    active_instances = await WorkflowInstance.find(
        query_filters
    ).sort(-WorkflowInstance.updated_at).skip(offset).limit(limit).to_list()
    
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


@router.get("/{instance_id}/logs")
async def get_instance_logs(
    instance_id: str,
    current_user: dict = Depends(require_permission("VIEW_INSTANCES")),
    level: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    group_by_step: bool = Query(False, description="Group logs by workflow step")
):
    """Get logs for a specific instance, optionally grouped by step"""
    from ...models.instance_log import InstanceLog, LogLevel, LogType
    
    # Verify instance exists
    instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    # Get logs
    logs = await InstanceLog.get_instance_logs(
        instance_id=instance_id,
        level=LogLevel(level) if level else None,
        limit=limit
    )
    
    if group_by_step:
        # Group logs by step
        grouped_logs = {}
        system_logs = []
        
        for log in logs:
            log_dict = log.to_display_dict()
            
            # Separate system logs from operator logs
            if log.task_id:
                if log.task_id not in grouped_logs:
                    grouped_logs[log.task_id] = {
                        "step_id": log.task_id,
                        "operator_logs": [],
                        "system_logs": []
                    }
                
                # Distinguish between system and operator logs
                if log.log_type in [LogType.TASK_START, LogType.TASK_COMPLETE, LogType.TASK_FAILED, LogType.TASK_WAITING]:
                    grouped_logs[log.task_id]["system_logs"].append(log_dict)
                else:
                    # These are operator-generated logs (more important)
                    grouped_logs[log.task_id]["operator_logs"].append(log_dict)
            else:
                # System-level logs without specific task
                system_logs.append(log_dict)
        
        return {
            "instance_id": instance_id,
            "grouped_logs": grouped_logs,
            "system_logs": system_logs,
            "total": len(logs)
        }
    else:
        return {
            "instance_id": instance_id,
            "logs": [log.to_display_dict() for log in logs],
            "total": len(logs)
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
    current_user: dict = Depends(require_permission("MANAGE_INSTANCES")),
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
        # Get workflow from DAG system for metadata
        dag = await workflow_service.get_dag(instance.workflow_id)
        if not dag:
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
    workflow_service.executor.resume_instance(instance_id)
    
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
        workflow_service.executor.resume_instance(approval.instance_id)
    
    return {
        "message": f"Approval {approval.decision} recorded",
        "instance_id": approval.instance_id,
        "step_id": approval.step_id
    }


@router.post("/approve-step")
async def approve_step_dag(
    approval: ApprovalRequest, 
    background_tasks: BackgroundTasks,
    current_user = Depends(get_current_user)
):
    """Submit approval decision for ApprovalOperator - integrates with DAG system"""
    # Set approver_id from authenticated user
    approval.approver_id = str(current_user.get("sub"))
    
    instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == approval.instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    try:
        # Validate decision
        valid_decisions = ['approved', 'rejected', 'request_changes', 'escalate']
        if approval.decision.lower() not in valid_decisions:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid approval decision: {approval.decision}. Must be one of: {', '.join(valid_decisions)}"
            )
        
        # Save approval decision to instance context
        instance.context = instance.context or {}
        instance.context[f"{approval.step_id}_decision"] = approval.decision
        instance.context[f"{approval.step_id}_decided_by"] = approval.approver_id
        instance.context[f"{approval.step_id}_decided_at"] = datetime.utcnow().isoformat()
        instance.context[f"{approval.step_id}_comments"] = approval.comments
        
        # Also try to feed the decision directly to the ApprovalOperator if possible
        try:
            dag = await workflow_service.get_dag(instance.workflow_id)
            if dag and approval.step_id in dag.tasks:
                current_task = dag.tasks[approval.step_id]
                if current_task.__class__.__name__ == 'ApprovalOperator':
                    # Map string decision to enum
                    from ...workflows.operators.approval import ApprovalDecision
                    decision_mapping = {
                        'approved': ApprovalDecision.APPROVED,
                        'rejected': ApprovalDecision.REJECTED,
                        'request_changes': ApprovalDecision.REQUEST_CHANGES,
                        'escalate': ApprovalDecision.ESCALATE
                    }
                    
                    decision_enum = decision_mapping.get(approval.decision.lower())
                    if decision_enum:
                        current_task.receive_decision(
                            decision=decision_enum,
                            decided_by=approval.approver_id,
                            comments=approval.comments
                        )
                        print(f"🔍 DEBUG: Decision fed to ApprovalOperator: {approval.decision}")
        except Exception as e:
            print(f"🔍 DEBUG: Could not feed decision to operator (will rely on context): {e}")
        
        # Update instance
        instance.updated_at = datetime.utcnow()
        await instance.save()
        
        # Create approval record for audit trail
        approval_record = ApprovalModel(
            approval_id=str(uuid.uuid4()),
            instance_id=approval.instance_id,
            step_id=approval.step_id,
            workflow_id=instance.workflow_id,
            title=f"Approval decision for step {approval.step_id}",
            decision=approval.decision,
            decision_reason=approval.comments,
            decided_by=approval.approver_id,
            status="completed",
            responded_at=datetime.utcnow()
        )
        await approval_record.create()
        
        # Update instance context with approval decision
        instance.context.update({
            f"{approval.step_id}_approval_decision": approval.decision,
            f"{approval.step_id}_approval_comments": approval.comments,
            f"{approval.step_id}_approver_id": approval.approver_id,
            f"{approval.step_id}_approval_timestamp": datetime.utcnow().isoformat()
        })
        await instance.save()
        
        # Resume workflow execution to continue processing
        workflow_service.executor.resume_instance(approval.instance_id)
        
        return {
            "message": f"Approval decision '{approval.decision}' processed successfully",
            "instance_id": approval.instance_id,
            "step_id": approval.step_id,
            "decision": approval.decision
        }
        
    except Exception as e:
        print(f"Error processing approval decision: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error processing approval decision: {str(e)}")


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


@router.post("/{instance_id}/validate")
async def validate_citizen_data(
    instance_id: str,
    validation_request: Dict[str, Any],
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(require_permission("MANAGE_INSTANCES"))
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
    decision = validation_request.get("decision")
    entity_type = validation_request.get("entity_type")
    comments = validation_request.get("comments", "")
    
    if decision not in ["approve", "reject", "request_changes"]:
        raise HTTPException(
            status_code=400,
            detail="Decision must be 'approve', 'reject', or 'request_changes'"
        )
    
    # Get workflow from DAG system
    dag = await workflow_service.get_dag(instance.workflow_id)
    if not dag:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    # Find which task needs validation (the current task requiring citizen input)
    current_task = None
    if instance.current_step and instance.current_step in dag.tasks:
        current_task = dag.tasks[instance.current_step]
    
    # Determine validation keys based on entity_type or step
    if entity_type:
        # Entity-specific validation
        step_validation_key = f"{entity_type}_validation_status"
        step_comments_key = f"{entity_type}_validation_comments"
        step_validator_key = f"{entity_type}_validated_by"
        step_timestamp_key = f"{entity_type}_validation_timestamp"
    elif current_step and hasattr(current_step, 'requires_citizen_input') and current_step.requires_citizen_input:
        # Step-specific validation
        step_validation_key = f"{current_step.step_id}_admin_validation_decision"
        step_comments_key = f"{current_step.step_id}_admin_validation_comments"
        step_validator_key = f"{current_step.step_id}_validated_by"
        step_timestamp_key = f"{current_step.step_id}_validation_timestamp"
    else:
        # Fall back to global validation
        step_validation_key = "admin_validation_decision"
        step_comments_key = "admin_validation_comments"
        step_validator_key = "validated_by"
        step_timestamp_key = "validation_timestamp"
    
    # Update instance with validation decision
    instance.context = instance.context or {}
    instance.context.update({
        step_validation_key: decision,
        step_comments_key: comments,
        step_validator_key: str(current_user.get("sub")),
        step_timestamp_key: datetime.utcnow().isoformat()
    })
    
    if decision == "approve":
        # INTEGRATE WITH REVIEW SYSTEM: Instead of direct approval, assign to reviewer
        from ...services.assignment_service import AssignmentService
        
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
        
        # Simple step completion logic - let each step handle its own validation
        print(f"🔍 Validation request: decision={validation_request.get('decision')}, current_step={instance.current_step}")
        
        if validation_request.get('decision') == 'approve':
            print(f"✅ Approving step: {instance.current_step}")
            # Step approved - execute the current step and advance to next
            current_step = workflow.steps.get(instance.current_step)
            if current_step:
                print(f"📝 Found step: {current_step.name}")
                try:
                    # Execute the step with current context
                    from ...workflows.executor import StepExecutionContext
                    execution_context = StepExecutionContext(
                        instance_id=instance.instance_id,
                        user_id=instance.user_id
                    )
                    
                    step_result = await step_executor.execute_step(
                        step=current_step,
                        inputs=instance.context,
                        context=instance.context,
                        execution_context=execution_context
                    )
                    print(f"🔧 Step executed, result type: {type(step_result)}")
                    
                    # Update context with step results
                    if isinstance(step_result, dict):
                        instance.context.update(step_result)
                        print(f"📊 Context updated with: {list(step_result.keys())}")
                    
                    # Mark step as completed
                    if instance.current_step not in instance.completed_steps:
                        instance.completed_steps.append(instance.current_step)
                    
                    # Advance to next step
                    if hasattr(current_step, 'next_steps') and current_step.next_steps:
                        next_step_id = current_step.next_steps[0].step_id if hasattr(current_step.next_steps[0], 'step_id') else current_step.next_steps[0]
                        instance.current_step = next_step_id
                        instance.status = "awaiting_input"
                        print(f"✅ Step completed, advancing to: {next_step_id}")
                    else:
                        # No next steps, workflow complete
                        instance.status = "completed"
                        instance.current_step = None
                        instance.completed_at = datetime.utcnow()
                        print(f"✅ Workflow completed")
                        
                except Exception as e:
                    print(f"❌ Error executing step: {str(e)}")
                    import traceback
                    traceback.print_exc()
                    instance.status = "failed"
            else:
                print(f"❌ Current step not found in workflow: {instance.current_step}")
                    
        elif validation_request.get('decision') == 'reject':
            # Step rejected - workflow failed
            instance.status = "failed"
            instance.current_step = None
            instance.completed_at = datetime.utcnow()
    
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
        outputs={"validation_result": decision, "validator": str(current_user.get("sub"))},
        started_at=datetime.utcnow(),
        completed_at=datetime.utcnow(),
        duration_seconds=0
    )
    await step_execution.create()
    
    # Continue workflow execution if approved and status allows it
    if decision == "approve" and instance.status in ["running", "awaiting_input"]:
        workflow_service.executor.resume_instance(instance_id)
    
    return {
        "success": True,
        "message": f"Citizen data {decision}d successfully",
        "instance_id": instance_id,
        "decision": decision,
        "next_status": instance.status,
        "validated_by": str(current_user.get("sub")),
        "validation_timestamp": datetime.utcnow().isoformat()
    }


@router.get("/{instance_id}/citizen-data")
async def get_citizen_data(
    instance_id: str,
    current_user: dict = Depends(require_permission("VIEW_INSTANCES"))
):
    """Get detailed citizen data for an instance"""
    from ...models.user import UserModel
    
    # Find the instance
    instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    # Get workflow from DAG system
    dag = await workflow_service.get_dag(instance.workflow_id)
    if not dag:
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


# Assignment management endpoints

@router.post("/{instance_id}/assign-to-user")
async def assign_instance_to_user(
    instance_id: str,
    request: Dict[str, Any],
    current_user: dict = Depends(require_permission("MANAGE_INSTANCES"))
):
    """Assign instance to a specific user"""
    
    # Get instance
    instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    # Get assignment data
    user_id = request.get("user_id")
    notes = request.get("notes")
    
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")
    
    # Verify target user exists
    target_user = None
    if not target_user:
        raise HTTPException(status_code=404, detail="Target user not found")
    
    # Check if instance can be assigned
    if not instance.can_be_assigned():
        raise HTTPException(
            status_code=400, 
            detail=f"Instance in status '{instance.status}' with assignment status '{instance.assignment_status}' cannot be assigned"
        )
    
    # Assign instance
    instance.assign_to_user(
        user_id=user_id,
        assigned_by=str(current_user.get("sub")),
        assignment_type=AssignmentType.MANUAL,
        notes=notes
    )
    
    await instance.save()
    
    return {
        "success": True,
        "message": f"Instance assigned to user {user_id}",
        "instance_id": instance_id,
        "assigned_to": {
            "user_id": user_id,
            "name": user_id,
            "email": ""
        },
        "assigned_by": current_user.get("name", current_user.get("username", "Unknown")),
        "assigned_at": instance.assigned_at.isoformat()
    }


@router.post("/{instance_id}/assign-to-team")
async def assign_instance_to_team(
    instance_id: str,
    request: Dict[str, Any],
    current_user: dict = Depends(require_permission("MANAGE_INSTANCES"))
):
    """Assign instance to a team"""
    
    # Get instance
    instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    # Get assignment data
    team_id = request.get("team_id")
    notes = request.get("notes")
    
    if not team_id:
        raise HTTPException(status_code=400, detail="team_id is required")
    
    # Verify target team exists
    target_team = await TeamModel.find_one(TeamModel.team_id == team_id)
    if not target_team:
        raise HTTPException(status_code=404, detail="Target team not found")
    
    # Check if instance can be assigned
    if not instance.can_be_assigned():
        raise HTTPException(
            status_code=400, 
            detail=f"Instance in status '{instance.status}' with assignment status '{instance.assignment_status}' cannot be assigned"
        )
    
    # Assign instance
    instance.assign_to_team(
        team_id=team_id,
        assigned_by=str(current_user.get("sub")),
        assignment_type=AssignmentType.MANUAL,
        notes=notes
    )
    
    await instance.save()
    
    return {
        "success": True,
        "message": f"Instance assigned to team {target_team.name}",
        "instance_id": instance_id,
        "assigned_to": {
            "team_id": team_id,
            "name": target_team.name,
            "members": len(target_team.members)
        },
        "assigned_by": current_user.get("name", current_user.get("username", "Unknown")),
        "assigned_at": instance.assigned_at.isoformat()
    }


@router.post("/{instance_id}/start-review")
async def start_review_on_instance(
    instance_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Start reviewing an assigned instance"""
    
    # Get instance
    instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    user_id = str(current_user.get("sub"))
    
    # Check if user can review this instance (directly assigned, team member, or admin/manager)
    can_review = False
    
    if instance.assigned_user_id == user_id:
        can_review = True
    elif instance.assigned_team_id and current_user.get("team_ids", []) and instance.assigned_team_id in [str(t) for t in current_user.get("team_ids", [])]:
        can_review = True
    elif "admin" in current_user.get("roles", []):
        can_review = True
    
    if not can_review:
        raise HTTPException(
            status_code=403, 
            detail="You are not authorized to review this instance"
        )
    
    # Start review
    if not instance.start_review(user_id):
        raise HTTPException(
            status_code=400, 
            detail=f"Cannot start review on instance in status '{instance.assignment_status}'"
        )
    
    await instance.save()
    
    return {
        "success": True,
        "message": "Review started on instance",
        "instance_id": instance_id,
        "assignment_status": instance.assignment_status.value,
        "reviewer": current_user.get("name", current_user.get("username", "Unknown")),
        "updated_at": instance.updated_at.isoformat()
    }


@router.post("/{instance_id}/approve-by-reviewer")
async def approve_instance_by_reviewer(
    instance_id: str,
    request: Optional[Dict[str, Any]] = None,
    current_user: dict = Depends(get_current_user)
):
    """Reviewer approves instance - sends to approver for final signature"""
    
    # Get instance
    instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    user_id = str(current_user.get("sub"))
    
    # Check if user can approve (must be the reviewer or admin/manager)
    can_approve = False
    
    if instance.reviewed_by == user_id:
        can_approve = True
    elif "admin" in current_user.get("roles", []):
        can_approve = True
    
    if not can_approve:
        raise HTTPException(
            status_code=403, 
            detail="You are not authorized to approve this instance"
        )
    
    # Get approval comments
    comments = None
    if request:
        comments = request.get("comments")
    
    # Approve by reviewer
    if not instance.approve_by_reviewer(user_id, comments):
        raise HTTPException(
            status_code=400, 
            detail=f"Cannot approve instance in current status '{instance.assignment_status}'"
        )
    
    await instance.save()
    
    # INTEGRATION: Continue workflow after reviewer approval
    # The instance is now approved_by_reviewer and needs final signature
    try:
        # Resume workflow execution for next steps (like signature/final approval)
        # Check if there are more steps in the workflow to execute
        dag = await workflow_service.get_dag(instance.workflow_id)
        if dag and instance.current_step:
            current_task = dag.tasks.get(instance.current_step)
            
            # If current task has downstream tasks, continue workflow execution
            if current_task and hasattr(current_task, 'downstream_tasks') and current_task.downstream_tasks:
                # Update status to indicate it's ready for next stage (final approval)
                instance.assignment_status = AssignmentStatus.PENDING_SIGNATURE
                instance.status = "running"
                await instance.save()
                
                # Execute next workflow step asynchronously
                workflow_service.executor.resume_instance(instance_id)
                
        print(f"Instance {instance_id} approved by reviewer - workflow execution continued")
    except Exception as e:
        print(f"Warning: Post-approval processing failed: {e}")
        # Continue even if workflow execution fails - the approval is still recorded
    
    return {
        "success": True,
        "message": "Instance approved by reviewer - forwarded for final approval",
        "instance_id": instance_id,
        "assignment_status": instance.assignment_status.value,
        "approved_by": current_user.get("name", current_user.get("username", "Unknown")),
        "comments": comments,
        "updated_at": instance.updated_at.isoformat()
    }


@router.post("/{instance_id}/reject-by-reviewer")
async def reject_instance_by_reviewer(
    instance_id: str,
    request: Dict[str, Any],
    current_user: dict = Depends(get_current_user)
):
    """Reviewer rejects instance with reason"""
    
    # Get instance
    instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    user_id = str(current_user.get("sub"))
    
    # Check if user can reject (must be the reviewer or admin/manager)
    can_reject = False
    
    if instance.reviewed_by == user_id:
        can_reject = True
    elif "admin" in current_user.get("roles", []):
        can_reject = True
    
    if not can_reject:
        raise HTTPException(
            status_code=403, 
            detail="You are not authorized to reject this instance"
        )
    
    # Get rejection reason and comments
    reason = request.get("reason")
    comments = request.get("comments")
    
    if not reason:
        raise HTTPException(status_code=400, detail="Rejection reason is required")
    
    # Reject by reviewer
    if not instance.reject_by_reviewer(user_id, reason, comments):
        raise HTTPException(
            status_code=400, 
            detail=f"Cannot reject instance in current status '{instance.assignment_status}'"
        )
    
    await instance.save()
    
    return {
        "success": True,
        "message": "Instance rejected by reviewer",
        "instance_id": instance_id,
        "assignment_status": instance.assignment_status.value,
        "rejected_by": current_user.get("name", current_user.get("username", "Unknown")),
        "reason": reason,
        "comments": comments,
        "updated_at": instance.updated_at.isoformat()
    }


@router.post("/{instance_id}/request-modifications")
async def request_modifications_by_reviewer(
    instance_id: str,
    request: Dict[str, Any],
    current_user: dict = Depends(get_current_user)
):
    """Reviewer requests modifications from citizen"""
    
    # Get instance
    instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    user_id = str(current_user.get("sub"))
    
    # Check if user can request modifications (must be the reviewer or admin/manager)
    can_request = False
    
    if instance.reviewed_by == user_id:
        can_request = True
    elif "admin" in current_user.get("roles", []):
        can_request = True
    
    if not can_request:
        raise HTTPException(
            status_code=403, 
            detail="You are not authorized to request modifications for this instance"
        )
    
    # Get modification requests and comments
    modifications = request.get("modifications", [])
    comments = request.get("comments")
    
    if not modifications:
        raise HTTPException(status_code=400, detail="At least one modification request is required")
    
    # Request modifications
    if not instance.request_modifications(user_id, modifications, comments):
        raise HTTPException(
            status_code=400, 
            detail=f"Cannot request modifications for instance in current status '{instance.assignment_status}'"
        )
    
    await instance.save()
    
    return {
        "success": True,
        "message": "Modifications requested from citizen",
        "instance_id": instance_id,
        "assignment_status": instance.assignment_status.value,
        "requested_by": current_user.get("name", current_user.get("username", "Unknown")),
        "modifications": modifications,
        "comments": comments,
        "updated_at": instance.updated_at.isoformat()
    }


@router.post("/{instance_id}/final-approval")
async def give_final_approval(
    instance_id: str,
    request: Optional[Dict[str, Any]] = None,
    current_user: dict = Depends(get_current_user)
):
    """Final approval and signature by manager/approver"""
    
    # Get instance
    instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    # Check if user can give final approval (managers and approvers)
    can_approve = "admin" in current_user.get("roles", [])
    
    if not can_approve:
        raise HTTPException(
            status_code=403, 
            detail="You are not authorized to give final approval"
        )
    
    user_id = str(current_user.get("sub"))
    
    # Get approval comments
    comments = None
    if request:
        comments = request.get("comments")
    
    # Give final approval
    if not instance.final_approval(user_id, comments):
        raise HTTPException(
            status_code=400, 
            detail=f"Cannot give final approval for instance in current status '{instance.assignment_status}'"
        )
    
    await instance.save()
    
    return {
        "success": True,
        "message": "Instance approved and signed - process completed",
        "instance_id": instance_id,
        "assignment_status": instance.assignment_status.value,
        "approved_by": current_user.get("name", current_user.get("username", "Unknown")),
        "comments": comments,
        "updated_at": instance.updated_at.isoformat()
    }


@router.post("/{instance_id}/unassign")
async def unassign_instance(
    instance_id: str,
    request: Optional[Dict[str, Any]] = None,
    current_user: dict = Depends(require_permission("MANAGE_INSTANCES"))
):
    """Remove assignment from instance"""
    
    # Get instance
    instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    # Get reason
    reason = "manual_unassignment"
    if request:
        reason = request.get("reason", reason)
    
    # Unassign
    instance.unassign(reason=reason, unassigned_by=str(current_user.get("sub")))
    await instance.save()
    
    return {
        "success": True,
        "message": "Instance unassigned successfully",
        "instance_id": instance_id,
        "unassigned_by": current_user.get("name", current_user.get("username", "Unknown")),
        "reason": reason,
        "updated_at": instance.updated_at.isoformat()
    }


@router.post("/{instance_id}/auto-assign")
async def auto_assign_instance(
    instance_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Automatically assign an instance to the best available team/user"""
    
    # Require admin or manager permission
    if "admin" not in current_user.get("roles", []):
        raise HTTPException(
            status_code=403, 
            detail="Only administrators and managers can trigger auto-assignment"
        )
    
    # Get the instance
    instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    # Check if already assigned
    if instance.assignment_status not in [AssignmentStatus.UNASSIGNED, None]:
        raise HTTPException(
            status_code=400, 
            detail=f"Instance is already {instance.assignment_status}. Unassign first if needed."
        )
    
    # Get workflow definition for context
    workflow_def = await WorkflowDefinition.find_one(
        WorkflowDefinition.workflow_id == instance.workflow_id
    )
    
    # Perform auto-assignment
    success = await assignment_service.auto_assign_instance(instance, workflow_def)
    
    if success:
        # Refresh instance to get updated assignment data
        updated_instance = await WorkflowInstance.find_one(
            WorkflowInstance.instance_id == instance_id
        )
        
        return {
            "success": True,
            "message": "Instance assigned automatically",
            "instance_id": instance_id,
            "assigned_to": {
                "team_id": updated_instance.assigned_team_id,
                "user_id": updated_instance.assigned_user_id,
                "assignment_type": updated_instance.assignment_type,
                "assigned_at": updated_instance.assigned_at.isoformat() if updated_instance.assigned_at else None
            },
            "triggered_by": current_user.get("name", current_user.get("username", "Unknown"))
        }
    else:
        raise HTTPException(
            status_code=400, 
            detail="Could not find suitable assignment for this instance"
        )


@router.post("/{instance_id}/validate-data")
async def validate_instance_data(
    instance_id: str,
    request: dict,
    current_user: dict = Depends(get_current_user)
):
    """Validate individual fields of submitted citizen data"""
    
    # Get the instance
    instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    # Verify user has permission to validate this instance
    # Must be assigned to the user or user's team, or user must be admin/manager
    can_validate = False
    
    if "admin" in current_user.get("roles", []):
        can_validate = True
    elif instance.assigned_user_id == str(current_user.get("sub")):
        can_validate = True
    elif instance.assigned_team_id and current_user.get("team_ids", []) and instance.assigned_team_id in [str(tid) for tid in current_user.get("team_ids", [])]:
        can_validate = True
    
    if not can_validate:
        raise HTTPException(
            status_code=403, 
            detail="You do not have permission to validate this instance"
        )
    
    try:
        field_validations = request.get("field_validations", {})
        overall_status = request.get("overall_status", "pending")
        validation_summary = request.get("validation_summary", "")
        
        # Store validation results in the instance context
        if not instance.context:
            instance.context = {}
        
        instance.context["field_validations"] = field_validations
        instance.context["validation_summary"] = validation_summary
        instance.context["validated_by"] = str(current_user.get("sub"))
        instance.context["validated_at"] = datetime.utcnow().isoformat()
        
        # Update assignment status based on validation result
        if overall_status == "approved":
            instance.assignment_status = AssignmentStatus.APPROVED_BY_REVIEWER
        elif overall_status == "rejected":
            instance.assignment_status = AssignmentStatus.REJECTED
        else:
            instance.assignment_status = AssignmentStatus.UNDER_REVIEW
        
        instance.updated_at = datetime.utcnow()
        await instance.save()
        
        return {
            "success": True,
            "message": f"Data validation completed: {overall_status}",
            "instance_id": instance_id,
            "validation_summary": validation_summary,
            "validated_by": current_user.get("name", current_user.get("username", "Unknown")),
            "assignment_status": instance.assignment_status
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to save validation results: {str(e)}"
        )