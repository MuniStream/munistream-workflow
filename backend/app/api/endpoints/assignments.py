"""
Assignment API endpoints for workflow assignment management.

This module provides endpoints for assigning, reassigning, and starting
administrative workflows that require manual intervention.
"""

from fastapi import APIRouter, HTTPException, Depends, Query, Body
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from beanie.operators import In
import logging

from ...models.workflow import (
    WorkflowInstance, WorkflowDefinition, AssignmentStatus,
    AssignmentType, WorkflowType
)
from ...models.user import UserModel, UserRole
from ...models.team import TeamModel
from ...auth.provider import get_current_user, require_permission
from ...schemas.assignment import (
    AssignmentRequest, ReassignmentRequest, AssignmentResponse,
    AssignmentListResponse, AssignmentStatsResponse, TeamInfo,
    UserAssignmentInfo, WorkflowStartRequest, WorkflowStartResponse
)
from ...core.logging_config import get_workflow_logger
from ...workflows.executor import DAGExecutor

logger = get_workflow_logger(__name__)

router = APIRouter(tags=["assignments"])


# Admin dependency
async def get_current_admin(current_user: dict = Depends(get_current_user)):
    """Get current admin user with permission validation"""
    user_roles = current_user.get("roles", [])

    # Check if user has admin/management roles
    if not any(role in user_roles for role in ["admin", "manager", "reviewer", "approver"]):
        raise HTTPException(
            status_code=403,
            detail=f"Insufficient permissions for admin operations. Required: admin/manager/reviewer/approver. Found roles: {user_roles}"
        )

    return current_user


