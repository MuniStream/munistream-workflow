"""
Administrative schemas for team and user management.
These are used by admin endpoints and include additional administrative fields.
"""

from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

from ..models.user import UserRole, UserStatus


class AdminUserListRequest(BaseModel):
    """Request schema for listing users with admin filters"""
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=100)
    role: Optional[UserRole] = None
    status: Optional[UserStatus] = None
    team_id: Optional[str] = None
    search: Optional[str] = None


class AdminUserCreateRequest(BaseModel):
    """Request schema for creating users by admin"""
    email: str = Field(..., description="User email address")
    username: str = Field(..., min_length=3, max_length=50)
    full_name: str = Field(..., min_length=2, max_length=100)
    password: str = Field(..., min_length=8)
    role: UserRole = Field(default=UserRole.VIEWER)
    department: Optional[str] = Field(None, max_length=100)
    phone: Optional[str] = Field(None, max_length=20)
    sync_to_keycloak: bool = Field(default=True)


class AdminUserUpdateRequest(BaseModel):
    """Request schema for updating users by admin"""
    full_name: Optional[str] = Field(None, min_length=2, max_length=100)
    role: Optional[UserRole] = None
    status: Optional[UserStatus] = None
    department: Optional[str] = Field(None, max_length=100)
    phone: Optional[str] = Field(None, max_length=20)
    team_ids: Optional[List[str]] = None
    primary_team_id: Optional[str] = None
    sync_to_keycloak: bool = Field(default=True)


class AdminRoleChangeRequest(BaseModel):
    """Request schema for changing user roles"""
    new_role: UserRole
    sync_to_keycloak: bool = Field(default=True)


class AdminTeamAssignmentRequest(BaseModel):
    """Request schema for assigning users to teams"""
    team_ids: List[str]
    primary_team_id: Optional[str] = None


class AdminTeamListRequest(BaseModel):
    """Request schema for listing teams with admin filters"""
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=100)
    department: Optional[str] = None
    search: Optional[str] = None


class AdminTeamCreateRequest(BaseModel):
    """Request schema for creating teams by admin"""
    team_id: str = Field(..., min_length=3, max_length=50, description="Unique team identifier")
    name: str = Field(..., min_length=2, max_length=100, description="Team name")
    description: Optional[str] = Field(None, max_length=500)
    department: Optional[str] = Field(None, max_length=100)
    max_concurrent_tasks: int = Field(default=10, ge=1, le=100)
    specializations: List[str] = Field(default_factory=list)
    working_hours: Dict[str, Any] = Field(default_factory=dict)
    sync_to_keycloak: bool = Field(default=True)


class AdminManagerAssignmentRequest(BaseModel):
    """Request schema for assigning managers to teams"""
    user_id: str


class AdminTeamMemberRequest(BaseModel):
    """Request schema for adding/updating team members"""
    user_id: str
    role: str = Field(default="member", pattern="^(member|reviewer|approver|manager)$")


class AdminSyncStatusResponse(BaseModel):
    """Response schema for sync status"""
    success: bool
    timestamp: str
    requested_by: str
    sync_status: Dict[str, Any]


class AdminSyncOperationResponse(BaseModel):
    """Response schema for sync operations"""
    success: bool
    operation: str
    timestamp: str
    requested_by: str
    results: Dict[str, Any]
    message: str


class AdminUserResponse(BaseModel):
    """Enhanced user response for admin endpoints"""
    id: str
    email: str
    username: str
    full_name: str
    role: UserRole
    status: UserStatus
    department: Optional[str]
    phone: Optional[str]
    team_ids: List[str]
    primary_team_id: Optional[str]
    created_at: datetime
    updated_at: datetime
    last_login: Optional[datetime]

    # Admin-specific fields
    team_count: int
    is_admin_root: bool
    is_team_administrator: bool
    manageable_teams: List[str]


class AdminTeamResponse(BaseModel):
    """Enhanced team response for admin endpoints"""
    team_id: str
    name: str
    description: Optional[str]
    department: Optional[str]
    max_concurrent_tasks: int
    specializations: List[str]
    working_hours: Dict[str, Any]
    assigned_workflows: List[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime
    created_by: Optional[str]

    # Member information
    members: List[Dict[str, Any]]
    member_count: int
    manager_count: int

    # Admin-specific fields
    managers: List[str]  # List of manager user IDs
    can_current_user_manage: bool


class AdminUserListResponse(BaseModel):
    """Response schema for user list"""
    users: List[AdminUserResponse]
    pagination: Dict[str, Any]


class AdminTeamListResponse(BaseModel):
    """Response schema for team list"""
    teams: List[AdminTeamResponse]
    pagination: Dict[str, Any]


class AdminOperationResponse(BaseModel):
    """Generic response for admin operations"""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None
    timestamp: str


class AdminFullSyncResponse(BaseModel):
    """Response schema for full synchronization"""
    success: bool
    operation: str
    timestamp: str
    requested_by: str
    results: Dict[str, Any]
    summary: Dict[str, Any]
    message: str


# Sync-specific schemas
class SyncResults(BaseModel):
    """Results of a sync operation"""
    success: int
    failed: int
    total: int


class ImportResults(BaseModel):
    """Results of an import operation"""
    imported: int
    updated: int
    failed: int


class FullSyncResults(BaseModel):
    """Results of a full sync operation"""
    users_to_keycloak: SyncResults
    teams_to_keycloak: SyncResults
    import_from_keycloak: Dict[str, ImportResults]