"""
Admin team management API endpoints.
ADMIN (root) can manage all teams, MANAGER can only manage their assigned teams.
"""

from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Query, Depends, status
from datetime import datetime

from ...models.user import UserModel, UserRole
from ...models.team import TeamModel, TeamMember
from ...schemas.team import TeamCreate, TeamResponse
from ...auth.provider import get_current_user
from ...services.keycloak_sync import keycloak_sync_service
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


async def get_admin_or_manager(current_user: dict = Depends(get_current_user)) -> dict:
    """Require ADMIN or MANAGER role"""
    user_roles = current_user.get("roles", [])

    if not any(role in user_roles for role in ["admin", "manager"]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or manager access required."
        )

    return current_user


async def require_admin_root(current_user: dict = Depends(get_current_user)) -> dict:
    """Require ADMIN (root) role with full system access"""
    user_roles = current_user.get("roles", [])

    if "admin" not in user_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin (root) access required."
        )

    return current_user


async def get_user_manageable_teams(current_user: dict) -> List[str]:
    """Get list of teams the current user can manage"""
    user_roles = current_user.get("roles", [])

    # Admin can manage all teams
    if "admin" in user_roles:
        return []  # Empty list means "all teams"

    # Manager can only manage teams they belong to as managers
    if "manager" in user_roles:
        user_id = current_user.get("sub")
        if user_id:
            # Find teams where this user is a manager
            teams = await TeamModel.find(
                {"members.user_id": user_id, "members.role": "manager", "members.is_active": True}
            ).to_list()
            return [team.team_id for team in teams]

    return []


@router.get("/teams", response_model=Dict[str, Any])
async def list_teams(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    department: Optional[str] = Query(None, description="Filter by department"),
    search: Optional[str] = Query(None, description="Search in team name or description"),
    current_user: dict = Depends(get_admin_or_manager)
):
    """
    List teams. ADMIN sees all teams, MANAGER sees only their managed teams.
    """
    try:
        # Get teams the user can manage
        manageable_team_ids = await get_user_manageable_teams(current_user)

        # Build query filters
        query_filters = []

        # Restrict to manageable teams if not admin
        if manageable_team_ids is not None and len(manageable_team_ids) > 0:
            query_filters.append({"team_id": {"$in": manageable_team_ids}})
        elif manageable_team_ids is not None and len(manageable_team_ids) == 0 and "admin" not in current_user.get("roles", []):
            # Manager but no teams - return empty
            return {
                "teams": [],
                "pagination": {"total": 0, "skip": skip, "limit": limit, "has_more": False}
            }

        if department:
            query_filters.append({"department": department})

        if search:
            search_filter = {
                "$or": [
                    {"name": {"$regex": search, "$options": "i"}},
                    {"description": {"$regex": search, "$options": "i"}}
                ]
            }
            query_filters.append(search_filter)

        # Execute query
        if query_filters:
            teams_query = TeamModel.find({"$and": query_filters})
        else:
            teams_query = TeamModel.find_all()

        # Get total count for pagination
        total = await teams_query.count()

        # Apply pagination
        teams = await teams_query.skip(skip).limit(limit).to_list()

        # Convert to response format
        team_responses = []
        for team in teams:
            team_response = TeamResponse(
                team_id=team.team_id,
                name=team.name,
                description=team.description,
                department=team.department,
                members=[{
                    "user_id": member.user_id,
                    "role": member.role,
                    "joined_at": member.joined_at,
                    "is_active": member.is_active
                } for member in team.members],
                max_concurrent_tasks=team.max_concurrent_tasks,
                specializations=team.specializations,
                working_hours=team.working_hours,
                assigned_workflows=team.assigned_workflows,
                is_active=team.is_active,
                created_at=team.created_at,
                updated_at=team.updated_at,
                created_by=team.created_by,
                member_count=team.get_member_count(),
                manager_count=team.get_manager_count()
            )
            team_responses.append(team_response)

        return {
            "teams": team_responses,
            "pagination": {
                "total": total,
                "skip": skip,
                "limit": limit,
                "has_more": skip + limit < total
            }
        }

    except Exception as e:
        logger.error(f"Error listing teams: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error while listing teams"
        )


