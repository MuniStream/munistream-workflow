from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Query, BackgroundTasks, Depends, Request
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
    AssignmentType,
    EventType,
)
from ...core.database import get_database
from ...workflows.dag import DAGInstance, InstanceStatus
from ...services.workflow_service import workflow_service
from ...services.entity_service import EntityService
from ...models.legal_entity import LegalEntity, EntityType
from ...auth.provider import require_permission, get_current_user
from ...services.assignment_service import assignment_service
from ...services.entity_serialization import slim_entity_data, describe_blobs
from ...services.instance_attachments import find_attachment
from ...services.instance_dossier import build_admin_detail, entity_ids_in_use
from ...models.team import TeamModel

router = APIRouter()
logger = logging.getLogger(__name__)


async def check_instance_access(instance: WorkflowInstance, current_user: dict) -> bool:
    """
    Check if current user has access to view/modify a specific instance based on their role and assignment.

    Access rules:
    - admin: can access all instances
    - manager: can access instances assigned to their teams
    - reviewer/approver: can access instances directly assigned to them only
    - viewer: can access instances directly assigned to them only
    """
    user_roles = current_user.get("roles", [])
    user_id = current_user.get("sub")
    user_teams = current_user.get("teams", [])

    # Admin has access to everything
    if "admin" in user_roles:
        return True

    # Check if instance is assigned to the user directly
    if instance.assigned_user_id == user_id:
        return True

    # Manager: check if instance is assigned to their team
    if "manager" in user_roles:
        if instance.assigned_team_id and instance.assigned_team_id in user_teams:
            return True

    # Reviewer, approver, and viewer: only direct assignments (already checked above)
    # If we reach here, access is denied
    return False


async def require_instance_access(instance_id: str, current_user: dict = Depends(require_permission("VIEW_INSTANCES"))) -> WorkflowInstance:
    """
    Dependency that ensures user has access to the specified instance.
    Returns the instance if access is granted, raises HTTPException otherwise.
    """
    # Get the instance
    instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")

    # Check access
    if not await check_instance_access(instance, current_user):
        raise HTTPException(
            status_code=403,
            detail="Access denied. You can only view instances assigned to you or your team."
        )

    return instance


async def _find_missing_entity_prerequisites(workflow_id: str, user_id: str) -> List[Dict[str, Any]]:
    """Prerequisite entities the user is missing to start ``workflow_id``.

    Inspects the workflow's EntityPicker requirements (``min_count > 0``) and
    verifies the user already owns enough matching entities. Used to block
    starting a trámite that would immediately dead-end at the entity picker
    (e.g. an aviso de cosecha without a permiso de acuacultura). Returns an
    empty list when every mandatory prerequisite is satisfied.
    """
    dag = await workflow_service.get_dag(workflow_id)
    if not dag:
        return []
    tasks = dag.tasks.values() if isinstance(dag.tasks, dict) else dag.tasks
    missing: List[Dict[str, Any]] = []
    for task in tasks:
        requirements = getattr(task, "requirements", None)
        if not isinstance(requirements, list):
            continue
        for req in requirements:
            if not isinstance(req, dict) or not req.get("entity_type"):
                continue
            min_count = req.get("min_count", 1)
            if not min_count or min_count <= 0:
                continue  # optional prerequisite — never blocks starting
            entities = await EntityService.find_entities(
                owner_user_id=user_id,
                entity_type=req["entity_type"],
                filters=req.get("filters", {}) or {},
            )
            if len(entities) < min_count:
                info = req.get("info", {}) or {}
                prereq_workflow = info.get("workflow_id")
                missing.append({
                    "entity_type": req["entity_type"],
                    "display_name": info.get("display_name") or req.get("display_title") or req["entity_type"],
                    "required": min_count,
                    "found": len(entities),
                    "workflow_id": prereq_workflow,
                    "action_url": f"/services/{prereq_workflow}" if prereq_workflow else None,
                })
    return missing


