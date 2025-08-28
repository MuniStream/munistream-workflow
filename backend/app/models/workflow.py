from typing import Dict, Any, List, Optional
from datetime import datetime
from enum import Enum
from beanie import Document, Indexed
from pydantic import Field
from pymongo import IndexModel


class AssignmentStatus(str, Enum):
    """Status of instance assignment for review process"""
    PENDING_REVIEW = "pending_review"           # Asignado, esperando revisión
    UNDER_REVIEW = "under_review"               # Revisor trabajando en él
    APPROVED_BY_REVIEWER = "approved_by_reviewer"  # Aprobado por revisor, esperando firma
    REJECTED = "rejected"                       # Rechazado por revisor
    MODIFICATION_REQUESTED = "modification_requested"  # Modificaciones solicitadas al ciudadano
    PENDING_SIGNATURE = "pending_signature"     # Esperando firma de aprobador/gerente
    COMPLETED = "completed"                     # Completado y firmado
    ESCALATED = "escalated"                     # Escalado por problemas
    ON_HOLD = "on_hold"                        # En pausa temporal


class AssignmentType(str, Enum):
    """Type of assignment"""
    MANUAL = "manual"
    AUTOMATIC = "automatic"
    ESCALATED = "escalated"
    REASSIGNED = "reassigned"


class WorkflowStep(Document):
    """Individual step definition within a workflow"""
    step_id: str = Field(..., description="Unique identifier for the step")
    workflow_id: str = Field(..., description="Parent workflow ID")
    name: str = Field(..., description="Human-readable step name")
    step_type: str = Field(..., description="Type of step (action, conditional, etc.)")
    description: Optional[str] = Field(None, description="Step description")
    required_inputs: List[str] = Field(default_factory=list, description="Required input parameters")
    optional_inputs: List[str] = Field(default_factory=list, description="Optional input parameters")
    next_steps: List[str] = Field(default_factory=list, description="Next step IDs")
    configuration: Dict[str, Any] = Field(default_factory=dict, description="Step-specific configuration")
    
    # Citizen input fields
    requires_citizen_input: bool = Field(default=False, description="Whether this step requires citizen input")
    input_form: Dict[str, Any] = Field(default_factory=dict, description="Form configuration for citizen input")
    
    # Operator details
    operator_class: Optional[str] = Field(None, description="Actual operator class name (e.g., UserInputOperator, AdminInputOperator)")
    
    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = Field(None, description="User who created this step")
    
    class Settings:
        name = "workflow_steps"
        indexes = [
            IndexModel([("workflow_id", 1), ("step_id", 1)], unique=True),
            IndexModel([("workflow_id", 1)]),
            IndexModel([("step_type", 1)]),
        ]


class WorkflowDefinition(Document):
    """Complete workflow definition with metadata"""
    workflow_id: str = Field(..., description="Unique workflow identifier")
    name: str = Field(..., description="Workflow name")
    description: Optional[str] = Field(None, description="Workflow description")
    version: str = Field(default="1.0.0", description="Workflow version")
    status: str = Field(default="draft", description="Workflow status")
    start_step_id: Optional[str] = Field(None, description="ID of the starting step")
    
    # Metadata
    category: Optional[str] = Field(None, description="Workflow category")
    tags: List[str] = Field(default_factory=list, description="Workflow tags")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    
    # Audit fields
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = Field(None, description="User who created this workflow")
    updated_by: Optional[str] = Field(None, description="User who last updated this workflow")
    
    # Statistics
    total_instances: int = Field(default=0, description="Total number of instances created")
    successful_instances: int = Field(default=0, description="Number of successful instances")
    failed_instances: int = Field(default=0, description="Number of failed instances")
    
    class Settings:
        name = "workflow_definitions"
        indexes = [
            IndexModel([("workflow_id", 1)], unique=True),
            IndexModel([("name", 1)]),
            IndexModel([("status", 1)]),
            IndexModel([("category", 1)]),
            IndexModel([("tags", 1)]),
            IndexModel([("created_at", -1)]),
        ]


