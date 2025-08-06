"""
Authentication API endpoints.
"""

from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import APIRouter, HTTPException, status, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials

from ...models.user import (
    UserModel, RefreshTokenModel, UserCreate, UserUpdate, UserResponse,
    LoginRequest, LoginResponse, TokenResponse, PasswordChangeRequest,
    PasswordResetRequest, PasswordResetConfirm, UserRole, UserStatus, Permission
)
from ...services.auth_service import (
    AuthService, get_current_user, require_admin, require_manager_or_admin,
    require_permission, require_any_role, ACCESS_TOKEN_EXPIRE_MINUTES
)
from ...core.locale import get_locale_from_request
from ...core.i18n import t

router = APIRouter()

def convert_user_to_response(user: UserModel) -> UserResponse:
    """Convert UserModel to UserResponse with proper permission handling"""
    from ...models.user import ROLE_PERMISSIONS
    
    # Get role-based permissions
    role_permissions = ROLE_PERMISSIONS.get(user.role, [])
    
    # Convert explicit user permissions to strings
    explicit_permissions = []
    for p in user.permissions:
        if hasattr(p, 'value'):
            explicit_permissions.append(p.value)
        elif isinstance(p, str):
            # Handle case where it's stored as "Permission.VIEW_DOCUMENTS"
            if p.startswith('Permission.'):
                explicit_permissions.append(p.replace('Permission.', '').lower())
            else:
                explicit_permissions.append(p)
        else:
            explicit_permissions.append(str(p))
    
    # Combine role permissions and explicit permissions (remove duplicates)
    all_permissions = list(set([perm.value for perm in role_permissions] + explicit_permissions))
    
    return UserResponse(
        id=str(user.id),
        email=user.email,
        username=user.username,
        full_name=user.full_name,
        role=user.role,
        status=user.status,
        permissions=all_permissions,
        created_at=user.created_at,
        updated_at=user.updated_at,
        last_login=user.last_login,
        department=user.department,
        phone=user.phone,
        avatar_url=user.avatar_url,
        email_notifications=user.email_notifications,
        two_factor_enabled=user.two_factor_enabled,
        team_ids=user.team_ids,
        primary_team_id=user.primary_team_id,
        max_concurrent_tasks=user.max_concurrent_tasks,
        specializations=user.specializations,
        availability_status=user.availability_status
    )

@router.post("/login", response_model=LoginResponse)
async def login(login_data: LoginRequest, request: Request):
    """Authenticate user and return JWT tokens"""
    locale = get_locale_from_request(request)
    
    try:
        # Authenticate user
        user = await AuthService.authenticate_user(login_data.username, login_data.password)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=t("auth.invalid_credentials", locale)
            )
        
        # Create tokens
        access_token = AuthService.create_access_token(
            data={"sub": str(user.id), "username": user.username, "role": user.role}
        )
        
        # Get client info
        user_agent = request.headers.get("user-agent", "Unknown")
        client_ip = request.client.host if request.client else "Unknown"
        
        # Create and save refresh token
        refresh_token = AuthService.create_refresh_token(
            user_id=str(user.id),
            device_info=user_agent,
            ip_address=client_ip
        )
        
        # Save refresh token to database
        refresh_token_doc = RefreshTokenModel(
            user_id=str(user.id),
            token=refresh_token,
            expires_at=datetime.utcnow() + timedelta(days=7),
            device_info=user_agent,
            ip_address=client_ip
        )
        await refresh_token_doc.save()
        
        # Convert user to response format
        user_response = convert_user_to_response(user)
        
        return LoginResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            user=user_response
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Login failed: {str(e)}"
        )

@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(refresh_token: str):
    """Refresh access token using refresh token"""
    try:
        token_data = await AuthService.refresh_access_token(refresh_token)
        return TokenResponse(**token_data)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Token refresh failed: {str(e)}"
        )

@router.post("/logout")
async def logout(refresh_token: str, current_user: UserModel = Depends(get_current_user)):
    """Logout user and revoke refresh token"""
    try:
        await AuthService.revoke_refresh_token(refresh_token)
        return {"message": "Successfully logged out"}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Logout failed: {str(e)}"
        )