@router.post("/", response_model=InstanceResponse)
async def create_workflow_instance(
    request: WorkflowExecuteRequest,
    current_user: dict = Depends(get_current_user)
):
    """Create and execute a new workflow instance using DAG architecture"""
    try:
        user_id = str(current_user.get("sub"))

        # Allow admin/manager to create on behalf of another user
        if request.on_behalf_of_user_id:
            user_roles = current_user.get("roles", [])
            if "admin" not in user_roles and "manager" not in user_roles:
                raise HTTPException(status_code=403, detail="Only admin/manager can create instances on behalf of another user")
            user_id = request.on_behalf_of_user_id

        # Block creation when the citizen lacks a required prerequisite entity
        # (e.g. a permiso de acuacultura for an aviso de cosecha). Without this
        # the instance would start and immediately dead-end at the entity picker.
        missing_prereqs = await _find_missing_entity_prerequisites(request.workflow_id, user_id)
        if missing_prereqs:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "missing_requirements",
                    "message": "Faltan requisitos para iniciar este trámite.",
                    "missing": missing_prereqs,
                },
            )

        # Create DAG instance
        initial_data = request.initial_context or {}
        if request.on_behalf_of_user_id:
            initial_data["created_by_admin"] = str(current_user.get("sub"))

        dag_instance = await workflow_service.create_instance(
            workflow_id=request.workflow_id,
            user_id=user_id,
            initial_data=initial_data
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
            step_results={},
            current_step=dag_instance.current_task,
            created_at=dag_instance.created_at,
            updated_at=dag_instance.updated_at,
            completed_at=dag_instance.completed_at if dag_instance.status.value == "completed" else None
        )

    except HTTPException:
        # Preserve structured errors (e.g. 409 missing prerequisites) instead of
        # collapsing them into a generic 400 below.
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


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


@router.get("/analytics/bottlenecks", response_model=BottleneckAnalysisResponse)
async def get_bottleneck_analysis(
    current_user: dict = Depends(require_permission("VIEW_INSTANCES")),
):
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
@router.put("/{instance_id}", response_model=InstanceResponse)
async def update_instance(
    update_data: InstanceUpdateRequest,
    instance: WorkflowInstance = Depends(require_instance_access),
    current_user: dict = Depends(get_current_user),
):
    """Update instance status or context"""
    if update_data.status is not None:
        instance.status = update_data.status
    
    if update_data.context_updates is not None:
        instance.context.update(update_data.context_updates)
    
    instance.updated_at = datetime.utcnow()
    await instance.save()
    
    return convert_instance_to_response(instance)


@router.post("/{instance_id}/pause")
async def pause_instance(
    instance: WorkflowInstance = Depends(require_instance_access),
    current_user: dict = Depends(get_current_user),
):
    """Pause a running workflow instance"""
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
async def resume_instance(
    background_tasks: BackgroundTasks,
    instance: WorkflowInstance = Depends(require_instance_access),
    current_user: dict = Depends(get_current_user),
):
    """Resume a paused workflow instance"""
    instance_id = instance.instance_id
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
async def approve_step(
    approval: ApprovalRequest,
    background_tasks: BackgroundTasks,
    instance: WorkflowInstance = Depends(require_instance_access),
    current_user: dict = Depends(get_current_user),
):
    """Submit approval decision for a workflow step"""
    # La instancia es la de la ruta (la que autorizo `require_instance_access`),
    # no la que venga en el cuerpo: si no, el permiso se comprueba sobre una y
    # se actua sobre otra. Y el aprobador sale del token, nunca del JSON.
    approval.instance_id = instance.instance_id
    approval.approver_id = str(current_user.get("sub"))

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