class StepExecution(Document):
    """Individual step execution within a workflow instance"""
    execution_id: str = Field(..., description="Unique execution identifier")
    instance_id: str = Field(..., description="Parent instance ID")
    step_id: str = Field(..., description="Step being executed")
    workflow_id: str = Field(..., description="Parent workflow ID")
    
    # Execution details
    status: str = Field(default="pending", description="Execution status")
    inputs: Dict[str, Any] = Field(default_factory=dict, description="Step inputs")
    outputs: Dict[str, Any] = Field(default_factory=dict, description="Step outputs")
    error_message: Optional[str] = Field(None, description="Error message if failed")
    
    # Timing
    started_at: Optional[datetime] = Field(None, description="When step started")
    completed_at: Optional[datetime] = Field(None, description="When step completed")
    duration_seconds: Optional[float] = Field(None, description="Execution duration")
    
    # Retry information
    retry_count: int = Field(default=0, description="Number of retries")
    max_retries: int = Field(default=3, description="Maximum retry attempts")
    
    # Audit
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Settings:
        name = "step_executions"
        indexes = [
            IndexModel([("instance_id", 1), ("step_id", 1)]),
            IndexModel([("instance_id", 1)]),
            IndexModel([("workflow_id", 1)]),
            IndexModel([("status", 1)]),
            IndexModel([("started_at", -1)]),
        ]