@router.post("/teams", response_model=TeamResponse, status_code=status.HTTP_201_CREATED)
async def create_team(
    team_data: TeamCreate,
    sync_to_keycloak: bool = Query(True, description="Sync team to Keycloak group"),
    current_user: dict = Depends(require_admin_root)
):
    """
    Create a new team. Only accessible by ADMIN (root).
    Optionally syncs to Keycloak as a group.
    """
    try:
        # Check if team already exists
        existing_team = await TeamModel.find_one(
            {"$or": [
                {"team_id": team_data.team_id},
                {"name": team_data.name}
            ]}
        )

        if existing_team:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Team with this ID or name already exists"
            )

        # Create new team
        new_team = TeamModel(
            team_id=team_data.team_id,
            name=team_data.name,
            description=team_data.description,
            department=team_data.department,
            max_concurrent_tasks=team_data.max_concurrent_tasks or 10,
            specializations=team_data.specializations or [],
            working_hours=team_data.working_hours or {},
            is_active=True,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            created_by=current_user.get("sub")
        )

        await new_team.save()

        # Sync to Keycloak if requested
        if sync_to_keycloak:
            try:
                sync_success = await keycloak_sync_service.sync_team_to_keycloak_group(new_team)
                if not sync_success:
                    logger.warning(f"Failed to sync team {new_team.name} to Keycloak")
            except Exception as e:
                logger.error(f"Error syncing team to Keycloak: {e}")

        # Convert to response
        team_response = TeamResponse(
            team_id=new_team.team_id,
            name=new_team.name,
            description=new_team.description,
            department=new_team.department,
            members=[],
            max_concurrent_tasks=new_team.max_concurrent_tasks,
            specializations=new_team.specializations,
            working_hours=new_team.working_hours,
            assigned_workflows=new_team.assigned_workflows,
            is_active=new_team.is_active,
            created_at=new_team.created_at,
            updated_at=new_team.updated_at,
            created_by=new_team.created_by,
            member_count=0,
            manager_count=0
        )

        logger.info(f"Team {new_team.name} created by admin {current_user.get('email')}")
        return team_response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating team: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error while creating team"
        )


@router.put("/teams/{team_id}/manager")
async def assign_manager_to_team(
    team_id: str,
    user_id: str,
    current_user: dict = Depends(require_admin_root)
):
    """
    Assign a manager to a team. Only accessible by ADMIN (root).
    """
    try:
        # Get team
        team = await TeamModel.find_one(TeamModel.team_id == team_id)
        if not team:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Team not found"
            )

        # Get user
        user = await UserModel.get(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )

        # Check if user has manager role
        if user.role != UserRole.MANAGER:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User must have MANAGER role to be assigned as team manager"
            )

        # Add user as manager to team
        success = team.add_manager(str(user.id))
        if not success:
            # User might already be a manager, update their role
            team.update_member_role(str(user.id), "manager")

        await team.save()

        # Update user's team assignments
        if team.team_id not in user.team_ids:
            user.team_ids.append(team.team_id)

        # Set as primary team if user has no primary team
        if not user.primary_team_id:
            user.primary_team_id = team.team_id

        user.updated_at = datetime.utcnow()
        await user.save()

        logger.info(
            f"User {user.email} assigned as manager to team {team.name} "
            f"by admin {current_user.get('email')}"
        )

        return {
            "success": True,
            "message": f"User {user.email} assigned as manager to team {team.name}",
            "team_id": team_id,
            "user_id": user_id,
            "updated_at": team.updated_at.isoformat()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error assigning manager to team: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error while assigning manager"
        )