# NOTA DE ORDEN: `/{instance_id}` captura cualquier ruta de un solo segmento,
# asi que debe declararse DESPUES de todas las rutas estaticas del router
# (/my-assignments, /assignment-statistics, /unassigned-instances, /active,
# /citizen-validations). Estaba declarada arriba del todo y se comia las cinco:
# devolvian 404 'Instance not found' en vez de su propia respuesta.
@router.get("/{instance_id}", response_model=InstanceResponse)
async def get_instance(
    db_instance: WorkflowInstance = Depends(require_instance_access),
):
    """Get DAG instance details with role-based access control"""
    instance_id = db_instance.instance_id
    try:
        dag_instance = await workflow_service.get_instance(instance_id)
    except Exception as e:
        logger.error(f"Error getting instance {instance_id}: {e}")
        logger.error(f"Exception type: {type(e)}")
        logger.error(f"Exception details: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

    if not dag_instance:
        raise HTTPException(status_code=404, detail="Instance not found")

    # La autorizacion ya la resolvio require_instance_access (admin, asignado
    # directo, o manager del equipo asignado). No se compara el user_id contra
    # el sub del token: para tramites ciudadanos user_id es el id del Customer
    # y sub es el id de staff en Keycloak, asi que esa comparacion dejaba fuera
    # a todo el personal no-admin aunque tuviera la instancia asignada.
    response = convert_instance_to_response(db_instance)

    # El estado vivo del DAG es mas fresco que el documento persistido.
    live_status = (
        dag_instance.status
        if isinstance(dag_instance.status, str)
        else dag_instance.status.value
    )
    try:
        response.status = InstanceStatus(live_status)
    except ValueError:
        pass  # estado no reconocido: se conserva el persistido

    response.context = dag_instance.context
    response.current_step = dag_instance.current_task
    if live_status == "completed" and dag_instance.completed_at:
        response.completed_at = dag_instance.completed_at

    return response



@router.get("/{instance_id}/history", response_model=InstanceHistoryResponse)
async def get_instance_history(instance: WorkflowInstance = Depends(require_instance_access)):
    """Get execution history of a workflow instance with role-based access control"""
    instance_id = instance.instance_id
    
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
async def get_instance_progress(instance: WorkflowInstance = Depends(require_instance_access)):
    """Get detailed progress information for a workflow instance with role-based access control"""
    instance_id = instance.instance_id
    
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


@router.get("/{instance_id}/track")
async def track_instance_for_admin(
    db_instance: WorkflowInstance = Depends(require_instance_access)
):
    """
    Track workflow instance status with role-based access control.
    Returns current state and any required actions.

    Access control:
    - Admin: can track all instances
    - Manager: can track instances assigned to their teams
    - Reviewer/Approver: can track instances directly assigned to them
    - Viewer: can track instances directly assigned to them
    """
    instance_id = db_instance.instance_id

    # Get DAG instance for detailed state
    dag_instance = await workflow_service.get_instance(instance_id)

    # Check if waiting for input - Current step only
    requires_input = False
    input_form = {}
    waiting_for = None

    if dag_instance:
        current_step = db_instance.current_step

        # Check if instance is paused and waiting for input
        if db_instance.status == "paused" and dag_instance.context.get("waiting_for"):
            requires_input = True
            waiting_for = dag_instance.context.get("waiting_for")
            if dag_instance.context.get("form_config"):
                input_form = dag_instance.context["form_config"]

        # Also check current step state for waiting status
        if current_step and current_step in dag_instance.task_states:
            state = dag_instance.task_states[current_step]

            if state.get("status") == "waiting":
                requires_input = True
                waiting_for = (
                    state.get("waiting_for") or
                    state.get("output_data", {}).get("waiting_for") or
                    dag_instance.context.get("waiting_for") or
                    None
                )

                # Get form from task output_data or context fallback
                if state.get("output_data", {}).get("form_config"):
                    input_form = state["output_data"]["form_config"]
                elif dag_instance.context.get("form_config"):
                    # Fallback to context form_config for signature forms
                    input_form = dag_instance.context["form_config"]


    # Calculate progress
    total_steps = len(dag_instance.dag.tasks) if dag_instance and dag_instance.dag else 0
    completed_steps = 0
    step_progress = []

    if dag_instance:
        for task_id, state in dag_instance.task_states.items():
            status_val = state.get("status", "pending")
            if status_val == "completed":
                completed_steps += 1

            task_obj = dag_instance.dag.tasks.get(task_id)
            task_name = getattr(task_obj, 'name', None) or task_id.replace("_", " ").title()
            task_group = getattr(task_obj, 'group', None)

            step_info = {
                "step_id": task_id,
                "name": task_name,
                "description": f"Step {task_id}",
                "status": status_val,
                "started_at": state.get("started_at"),
                "completed_at": state.get("completed_at")
            }
            if task_group:
                step_info["group"] = task_group
            step_progress.append(step_info)

    progress_percentage = (completed_steps / total_steps * 100) if total_steps > 0 else 0

    # Get workflow info
    workflow = await workflow_service.get_workflow_definition(db_instance.workflow_id)
    workflow_name = workflow.name if workflow else db_instance.workflow_id

    return {
        "instance_id": instance_id,
        "workflow_id": db_instance.workflow_id,
        "workflow_name": workflow_name,
        "status": db_instance.status,
        "progress_percentage": progress_percentage,
        "current_step": db_instance.current_step,
        "created_at": db_instance.created_at.isoformat() if db_instance.created_at else None,
        "updated_at": db_instance.updated_at.isoformat() if db_instance.updated_at else None,
        "completed_at": db_instance.completed_at.isoformat() if db_instance.completed_at else None,
        "total_steps": total_steps,
        "completed_steps": completed_steps,
        "step_progress": step_progress,
        "requires_input": requires_input,
        "input_form": input_form,
        "waiting_for": waiting_for,
        "estimated_completion": None,  # Could calculate based on average step time
        "message": f"Workflow {db_instance.status}"
    }


@router.post("/{instance_id}/start")
async def start_workflow_instance(
    db_instance: WorkflowInstance = Depends(require_instance_access),
    current_user: dict = Depends(get_current_user)
):
    """
    Start a workflow instance that is waiting to be started with role-based access control.
    Used by admin workflows that need manual initiation.

    Access control:
    - Admin: can start all instances
    - Manager: can start instances assigned to their teams
    - Reviewer/Approver: can start instances directly assigned to them
    - Viewer: can start instances directly assigned to them
    """
    instance_id = db_instance.instance_id

    # Check if instance can be started
    if db_instance.status not in ["waiting_for_start", "pending_assignment", "paused"]:
        raise HTTPException(
            status_code=400,
            detail=f"Instance status '{db_instance.status}' cannot be started. Must be 'waiting_for_start', 'pending_assignment', or 'paused'."
        )

    try:
        # Get DAG instance
        dag_instance = await workflow_service.get_instance(instance_id)
        if not dag_instance:
            raise HTTPException(status_code=404, detail="Instance not found in workflow system")

        # Store original status before updating
        original_status = db_instance.status

        # Update instance status to running
        db_instance.status = "running"
        db_instance.started_at = datetime.utcnow()
        db_instance.updated_at = datetime.utcnow()
        await db_instance.save()

        # Start/resume the workflow execution based on original status
        if original_status in ["waiting_for_start", "pending_assignment"]:
            # Start fresh execution
            await workflow_service.execute_instance(instance_id)
        elif original_status == "paused":
            # Resume paused execution
            workflow_service.executor.resume_instance(instance_id)

        return {
            "success": True,
            "message": f"Workflow instance {instance_id} started successfully",
            "instance_id": instance_id,
            "status": "running",
            "started_by": current_user.get("sub"),
            "started_at": db_instance.started_at.isoformat()
        }

    except Exception as e:
        logger.error(f"Error starting workflow instance {instance_id}: {e}", exc_info=True)
        error_detail = str(e) if str(e) else f"Unknown error of type {type(e).__name__}"
        raise HTTPException(status_code=500, detail=f"Failed to start workflow instance: {error_detail}")


@router.post("/{instance_id}/submit-data")
async def submit_workflow_data(
    request: Request,
    db_instance: WorkflowInstance = Depends(require_instance_access),
    current_user: dict = Depends(get_current_user)
):
    """
    Submit data for a workflow waiting for input with role-based access control.
    Used by admin workflows that need data input.

    Access control:
    - Admin: can submit data for all instances
    - Manager: can submit data for instances assigned to their teams
    - Reviewer/Approver: can submit data for instances directly assigned to them
    - Viewer: can submit data for instances directly assigned to them
    """
    instance_id = db_instance.instance_id

    # Get DAG instance
    dag_instance = await workflow_service.get_instance(instance_id)
    if not dag_instance:
        raise HTTPException(status_code=404, detail="Instance not found in workflow system")

    # Find waiting task
    waiting_task = None
    for task_id, state in dag_instance.task_states.items():
        if state.get("status") == "waiting":
            waiting_task = task_id
            break

    if not waiting_task:
        raise HTTPException(status_code=400, detail="Instance is not waiting for input")

    # Get submitted data - handle both JSON and FormData
    content_type = request.headers.get("content-type", "")

    if "multipart/form-data" in content_type:
        # Handle FormData — los files se suben a S3 y solo guardamos la
        # referencia en context (ver app/services/s3_storage.py).
        from app.services import s3_storage
        form = await request.form()
        data = {}
        for key, value in form.items():
            if hasattr(value, 'filename'):
                file_content = await value.read()
                data[key] = s3_storage.upload_pending_file(
                    instance_id=instance_id,
                    task_id=waiting_task,
                    field_name=key,
                    filename=value.filename,
                    content_type=value.content_type,
                    file_content=file_content,
                )
            else:
                data[key] = value
    else:
        # Handle JSON
        data = await request.json()

    # Update BOTH the DAG instance and database with input
    dag_instance.context[f"{waiting_task}_input"] = data
    dag_instance.context[f"{waiting_task}_submitted_at"] = datetime.utcnow().isoformat()

    # Change status from paused to running to avoid delays
    if db_instance.status == "paused":
        db_instance.status = "running"

    # Save to database
    db_instance.context = dag_instance.context
    db_instance.updated_at = datetime.utcnow()
    await db_instance.save()

    # Resume execution - the DAG instance now has the data in context
    workflow_service.executor.resume_instance(instance_id)

    return {
        "success": True,
        "message": "Data submitted successfully",
        "instance_id": instance_id,
        "submitted_by": current_user.get("sub"),
        "submitted_at": datetime.utcnow().isoformat()
    }
@router.post("/{instance_id}/assign-to-user")
async def assign_instance_to_user(
    request: Dict[str, Any],
    instance: WorkflowInstance = Depends(require_instance_access),
    current_user: dict = Depends(get_current_user)
):
    """Assign instance to a specific user with role-based access control"""

    instance_id = instance.instance_id
    
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
    request: Dict[str, Any],
    instance: WorkflowInstance = Depends(require_instance_access),
    current_user: dict = Depends(get_current_user)
):
    """Assign instance to a team with role-based access control"""

    instance_id = instance.instance_id
    
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

    try:
        await workflow_service.executor.event_manager.publish_event(
            event_type=EventType.MODIFICATION_REQUESTED,
            workflow_id=instance.workflow_id,
            instance_id=instance_id,
            user_id=instance.user_id,
            event_data={
                "current_step": instance.current_step,
                "modifications": modifications,
                "comments": comments,
                "requested_by": user_id,
            },
        )
    except Exception:
        logger.exception(
            "Failed to publish MODIFICATION_REQUESTED for instance %s", instance_id
        )

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
    request: Optional[Dict[str, Any]] = None,
    instance: WorkflowInstance = Depends(require_instance_access),
    current_user: dict = Depends(get_current_user)
):
    """Remove assignment from instance with role-based access control"""

    instance_id = instance.instance_id
    
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
@router.get("/{instance_id}/admin-detail")
async def get_instance_admin_detail(
    db_instance: WorkflowInstance = Depends(require_instance_access),
):
    """Identidad del ciudadano, contexto curado y adjuntos, en una sola llamada.

    Se sirve agregado en vez de componer los endpoints existentes porque el
    encabezado de la vista tiene que pintar de una sola vez: repartirlo en
    varias llamadas hace que la identidad aparezca despues del resto.
    """
    dag = await workflow_service.get_dag(db_instance.workflow_id)
    return await build_admin_detail(db_instance, dag)


