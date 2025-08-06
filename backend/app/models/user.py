"""
User authentication models for CivicStream admin system.
"""

from datetime import datetime, timedelta
from typing import Optional, List
from enum import Enum
from pydantic import BaseModel, Field, EmailStr
from beanie import Document, Indexed
from passlib.context import CryptContext
import jwt

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

class UserRole(str, Enum):
    """User role enumeration"""
    ADMIN = "admin"
    MANAGER = "manager"
    REVIEWER = "reviewer"
    APPROVER = "approver"
    VIEWER = "viewer"

class UserStatus(str, Enum):
    """User status enumeration"""
    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"
    PENDING = "pending"

class Permission(str, Enum):
    """Permission enumeration"""
    # Document permissions
    VIEW_DOCUMENTS = "view_documents"
    VERIFY_DOCUMENTS = "verify_documents"
    APPROVE_DOCUMENTS = "approve_documents"
    DELETE_DOCUMENTS = "delete_documents"
    
    # Workflow permissions
    VIEW_WORKFLOWS = "view_workflows"
    MANAGE_WORKFLOWS = "manage_workflows"
    EXECUTE_WORKFLOWS = "execute_workflows"
    
    # Admin permissions
    MANAGE_USERS = "manage_users"
    VIEW_ANALYTICS = "view_analytics"
    MANAGE_SYSTEM = "manage_system"
    
    # Instance permissions
    VIEW_INSTANCES = "view_instances"
    MANAGE_INSTANCES = "manage_instances"

# Role-based permissions mapping
ROLE_PERMISSIONS = {
    UserRole.ADMIN: [
        Permission.VIEW_DOCUMENTS,
        Permission.VERIFY_DOCUMENTS,
        Permission.APPROVE_DOCUMENTS,
        Permission.DELETE_DOCUMENTS,
        Permission.VIEW_WORKFLOWS,
        Permission.MANAGE_WORKFLOWS,
        Permission.EXECUTE_WORKFLOWS,
        Permission.MANAGE_USERS,
        Permission.VIEW_ANALYTICS,
        Permission.MANAGE_SYSTEM,
        Permission.VIEW_INSTANCES,
        Permission.MANAGE_INSTANCES,
    ],
    UserRole.MANAGER: [
        Permission.VIEW_DOCUMENTS,
        Permission.VERIFY_DOCUMENTS,
        Permission.APPROVE_DOCUMENTS,
        Permission.VIEW_WORKFLOWS,
        Permission.EXECUTE_WORKFLOWS,
        Permission.VIEW_ANALYTICS,
        Permission.VIEW_INSTANCES,
        Permission.MANAGE_INSTANCES,
    ],
    UserRole.REVIEWER: [
        Permission.VIEW_DOCUMENTS,
        Permission.VERIFY_DOCUMENTS,
        Permission.VIEW_WORKFLOWS,
        Permission.VIEW_INSTANCES,
    ],
    UserRole.APPROVER: [
        Permission.VIEW_DOCUMENTS,
        Permission.APPROVE_DOCUMENTS,
        Permission.VIEW_WORKFLOWS,
        Permission.EXECUTE_WORKFLOWS,
        Permission.VIEW_INSTANCES,
    ],
    UserRole.VIEWER: [
        Permission.VIEW_DOCUMENTS,
        Permission.VIEW_WORKFLOWS,
        Permission.VIEW_INSTANCES,
        Permission.VIEW_ANALYTICS,
    ],
}

