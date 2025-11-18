"""
Admin user management API endpoints.
Only accessible by users with ADMIN (root) role.
"""

from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Query, Depends, status
from datetime import datetime

from ...models.user import UserModel, UserRole, UserStatus, Permission, UserCreate, UserResponse
from ...models.team import TeamModel
from ...schemas.admin_schemas import AdminUserCreateRequest, AdminUserUpdateRequest, AdminUserResponse
from ...auth.provider import get_current_user
from ...services.keycloak_sync import keycloak_sync_service
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


async def require_admin_root(current_user: dict = Depends(get_current_user)) -> dict:
    """Require ADMIN (root) role with full system access"""
    user_roles = current_user.get("roles", [])

    if "admin" not in user_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin (root) access required. Only admins can manage users."
        )

    return current_user


@router.get("/users", response_model=Dict[str, Any])
async def list_all_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    role: Optional[UserRole] = Query(None, description="Filter by role"),
    status: Optional[UserStatus] = Query(None, description="Filter by status"),
    team_id: Optional[str] = Query(None, description="Filter by team membership"),
    search: Optional[str] = Query(None, description="Search in email, username, or name"),
    current_user: dict = Depends(require_admin_root)
):
    """
    List all users in the system. Only accessible by ADMIN (root).
    Supports filtering and pagination.
    """
    try:
        # Build query filters
        query_filters = []

        if role:
            query_filters.append(UserModel.role == role)

        if status:
            query_filters.append(UserModel.status == status)

        if team_id:
            query_filters.append(UserModel.team_ids.contains(team_id))

        if search:
            # Search in email, username, or full_name (case insensitive)
            search_filter = {
                "$or": [
                    {"email": {"$regex": search, "$options": "i"}},
                    {"username": {"$regex": search, "$options": "i"}},
                    {"full_name": {"$regex": search, "$options": "i"}}
                ]
            }
            query_filters.append(search_filter)

        # Get users from Keycloak instead of MongoDB
        keycloak_users = await keycloak_sync_service.get_all_keycloak_users()

        # Get team assignments from MongoDB for all users
        team_assignments = {}
        teams = await TeamModel.find_all().to_list()
        for team in teams:
            for member in team.members:
                if member.user_id not in team_assignments:
                    team_assignments[member.user_id] = []
                team_assignments[member.user_id].append({
                    'team_id': team.team_id,
                    'team_name': team.name,
                    'role': member.role,
                    'is_active': member.is_active
                })

        # Convert Keycloak users to UserResponse format
        user_responses = []
        for kc_user in keycloak_users:
            # Extract user attributes from Keycloak format
            user_id = kc_user.get('id', '')
            email = kc_user.get('email', '')
            username = kc_user.get('username', '')
            first_name = kc_user.get('firstName', '')
            last_name = kc_user.get('lastName', '')
            full_name = f"{first_name} {last_name}".strip() or username
            enabled = kc_user.get('enabled', False)
            realm_roles = kc_user.get('realmRoles', [])
            attributes = kc_user.get('attributes', {})

            # Map Keycloak roles to our system roles
            user_role = 'viewer'  # default
            if 'admin' in realm_roles:
                user_role = 'admin'
            elif 'manager' in realm_roles:
                user_role = 'manager'
            elif 'reviewer' in realm_roles:
                user_role = 'reviewer'
            elif 'approver' in realm_roles:
                user_role = 'approver'

            # Apply filtering
            if role and user_role != role.value:
                continue
            if status and ((status.value == 'active' and not enabled) or (status.value != 'active' and enabled)):
                continue
            if search and not (
                search.lower() in email.lower() or
                search.lower() in username.lower() or
                search.lower() in full_name.lower()
            ):
                continue

            # Get team info for this user
            user_teams = team_assignments.get(user_id, [])
            team_ids = [t['team_id'] for t in user_teams if t['is_active']]
            primary_team_id = team_ids[0] if team_ids else None

            user_response = UserResponse(
                id=user_id,
                email=email,
                username=username,
                full_name=full_name,
                role=UserRole(user_role),
                status=UserStatus.ACTIVE if enabled else UserStatus.INACTIVE,
                permissions=[],  # Will be calculated from role
                created_at=datetime.utcnow(),  # Keycloak doesn't provide this easily
                updated_at=datetime.utcnow(),
                last_login=None,  # Could extract from sessions if needed
                department=attributes.get('department', [''])[0] if 'department' in attributes else '',
                phone=attributes.get('phone', [''])[0] if 'phone' in attributes else '',
                avatar_url=None,
                email_notifications=True,
                two_factor_enabled=False,
                team_ids=team_ids,
                primary_team_id=primary_team_id,
                max_concurrent_tasks=10,
                specializations=[],
                availability_status='available'
            )
            user_responses.append(user_response)

        # Apply pagination
        total = len(user_responses)
        user_responses = user_responses[skip:skip + limit]

        return {
            "users": user_responses,
            "pagination": {
                "total": total,
                "skip": skip,
                "limit": limit,
                "has_more": skip + limit < total
            }
        }

    except Exception as e:
        logger.error(f"Error listing users: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error while listing users"
        )


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    user_data: UserCreate,
    sync_to_keycloak: bool = Query(True, description="Sync user to Keycloak"),
    current_user: dict = Depends(require_admin_root)
):
    """
    Create a new user. Only accessible by ADMIN (root).
    Optionally syncs to Keycloak.
    """
    try:
        # Check if user already exists
        existing_user = await UserModel.find_one(
            {"$or": [
                {"email": user_data.email},
                {"username": user_data.username}
            ]}
        )

        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User with this email or username already exists"
            )

        # Create new user
        new_user = UserModel(
            email=user_data.email,
            username=user_data.username,
            full_name=user_data.full_name,
            hashed_password=UserModel.hash_password(user_data.password),
            role=user_data.role,
            status=UserStatus.ACTIVE,
            department=user_data.department,
            phone=user_data.phone,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )

        await new_user.save()

        # Sync to Keycloak if requested
        if sync_to_keycloak:
            try:
                sync_success = await keycloak_sync_service.sync_user_to_keycloak(new_user)
                if not sync_success:
                    logger.warning(f"Failed to sync user {new_user.email} to Keycloak")
            except Exception as e:
                logger.error(f"Error syncing user to Keycloak: {e}")

        # Convert to response
        user_response = UserResponse(
            id=str(new_user.id),
            email=new_user.email,
            username=new_user.username,
            full_name=new_user.full_name,
            role=new_user.role,
            status=new_user.status,
            permissions=list(new_user.permissions) if new_user.permissions else [],
            created_at=new_user.created_at,
            updated_at=new_user.updated_at,
            last_login=new_user.last_login,
            department=new_user.department,
            phone=new_user.phone,
            avatar_url=new_user.avatar_url,
            email_notifications=new_user.email_notifications,
            two_factor_enabled=new_user.two_factor_enabled,
            team_ids=new_user.team_ids,
            primary_team_id=new_user.primary_team_id,
            max_concurrent_tasks=new_user.max_concurrent_tasks,
            specializations=new_user.specializations,
            availability_status=new_user.availability_status
        )

        logger.info(f"User {new_user.email} created by admin {current_user.get('email')}")
        return user_response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating user: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error while creating user"
        )