@router.post("/logout-all")
async def logout_all(current_user: UserModel = Depends(get_current_user)):
    """Logout from all devices (revoke all refresh tokens)"""
    try:
        await AuthService.revoke_all_user_tokens(str(current_user.id))
        return {"message": "Successfully logged out from all devices"}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Logout all failed: {str(e)}"
        )

@router.get("/me", response_model=UserResponse)
async def get_current_user_info(current_user: UserModel = Depends(get_current_user)):
    """Get current user information"""
    return convert_user_to_response(current_user)

@router.put("/me", response_model=UserResponse)
async def update_current_user(
    user_update: UserUpdate,
    current_user: UserModel = Depends(get_current_user)
):
    """Update current user profile"""
    try:
        # Update allowed fields
        if user_update.full_name is not None:
            current_user.full_name = user_update.full_name
        if user_update.department is not None:
            current_user.department = user_update.department
        if user_update.phone is not None:
            current_user.phone = user_update.phone
        if user_update.email_notifications is not None:
            current_user.email_notifications = user_update.email_notifications
        
        current_user.updated_at = datetime.utcnow()
        await current_user.save()
        
        return UserResponse(
            id=str(current_user.id),
            email=current_user.email,
            username=current_user.username,
            full_name=current_user.full_name,
            role=current_user.role,
            status=current_user.status,
            permissions=[p.value if hasattr(p, 'value') else str(p) for p in current_user.permissions],
            created_at=current_user.created_at,
            updated_at=current_user.updated_at,
            last_login=current_user.last_login,
            department=current_user.department,
            phone=current_user.phone,
            avatar_url=current_user.avatar_url,
            email_notifications=current_user.email_notifications,
            two_factor_enabled=current_user.two_factor_enabled
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Update failed: {str(e)}"
        )

@router.post("/change-password")
async def change_password(
    password_data: PasswordChangeRequest,
    current_user: UserModel = Depends(get_current_user)
):
    """Change user password"""
    try:
        # Verify current password
        if not current_user.verify_password(password_data.current_password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is incorrect"
            )
        
        # Update password
        current_user.hashed_password = UserModel.hash_password(password_data.new_password)
        current_user.updated_at = datetime.utcnow()
        await current_user.save()
        
        # Revoke all refresh tokens to force re-login
        await AuthService.revoke_all_user_tokens(str(current_user.id))
        
        return {"message": "Password changed successfully. Please log in again."}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Password change failed: {str(e)}"
        )

# User management endpoints (admin only)
@router.post("/users", response_model=UserResponse)
async def create_user(
    user_data: UserCreate,
    current_user: UserModel = Depends(require_admin())
):
    """Create new user (admin only)"""
    try:
        # Check if username or email already exists
        existing_user = await UserModel.find_one(
            {"$or": [{"username": user_data.username}, {"email": user_data.email}]}
        )
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username or email already exists"
            )
        
        # Create new user
        user = UserModel(
            email=user_data.email,
            username=user_data.username,
            full_name=user_data.full_name,
            hashed_password=UserModel.hash_password(user_data.password),
            role=user_data.role,
            status=UserStatus.ACTIVE,
            department=user_data.department,
            phone=user_data.phone
        )
        
        await user.save()
        
        return UserResponse(
            id=str(user.id),
            email=user.email,
            username=user.username,
            full_name=user.full_name,
            role=user.role,
            status=user.status,
            permissions=user.permissions,
            created_at=user.created_at,
            updated_at=user.updated_at,
            last_login=user.last_login,
            department=user.department,
            phone=user.phone,
            avatar_url=user.avatar_url,
            email_notifications=user.email_notifications,
            two_factor_enabled=user.two_factor_enabled
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"User creation failed: {str(e)}"
        )