class UserModel(Document):
    """User model for authentication and authorization"""
    
    # Basic user information
    email: Indexed(EmailStr, unique=True)
    username: Indexed(str, unique=True)
    full_name: str
    hashed_password: str
    
    # Role and permissions
    role: UserRole = Field(default=UserRole.VIEWER)
    status: UserStatus = Field(default=UserStatus.PENDING)
    permissions: List[Permission] = Field(default_factory=list)
    
    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_login: Optional[datetime] = None
    failed_login_attempts: int = Field(default=0)
    locked_until: Optional[datetime] = None
    
    # Profile information
    department: Optional[str] = None
    phone: Optional[str] = None
    avatar_url: Optional[str] = None
    
    # Settings
    email_notifications: bool = Field(default=True)
    two_factor_enabled: bool = Field(default=False)
    
    # Team memberships
    team_ids: List[str] = Field(default_factory=list, description="Teams this user belongs to")
    primary_team_id: Optional[str] = Field(None, description="Primary team for task assignment")
    
    # Work capacity and availability
    max_concurrent_tasks: int = Field(default=3, description="Maximum concurrent tasks for this user")
    specializations: List[str] = Field(default_factory=list, description="User specializations/skills")
    availability_status: str = Field(default="available", description="Current availability status")  # available, busy, away, offline
    
    class Settings:
        name = "users"
        use_state_management = True
    
    def verify_password(self, password: str) -> bool:
        """Verify password against stored hash"""
        return pwd_context.verify(password, self.hashed_password)
    
    @staticmethod
    def hash_password(password: str) -> str:
        """Hash a password"""
        return pwd_context.hash(password)
    
    def has_permission(self, permission: Permission) -> bool:
        """Check if user has a specific permission"""
        if self.status != UserStatus.ACTIVE:
            return False
        
        # Check explicit permissions
        if permission in self.permissions:
            return True
        
        # Check role-based permissions
        role_permissions = ROLE_PERMISSIONS.get(self.role, [])
        return permission in role_permissions
    
    def has_any_permission(self, permissions: List[Permission]) -> bool:
        """Check if user has any of the specified permissions"""
        return any(self.has_permission(perm) for perm in permissions)
    
    def has_all_permissions(self, permissions: List[Permission]) -> bool:
        """Check if user has all specified permissions"""
        return all(self.has_permission(perm) for perm in permissions)
    
    def is_locked(self) -> bool:
        """Check if user account is locked"""
        if self.locked_until is None:
            return False
        return datetime.utcnow() < self.locked_until
    
    def lock_account(self, duration_minutes: int = 30):
        """Lock user account for specified duration"""
        self.locked_until = datetime.utcnow() + timedelta(minutes=duration_minutes)
        self.failed_login_attempts = 0
    
    def unlock_account(self):
        """Unlock user account"""
        self.locked_until = None
        self.failed_login_attempts = 0
    
    def update_last_login(self):
        """Update last login timestamp"""
        self.last_login = datetime.utcnow()
        self.failed_login_attempts = 0
    
    def increment_failed_attempts(self):
        """Increment failed login attempts"""
        self.failed_login_attempts += 1
        # Lock account after 5 failed attempts
        if self.failed_login_attempts >= 5:
            self.lock_account()
    
    # Team-related methods
    def add_to_team(self, team_id: str, is_primary: bool = False) -> bool:
        """Add user to a team"""
        if team_id not in self.team_ids:
            self.team_ids.append(team_id)
            if is_primary or not self.primary_team_id:
                self.primary_team_id = team_id
            self.updated_at = datetime.utcnow()
            return True
        return False
    
    def remove_from_team(self, team_id: str) -> bool:
        """Remove user from a team"""
        if team_id in self.team_ids:
            self.team_ids.remove(team_id)
            if self.primary_team_id == team_id:
                # Assign new primary team if available
                self.primary_team_id = self.team_ids[0] if self.team_ids else None
            self.updated_at = datetime.utcnow()
            return True
        return False
    
    def set_primary_team(self, team_id: str) -> bool:
        """Set primary team for user"""
        if team_id in self.team_ids:
            self.primary_team_id = team_id
            self.updated_at = datetime.utcnow()
            return True
        return False
    
    def is_available_for_task(self) -> bool:
        """Check if user is available for new tasks"""
        return (
            self.status == UserStatus.ACTIVE and
            self.availability_status == "available" and
            not self.is_account_locked()
        )
    
    def has_specialization(self, required_skills: List[str]) -> bool:
        """Check if user has required specializations"""
        if not required_skills:
            return True
        return any(skill in self.specializations for skill in required_skills)
    
    def get_team_count(self) -> int:
        """Get number of teams user belongs to"""
        return len(self.team_ids)

class RefreshTokenModel(Document):
    """Refresh token model for JWT token management"""
    
    user_id: str = Field(..., index=True)
    token: str = Field(..., unique=True)
    expires_at: datetime
    created_at: datetime = Field(default_factory=datetime.utcnow)
    is_revoked: bool = Field(default=False)
    device_info: Optional[str] = None
    ip_address: Optional[str] = None
    
    class Settings:
        name = "refresh_tokens"
        use_state_management = True
    
    def is_valid(self) -> bool:
        """Check if refresh token is valid"""
        return not self.is_revoked and datetime.utcnow() < self.expires_at
    
    def revoke(self):
        """Revoke the refresh token"""
        self.is_revoked = True

# Pydantic models for API
class UserCreate(BaseModel):
    """User creation schema"""
    email: EmailStr
    username: str
    full_name: str
    password: str
    role: UserRole = UserRole.VIEWER
    department: Optional[str] = None
    phone: Optional[str] = None

class UserUpdate(BaseModel):
    """User update schema"""
    full_name: Optional[str] = None
    role: Optional[UserRole] = None
    status: Optional[UserStatus] = None
    department: Optional[str] = None
    phone: Optional[str] = None
    email_notifications: Optional[bool] = None
    permissions: Optional[List[Permission]] = None
    team_ids: Optional[List[str]] = None
    primary_team_id: Optional[str] = None
    max_concurrent_tasks: Optional[int] = None
    specializations: Optional[List[str]] = None
    availability_status: Optional[str] = None

class UserResponse(BaseModel):
    """User response schema"""
    id: str
    email: str
    username: str
    full_name: str
    role: UserRole
    status: UserStatus
    permissions: List[Permission]
    created_at: datetime
    updated_at: datetime
    last_login: Optional[datetime]
    department: Optional[str]
    phone: Optional[str]
    avatar_url: Optional[str]
    email_notifications: bool
    two_factor_enabled: bool
    team_ids: Optional[List[str]] = None
    primary_team_id: Optional[str] = None
    max_concurrent_tasks: Optional[int] = None
    specializations: Optional[List[str]] = None
    availability_status: Optional[str] = None

class LoginRequest(BaseModel):
    """Login request schema"""
    username: str
    password: str
    remember_me: bool = False

class LoginResponse(BaseModel):
    """Login response schema"""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse

class TokenResponse(BaseModel):
    """Token response schema"""
    access_token: str
    token_type: str = "bearer"
    expires_in: int

class PasswordChangeRequest(BaseModel):
    """Password change request schema"""
    current_password: str
    new_password: str

class PasswordResetRequest(BaseModel):
    """Password reset request schema"""
    email: EmailStr

class PasswordResetConfirm(BaseModel):
    """Password reset confirmation schema"""
    token: str
    new_password: str