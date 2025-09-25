"""
Team management API endpoints.
"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query, Depends
from datetime import datetime

from ...models.team import TeamModel
from ...schemas.team import (
    TeamCreate,
    TeamUpdate,
    TeamResponse,
    TeamListResponse,
    TeamMemberCreate,
    TeamMemberUpdate,
    TeamMemberSchema,
    TeamStats,
    WorkflowAssignment,
    TaskAssignmentRequest,
    TaskAssignmentResponse
)
from ...auth.provider import get_current_user, require_admin, require_manager_or_admin

router = APIRouter()


async def convert_team_to_response(team: TeamModel) -> TeamResponse:
    """Convert TeamModel to TeamResponse"""
    return TeamResponse(
        team_id=team.team_id,
        name=team.name,
        description=team.description,
        department=team.department,
        members=[
            TeamMemberSchema(
                user_id=member.user_id,
                role=member.role,
                joined_at=member.joined_at,
                is_active=member.is_active
            )
            for member in team.members
        ],
        max_concurrent_tasks=team.max_concurrent_tasks,
        specializations=team.specializations,
        working_hours=team.working_hours,
        assigned_workflows=team.assigned_workflows,
        is_active=team.is_active,
        created_at=team.created_at,
        updated_at=team.updated_at,
        created_by=team.created_by,
        active_member_count=team.get_member_count(),
        leader_count=len(team.get_leaders())
    )


@router.post("/", response_model=TeamResponse)
async def create_team(
    team_data: TeamCreate,
    current_user: dict = Depends(require_manager_or_admin)
):
    """Create a new team"""
    # Check if team_id already exists
    existing_team = await TeamModel.find_one(TeamModel.team_id == team_data.team_id)
    if existing_team:
        raise HTTPException(status_code=400, detail="Team ID already exists")
    
    # Create team
    team = TeamModel(
        team_id=team_data.team_id,
        name=team_data.name,
        description=team_data.description,
        department=team_data.department,
        max_concurrent_tasks=team_data.max_concurrent_tasks,
        specializations=team_data.specializations,
        working_hours=team_data.working_hours,
        created_by=str(current_user.id)
    )
    
    await team.create()
    return await convert_team_to_response(team)


@router.get("/", response_model=TeamListResponse)
async def list_teams(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    department: Optional[str] = None,
    is_active: bool = True,
    current_user: dict = Depends(get_current_user)
):
    """List teams with pagination"""
    query = {"is_active": is_active}
    if department:
        query["department"] = department
    
    # Get total count
    total = await TeamModel.find(query).count()
    
    # Get paginated results
    skip = (page - 1) * page_size
    teams = await TeamModel.find(query).sort(-TeamModel.created_at).skip(skip).limit(page_size).to_list()
    
    # Convert to response format
    team_responses = []
    for team in teams:
        team_responses.append(await convert_team_to_response(team))
    
    return TeamListResponse(
        teams=team_responses,
        total=total,
        page=page,
        page_size=page_size
    )


@router.get("/{team_id}", response_model=TeamResponse)
async def get_team(
    team_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get a specific team"""
    team = await TeamModel.find_one(TeamModel.team_id == team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    return await convert_team_to_response(team)


@router.put("/{team_id}", response_model=TeamResponse)
async def update_team(
    team_id: str,
    update_data: TeamUpdate,
    current_user: dict = Depends(require_manager_or_admin)
):
    """Update a team"""
    team = await TeamModel.find_one(TeamModel.team_id == team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    # Update fields
    if update_data.name is not None:
        team.name = update_data.name
    if update_data.description is not None:
        team.description = update_data.description
    if update_data.department is not None:
        team.department = update_data.department
    if update_data.max_concurrent_tasks is not None:
        team.max_concurrent_tasks = update_data.max_concurrent_tasks
    if update_data.specializations is not None:
        team.specializations = update_data.specializations
    if update_data.working_hours is not None:
        team.working_hours = update_data.working_hours
    if update_data.is_active is not None:
        team.is_active = update_data.is_active
    
    team.updated_at = datetime.utcnow()
    await team.save()
    
    return await convert_team_to_response(team)


@router.delete("/{team_id}")
async def delete_team(
    team_id: str,
    current_user: dict = Depends(require_admin)
):
    """Delete a team"""
    team = await TeamModel.find_one(TeamModel.team_id == team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    await team.delete()
    return {"message": "Team deleted successfully"}


# Team Member Management

@router.post("/{team_id}/members", response_model=TeamResponse)
async def add_team_member(
    team_id: str,
    member_data: TeamMemberCreate,
    current_user: dict = Depends(require_manager_or_admin)
):
    """Add a member to a team"""
    team = await TeamModel.find_one(TeamModel.team_id == team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    # Check if user exists
    user = None
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Add member to team
    if not team.add_member(member_data.user_id, member_data.role):
        raise HTTPException(status_code=400, detail="User is already a member of this team")
    
    await team.save()
    return await convert_team_to_response(team)


@router.put("/{team_id}/members/{user_id}", response_model=TeamResponse)
async def update_team_member(
    team_id: str,
    user_id: str,
    update_data: TeamMemberUpdate,
    current_user: dict = Depends(require_manager_or_admin)
):
    """Update a team member"""
    team = await TeamModel.find_one(TeamModel.team_id == team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    updated = False
    if update_data.role is not None:
        if team.update_member_role(user_id, update_data.role):
            updated = True
    
    if update_data.is_active is not None:
        if not update_data.is_active:
            if team.deactivate_member(user_id):
                updated = True
        else:
            # Reactivate member
            for member in team.members:
                if member.user_id == user_id:
                    member.is_active = True
                    team.updated_at = datetime.utcnow()
                    updated = True
                    break
    
    if not updated:
        raise HTTPException(status_code=404, detail="Member not found in team")
    
    await team.save()
    return await convert_team_to_response(team)


@router.delete("/{team_id}/members/{user_id}")
async def remove_team_member(
    team_id: str,
    user_id: str,
    current_user: dict = Depends(require_manager_or_admin)
):
    """Remove a member from a team"""
    team = await TeamModel.find_one(TeamModel.team_id == team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    if not team.remove_member(user_id):
        raise HTTPException(status_code=404, detail="Member not found in team")
    
    await team.save()
    return {"message": "Member removed from team successfully"}


# Team Workflow Assignment

@router.post("/{team_id}/workflows/{workflow_id}")
async def assign_workflow_to_team(
    team_id: str,
    workflow_id: str,
    current_user: dict = Depends(require_manager_or_admin)
):
    """Assign a workflow to a team"""
    team = await TeamModel.find_one(TeamModel.team_id == team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    if not team.assign_workflow(workflow_id):
        raise HTTPException(status_code=400, detail="Workflow already assigned to this team")
    
    await team.save()
    return {"message": "Workflow assigned to team successfully"}


@router.delete("/{team_id}/workflows/{workflow_id}")
async def unassign_workflow_from_team(
    team_id: str,
    workflow_id: str,
    current_user: dict = Depends(require_manager_or_admin)
):
    """Unassign a workflow from a team"""
    team = await TeamModel.find_one(TeamModel.team_id == team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    if not team.unassign_workflow(workflow_id):
        raise HTTPException(status_code=400, detail="Workflow not assigned to this team")
    
    await team.save()
    return {"message": "Workflow unassigned from team successfully"}


# Team Statistics and Analytics

@router.get("/{team_id}/stats", response_model=TeamStats)
async def get_team_stats(
    team_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get team statistics and performance metrics"""
    team = await TeamModel.find_one(TeamModel.team_id == team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    # TODO: Implement actual statistics calculation
    # This would involve querying workflow instances, task completions, etc.
    
    return TeamStats(
        team_id=team.team_id,
        name=team.name,
        active_members=team.get_member_count(),
        assigned_workflows=len(team.assigned_workflows),
        current_workload=0,  # TODO: Calculate from active instances
        capacity_utilization=0.0,  # TODO: Calculate based on concurrent tasks
        average_completion_time=None,  # TODO: Calculate from completed instances
        success_rate=1.0  # TODO: Calculate from completed vs failed instances
    )


# Smart Task Assignment

@router.post("/assign-task", response_model=TaskAssignmentResponse)
async def assign_task_to_team(
    request: TaskAssignmentRequest,
    current_user: dict = Depends(get_current_user)
):
    """Intelligently assign a task to the best available team"""
    
    # Find teams that can handle this workflow
    teams = await TeamModel.find(
        TeamModel.assigned_workflows.in_([request.workflow_id]) & 
        TeamModel.is_active == True
    ).to_list()
    
    if not teams:
        raise HTTPException(status_code=404, detail="No teams available for this workflow")
    
    # Simple assignment algorithm - choose team with least current workload
    # TODO: Implement more sophisticated algorithm considering:
    # - Current workload
    # - Specializations match
    # - Working hours
    # - Historical performance
    # - Member availability
    
    best_team = teams[0]  # Simplified for now
    
    return TaskAssignmentResponse(
        assigned_team_id=best_team.team_id,
        assigned_user_id=None,  # TODO: Implement user-level assignment
        assignment_reason="Selected based on workflow assignment and availability",
        estimated_start_time=None,  # TODO: Calculate based on current queue
        queue_position=None  # TODO: Calculate queue position
    )