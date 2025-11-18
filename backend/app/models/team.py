"""
Team model for organizing users into work groups.
"""

from datetime import datetime
from typing import List, Optional
from beanie import Document
from pydantic import BaseModel, Field


class TeamMember(BaseModel):
    """Team member relationship"""
    user_id: str
    role: str = "member"  # member, leader, coordinator
    joined_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = True


class TeamModel(Document):
    """Team model for user organization"""
    
    team_id: str = Field(..., unique=True, description="Unique team identifier")
    name: str = Field(..., description="Team name")
    description: Optional[str] = Field(None, description="Team description")
    department: Optional[str] = Field(None, description="Department or area")
    
    # Team members
    members: List[TeamMember] = Field(default_factory=list, description="Team members")
    
    # Team configuration
    max_concurrent_tasks: int = Field(default=10, description="Maximum concurrent tasks per team")
    specializations: List[str] = Field(default_factory=list, description="Team specializations/skills")
    working_hours: dict = Field(default_factory=dict, description="Working hours configuration")
    
    # Workflow assignments
    assigned_workflows: List[str] = Field(default_factory=list, description="Workflows assigned to this team")
    
    # Team status
    is_active: bool = Field(default=True, description="Whether team is active")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = Field(None, description="User who created the team")
    
    class Settings:
        name = "teams"
        indexes = [
            "team_id",
            "name",
            "department",
            "is_active",
            "members.user_id"
        ]
    
    def add_member(self, user_id: str, role: str = "member") -> bool:
        """Add a member to the team"""
        # Check if user is already a member
        for member in self.members:
            if member.user_id == user_id:
                return False
        
        # Add new member
        self.members.append(TeamMember(
            user_id=user_id,
            role=role,
            joined_at=datetime.utcnow(),
            is_active=True
        ))
        self.updated_at = datetime.utcnow()
        return True
    
    def remove_member(self, user_id: str) -> bool:
        """Remove a member from the team"""
        original_length = len(self.members)
        self.members = [m for m in self.members if m.user_id != user_id]
        
        if len(self.members) < original_length:
            self.updated_at = datetime.utcnow()
            return True
        return False
    
    def update_member_role(self, user_id: str, new_role: str) -> bool:
        """Update a member's role"""
        for member in self.members:
            if member.user_id == user_id:
                member.role = new_role
                self.updated_at = datetime.utcnow()
                return True
        return False
    
    def deactivate_member(self, user_id: str) -> bool:
        """Deactivate a member (but keep them in the team)"""
        for member in self.members:
            if member.user_id == user_id:
                member.is_active = False
                self.updated_at = datetime.utcnow()
                return True
        return False
    
    def get_active_members(self) -> List[TeamMember]:
        """Get only active members"""
        return [m for m in self.members if m.is_active]
    
    def get_leaders(self) -> List[TeamMember]:
        """Get team leaders"""
        return [m for m in self.members if m.role in ["leader", "coordinator"] and m.is_active]
    
    def get_member_count(self) -> int:
        """Get count of active members"""
        return len(self.get_active_members())
    
    def can_take_more_tasks(self) -> bool:
        """Check if team can take more concurrent tasks"""
        # This would need to be integrated with actual task tracking
        # For now, assume teams can always take more tasks
        return self.is_active and self.get_member_count() > 0
    
    def assign_workflow(self, workflow_id: str) -> bool:
        """Assign a workflow to this team"""
        if workflow_id not in self.assigned_workflows:
            self.assigned_workflows.append(workflow_id)
            self.updated_at = datetime.utcnow()
            return True
        return False
    
    def unassign_workflow(self, workflow_id: str) -> bool:
        """Unassign a workflow from this team"""
        if workflow_id in self.assigned_workflows:
            self.assigned_workflows.remove(workflow_id)
            self.updated_at = datetime.utcnow()
            return True
        return False

    def add_manager(self, user_id: str) -> bool:
        """Add a manager to the team"""
        return self.add_member(user_id, role="manager")

    def remove_manager(self, user_id: str) -> bool:
        """Remove manager role from user (keeps as member)"""
        for member in self.members:
            if member.user_id == user_id and member.role == "manager":
                member.role = "member"
                self.updated_at = datetime.utcnow()
                return True
        return False

    def get_managers(self) -> List[TeamMember]:
        """Get team managers"""
        return [m for m in self.members if m.role == "manager" and m.is_active]

    def is_manager(self, user_id: str) -> bool:
        """Check if user is a manager of this team"""
        for member in self.members:
            if member.user_id == user_id and member.role == "manager" and member.is_active:
                return True
        return False

    def can_user_manage(self, user_id: str) -> bool:
        """Check if user can manage this team (is manager of this team)"""
        return self.is_manager(user_id)

    def get_manager_count(self) -> int:
        """Get count of active managers"""
        return len(self.get_managers())