class WorkflowInstance(Document):
    """Individual workflow execution instance"""
    instance_id: str = Field(..., description="Unique instance identifier")
    workflow_id: str = Field(..., description="Parent workflow ID")
    workflow_version: str = Field(default="1.0.0", description="Workflow version used")
    
    # User context
    user_id: str = Field(..., description="User who initiated the instance")
    user_data: Dict[str, Any] = Field(default_factory=dict, description="User-specific data")
    
    # Instance state
    status: str = Field(default="running", description="Instance status")
    current_step: Optional[str] = Field(None, description="Current step being executed")
    context: Dict[str, Any] = Field(default_factory=dict, description="Workflow context data")
    
    # Progress tracking
    completed_steps: List[str] = Field(default_factory=list, description="List of completed step IDs")
    failed_steps: List[str] = Field(default_factory=list, description="List of failed step IDs")
    pending_approvals: List[str] = Field(default_factory=list, description="Steps waiting for approval")
    
    # Timing
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = Field(None, description="When instance completed")
    duration_seconds: Optional[float] = Field(None, description="Total execution duration")
    
    # Audit
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    # Priority and scheduling
    priority: int = Field(default=5, description="Execution priority (1-10)")
    scheduled_at: Optional[datetime] = Field(None, description="When to start execution")
    
    # Assignment information
    assigned_user_id: Optional[str] = Field(None, description="User assigned to handle this instance")
    assigned_team_id: Optional[str] = Field(None, description="Team assigned to handle this instance")
    assignment_status: AssignmentStatus = Field(default=AssignmentStatus.PENDING_REVIEW, description="Current assignment status")
    assignment_type: AssignmentType = Field(default=AssignmentType.AUTOMATIC, description="How the assignment was made")
    assigned_at: Optional[datetime] = Field(None, description="When the instance was assigned")
    assigned_by: Optional[str] = Field(None, description="User who made the assignment")
    
    # Assignment history and notes
    assignment_notes: Optional[str] = Field(None, description="Notes about the assignment")
    previous_assignments: List[Dict[str, Any]] = Field(default_factory=list, description="History of previous assignments")
    
    # Review process fields
    reviewed_by: Optional[str] = Field(None, description="User who reviewed the instance")
    reviewed_at: Optional[datetime] = Field(None, description="When the review was completed")
    review_decision: Optional[str] = Field(None, description="Review decision: approve, reject, request_modifications")
    review_comments: Optional[str] = Field(None, description="Reviewer's comments and feedback")
    modification_requests: List[Dict[str, Any]] = Field(default_factory=list, description="Specific modifications requested")
    
    # Approval process fields
    approved_by: Optional[str] = Field(None, description="User who gave final approval/signature")
    approved_at: Optional[datetime] = Field(None, description="When final approval was given")
    approval_comments: Optional[str] = Field(None, description="Approver's comments")
    rejection_reason: Optional[str] = Field(None, description="Reason for rejection if applicable")
    
    # Terminal status
    terminal_status: Optional[str] = Field(None, description="Final status (SUCCESS, FAILURE, etc.)")
    terminal_message: Optional[str] = Field(None, description="Final status message")
    
    class Settings:
        name = "workflow_instances"
        indexes = [
            IndexModel([("instance_id", 1)], unique=True),
            IndexModel([("workflow_id", 1)]),
            IndexModel([("user_id", 1)]),
            IndexModel([("status", 1)]),
            IndexModel([("started_at", -1)]),
            IndexModel([("current_step", 1)]),
            IndexModel([("terminal_status", 1)]),
            # Assignment indexes
            IndexModel([("assigned_user_id", 1)]),
            IndexModel([("assigned_team_id", 1)]),
            IndexModel([("assignment_status", 1)]),
            IndexModel([("assigned_at", -1)]),
            # Compound indexes for efficient filtering
            IndexModel([("assigned_user_id", 1), ("assignment_status", 1)]),
            IndexModel([("assigned_team_id", 1), ("assignment_status", 1)]),
        ]
    
    # Assignment management methods
    def assign_to_user(self, user_id: str, assigned_by: str, assignment_type: AssignmentType = AssignmentType.MANUAL, notes: Optional[str] = None):
        """Assign instance to a user"""
        # Save previous assignment to history
        if self.assigned_user_id or self.assigned_team_id:
            self.previous_assignments.append({
                "user_id": self.assigned_user_id,
                "team_id": self.assigned_team_id,
                "status": self.assignment_status,
                "assigned_at": self.assigned_at,
                "assigned_by": self.assigned_by,
                "unassigned_at": datetime.utcnow(),
                "reason": "reassigned"
            })
        
        self.assigned_user_id = user_id
        self.assigned_team_id = None  # Clear team assignment when assigning to user
        self.assignment_status = AssignmentStatus.PENDING_REVIEW
        self.assignment_type = assignment_type
        self.assigned_at = datetime.utcnow()
        self.assigned_by = assigned_by
        self.assignment_notes = notes
        self.updated_at = datetime.utcnow()
    
    def assign_to_team(self, team_id: str, assigned_by: str, assignment_type: AssignmentType = AssignmentType.AUTOMATIC, notes: Optional[str] = None):
        """Assign instance to a team"""
        # Save previous assignment to history
        if self.assigned_user_id or self.assigned_team_id:
            self.previous_assignments.append({
                "user_id": self.assigned_user_id,
                "team_id": self.assigned_team_id,
                "status": self.assignment_status,
                "assigned_at": self.assigned_at,
                "assigned_by": self.assigned_by,
                "unassigned_at": datetime.utcnow(),
                "reason": "reassigned"
            })
        
        self.assigned_user_id = None  # Clear user assignment when assigning to team
        self.assigned_team_id = team_id
        self.assignment_status = AssignmentStatus.PENDING_REVIEW
        self.assignment_type = assignment_type
        self.assigned_at = datetime.utcnow()
        self.assigned_by = assigned_by
        self.assignment_notes = notes
        self.updated_at = datetime.utcnow()
    
    def start_review(self, reviewer_id: str):
        """Reviewer starts working on the instance"""
        if self.assignment_status == AssignmentStatus.PENDING_REVIEW:
            self.assignment_status = AssignmentStatus.UNDER_REVIEW
            self.reviewed_by = reviewer_id
            self.updated_at = datetime.utcnow()
            return True
        return False
    
    def approve_by_reviewer(self, reviewer_id: str, comments: Optional[str] = None):
        """Reviewer approves the instance - sends to approver for signature"""
        if self.assignment_status == AssignmentStatus.UNDER_REVIEW and self.reviewed_by == reviewer_id:
            self.assignment_status = AssignmentStatus.APPROVED_BY_REVIEWER
            self.review_decision = "approve"
            self.review_comments = comments
            self.reviewed_at = datetime.utcnow()
            self.updated_at = datetime.utcnow()
            return True
        return False
    
    def reject_by_reviewer(self, reviewer_id: str, reason: str, comments: Optional[str] = None):
        """Reviewer rejects the instance"""
        if self.assignment_status == AssignmentStatus.UNDER_REVIEW and self.reviewed_by == reviewer_id:
            self.assignment_status = AssignmentStatus.REJECTED
            self.review_decision = "reject"
            self.rejection_reason = reason
            self.review_comments = comments
            self.reviewed_at = datetime.utcnow()
            self.updated_at = datetime.utcnow()
            return True
        return False
    
    def request_modifications(self, reviewer_id: str, modifications: List[Dict[str, Any]], comments: Optional[str] = None):
        """Reviewer requests modifications from citizen"""
        if self.assignment_status == AssignmentStatus.UNDER_REVIEW and self.reviewed_by == reviewer_id:
            self.assignment_status = AssignmentStatus.MODIFICATION_REQUESTED
            self.review_decision = "request_modifications"
            self.modification_requests = modifications
            self.review_comments = comments
            self.reviewed_at = datetime.utcnow()
            self.updated_at = datetime.utcnow()
            return True
        return False
    
    def final_approval(self, approver_id: str, comments: Optional[str] = None):
        """Final approval and signature by manager/approver"""
        if self.assignment_status == AssignmentStatus.APPROVED_BY_REVIEWER:
            self.assignment_status = AssignmentStatus.COMPLETED
            self.approved_by = approver_id
            self.approved_at = datetime.utcnow()
            self.approval_comments = comments
            self.updated_at = datetime.utcnow()
            return True
        return False
    
    def escalate_assignment(self, reason: str, escalated_by: str):
        """Escalate instance assignment"""
        self.assignment_status = AssignmentStatus.ESCALATED
        self.assignment_notes = f"Escalated: {reason}"
        self.previous_assignments.append({
            "user_id": self.assigned_user_id,
            "team_id": self.assigned_team_id,
            "status": self.assignment_status,
            "assigned_at": self.assigned_at,
            "assigned_by": self.assigned_by,
            "escalated_at": datetime.utcnow(),
            "escalated_by": escalated_by,
            "reason": reason
        })
        self.updated_at = datetime.utcnow()
    
    def unassign(self, reason: str = "manual", unassigned_by: Optional[str] = None):
        """Remove assignment from instance"""
        # Save to history
        if self.assigned_user_id or self.assigned_team_id:
            self.previous_assignments.append({
                "user_id": self.assigned_user_id,
                "team_id": self.assigned_team_id,
                "status": self.assignment_status,
                "assigned_at": self.assigned_at,
                "assigned_by": self.assigned_by,
                "unassigned_at": datetime.utcnow(),
                "unassigned_by": unassigned_by,
                "reason": reason
            })
        
        self.assigned_user_id = None
        self.assigned_team_id = None
        self.assignment_status = AssignmentStatus.PENDING_REVIEW
        self.assignment_notes = "Unassigned - needs reassignment"
        self.updated_at = datetime.utcnow()
    
    def is_assigned_to_user(self, user_id: str) -> bool:
        """Check if instance is assigned to specific user"""
        return self.assigned_user_id == user_id and self.assignment_status in [
            AssignmentStatus.PENDING_REVIEW, 
            AssignmentStatus.UNDER_REVIEW
        ]
    
    def is_assigned_to_team(self, team_id: str) -> bool:
        """Check if instance is assigned to specific team"""
        return self.assigned_team_id == team_id and self.assignment_status in [
            AssignmentStatus.PENDING_REVIEW, 
            AssignmentStatus.UNDER_REVIEW
        ]
    
    def can_be_assigned(self) -> bool:
        """Check if instance can be assigned"""
        return self.assignment_status in [
            AssignmentStatus.PENDING_REVIEW, 
            AssignmentStatus.ESCALATED
        ] and self.status == "running"