@router.get("/users", response_model=List[UserResponse])
async def list_users(
    skip: int = 0,
    limit: int = 50,
    role: Optional[UserRole] = None,
    status: Optional[UserStatus] = None,
    current_user: UserModel = Depends(require_manager_or_admin())
):
    """List users with filtering (admin/manager only)"""
    try:
        query = {}
        if role:
            query["role"] = role
        if status:
            query["status"] = status
        
        users = await UserModel.find(query).skip(skip).limit(limit).to_list()
        
        return [
            UserResponse(
                id=str(user.id),
                email=user.email,
                username=user.username,
                full_name=user.full_name,
                role=user.role,
                status=user.status,
                permissions=user.permissions,
                created_at=user.created_at,
                updated_at=user.updated_at,
                last_login=user.last_login,
                department=user.department,
                phone=user.phone,
                avatar_url=user.avatar_url,
                email_notifications=user.email_notifications,
                two_factor_enabled=user.two_factor_enabled
            )
            for user in users
        ]
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list users: {str(e)}"
        )

@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: str,
    current_user: UserModel = Depends(require_manager_or_admin())
):
    """Get user by ID (admin/manager only)"""
    try:
        user = await UserModel.get(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        return UserResponse(
            id=str(user.id),
            email=user.email,
            username=user.username,
            full_name=user.full_name,
            role=user.role,
            status=user.status,
            permissions=user.permissions,
            created_at=user.created_at,
            updated_at=user.updated_at,
            last_login=user.last_login,
            department=user.department,
            phone=user.phone,
            avatar_url=user.avatar_url,
            email_notifications=user.email_notifications,
            two_factor_enabled=user.two_factor_enabled
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get user: {str(e)}"
        )

@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    user_update: UserUpdate,
    current_user: UserModel = Depends(require_admin())
):
    """Update user (admin only)"""
    try:
        user = await UserModel.get(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        # Update fields
        if user_update.full_name is not None:
            user.full_name = user_update.full_name
        if user_update.role is not None:
            user.role = user_update.role
        if user_update.status is not None:
            user.status = user_update.status
        if user_update.department is not None:
            user.department = user_update.department
        if user_update.phone is not None:
            user.phone = user_update.phone
        if user_update.email_notifications is not None:
            user.email_notifications = user_update.email_notifications
        if user_update.permissions is not None:
            user.permissions = user_update.permissions
        if user_update.team_ids is not None:
            user.team_ids = user_update.team_ids
        if user_update.primary_team_id is not None:
            user.primary_team_id = user_update.primary_team_id
        if user_update.max_concurrent_tasks is not None:
            user.max_concurrent_tasks = user_update.max_concurrent_tasks
        if user_update.specializations is not None:
            user.specializations = user_update.specializations
        if user_update.availability_status is not None:
            user.availability_status = user_update.availability_status
        
        user.updated_at = datetime.utcnow()
        await user.save()
        
        return convert_user_to_response(user)
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"User update failed: {str(e)}"
        )

@router.delete("/users/{user_id}")
async def delete_user(
    user_id: str,
    current_user: UserModel = Depends(require_admin())
):
    """Delete user (admin only)"""
    try:
        # Prevent self-deletion
        if str(current_user.id) == user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete your own account"
            )
        
        user = await UserModel.get(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        # Revoke all tokens
        await AuthService.revoke_all_user_tokens(user_id)
        
        # Delete user
        await user.delete()
        
        return {"message": "User deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"User deletion failed: {str(e)}"
        )

@router.get("/permissions", response_model=List[str])
async def list_permissions():
    """List all available permissions"""
    return [perm.value for perm in Permission]

@router.get("/roles", response_model=List[str])
async def list_roles():
    """List all available roles"""
    return [role.value for role in UserRole]

@router.get("/users-for-assignment", response_model=List[UserResponse])
async def list_users_for_assignment(
    current_user: UserModel = Depends(require_any_role([UserRole.REVIEWER, UserRole.MANAGER, UserRole.ADMIN]))
):
    """List users for instance assignment purposes (reviewer+ access)"""
    try:
        # Get active users with reviewer, approver, manager, or admin roles
        query = {
            "status": UserStatus.ACTIVE,
            "role": {"$in": [UserRole.REVIEWER.value, UserRole.APPROVER.value, UserRole.MANAGER.value, UserRole.ADMIN.value]}
        }
        
        users = await UserModel.find(query).to_list()
        
        return [convert_user_to_response(user) for user in users]
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list users for assignment: {str(e)}"
        )