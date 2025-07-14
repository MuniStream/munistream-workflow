from typing import Dict, Any, List, Optional
from datetime import datetime
from beanie import Document, Indexed
from pydantic import Field
from pymongo import IndexModel


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
        ]


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