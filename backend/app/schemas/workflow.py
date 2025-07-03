from typing import Dict, Any, List, Optional
from datetime import datetime
from pydantic import BaseModel, Field
from enum import Enum


class WorkflowStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    INACTIVE = "inactive"
    ARCHIVED = "archived"


class StepType(str, Enum):
    ACTION = "action"
    CONDITIONAL = "conditional"
    APPROVAL = "approval"
    INTEGRATION = "integration"
    TERMINAL = "terminal"


class StepSchema(BaseModel):
    step_id: str
    name: str
    step_type: StepType
    description: Optional[str] = None
    required_inputs: List[str] = Field(default_factory=list)
    optional_inputs: List[str] = Field(default_factory=list)
    next_steps: List[str] = Field(default_factory=list)
    
    class Config:
        use_enum_values = True


class WorkflowBase(BaseModel):
    name: str
    description: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class WorkflowCreate(WorkflowBase):
    workflow_id: str
    version: str = "1.0.0"


class WorkflowUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[WorkflowStatus] = None
    metadata: Optional[Dict[str, Any]] = None


class WorkflowResponse(WorkflowBase):
    workflow_id: str
    version: str
    status: WorkflowStatus
    steps: List[StepSchema]
    start_step_id: Optional[str]
    created_at: datetime
    updated_at: datetime
    
    class Config:
        use_enum_values = True


class WorkflowListResponse(BaseModel):
    workflows: List[WorkflowResponse]
    total: int
    page: int = 1
    page_size: int = 20


class WorkflowDiagram(BaseModel):
    workflow_id: str
    diagram_type: str = "mermaid"
    content: str


class WorkflowExecuteRequest(BaseModel):
    workflow_id: str
    initial_context: Dict[str, Any] = Field(default_factory=dict)
    user_id: str


class InstanceStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"


class InstanceResponse(BaseModel):
    instance_id: str
    workflow_id: str
    user_id: str
    status: InstanceStatus
    current_step: Optional[str]
    context: Dict[str, Any]
    step_results: Dict[str, Any]
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime]
    
    class Config:
        use_enum_values = True


class InstanceListResponse(BaseModel):
    instances: List[InstanceResponse]
    total: int
    page: int = 1
    page_size: int = 20


class InstanceUpdateRequest(BaseModel):
    status: Optional[InstanceStatus] = None
    context_updates: Optional[Dict[str, Any]] = None


class ApprovalRequest(BaseModel):
    instance_id: str
    step_id: str
    decision: str  # "approved" or "rejected"
    comments: Optional[str] = None
    approver_id: str