@router.put("/users/{user_id}/role")
async def change_user_role(
    user_id: str,
    new_role: UserRole,
    sync_to_keycloak: bool = Query(True, description="Sync role change to Keycloak"),
    current_user: dict = Depends(require_admin_root)
):
    """
    Change a user's role. Only accessible by ADMIN (root).
    Critical operation that's logged and audited.
    """
    try:
        user = await UserModel.get(user_id)

        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )

        old_role = user.role
        user.role = new_role
        user.updated_at = datetime.utcnow()
        await user.save()

        # Sync to Keycloak if requested
        if sync_to_keycloak:
            try:
                sync_success = await keycloak_sync_service.sync_user_to_keycloak(user)
                if not sync_success:
                    logger.warning(f"Failed to sync role change to Keycloak for user {user.email}")
            except Exception as e:
                logger.error(f"Error syncing role change to Keycloak: {e}")

        logger.info(
            f"Role changed for user {user.email}: {old_role.value} -> {new_role.value} "
            f"by admin {current_user.get('email')}"
        )

        return {
            "success": True,
            "message": f"User role changed from {old_role.value} to {new_role.value}",
            "user_id": user_id,
            "old_role": old_role.value,
            "new_role": new_role.value,
            "updated_at": user.updated_at.isoformat()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error changing user role {user_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error while changing user role"
        )


@router.put("/users/{user_id}/teams")
async def assign_user_to_teams(
    user_id: str,
    team_ids: List[str],
    primary_team_id: Optional[str] = None,
    current_user: dict = Depends(require_admin_root)
):
    """
    Assign user to teams. Only accessible by ADMIN (root).
    Also updates team memberships accordingly.
    """
    try:
        user = await UserModel.get(user_id)

        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )

        # Validate that all teams exist
        teams = await TeamModel.find({"team_id": {"$in": team_ids}}).to_list()
        found_team_ids = [team.team_id for team in teams]

        missing_teams = set(team_ids) - set(found_team_ids)
        if missing_teams:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Teams not found: {', '.join(missing_teams)}"
            )

        # Remove user from old teams
        old_teams = await TeamModel.find({"members.user_id": str(user.id)}).to_list()
        for old_team in old_teams:
            old_team.remove_member(str(user.id))
            await old_team.save()

        # Update user's team assignments
        user.team_ids = team_ids
        if primary_team_id and primary_team_id in team_ids:
            user.primary_team_id = primary_team_id
        elif team_ids:
            user.primary_team_id = team_ids[0]
        else:
            user.primary_team_id = None

        user.updated_at = datetime.utcnow()
        await user.save()

        # Add user to new teams
        for team in teams:
            team.add_member(str(user.id), role="member")
            await team.save()

        # Sync to Keycloak groups
        try:
            sync_success = await keycloak_sync_service.sync_user_to_team_groups(user)
            if not sync_success:
                logger.warning(f"Failed to sync team assignments to Keycloak for user {user.email}")
        except Exception as e:
            logger.error(f"Error syncing team assignments to Keycloak: {e}")

        logger.info(
            f"User {user.email} assigned to teams {team_ids} by admin {current_user.get('email')}"
        )

        return {
            "success": True,
            "message": f"User assigned to {len(team_ids)} teams",
            "user_id": user_id,
            "team_ids": team_ids,
            "primary_team_id": user.primary_team_id,
            "updated_at": user.updated_at.isoformat()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error assigning user to teams {user_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error while assigning user to teams"
        )