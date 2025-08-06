"""
Automatic Assignment Service for Workflow Instances

Handles automatic assignment of workflow instances to teams and users based on:
- Workload balancing
- Team specializations
- User availability
- Workflow complexity
- Historical performance
"""

from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta
from enum import Enum
from dataclasses import dataclass
import random

from ..models.workflow import WorkflowInstance, WorkflowDefinition, AssignmentStatus, AssignmentType
from ..models.user import UserModel, UserRole
from ..models.team import TeamModel
from ..core.database import get_database


class AssignmentStrategy(str, Enum):
    """Assignment strategies"""
    ROUND_ROBIN = "round_robin"  # Rotate assignments evenly
    WORKLOAD_BASED = "workload_based"  # Assign based on current workload
    EXPERTISE_BASED = "expertise_based"  # Assign based on specializations
    RANDOM = "random"  # Random assignment
    PRIORITY_BASED = "priority_based"  # High priority instances get best available


@dataclass
class AssignmentRule:
    """Assignment rule configuration"""
    workflow_id: Optional[str] = None
    workflow_category: Optional[str] = None
    priority_level: Optional[str] = None
    strategy: AssignmentStrategy = AssignmentStrategy.WORKLOAD_BASED
    preferred_teams: List[str] = None
    required_specializations: List[str] = None
    max_instances_per_user: int = 5
    prefer_team_assignment: bool = True


@dataclass
class UserWorkload:
    """User workload information"""
    user_id: str
    active_instances: int
    in_progress_instances: int
    total_assigned: int
    avg_completion_time: float
    availability_score: float  # 0.0 to 1.0


