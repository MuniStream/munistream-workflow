"""
Team schemas for API requests and responses.
"""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field


class TeamMemberSchema(BaseModel):
    """Team member schema"""
    user_id: str
    role: str = "member"  # member, manager, reviewer, approver
    joined_at: datetime
    is_active: bool = True


class TeamMemberCreate(BaseModel):
    """Schema for adding a member to a team"""
    user_id: str
    role: str = Field(default="member", pattern="^(member|manager|reviewer|approver)$")


class TeamMemberUpdate(BaseModel):
    """Schema for updating a team member"""
    role: Optional[str] = Field(None, pattern="^(member|manager|reviewer|approver)$")
    is_active: Optional[bool] = None


class TeamCreate(BaseModel):
    """Schema for creating a new team"""
    team_id: str = Field(..., min_length=3, max_length=50, description="Unique team identifier")
    name: str = Field(..., min_length=2, max_length=100, description="Team name")
    description: Optional[str] = Field(None, max_length=500, description="Team description")
    department: Optional[str] = Field(None, max_length=100, description="Department or area")
    max_concurrent_tasks: int = Field(default=10, ge=1, le=100, description="Maximum concurrent tasks")
    specializations: List[str] = Field(default_factory=list, description="Team specializations")
    working_hours: dict = Field(default_factory=dict, description="Working hours configuration")


class TeamUpdate(BaseModel):
    """Schema for updating a team"""
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    department: Optional[str] = Field(None, max_length=100)
    max_concurrent_tasks: Optional[int] = Field(None, ge=1, le=100)
    specializations: Optional[List[str]] = None
    working_hours: Optional[dict] = None
    is_active: Optional[bool] = None


class TeamResponse(BaseModel):
    """Schema for team API responses"""
    team_id: str
    name: str
    description: Optional[str]
    department: Optional[str]
    members: List[TeamMemberSchema]
    max_concurrent_tasks: int
    specializations: List[str]
    working_hours: dict
    assigned_workflows: List[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime
    created_by: Optional[str]

    # Computed fields
    member_count: int
    manager_count: int


class TeamListResponse(BaseModel):
    """Schema for paginated team list responses"""
    teams: List[TeamResponse]
    total: int
    page: int
    page_size: int


class TeamStats(BaseModel):
    """Team statistics schema"""
    team_id: str
    name: str
    active_members: int
    assigned_workflows: int
    current_workload: int
    capacity_utilization: float  # 0.0 to 1.0
    average_completion_time: Optional[float]  # in hours
    success_rate: float  # 0.0 to 1.0


class WorkflowAssignment(BaseModel):
    """Schema for assigning workflows to teams"""
    workflow_id: str
    team_id: str
    priority: int = Field(default=1, ge=1, le=5, description="Assignment priority (1=highest)")
    assignment_type: str = Field(default="primary", pattern="^(primary|backup|overflow)$")


class TaskAssignmentRequest(BaseModel):
    """Schema for requesting task assignment"""
    workflow_id: str
    instance_id: str
    step_id: str
    priority: int = Field(default=1, ge=1, le=5)
    required_skills: List[str] = Field(default_factory=list)
    estimated_duration: Optional[int] = Field(None, description="Estimated duration in minutes")


class TaskAssignmentResponse(BaseModel):
    """Schema for task assignment response"""
    assigned_team_id: str
    assigned_user_id: Optional[str]  # Specific user if available
    assignment_reason: str
    estimated_start_time: Optional[datetime]
    queue_position: Optional[int]