@router.get("/{instance_id}/citizen-entities")
async def get_instance_citizen_entities(
    db_instance: WorkflowInstance = Depends(require_instance_access),
    entity_type: Optional[str] = Query(None, description="Filtrar por tipo de entidad"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """Cartera completa del ciudadano dueno del tramite.

    El dueno se resuelve en el servidor a partir de la instancia, de modo que la
    llamada no depende de conocer el user_id y hereda el control de acceso del
    tramite.

    El `data` va recortado: las entidades legadas embeben imagenes en base64 de
    varios MB y aqui solo se pintan tarjetas.
    """
    owner_id = db_instance.user_id

    entities = await EntityService.find_entities(
        owner_user_id=owner_id,
        entity_type=entity_type,
        skip=skip,
        limit=limit,
    )

    total = await LegalEntity.find(LegalEntity.owner_user_id == owner_id).count()

    # Tipos de entidad para el icono/color de cada tarjeta. Se piden en bloque:
    # el listado del portal hace una consulta por entidad dentro del bucle y eso
    # es justo lo que no queremos repetir aqui.
    type_ids = {e.entity_type for e in entities if e.entity_type}
    type_map = {}
    if type_ids:
        for et in await EntityType.find({"type_id": {"$in": list(type_ids)}}).to_list():
            type_map[et.type_id] = et

    in_use = set(entity_ids_in_use(db_instance, {e.entity_id for e in entities}))

    items = []
    for e in entities:
        et = type_map.get(e.entity_type)
        items.append({
            "entity_id": e.entity_id,
            "entity_type": e.entity_type,
            "entity_type_label": getattr(et, "name", None) or e.entity_type,
            "entity_type_icon": getattr(et, "icon", None),
            "entity_type_color": getattr(et, "color", None),
            "name": e.name,
            "status": e.status,
            "verified": e.verified,
            "data": slim_entity_data(e.data),
            "created_at": e.created_at,
            "updated_at": e.updated_at,
            "relationships_count": len([r for r in e.relationships if r.is_active]),
            "in_use_by_this_instance": e.entity_id in in_use,
        })

    return {"entities": items, "total": total, "skip": skip, "limit": limit}


@router.get("/{instance_id}/entities/{entity_id}")
async def get_instance_citizen_entity(
    entity_id: str,
    db_instance: WorkflowInstance = Depends(require_instance_access),
):
    """Detalle de una entidad de la cartera del ciudadano del tramite.

    Se responde 404 --y no 403-- cuando la entidad es de otro ciudadano, para no
    confirmar que existe.

    Los valores pesados se sustituyen por un descriptor en vez de devolverse: la
    interfaz muestra que el campo existe y pide su contenido aparte solo si el
    revisor lo abre.
    """
    entity = await EntityService.get_entity(entity_id)
    if not entity or entity.owner_user_id != db_instance.user_id:
        raise HTTPException(status_code=404, detail="Entity not found")

    entity_type = await EntityService.get_entity_type(entity.entity_type)

    return {
        "entity_id": entity.entity_id,
        "entity_type": entity.entity_type,
        "entity_type_label": getattr(entity_type, "name", None) or entity.entity_type,
        "entity_type_icon": getattr(entity_type, "icon", None),
        "entity_type_color": getattr(entity_type, "color", None),
        "name": entity.name,
        "status": entity.status,
        "verified": entity.verified,
        "verification_date": entity.verification_date,
        "data": describe_blobs(entity.data),
        "created_at": entity.created_at,
        "updated_at": entity.updated_at,
        "relationships_count": len([r for r in entity.relationships if r.is_active]),
    }


@router.get("/{instance_id}/attachments/{attachment_id}/content")
async def get_instance_attachment_content(
    attachment_id: str,
    db_instance: WorkflowInstance = Depends(require_instance_access),
    download: bool = Query(False, description="Forzar descarga en vez de vista en linea"),
):
    """Sirve un adjunto de *esta* instancia.

    La comprobacion de pertenencia es la misma idea que usa el proxy de archivos
    de entidades, pero mas estricta: en vez de adivinar por el nombre del campo,
    el identificador pedido tiene que salir del indice de adjuntos de la propia
    instancia. Una s3_key valida de otro tramite no se sirve.
    """
    attachment = find_attachment(db_instance, attachment_id)
    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")

    if not attachment.get("s3_key"):
        # Adjunto legado embebido en el context; no hay objeto en S3 que servir.
        raise HTTPException(
            status_code=409,
            detail="Este adjunto es un archivo legado embebido en el contexto y no tiene copia en S3",
        )

    import asyncio as _asyncio
    import io as _io
    from botocore.exceptions import ClientError
    from fastapi.responses import StreamingResponse
    from ...services import s3_storage

    bucket = attachment.get("s3_bucket") or s3_storage.default_bucket()
    client = s3_storage.get_s3_client()

    try:
        obj = await _asyncio.to_thread(
            client.get_object, Bucket=bucket, Key=attachment["s3_key"]
        )
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "NoSuchBucket", "404"):
            raise HTTPException(status_code=404, detail="Attachment file not found in storage")
        logger.error(f"S3 error sirviendo adjunto {attachment_id}: {e}")
        raise HTTPException(status_code=502, detail=f"S3 error: {code or 'unknown'}")

    body = await _asyncio.to_thread(obj["Body"].read)
    filename = attachment.get("filename") or "archivo"
    content_type = attachment.get("content_type") or obj.get("ContentType") or "application/octet-stream"
    disposition = "attachment" if download else "inline"

    return StreamingResponse(
        _io.BytesIO(body),
        media_type=content_type,
        headers={
            "Content-Disposition": f'{disposition}; filename="{filename}"',
            "Content-Length": str(len(body)),
            # Material de expediente: cacheable en el navegador del revisor,
            # nunca en un cache compartido.
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.post("/{instance_id}/files/grant")
async def grant_instance_file_access(
    payload: Dict[str, Any],
    db_instance: WorkflowInstance = Depends(require_instance_access),
):
    """Permisos de descarga de vida corta para archivos de esta instancia.

    Existe para los formularios del revisor que ya traen la `s3_key` en su
    configuracion y no el identificador de adjunto. Solo se firman llaves que
    aparecen en el contexto de *esta* instancia: pedir una llave ajena no emite
    token, y no se distingue de pedir una inexistente.
    """
    from ...core.file_tokens import issue_token
    from ...services.instance_attachments import instance_s3_keys

    solicitadas = payload.get("s3_keys") or []
    if not isinstance(solicitadas, list) or len(solicitadas) > 200:
        raise HTTPException(status_code=400, detail="s3_keys invalido")

    permitidas = instance_s3_keys(db_instance)

    grants = []
    for key in solicitadas:
        if not isinstance(key, str) or key not in permitidas:
            logger.warning(
                f"Grant denegado: {key} no pertenece a la instancia {db_instance.instance_id}"
            )
            continue
        token, expires_at = issue_token(key, db_instance.instance_id)
        grants.append({"s3_key": key, "token": token, "expires_at": expires_at})

    return {"grants": grants}


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


@router.get("/{instance_id}/entities/{entity_id}/document")
async def get_instance_entity_document(
    entity_id: str,
    db_instance: WorkflowInstance = Depends(require_instance_access),
    format: str = Query("html", pattern="^(html|pdf)$", description="html para el visor, pdf para descargar"),
):
    """Documento renderizado de una entidad de la cartera del ciudadano.

    Es la misma representacion que ve el ciudadano --acuse, credencial,
    certificado-- generada con el visualizador que la propia entidad declara.
    El revisor necesita verla, no solo los campos sueltos.

    Existen endpoints equivalentes bajo /signatures, pero con autenticacion
    opcional: bastaria conocer un entity_id para obtener el documento con los
    datos personales de cualquier ciudadano. Este cuelga de la instancia y
    reutiliza el mismo selector de visualizador, para que la vista, la impresion
    y la descarga sigan compartiendo plantilla.
    """
    import io as _io
    from fastapi.responses import Response, StreamingResponse
    from ...core.config import settings as _settings
    from ...services.visualizers.visualizer_factory import VisualizerFactory
    from .signatures import _select_entity_visualizer

    entity = await EntityService.get_entity(entity_id)
    if not entity or entity.owner_user_id != db_instance.user_id:
        raise HTTPException(status_code=404, detail="Entity not found")

    config = {
        **(entity.entity_display_config or {}),
        "base_url": _settings.FRONTEND_BASE_URL,
    }
    visualizer = VisualizerFactory.get_visualizer(
        visualizer_type=_select_entity_visualizer(entity),
        config=config,
    )
    if not visualizer:
        raise HTTPException(status_code=404, detail="Esta entidad no tiene visualizador configurado")

    if format == "pdf":
        pdf = await visualizer.generate_pdf(entity)
        if not pdf:
            raise HTTPException(status_code=500, detail="No se pudo generar el PDF")
        info = await visualizer.get_download_info(entity)
        return StreamingResponse(
            _io.BytesIO(pdf),
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=\"{info['filename']}\""},
        )

    html = await visualizer.generate_html(entity)
    if not html:
        raise HTTPException(status_code=404, detail="Esta entidad no tiene representacion visual")
    return Response(content=html, media_type="text/html", headers={"Cache-Control": "no-cache"})
