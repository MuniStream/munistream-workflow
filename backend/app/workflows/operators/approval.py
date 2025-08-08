"""
Approval Operator for human approvals in workflows.
"""
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from enum import Enum

from .base import BaseOperator, TaskResult, TaskStatus


class ApprovalDecision(str, Enum):
    """Possible approval decisions"""
    APPROVED = "approved"
    REJECTED = "rejected"
    REQUEST_CHANGES = "request_changes"
    ESCALATE = "escalate"


class ApprovalOperator(BaseOperator):
    """
    Waits for human approval - completely self-contained.
    This operator doesn't know about other steps, it only knows
    it needs approval from a specific role or user.
    
    The operator receives context from previous steps but doesn't
    know where it came from or what steps will come after.
    """
    
    def __init__(
        self,
        task_id: str,
        approver_role: Optional[str] = None,
        approver_user: Optional[str] = None,
        approval_message: Optional[str] = None,
        context_keys_to_review: Optional[List[str]] = None,  # Which context keys to show for review
        timeout_hours: Optional[int] = 48,
        auto_approve_on_timeout: bool = False,
        escalation_path: Optional[List[str]] = None,
        **kwargs
    ):
        """
        Initialize approval operator.
        
        Args:
            task_id: Unique identifier for this task
            approver_role: Role that can approve (e.g., "manager", "validator")
            approver_user: Specific user ID that can approve
            approval_message: Message to show to approver
            context_keys_to_review: List of context keys to present for review
            timeout_hours: Hours to wait for approval
            auto_approve_on_timeout: Whether to auto-approve on timeout
            escalation_path: List of roles/users to escalate to
            **kwargs: Additional configuration
        """
        super().__init__(task_id, **kwargs)
        self.approver_role = approver_role
        self.approver_user = approver_user
        self.approval_message = approval_message or "Se requiere aprobación para continuar"
        self.context_keys_to_review = context_keys_to_review
        self.timeout_hours = timeout_hours
        self.auto_approve_on_timeout = auto_approve_on_timeout
        self.escalation_path = escalation_path or []
        self.request_sent_at: Optional[datetime] = None
        self.current_escalation_level = 0
    
    def execute(self, context: Dict[str, Any]) -> TaskResult:
        """
        Wait for and process approval.
        
        Args:
            context: Execution context with accumulated data from previous steps
            
        Returns:
            TaskResult based on approval decision
        """
        # Extract relevant data from context for review
        data_to_review = self.extract_review_data(context)
        
        # Check if we have a decision
        if self.state.approval_decision:
            return self.process_decision()
        
        # Check for timeout
        if self.request_sent_at and self.timeout_hours:
            timeout_at = self.request_sent_at + timedelta(hours=self.timeout_hours)
            if datetime.utcnow() > timeout_at:
                if self.auto_approve_on_timeout:
                    # Auto-approve
                    self.state.approval_decision = ApprovalDecision.APPROVED
                    self.state.metadata["auto_approved"] = True
                    self.state.metadata["auto_approved_reason"] = "timeout"
                    return TaskResult(
                        status="continue",
                        data={"approved": True, "auto_approved": True, "approver": "system_timeout"}
                    )
                elif self.escalation_path and self.current_escalation_level < len(self.escalation_path):
                    # Escalate to next level
                    return self.escalate()
                else:
                    # Timeout without auto-approval
                    return TaskResult(
                        status="failed",
                        error=f"Approval timeout after {self.timeout_hours} hours"
                    )
        
        # Assign to approver if not done
        if not self.state.assigned_to:
            self.assign_to_approver(context)
        
        # Request approval if not done
        if not self.request_sent_at:
            self.request_approval(data_to_review)
            self.request_sent_at = datetime.utcnow()
        
        # Still waiting for approval
        self.state.status = TaskStatus.WAITING_APPROVAL
        self.state.waiting_for = "approval"
        
        return TaskResult(
            status="waiting",
            data={
                "waiting_for": "approval",
                "assigned_to": self.state.assigned_to,
                "message": self.approval_message,
                "data_under_review": data_to_review
            }
        )
    
    def extract_review_data(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract relevant data from context for review.
        The operator doesn't know where this data came from,
        it just knows which keys it needs to review.
        
        Args:
            context: Full execution context
            
        Returns:
            Filtered data for review
        """
        if not self.context_keys_to_review:
            # Review everything if no specific keys specified
            return context
        
        # Extract only specified keys from context
        review_data = {}
        for key in self.context_keys_to_review:
            if key in context:
                review_data[key] = context[key]
            # Handle nested keys (e.g., "user_data.rfc")
            elif "." in key:
                parts = key.split(".")
                value = context
                for part in parts:
                    if isinstance(value, dict) and part in value:
                        value = value[part]
                    else:
                        value = None
                        break
                if value is not None:
                    review_data[key] = value
        
        return review_data
    
    def assign_to_approver(self, context: Dict[str, Any]):
        """
        Assign task to approver.
        Can use context to make intelligent assignment decisions.
        
        Args:
            context: Execution context that might influence assignment
        """
        if self.approver_user:
            self.state.assigned_to = self.approver_user
            self.state.assigned_team = None
        elif self.approver_role:
            # Could use context to determine specific user
            # For example, assign based on geographic region from context
            if "region" in context:
                self.state.assigned_team = f"{self.approver_role}_{context['region']}"
            else:
                self.state.assigned_team = self.approver_role
            self.state.metadata["assignment_strategy"] = "role_based"
        else:
            # Default assignment
            self.state.assigned_team = "approvers"
        
        self.state.metadata["assigned_at"] = datetime.utcnow().isoformat()
    
    def request_approval(self, data_to_review: Dict[str, Any]):
        """Send approval request with filtered data"""
        self.state.metadata["approval_requested_at"] = datetime.utcnow().isoformat()
        self.state.metadata["approval_context"] = {
            "message": self.approval_message,
            "data_to_review": data_to_review,
            "timeout_hours": self.timeout_hours
        }
    
    def process_decision(self) -> TaskResult:
        """Process the approval decision"""
        decision = self.state.approval_decision
        
        if decision == ApprovalDecision.APPROVED:
            return TaskResult(
                status="continue",
                data={
                    "approval_status": "approved",
                    "approved_by": self.state.metadata.get("decided_by"),
                    "approved_at": self.state.metadata.get("decided_at"),
                    "approval_comments": self.state.metadata.get("decision_comments")
                }
            )
        elif decision == ApprovalDecision.REJECTED:
            return TaskResult(
                status="failed",
                error=f"Rechazado: {self.state.rejection_reason or 'Sin razón especificada'}"
            )
        elif decision == ApprovalDecision.REQUEST_CHANGES:
            # Reset for another approval cycle
            self.state.approval_decision = None
            self.state.has_input = False
            self.request_sent_at = None
            return TaskResult(
                status="retry",
                error=f"Cambios solicitados: {self.state.metadata.get('requested_changes', 'Ver comentarios')}"
            )
        elif decision == ApprovalDecision.ESCALATE:
            return self.escalate()
        
        return TaskResult(
            status="waiting",
            data={"waiting_for": "approval"}
        )
    
    def escalate(self) -> TaskResult:
        """Escalate to next level"""
        if self.current_escalation_level < len(self.escalation_path):
            next_approver = self.escalation_path[self.current_escalation_level]
            self.current_escalation_level += 1
            
            # Reset approval state for new approver
            self.state.assigned_to = next_approver
            self.state.approval_decision = None
            self.request_sent_at = None
            
            self.state.metadata["escalated"] = True
            self.state.metadata["escalation_level"] = self.current_escalation_level
            self.state.metadata["escalated_to"] = next_approver
            self.state.metadata["escalated_at"] = datetime.utcnow().isoformat()
            
            return TaskResult(
                status="waiting",
                data={
                    "escalated": True,
                    "escalated_to": next_approver
                }
            )
        else:
            return TaskResult(
                status="failed",
                error="No hay más niveles de escalación disponibles"
            )
    
    def receive_decision(
        self,
        decision: ApprovalDecision,
        decided_by: str,
        comments: Optional[str] = None,
        rejection_reason: Optional[str] = None,
        requested_changes: Optional[str] = None
    ):
        """
        Receive approval decision (called by external system).
        
        Args:
            decision: The approval decision
            decided_by: User ID who made the decision
            comments: Optional comments
            rejection_reason: Reason if rejected
            requested_changes: Changes requested if applicable
        """
        self.state.approval_decision = decision
        self.state.metadata["decided_by"] = decided_by
        self.state.metadata["decided_at"] = datetime.utcnow().isoformat()
        self.state.metadata["decision_comments"] = comments
        
        if decision == ApprovalDecision.REJECTED:
            self.state.rejection_reason = rejection_reason
        elif decision == ApprovalDecision.REQUEST_CHANGES:
            self.state.metadata["requested_changes"] = requested_changes


class ConditionalApprovalOperator(ApprovalOperator):
    """
    Approval operator that can skip approval based on context conditions.
    """
    
    def __init__(
        self,
        task_id: str,
        skip_condition: Optional[callable] = None,
        **kwargs
    ):
        """
        Initialize conditional approval operator.
        
        Args:
            task_id: Unique identifier
            skip_condition: Function that receives context and returns True to skip approval
            **kwargs: Additional configuration for base ApprovalOperator
        """
        super().__init__(task_id, **kwargs)
        self.skip_condition = skip_condition
    
    def execute(self, context: Dict[str, Any]) -> TaskResult:
        """
        Execute with conditional logic.
        
        Args:
            context: Execution context from previous steps
            
        Returns:
            TaskResult - may skip approval based on context
        """
        # Check if we should skip approval based on context
        if self.skip_condition and self.skip_condition(context):
            # Auto-approve based on condition
            return TaskResult(
                status="continue",
                data={
                    "approval_status": "auto_approved",
                    "auto_approval_reason": "condition_met",
                    "condition": self.skip_condition.__name__ if hasattr(self.skip_condition, '__name__') else "custom"
                }
            )
        
        # Otherwise proceed with normal approval
        return super().execute(context)


# Example skip conditions for common scenarios
def skip_if_low_value(context: Dict[str, Any]) -> bool:
    """Skip approval if transaction value is below threshold"""
    value = context.get("transaction_value", 0)
    return value < 10000


def skip_if_verified_user(context: Dict[str, Any]) -> bool:
    """Skip approval if user is already verified"""
    user_data = context.get("user_data", {})
    return user_data.get("verified", False) and user_data.get("trust_level", 0) >= 3


def skip_if_auto_validated(context: Dict[str, Any]) -> bool:
    """Skip approval if all validations passed automatically"""
    validations = context.get("validations", {})
    return all(
        validations.get(key, {}).get("status") == "valid"
        for key in ["rfc", "curp", "address", "documents"]
    )