@router.get("/", response_model=AssignmentListResponse)
async def list_assignments(
    workflow_type: Optional[WorkflowType] = Query(None, description="Filter by workflow type"),
    team_id: Optional[str] = Query(None, description="Filter by team ID"),
    user_id: Optional[str] = Query(None, description="Filter by user ID"),
    status: Optional[AssignmentStatus] = Query(None, description="Filter by assignment status"),
    parent_instance_id: Optional[str] = Query(None, description="Filter by parent instance"),
    skip: int = Query(0, ge=0, description="Number of items to skip"),
    limit: int = Query(20, ge=1, le=100, description="Number of items to return"),
    admin: dict = Depends(get_current_admin)
):
    """
    List workflow assignments with filtering.

    Access control:
    - Admins and Managers see all assignments
    - Reviewers and Approvers see assignments for their teams
    - Viewers see only their own assignments
    """
    # Roles are flattened to the top level by the auth provider
    user_role = admin.get("roles", [])
    user_id_from_token = admin.get("sub")
    user_teams = admin.get("teams", [])

    # Build query based on user role
    query = {}

    # Apply filters
    if workflow_type:
        query["workflow_type"] = workflow_type

    if parent_instance_id:
        query["parent_instance_id"] = parent_instance_id

    if status:
        query["assignment_status"] = status

    # Role-based filtering
    if "admin" not in user_role and "manager" not in user_role:
        # Non-managers only see relevant assignments
        if "reviewer" in user_role or "approver" in user_role:
            # See team assignments
            if team_id:
                query["assigned_team_id"] = team_id
            elif user_teams:
                query["$or"] = [
                    {"assigned_user_id": user_id_from_token},
                    {"assigned_team_id": {"$in": user_teams}}
                ]
            else:
                query["assigned_user_id"] = user_id_from_token
        else:
            # Viewers only see their own
            query["assigned_user_id"] = user_id_from_token
    else:
        # Admins and Managers see unassigned instances by default
        if team_id:
            query["assigned_team_id"] = team_id
        elif user_id:
            query["assigned_user_id"] = user_id
        else:
            # Show only unassigned instances for admins/managers
            query["$and"] = [
                {"assigned_user_id": {"$in": [None, ""]}},
                {"assigned_team_id": {"$in": [None, ""]}}
            ]

    # Only show ADMIN workflows and those needing assignment (exclude PROCESS workflows)
    # Always exclude process workflows unless explicitly requested
    if workflow_type != WorkflowType.PROCESS:
        query["workflow_type"] = {"$ne": WorkflowType.PROCESS}

    try:
        # Debug: Print the query being executed
        logger.info("Assignments endpoint query", query=query)
        print(f"[ASSIGNMENTS DEBUG] Query: {query}")

        # Get total count
        total = await WorkflowInstance.find(query).count()
        print(f"[ASSIGNMENTS DEBUG] Total count: {total}")

        # Debug: Check if specific child workflow exists
        child_id = "4060a9a0-7ebb-4b9f-8922-3599f586514b"
        child_instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == child_id)
        if child_instance:
            print(f"[ASSIGNMENTS DEBUG] Child workflow found: status={child_instance.status}, assignment_status={child_instance.assignment_status}, workflow_type={child_instance.workflow_type}")
        else:
            print(f"[ASSIGNMENTS DEBUG] Child workflow {child_id} not found in database")

        # Get paginated results
        instances = await WorkflowInstance.find(query).skip(skip).limit(limit).to_list()
        print(f"[ASSIGNMENTS DEBUG] Retrieved {len(instances)} instances")

        # Build response
        assignments = []
        for inst in instances:
            # Get workflow definition for name
            workflow_def = await WorkflowDefinition.find_one(
                WorkflowDefinition.workflow_id == inst.workflow_id
            )

            assignments.append(AssignmentResponse(
                instance_id=inst.instance_id,
                workflow_id=inst.workflow_id,
                workflow_type=inst.workflow_type,
                workflow_name=workflow_def.name if workflow_def else inst.workflow_id,
                status=inst.assignment_status,
                assigned_to_user=inst.assigned_user_id,
                assigned_to_team=inst.assigned_team_id,
                assigned_at=inst.assigned_at,
                assigned_by=inst.assigned_by,
                parent_instance_id=inst.parent_instance_id,
                parent_workflow_id=inst.parent_workflow_id,
                priority=inst.priority,
                created_at=inst.created_at,
                updated_at=inst.updated_at,
                citizen_email=inst.context.get("parent_customer_email"),
                current_step=inst.current_step,
                completion_percentage=(
                    len(inst.completed_steps) / len(inst.context.get("total_steps", [1])) * 100
                    if inst.completed_steps else 0
                )
            ))

        return AssignmentListResponse(
            assignments=assignments,
            total=total,
            page=(skip // limit) + 1,
            page_size=limit
        )

    except Exception as e:
        logger.error("Failed to list assignments", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list assignments: {str(e)}")


@router.post("/{instance_id}/assign")
async def assign_workflow(
    instance_id: str,
    request: AssignmentRequest,
    admin: dict = Depends(get_current_admin)
):
    """
    Assign or reassign a workflow to a user or team.

    Requires Manager or Admin role.
    """
    user_role = admin.get("roles", [])
    user_id = admin.get("sub")

    # Check permissions
    if "admin" not in user_role and "manager" not in user_role:
        raise HTTPException(
            status_code=403,
            detail="Only managers and admins can assign workflows"
        )

    # Get instance
    instance = await WorkflowInstance.find_one(
        WorkflowInstance.instance_id == instance_id
    )
    if not instance:
        raise HTTPException(status_code=404, detail="Workflow instance not found")

    # Validate assignment target
    if "team" in request.assign_to:
        # Verify team exists
        team = await TeamModel.find_one(TeamModel.team_id == request.assign_to["team"])
        if not team:
            raise HTTPException(
                status_code=400,
                detail=f"Team {request.assign_to['team']} not found"
            )

        instance.assign_to_team(
            team_id=request.assign_to["team"],
            assigned_by=user_id,
            assignment_type=AssignmentType.MANUAL,
            notes=request.notes
        )

        logger.info("Workflow assigned to team",
                   instance_id=instance_id,
                   team_id=request.assign_to["team"],
                   assigned_by=user_id)

    elif "admin" in request.assign_to:
        # Log the assignment target for debugging
        logger.info(f"[ASSIGNMENT DEBUG] assign_to data: {request.assign_to}")
        logger.info(f"[ASSIGNMENT DEBUG] admin value: {request.assign_to['admin']}")
        logger.info(f"[ASSIGNMENT DEBUG] Current user email: {admin.get('email')}")

        # Handle special case where frontend sends "current_user"
        if request.assign_to["admin"] == "current_user":
            # Assign to current authenticated user using their JWT sub (Keycloak ID)
            assigned_user_id = admin.get("sub")  # Keycloak user ID
            logger.info(f"[ASSIGNMENT DEBUG] Assigning to current user: {admin.get('email')} (ID: {assigned_user_id})")
        else:
            # For specific email assignments, verify user exists in UserModel
            target_email = request.assign_to["admin"]
            user = await UserModel.find_one(UserModel.email == target_email)
            if not user:
                raise HTTPException(
                    status_code=400,
                    detail=f"User {target_email} not found"
                )
            assigned_user_id = user.id

        instance.assign_to_user(
            user_id=assigned_user_id,
            assigned_by=user_id,
            assignment_type=AssignmentType.MANUAL,
            notes=request.notes
        )

        logger.info("Workflow assigned to user",
                   instance_id=instance_id,
                   user_id=assigned_user_id,
                   assigned_by=user_id)
    else:
        raise HTTPException(
            status_code=400,
            detail="Must specify either 'team' or 'admin' in assign_to"
        )

    # Update priority if provided
    if request.priority:
        instance.priority = request.priority

    # Update status if needed
    if instance.status == "pending_assignment":
        instance.status = "waiting_for_start"

    await instance.save()

    return {
        "instance_id": instance_id,
        "assigned_to": request.assign_to,
        "assignment_status": instance.assignment_status,
        "status": instance.status,
        "message": "Workflow assigned successfully"
    }


@router.post("/{instance_id}/reassign")
async def reassign_workflow(
    instance_id: str,
    request: ReassignmentRequest,
    admin: dict = Depends(get_current_admin)
):
    """
    Reassign a workflow from current assignee to new assignee.

    Requires Manager or Admin role.
    """
    user_role = admin.get("roles", [])
    user_id = admin.get("sub")

    # Check permissions
    if "admin" not in user_role and "manager" not in user_role:
        raise HTTPException(
            status_code=403,
            detail="Only managers and admins can reassign workflows"
        )

    # Get instance
    instance = await WorkflowInstance.find_one(
        WorkflowInstance.instance_id == instance_id
    )
    if not instance:
        raise HTTPException(status_code=404, detail="Workflow instance not found")

    # Store previous assignment for audit
    previous_assignment = {
        "user": instance.assigned_user_id,
        "team": instance.assigned_team_id,
        "status": instance.assignment_status
    }

    # Perform reassignment
    if "team" in request.assign_to:
        instance.assign_to_team(
            team_id=request.assign_to["team"],
            assigned_by=user_id,
            assignment_type=AssignmentType.REASSIGNED,
            notes=f"Reassigned: {request.reason}. {request.notes or ''}"
        )
    elif "admin" in request.assign_to:
        user = await UserModel.find_one(UserModel.email == request.assign_to["admin"])
        if not user:
            raise HTTPException(
                status_code=400,
                detail=f"User {request.assign_to['admin']} not found"
            )

        instance.assign_to_user(
            user_id=user.id,
            assigned_by=user_id,
            assignment_type=AssignmentType.REASSIGNED,
            notes=f"Reassigned: {request.reason}. {request.notes or ''}"
        )

    await instance.save()

    logger.info("Workflow reassigned",
               instance_id=instance_id,
               from_assignment=previous_assignment,
               to_assignment=request.assign_to,
               reason=request.reason)

    return {
        "instance_id": instance_id,
        "reassigned_to": request.assign_to,
        "previous_assignment": previous_assignment,
        "reason": request.reason,
        "message": "Workflow reassigned successfully"
    }


@router.post("/{instance_id}/start", response_model=WorkflowStartResponse)
async def start_assigned_workflow(
    instance_id: str,
    request: WorkflowStartRequest = Body(default=WorkflowStartRequest()),
    admin: dict = Depends(get_current_admin),
    executor: DAGExecutor = Depends(lambda: DAGExecutor())
):
    """
    Start execution of an assigned workflow.

    Only the assigned user or a member of the assigned team can start the workflow.
    """
    user_id = admin.get("sub")
    user_teams = admin.get("teams", [])

    # Get instance
    instance = await WorkflowInstance.find_one(
        WorkflowInstance.instance_id == instance_id
    )
    if not instance:
        raise HTTPException(status_code=404, detail="Workflow instance not found")

    # Check assignment
    is_assigned = False
    if instance.assigned_user_id == user_id:
        is_assigned = True
    elif instance.assigned_team_id and instance.assigned_team_id in user_teams:
        is_assigned = True

    # Admins and managers can override
    user_role = admin.get("roles", [])
    if "admin" in user_role or "manager" in user_role:
        is_assigned = True

    if not is_assigned:
        raise HTTPException(
            status_code=403,
            detail="You are not assigned to this workflow"
        )

    # Check status
    if instance.status not in ["waiting_for_start", "pending_assignment"]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot start workflow in status '{instance.status}'"
        )

    # Update instance
    instance.status = "running"
    instance.started_at = datetime.utcnow()
    instance.assignment_status = AssignmentStatus.UNDER_REVIEW

    # Add any initial data
    if request.initial_data:
        instance.context.update(request.initial_data)

    # Add start note
    if request.notes:
        if not instance.assignment_notes:
            instance.assignment_notes = request.notes
        else:
            instance.assignment_notes += f"\nStarted: {request.notes}"

    await instance.save()

    # Submit to executor
    executor.submit_instance(instance_id)

    logger.info("Workflow started by user",
               instance_id=instance_id,
               started_by=user_id,
               workflow_type=instance.workflow_type.value if instance.workflow_type else None)

    return WorkflowStartResponse(
        instance_id=instance_id,
        status=instance.status,
        started_at=instance.started_at,
        started_by=user_id,
        message="Workflow started successfully"
    )


@router.get("/stats", response_model=AssignmentStatsResponse)
async def get_assignment_stats(
    team_id: Optional[str] = Query(None, description="Filter stats by team"),
    workflow_type: Optional[WorkflowType] = Query(None, description="Filter by workflow type"),
    admin: dict = Depends(get_current_admin)
):
    """
    Get statistics about workflow assignments.

    Useful for dashboards and workload monitoring.
    """
    user_role = admin.get("roles", [])
    user_id = admin.get("sub")
    user_teams = admin.get("teams", [])

    # Build base query
    query = {}

    # Apply filters
    if workflow_type:
        query["workflow_type"] = workflow_type
    else:
        # Default to non-PROCESS workflows
        query["workflow_type"] = {"$ne": WorkflowType.PROCESS}

    if team_id:
        query["assigned_team_id"] = team_id
    elif "admin" not in user_role and "manager" not in user_role:
        # Non-managers only see their team's stats
        if user_teams:
            query["$or"] = [
                {"assigned_user_id": user_id},
                {"assigned_team_id": {"$in": user_teams}}
            ]
        else:
            query["assigned_user_id"] = user_id

    try:
        # Get counts by status
        status_pipeline = [
            {"$match": query},
            {"$group": {
                "_id": "$assignment_status",
                "count": {"$sum": 1}
            }}
        ]
        status_stats = await WorkflowInstance.aggregate(status_pipeline).to_list()
        by_status = {str(stat["_id"]): stat["count"] for stat in status_stats if stat["_id"]}

        # Get counts by user
        user_pipeline = [
            {"$match": {**query, "assigned_user_id": {"$ne": None}}},
            {"$group": {
                "_id": "$assigned_user_id",
                "count": {"$sum": 1}
            }}
        ]
        user_stats = await WorkflowInstance.aggregate(user_pipeline).to_list()
        by_user = {stat["_id"]: stat["count"] for stat in user_stats if stat["_id"]}

        # Get counts by team
        team_pipeline = [
            {"$match": {**query, "assigned_team_id": {"$ne": None}}},
            {"$group": {
                "_id": "$assigned_team_id",
                "count": {"$sum": 1}
            }}
        ]
        team_stats = await WorkflowInstance.aggregate(team_pipeline).to_list()
        by_team = {stat["_id"]: stat["count"] for stat in team_stats if stat["_id"]}

        # Get counts by workflow type
        type_pipeline = [
            {"$match": query},
            {"$group": {
                "_id": "$workflow_type",
                "count": {"$sum": 1}
            }}
        ]
        type_stats = await WorkflowInstance.aggregate(type_pipeline).to_list()
        by_workflow_type = {
            str(stat["_id"]): stat["count"]
            for stat in type_stats
            if stat["_id"]
        }

        # Calculate time-based stats
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

        # Completed today
        completed_today = await WorkflowInstance.find({
            **query,
            "assignment_status": AssignmentStatus.COMPLETED,
            "completed_at": {"$gte": today_start}
        }).count()

        # Overdue (pending for more than 48 hours)
        overdue_time = datetime.utcnow() - timedelta(hours=48)
        overdue = await WorkflowInstance.find({
            **query,
            "assignment_status": {"$in": [
                AssignmentStatus.PENDING_REVIEW,
                AssignmentStatus.UNDER_REVIEW
            ]},
            "created_at": {"$lt": overdue_time}
        }).count()

        # Total
        total = sum(by_status.values())

        return AssignmentStatsResponse(
            by_status=by_status,
            by_user=by_user,
            by_team=by_team,
            by_workflow_type=by_workflow_type,
            total=total,
            pending=by_status.get(AssignmentStatus.PENDING_REVIEW.value, 0),
            in_progress=by_status.get(AssignmentStatus.UNDER_REVIEW.value, 0),
            completed_today=completed_today,
            overdue=overdue
        )

    except Exception as e:
        logger.error("Failed to get assignment stats", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get stats: {str(e)}")


@router.get("/teams", response_model=List[TeamInfo])
async def list_available_teams(
    admin: dict = Depends(get_current_admin)
):
    """
    List teams available for assignment.

    Returns team information including current workload.
    """
    try:
        teams = await TeamModel.find_all().to_list()

        team_info = []
        for team in teams:
            # Get current assignment count
            current_load = await WorkflowInstance.find({
                "assigned_team_id": team.team_id,
                "assignment_status": {"$in": [
                    AssignmentStatus.PENDING_REVIEW,
                    AssignmentStatus.UNDER_REVIEW
                ]}
            }).count()

            team_info.append(TeamInfo(
                team_id=team.team_id,
                team_name=team.name,
                member_count=len(team.members),
                current_load=current_load,
                available=team.is_active and current_load < (len(team.members) * 5)
            ))

        return team_info

    except Exception as e:
        logger.error("Failed to list teams", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list teams: {str(e)}")


@router.get("/users", response_model=List[UserAssignmentInfo])
async def list_assignable_users(
    role: Optional[UserRole] = Query(None, description="Filter by role"),
    team_id: Optional[str] = Query(None, description="Filter by team"),
    admin: dict = Depends(get_current_admin)
):
    """
    List users available for assignment.

    Returns user information including current workload.
    """
    # Only managers and admins can see all users
    # Roles are flattened to the top level by the auth provider
    user_roles = admin.get("roles", [])

    if "admin" not in user_roles and "manager" not in user_roles:
        raise HTTPException(
            status_code=403,
            detail=f"Only managers and admins can list assignable users. Found roles: {user_roles}"
        )

    try:
        query = {}
        if role:
            query["role"] = role

        users = await UserModel.find(query).to_list()

        user_info = []
        for user in users:
            # Filter by team if specified
            if team_id and team_id not in user.teams:
                continue

            # Get current assignment count
            current_assignments = await WorkflowInstance.find({
                "assigned_user_id": str(user.id),
                "assignment_status": {"$in": [
                    AssignmentStatus.PENDING_REVIEW,
                    AssignmentStatus.UNDER_REVIEW
                ]}
            }).count()

            max_assignments = 10  # Default max
            if user.role == UserRole.MANAGER:
                max_assignments = 15
            elif user.role == UserRole.ADMIN:
                max_assignments = 20

            user_info.append(UserAssignmentInfo(
                user_id=str(user.id),
                user_email=user.email,
                user_name=user.full_name,
                role=user.role,
                teams=user.teams,
                current_assignments=current_assignments,
                max_assignments=max_assignments,
                available=user.is_active and current_assignments < max_assignments
            ))

        return user_info

    except Exception as e:
        logger.error("Failed to list users", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list users: {str(e)}")