class ApprovalRequest(Document):
    """Approval request for workflow steps"""
    approval_id: str = Field(..., description="Unique approval identifier")
    instance_id: str = Field(..., description="Parent instance ID")
    step_id: str = Field(..., description="Step requiring approval")
    workflow_id: str = Field(..., description="Parent workflow ID")
    
    # Request details
    approval_type: str = Field(default="manual", description="Type of approval needed")
    required_approvers: List[str] = Field(default_factory=list, description="Required approver IDs")
    approval_rule: str = Field(default="any", description="Approval rule (any, all, majority)")
    
    # Request content
    title: str = Field(..., description="Approval request title")
    description: Optional[str] = Field(None, description="Approval request description")
    data_to_approve: Dict[str, Any] = Field(default_factory=dict, description="Data requiring approval")
    
    # Status tracking
    status: str = Field(default="pending", description="Approval status")
    responses: List[Dict[str, Any]] = Field(default_factory=list, description="Approver responses")
    
    # Timing
    requested_at: datetime = Field(default_factory=datetime.utcnow)
    responded_at: Optional[datetime] = Field(None, description="When approval was completed")
    expires_at: Optional[datetime] = Field(None, description="When approval expires")
    
    # Final decision
    decision: Optional[str] = Field(None, description="Final approval decision")
    decision_reason: Optional[str] = Field(None, description="Reason for decision")
    decided_by: Optional[str] = Field(None, description="Who made the final decision")
    
    class Settings:
        name = "approval_requests"
        indexes = [
            IndexModel([("approval_id", 1)], unique=True),
            IndexModel([("instance_id", 1)]),
            IndexModel([("workflow_id", 1)]),
            IndexModel([("status", 1)]),
            IndexModel([("required_approvers", 1)]),
            IndexModel([("requested_at", -1)]),
            IndexModel([("expires_at", 1)]),
        ]