@router.post("/teams/{team_id}/members")
async def add_member_to_team(
    team_id: str,
    user_id: str,
    role: str = Query("member", description="Role of member in team"),
    current_user: dict = Depends(get_admin_or_manager)
):
    """
    Add a member to a team.
    ADMIN can add to any team, MANAGER can only add to their managed teams.
    """
    try:
        # Check if user can manage this team
        manageable_teams = await get_user_manageable_teams(current_user)
        if manageable_teams is not None and len(manageable_teams) > 0 and team_id not in manageable_teams:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only manage your assigned teams"
            )

        # Get team
        team = await TeamModel.find_one(TeamModel.team_id == team_id)
        if not team:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Team not found"
            )

        # Get user
        user = await UserModel.get(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )

        # Validate role
        valid_roles = ["member", "reviewer", "approver", "manager"]
        if role not in valid_roles:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid role. Must be one of: {', '.join(valid_roles)}"
            )

        # Only admin can assign manager role
        if role == "manager" and "admin" not in current_user.get("roles", []):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only admin can assign manager role"
            )

        # Add member to team
        success = team.add_member(str(user.id), role)
        if not success:
            # User already exists, update role
            team.update_member_role(str(user.id), role)

        await team.save()

        # Update user's team assignments
        if team.team_id not in user.team_ids:
            user.team_ids.append(team.team_id)
            user.updated_at = datetime.utcnow()
            await user.save()

        logger.info(
            f"User {user.email} added to team {team.name} with role {role} "
            f"by {current_user.get('email')}"
        )

        return {
            "success": True,
            "message": f"User {user.email} added to team {team.name} as {role}",
            "team_id": team_id,
            "user_id": user_id,
            "role": role,
            "updated_at": team.updated_at.isoformat()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding member to team: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error while adding team member"
        )


@router.delete("/teams/{team_id}/members/{user_id}")
async def remove_member_from_team(
    team_id: str,
    user_id: str,
    current_user: dict = Depends(get_admin_or_manager)
):
    """
    Remove a member from a team.
    ADMIN can remove from any team, MANAGER can only remove from their managed teams.
    """
    try:
        # Check if user can manage this team
        manageable_teams = await get_user_manageable_teams(current_user)
        if manageable_teams is not None and len(manageable_teams) > 0 and team_id not in manageable_teams:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only manage your assigned teams"
            )

        # Get team
        team = await TeamModel.find_one(TeamModel.team_id == team_id)
        if not team:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Team not found"
            )

        # Get user
        user = await UserModel.get(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )

        # Remove member from team
        success = team.remove_member(str(user.id))
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User is not a member of this team"
            )

        await team.save()

        # Update user's team assignments
        if team.team_id in user.team_ids:
            user.team_ids.remove(team.team_id)

            # Update primary team if needed
            if user.primary_team_id == team.team_id:
                user.primary_team_id = user.team_ids[0] if user.team_ids else None

            user.updated_at = datetime.utcnow()
            await user.save()

        logger.info(
            f"User {user.email} removed from team {team.name} "
            f"by {current_user.get('email')}"
        )

        return {
            "success": True,
            "message": f"User {user.email} removed from team {team.name}",
            "team_id": team_id,
            "user_id": user_id,
            "updated_at": team.updated_at.isoformat()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error removing member from team: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error while removing team member"
        )


@router.put("/teams/{team_id}/members/{user_id}/role")
async def change_member_role(
    team_id: str,
    user_id: str,
    new_role: str,
    current_user: dict = Depends(get_admin_or_manager)
):
    """
    Change a team member's role.
    ADMIN can change roles in any team, MANAGER can only change roles in their managed teams.
    """
    try:
        # Check if user can manage this team
        manageable_teams = await get_user_manageable_teams(current_user)
        if manageable_teams is not None and len(manageable_teams) > 0 and team_id not in manageable_teams:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only manage your assigned teams"
            )

        # Validate role
        valid_roles = ["member", "reviewer", "approver", "manager"]
        if new_role not in valid_roles:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid role. Must be one of: {', '.join(valid_roles)}"
            )

        # Only admin can assign manager role
        if new_role == "manager" and "admin" not in current_user.get("roles", []):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only admin can assign manager role"
            )

        # Get team
        team = await TeamModel.find_one(TeamModel.team_id == team_id)
        if not team:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Team not found"
            )

        # Update member role
        success = team.update_member_role(str(user_id), new_role)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User is not a member of this team"
            )

        await team.save()

        logger.info(
            f"User role changed to {new_role} in team {team.name} "
            f"by {current_user.get('email')}"
        )

        return {
            "success": True,
            "message": f"Member role changed to {new_role}",
            "team_id": team_id,
            "user_id": user_id,
            "new_role": new_role,
            "updated_at": team.updated_at.isoformat()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error changing member role: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error while changing member role"
        )