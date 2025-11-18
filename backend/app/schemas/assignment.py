"""
Assignment schemas for API requests and responses.

These schemas define the data structures for workflow assignment management.
"""

from typing import Dict, Any, Optional, List
from datetime import datetime
from pydantic import BaseModel, Field
from ..models.workflow import AssignmentStatus, WorkflowType


class AssignmentRequest(BaseModel):
    """Request for assigning a workflow instance"""
    assign_to: Dict[str, str] = Field(..., description="Assignment target - {'team': 'id'} or {'admin': 'email'}")
    notes: Optional[str] = Field(None, description="Notes about the assignment")
    priority: Optional[int] = Field(None, ge=1, le=10, description="Priority level (1-10)")


class ReassignmentRequest(BaseModel):
    """Request for reassigning a workflow instance"""
    assign_to: Dict[str, str] = Field(..., description="New assignment target")
    reason: str = Field(..., description="Reason for reassignment")
    notes: Optional[str] = Field(None, description="Additional notes")


class AssignmentResponse(BaseModel):
    """Response containing assignment information"""
    instance_id: str = Field(..., description="Workflow instance ID")
    workflow_id: str = Field(..., description="Workflow ID")
    workflow_type: Optional[WorkflowType] = Field(None, description="Type of workflow")
    workflow_name: Optional[str] = Field(None, description="Human-readable workflow name")

    # Assignment details
    status: AssignmentStatus = Field(..., description="Current assignment status")
    workflow_status: str = Field(..., description="Current workflow execution status")
    assigned_to_user: Optional[str] = Field(None, description="Assigned user ID")
    assigned_to_team: Optional[str] = Field(None, description="Assigned team ID")
    assigned_at: Optional[datetime] = Field(None, description="When assignment was made")
    assigned_by: Optional[str] = Field(None, description="Who made the assignment")

    # Parent workflow info
    parent_instance_id: Optional[str] = Field(None, description="Parent workflow instance if child")
    parent_workflow_id: Optional[str] = Field(None, description="Parent workflow ID if child")

    # Metadata
    priority: int = Field(default=5, description="Priority level")
    created_at: datetime = Field(..., description="When instance was created")
    updated_at: datetime = Field(..., description="Last update time")

    # Additional context
    citizen_email: Optional[str] = Field(None, description="Email of citizen if applicable")
    current_step: Optional[str] = Field(None, description="Current step being executed")
    completion_percentage: Optional[float] = Field(None, description="Progress percentage")

    class Config:
        use_enum_values = True
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }


class AssignmentListResponse(BaseModel):
    """Response containing list of assignments"""
    assignments: List[AssignmentResponse] = Field(..., description="List of assignments")
    total: int = Field(..., description="Total count of assignments")
    page: int = Field(default=1, description="Current page")
    page_size: int = Field(default=20, description="Items per page")


class AssignmentStatsResponse(BaseModel):
    """Statistics about assignments"""
    by_status: Dict[str, int] = Field(default_factory=dict, description="Count by assignment status")
    by_user: Dict[str, int] = Field(default_factory=dict, description="Count by assigned user")
    by_team: Dict[str, int] = Field(default_factory=dict, description="Count by assigned team")
    by_workflow_type: Dict[str, int] = Field(default_factory=dict, description="Count by workflow type")

    # Time-based stats
    pending_average_hours: Optional[float] = Field(None, description="Average hours in pending status")
    completion_average_hours: Optional[float] = Field(None, description="Average hours to complete")

    # Current status
    total: int = Field(..., description="Total assignments")
    pending: int = Field(default=0, description="Pending assignment")
    in_progress: int = Field(default=0, description="Currently in progress")
    completed_today: int = Field(default=0, description="Completed today")
    overdue: int = Field(default=0, description="Overdue assignments")


class TeamInfo(BaseModel):
    """Information about a team for assignment"""
    team_id: str = Field(..., description="Team identifier")
    team_name: str = Field(..., description="Team name")
    member_count: int = Field(default=0, description="Number of team members")
    current_load: int = Field(default=0, description="Current number of assignments")
    available: bool = Field(default=True, description="Whether team can accept new assignments")


class UserAssignmentInfo(BaseModel):
    """Information about a user for assignment"""
    user_id: str = Field(..., description="User identifier")
    user_email: str = Field(..., description="User email")
    user_name: Optional[str] = Field(None, description="User full name")
    role: str = Field(..., description="User role")
    teams: List[str] = Field(default_factory=list, description="Teams user belongs to")
    current_assignments: int = Field(default=0, description="Number of active assignments")
    max_assignments: int = Field(default=10, description="Maximum concurrent assignments")
    available: bool = Field(default=True, description="Whether user can accept new assignments")


class WorkflowStartRequest(BaseModel):
    """Request to start an assigned workflow"""
    notes: Optional[str] = Field(None, description="Notes about starting the workflow")
    initial_data: Optional[Dict[str, Any]] = Field(None, description="Initial data for the workflow")


class WorkflowStartResponse(BaseModel):
    """Response after starting a workflow"""
    instance_id: str = Field(..., description="Workflow instance ID")
    status: str = Field(..., description="Current status")
    started_at: datetime = Field(..., description="When execution started")
    started_by: str = Field(..., description="User who started it")
    message: str = Field(default="Workflow started successfully", description="Status message")