class WorkflowAuditLog(Document):
    """Audit log for workflow operations"""
    log_id: str = Field(..., description="Unique log identifier")
    workflow_id: Optional[str] = Field(None, description="Related workflow ID")
    instance_id: Optional[str] = Field(None, description="Related instance ID")
    
    # Action details
    action: str = Field(..., description="Action performed")
    actor: str = Field(..., description="User who performed the action")
    target: str = Field(..., description="Target of the action")
    
    # Change details
    before_state: Optional[Dict[str, Any]] = Field(None, description="State before change")
    after_state: Optional[Dict[str, Any]] = Field(None, description="State after change")
    
    # Context
    ip_address: Optional[str] = Field(None, description="IP address of the actor")
    user_agent: Optional[str] = Field(None, description="User agent of the actor")
    session_id: Optional[str] = Field(None, description="Session ID")
    
    # Timing
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    
    class Settings:
        name = "workflow_audit_logs"
        indexes = [
            IndexModel([("workflow_id", 1)]),
            IndexModel([("instance_id", 1)]),
            IndexModel([("action", 1)]),
            IndexModel([("actor", 1)]),
            IndexModel([("timestamp", -1)]),
        ]


class IntegrationLog(Document):
    """Log for external service integrations"""
    log_id: str = Field(..., description="Unique log identifier")
    instance_id: str = Field(..., description="Related instance ID")
    step_id: str = Field(..., description="Related step ID")
    
    # Integration details
    service_name: str = Field(..., description="External service name")
    endpoint: str = Field(..., description="API endpoint called")
    method: str = Field(..., description="HTTP method used")
    
    # Request/Response
    request_data: Dict[str, Any] = Field(default_factory=dict, description="Request payload")
    response_data: Dict[str, Any] = Field(default_factory=dict, description="Response payload")
    status_code: Optional[int] = Field(None, description="HTTP status code")
    
    # Timing
    request_at: datetime = Field(default_factory=datetime.utcnow)
    response_at: Optional[datetime] = Field(None, description="When response was received")
    duration_ms: Optional[int] = Field(None, description="Request duration in milliseconds")
    
    # Error handling
    error_message: Optional[str] = Field(None, description="Error message if failed")
    retry_count: int = Field(default=0, description="Number of retries")
    
    class Settings:
        name = "integration_logs"
        indexes = [
            IndexModel([("instance_id", 1)]),
            IndexModel([("service_name", 1)]),
            IndexModel([("status_code", 1)]),
            IndexModel([("request_at", -1)]),
        ]