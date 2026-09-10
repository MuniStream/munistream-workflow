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
from ...models.workflow import StepExecution
from ...services.workflow_service import workflow_service

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
    search: Optional[str] = Query(None, description="Search in workflow name, citizen email, citizen name, instance ID, or context data"),
    skip: int = Query(0, ge=0, description="Number of items to skip"),
    limit: int = Query(20, ge=1, le=100, description="Number of items to return"),
    admin: dict = Depends(get_current_admin)
):
    """
    List workflow assignments with filtering and role-based access control.

    Access control:
    - Admin: can see all assignments
    - Manager: can see assignments for their teams + directly assigned to them
    - Reviewer/Approver: can see ONLY assignments directly assigned to them
    - Viewer: can see ONLY assignments directly assigned to them
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

    # Role-based filtering based on access control rules:
    # - admin: can see all instances
    # - manager: can see instances assigned to their teams + directly assigned to them
    # - reviewer/approver: can see ONLY instances directly assigned to them
    # - viewer: can see ONLY instances directly assigned to them

    if "admin" in user_role:
        # Admin sees everything - apply any explicit filters if provided
        if team_id:
            query["assigned_team_id"] = team_id
        elif user_id:
            query["assigned_user_id"] = user_id
        # Otherwise no filtering - show all instances

    elif "manager" in user_role:
        # Manager sees team assignments + direct assignments
        conditions = []

        # Always include direct assignments
        conditions.append({"assigned_user_id": user_id_from_token})

        # Include team assignments if user has teams
        if user_teams:
            conditions.append({"assigned_team_id": {"$in": user_teams}})

        # Apply explicit filters if provided
        if team_id:
            query["assigned_team_id"] = team_id
        elif user_id:
            query["assigned_user_id"] = user_id
        else:
            # Use OR condition for team + direct assignments
            query["$or"] = conditions

    else:
        # Reviewer, approver, viewer: only see direct assignments
        query["assigned_user_id"] = user_id_from_token

    # Only show ADMIN workflows by default
    if not workflow_type:
        query["workflow_type"] = WorkflowType.ADMIN

    # Add search functionality
    if search:
        # Base search conditions for instance and workflow IDs
        search_conditions = [
            {"instance_id": {"$regex": search, "$options": "i"}},
            {"workflow_id": {"$regex": search, "$options": "i"}}
        ]

        # Get all unique context keys from existing instances to build dynamic search
        pipeline = [
            {"$match": {"context": {"$exists": True, "$ne": {}}}},
            {"$project": {"context_keys": {"$objectToArray": "$context"}}},
            {"$unwind": "$context_keys"},
            {"$group": {"_id": "$context_keys.k"}},
            {"$project": {"key": "$_id", "_id": 0}}
        ]

        try:
            context_keys_result = await WorkflowInstance.aggregate(pipeline).to_list()
            context_keys = [item["key"] for item in context_keys_result if isinstance(item.get("key"), str)]

            # Build dynamic search conditions for all context keys
            for key in context_keys:
                search_conditions.append({
                    f"context.{key}": {"$regex": search, "$options": "i"}
                })

        except Exception as e:
            # Fallback: if aggregation fails, just search in the context object as text
            logger.warning(f"Failed to get dynamic context keys, using fallback: {e}")
            search_conditions.append({
                "$where": f"JSON.stringify(this.context).toLowerCase().includes('{search.lower()}')"
            })

        # If there are existing conditions, combine with AND
        if query:
            # Convert existing query conditions to a list for $and
            existing_conditions = []
            for key, value in query.items():
                existing_conditions.append({key: value})

            query = {"$and": existing_conditions + [{"$or": search_conditions}]}
        else:
            query = {"$or": search_conditions}

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

        # Get paginated results ordered by most recent first
        instances = await WorkflowInstance.find(query).sort([("created_at", -1)]).skip(skip).limit(limit).to_list()
        print(f"[ASSIGNMENTS DEBUG] Retrieved {len(instances)} instances")

        # Nombres de workflow en una sola consulta, para el propio tramite y para
        # el de origen. Antes se consultaba la definicion dentro del bucle, una
        # vez por fila.
        wf_ids = {i.workflow_id for i in instances if i.workflow_id}
        wf_ids |= {i.parent_workflow_id for i in instances if i.parent_workflow_id}
        definitions = {}
        if wf_ids:
            for wd in await WorkflowDefinition.find(
                {"workflow_id": {"$in": list(wf_ids)}}
            ).to_list():
                definitions[wd.workflow_id] = wd

        # Total de pasos por workflow, del DAG y una sola vez por workflow.
        # Antes se contaban documentos de `WorkflowStep`, el modelo anterior a
        # los DAG: para cualquier tramite actual esa coleccion esta vacia, asi
        # que el total era 0 y el progreso salia siempre 0%.
        totales_por_workflow = {}
        for wf_id in {i.workflow_id for i in instances if i.workflow_id}:
            try:
                dag = await workflow_service.get_dag(wf_id)
                tasks = getattr(dag, "tasks", None) or {}
                totales_por_workflow[wf_id] = len(tasks)
            except Exception:
                totales_por_workflow[wf_id] = 0

        # Pasos ya ejecutados, en una sola agregacion para toda la pagina.
        # `completed_steps` del documento no es fiable (hay instancias avanzadas
        # con la lista vacia) y `task_states` no se persiste: el avance real
        # vive en la coleccion de ejecuciones, que es de donde lo saca tambien
        # el endpoint de seguimiento.
        from ...services.instance_listing import _asignados
        nombres_asignados = await _asignados(instances)

        ids_pagina = [i.instance_id for i in instances]
        completados_por_instancia = {}
        if ids_pagina:
            cursor = StepExecution.get_motor_collection().aggregate([
                {"$match": {"instance_id": {"$in": ids_pagina}, "status": "completed"}},
                {"$group": {"_id": {"i": "$instance_id", "s": "$step_id"}}},
                {"$group": {"_id": "$_id.i", "n": {"$sum": 1}}},
            ])
            async for fila in cursor:
                completados_por_instancia[fila["_id"]] = fila["n"]

        # Build response
        assignments = []
        for inst in instances:
            workflow_def = definitions.get(inst.workflow_id)

            total_steps = totales_por_workflow.get(inst.workflow_id, 0)

            # Calculate progress percentage
            completados = completados_por_instancia.get(
                inst.instance_id, len(inst.completed_steps or [])
            )
            completion_percentage = 0
            if inst.status == "completed":
                completion_percentage = 100
            elif total_steps > 0 and completados:
                completion_percentage = min((completados / total_steps) * 100, 100)

            assignments.append(AssignmentResponse(
                instance_id=inst.instance_id,
                workflow_id=inst.workflow_id,
                workflow_type=inst.workflow_type,
                workflow_name=workflow_def.name if workflow_def else inst.workflow_id,
                status=inst.assignment_status,  # Keep assignment status for compatibility
                workflow_status=inst.status,  # Add workflow status as new field
                assigned_to_user=inst.assigned_user_id,
                assigned_to_team=inst.assigned_team_id,
                assigned_at=inst.assigned_at,
                assigned_by=inst.assigned_by,
                parent_instance_id=inst.parent_instance_id,
                parent_workflow_id=inst.parent_workflow_id,
                parent_workflow_name=(
                    getattr(definitions.get(inst.parent_workflow_id), "name", None)
                    or inst.parent_workflow_id
                ),
                priority=inst.priority,
                created_at=inst.created_at,
                updated_at=inst.updated_at,
                citizen_email=inst.context.get("parent_customer_email"),
                assigned_to_name=(
                    nombres_asignados.get(str(inst.assigned_user_id or ""))
                    or nombres_asignados.get(str(inst.assigned_team_id or ""))
                    # Los equipos viven en Keycloak y su identificador ya es
                    # legible; mejor presentarlo que dejar el hueco vacio.
                    or (
                        str(inst.assigned_team_id).replace("_", " ").capitalize()
                        if inst.assigned_team_id else None
                    )
                ),
                current_step=inst.current_step,
                completion_percentage=completion_percentage
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

    # Al ejecutor que de verdad esta corriendo.
    #
    # Antes se inyectaba `Depends(lambda: DAGExecutor())`, que construye un
    # ejecutor nuevo en cada peticion: `submit_instance` encolaba en un objeto de
    # usar y tirar que nadie drena, y el que arranca la aplicacion no se enteraba
    # nunca. El tramite quedaba en `running` con todos sus pasos en `pending` y
    # sin nada esperando, o sea parado para siempre.
    #
    # Nadie lo veia porque el boton de la bandeja no llamaba aqui: solo navegaba.
    # Al arreglar el boton, este camino se estreno.
    await workflow_service.execute_instance(instance_id)

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

        # Los equipos de trabajo viven en Keycloak, no en Mongo: la coleccion
        # TeamModel esta vacia en los despliegues reales, y por eso este
        # endpoint devolvia una lista vacia y no se podia asignar nada. Cuando
        # no hay documentos, se ofrecen los equipos que las propias instancias
        # ya usan, que es la unica fuente fiable que hay.
        if not team_info:
            en_uso = await WorkflowInstance.get_motor_collection().distinct("assigned_team_id")
            for team_id in sorted(t for t in en_uso if t):
                carga = await WorkflowInstance.find({
                    "assigned_team_id": team_id,
                    "assignment_status": {"$in": [
                        AssignmentStatus.PENDING_REVIEW,
                        AssignmentStatus.UNDER_REVIEW,
                    ]},
                }).count()
                team_info.append(TeamInfo(
                    team_id=team_id,
                    team_name=team_id.replace("_", " ").capitalize(),
                    member_count=0,
                    current_load=carga,
                    available=True,
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