class AssignmentService:
    """Service for automatic workflow instance assignment"""
    
    def __init__(self):
        self.default_rules = {
            "default": AssignmentRule(
                strategy=AssignmentStrategy.WORKLOAD_BASED,
                prefer_team_assignment=True,
                max_instances_per_user=5
            )
        }
    
    async def auto_assign_instance(
        self, 
        instance: WorkflowInstance, 
        workflow_def: Optional[WorkflowDefinition] = None
    ) -> bool:
        """
        Automatically assign an instance to the most appropriate team/user
        
        Returns:
            bool: True if assignment was successful
        """
        try:
            # Get assignment rule for this workflow
            rule = await self._get_assignment_rule(instance, workflow_def)
            
            # Get available teams and users
            available_teams = await self._get_available_teams(rule)
            
            if not available_teams:
                print(f"No available teams for assignment of instance {instance.instance_id}")
                return False
            
            # Apply assignment strategy
            assignment_result = await self._apply_assignment_strategy(
                instance, rule, available_teams
            )
            
            if assignment_result:
                team_id, user_id, confidence = assignment_result
                
                # Perform the assignment
                success = await self._execute_assignment(
                    instance, team_id, user_id, rule, confidence
                )
                
                if success:
                    print(f"Successfully auto-assigned instance {instance.instance_id} to team {team_id}, user {user_id}")
                    return True
            
            return False
            
        except Exception as e:
            print(f"Error in auto_assign_instance: {e}")
            return False
    
    async def _get_assignment_rule(
        self, 
        instance: WorkflowInstance, 
        workflow_def: Optional[WorkflowDefinition]
    ) -> AssignmentRule:
        """Get the appropriate assignment rule for this instance"""
        
        # Check if there are specific rules for this workflow
        if workflow_def and workflow_def.workflow_id in self.default_rules:
            return self.default_rules[workflow_def.workflow_id]
        
        # Check category-based rules
        if workflow_def and hasattr(workflow_def, 'category'):
            category_rule_key = f"category_{workflow_def.category}"
            if category_rule_key in self.default_rules:
                return self.default_rules[category_rule_key]
        
        # Return default rule
        return self.default_rules["default"]
    
    async def _get_available_teams(self, rule: AssignmentRule) -> List[TeamModel]:
        """Get teams available for assignment based on the rule"""
        
        query = {"is_active": True}
        
        # Filter by preferred teams if specified
        if rule.preferred_teams:
            query["team_id"] = {"$in": rule.preferred_teams}
        
        teams = await TeamModel.find(query).to_list()
        
        # Filter teams based on specializations if required
        if rule.required_specializations:
            filtered_teams = []
            for team in teams:
                team_specializations = getattr(team, 'specializations', [])
                if any(spec in team_specializations for spec in rule.required_specializations):
                    filtered_teams.append(team)
            teams = filtered_teams
        
        return teams
    
    async def _apply_assignment_strategy(
        self, 
        instance: WorkflowInstance, 
        rule: AssignmentRule, 
        available_teams: List[TeamModel]
    ) -> Optional[Tuple[str, Optional[str], float]]:
        """
        Apply the assignment strategy to select team/user
        
        Returns:
            Tuple[team_id, user_id, confidence_score] or None if no assignment possible
        """
        
        if rule.strategy == AssignmentStrategy.WORKLOAD_BASED:
            return await self._workload_based_assignment(instance, rule, available_teams)
        elif rule.strategy == AssignmentStrategy.ROUND_ROBIN:
            return await self._round_robin_assignment(instance, rule, available_teams)
        elif rule.strategy == AssignmentStrategy.EXPERTISE_BASED:
            return await self._expertise_based_assignment(instance, rule, available_teams)
        elif rule.strategy == AssignmentStrategy.RANDOM:
            return await self._random_assignment(instance, rule, available_teams)
        else:
            # Default to workload-based
            return await self._workload_based_assignment(instance, rule, available_teams)
    
    async def _workload_based_assignment(
        self, 
        instance: WorkflowInstance, 
        rule: AssignmentRule, 
        available_teams: List[TeamModel]
    ) -> Optional[Tuple[str, Optional[str], float]]:
        """Assign based on current workload of teams and users"""
        
        best_team = None
        best_user = None
        best_score = float('inf')
        
        for team in available_teams:
            # Get team workload
            team_workload = await self._calculate_team_workload(team)
            
            if rule.prefer_team_assignment:
                # Assign to team, let team distribute internally
                if team_workload < best_score:
                    best_score = team_workload
                    best_team = team
                    best_user = None
            else:
                # Find best user in this team
                team_users = await self._get_team_users(team)
                for user in team_users:
                    user_workload = await self._calculate_user_workload(user)
                    
                    if user_workload.total_assigned < rule.max_instances_per_user:
                        combined_score = team_workload * 0.3 + user_workload.total_assigned * 0.7
                        if combined_score < best_score:
                            best_score = combined_score
                            best_team = team
                            best_user = user
        
        if best_team:
            confidence = min(1.0, max(0.1, 1.0 - (best_score / 10.0)))
            return (best_team.team_id, best_user.id if best_user else None, confidence)
        
        return None
    
    async def _round_robin_assignment(
        self, 
        instance: WorkflowInstance, 
        rule: AssignmentRule, 
        available_teams: List[TeamModel]
    ) -> Optional[Tuple[str, Optional[str], float]]:
        """Assign using round-robin rotation"""
        
        if not available_teams:
            return None
        
        # Get the last assigned team to continue rotation
        last_assignment = await WorkflowInstance.find(
            {"assignment_type": AssignmentType.AUTOMATIC}
        ).sort(-WorkflowInstance.assigned_at).limit(1).to_list()
        
        if last_assignment and last_assignment[0].assigned_team_id:
            try:
                last_team_index = next(
                    i for i, team in enumerate(available_teams) 
                    if team.team_id == last_assignment[0].assigned_team_id
                )
                next_team_index = (last_team_index + 1) % len(available_teams)
            except StopIteration:
                next_team_index = 0
        else:
            next_team_index = 0
        
        selected_team = available_teams[next_team_index]
        
        # If individual assignment is preferred, select user within team
        selected_user = None
        if not rule.prefer_team_assignment:
            team_users = await self._get_team_users(selected_team)
            if team_users:
                # Round-robin within team
                selected_user = team_users[hash(instance.instance_id) % len(team_users)]
        
        return (selected_team.team_id, selected_user.id if selected_user else None, 0.8)
    
    async def _expertise_based_assignment(
        self, 
        instance: WorkflowInstance, 
        rule: AssignmentRule, 
        available_teams: List[TeamModel]
    ) -> Optional[Tuple[str, Optional[str], float]]:
        """Assign based on team/user expertise and specializations"""
        
        best_team = None
        best_user = None
        best_match_score = 0.0
        
        for team in available_teams:
            team_specializations = getattr(team, 'specializations', [])
            
            # Calculate team expertise match
            expertise_score = 0.0
            if rule.required_specializations:
                matches = len(set(team_specializations) & set(rule.required_specializations))
                expertise_score = matches / len(rule.required_specializations)
            else:
                # General competence score
                expertise_score = min(1.0, len(team_specializations) / 5.0)
            
            # Consider team workload as tiebreaker
            workload_factor = 1.0 - min(1.0, await self._calculate_team_workload(team) / 10.0)
            combined_score = expertise_score * 0.7 + workload_factor * 0.3
            
            if combined_score > best_match_score:
                best_match_score = combined_score
                best_team = team
                
                # If individual assignment preferred, find best user in team
                if not rule.prefer_team_assignment:
                    team_users = await self._get_team_users(team)
                    if team_users:
                        # Select user with best specialization match
                        best_user_score = 0.0
                        for user in team_users:
                            user_specializations = getattr(user, 'specializations', [])
                            if rule.required_specializations:
                                user_matches = len(set(user_specializations) & set(rule.required_specializations))
                                user_score = user_matches / len(rule.required_specializations)
                            else:
                                user_score = min(1.0, len(user_specializations) / 5.0)
                            
                            if user_score > best_user_score:
                                best_user_score = user_score
                                best_user = user
        
        if best_team:
            return (best_team.team_id, best_user.id if best_user else None, best_match_score)
        
        return None
    
    async def _random_assignment(
        self, 
        instance: WorkflowInstance, 
        rule: AssignmentRule, 
        available_teams: List[TeamModel]
    ) -> Optional[Tuple[str, Optional[str], float]]:
        """Random assignment for testing or when no specific criteria apply"""
        
        if not available_teams:
            return None
        
        selected_team = random.choice(available_teams)
        selected_user = None
        
        if not rule.prefer_team_assignment:
            team_users = await self._get_team_users(selected_team)
            if team_users:
                selected_user = random.choice(team_users)
        
        return (selected_team.team_id, selected_user.id if selected_user else None, 0.5)
    
    async def _calculate_team_workload(self, team: TeamModel) -> float:
        """Calculate current workload for a team"""
        
        # Count active instances assigned to this team
        active_count = await WorkflowInstance.find({
            "assigned_team_id": team.team_id,
            "assignment_status": {"$in": [AssignmentStatus.ASSIGNED, AssignmentStatus.IN_PROGRESS]}
        }).count()
        
        # Normalize by team size
        team_size = len(team.members) if team.members else 1
        workload_per_member = active_count / team_size
        
        return workload_per_member
    
    async def _calculate_user_workload(self, user: UserModel) -> UserWorkload:
        """Calculate detailed workload information for a user"""
        
        # Count different types of assignments
        active_instances = await WorkflowInstance.find({
            "assigned_user_id": str(user.id),
            "assignment_status": {"$in": [AssignmentStatus.ASSIGNED, AssignmentStatus.IN_PROGRESS]}
        }).count()
        
        in_progress_instances = await WorkflowInstance.find({
            "assigned_user_id": str(user.id),
            "assignment_status": AssignmentStatus.IN_PROGRESS
        }).count()
        
        total_assigned = await WorkflowInstance.find({
            "assigned_user_id": str(user.id)
        }).count()
        
        # Calculate availability score based on max concurrent tasks
        max_tasks = getattr(user, 'max_concurrent_tasks', 5)
        availability_score = max(0.0, 1.0 - (active_instances / max_tasks))
        
        return UserWorkload(
            user_id=str(user.id),
            active_instances=active_instances,
            in_progress_instances=in_progress_instances,
            total_assigned=total_assigned,
            avg_completion_time=24.0,  # Placeholder - could be calculated from history
            availability_score=availability_score
        )
    
    async def _get_team_users(self, team: TeamModel) -> List[UserModel]:
        """Get all users belonging to a team"""
        
        if not team.members:
            return []
        
        users = await UserModel.find({
            "team_ids": {"$in": [team.team_id]},
            "status": "active"
        }).to_list()
        
        return users
    
    async def _execute_assignment(
        self, 
        instance: WorkflowInstance, 
        team_id: str, 
        user_id: Optional[str], 
        rule: AssignmentRule, 
        confidence: float
    ) -> bool:
        """Execute the actual assignment"""
        
        try:
            now = datetime.utcnow()
            
            # Update instance with assignment
            update_data = {
                "assigned_team_id": team_id,
                "assignment_status": AssignmentStatus.ASSIGNED,
                "assignment_type": AssignmentType.AUTOMATIC,
                "assigned_at": now,
                "assignment_notes": f"Auto-assigned using {rule.strategy} strategy (confidence: {confidence:.2f})"
            }
            
            if user_id:
                update_data["assigned_user_id"] = user_id
            
            # Update the instance
            await instance.update({"$set": update_data})
            
            return True
            
        except Exception as e:
            print(f"Error executing assignment: {e}")
            return False
    
    async def get_assignment_statistics(self) -> Dict[str, Any]:
        """Get statistics about automatic assignments"""
        
        # Count assignments by type
        auto_assignments = await WorkflowInstance.find({
            "assignment_type": AssignmentType.AUTOMATIC
        }).count()
        
        manual_assignments = await WorkflowInstance.find({
            "assignment_type": AssignmentType.MANUAL
        }).count()
        
        # Count by status
        assigned_count = await WorkflowInstance.find({
            "assignment_status": AssignmentStatus.ASSIGNED
        }).count()
        
        in_progress_count = await WorkflowInstance.find({
            "assignment_status": AssignmentStatus.IN_PROGRESS
        }).count()
        
        completed_count = await WorkflowInstance.find({
            "assignment_status": AssignmentStatus.COMPLETED
        }).count()
        
        return {
            "total_assignments": auto_assignments + manual_assignments,
            "automatic_assignments": auto_assignments,
            "manual_assignments": manual_assignments,
            "assigned_instances": assigned_count,
            "in_progress_instances": in_progress_count,
            "completed_instances": completed_count,
            "automation_rate": auto_assignments / (auto_assignments + manual_assignments) if (auto_assignments + manual_assignments) > 0 else 0
        }


# Global service instance
assignment_service = AssignmentService()