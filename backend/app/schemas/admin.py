"""
Admin dashboard schemas for API responses
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
from pydantic import BaseModel, Field


class PendingApprovalResponse(BaseModel):
    instance_id: str
    workflow_name: str
    citizen_name: str
    citizen_id: str
    step_name: str
    submitted_at: datetime
    priority: str = "medium"
    approval_type: str
    context: Dict[str, Any]
    assigned_to: Optional[str] = None


class PendingDocumentResponse(BaseModel):
    document_id: str
    title: str
    document_type: str
    citizen_name: str
    citizen_id: str
    uploaded_at: datetime
    file_size: int
    mime_type: str
    status: str = "pending_verification"
    verification_priority: str = "normal"
    previous_attempts: int = 0


class PendingSignatureResponse(BaseModel):
    document_id: str
    title: str
    document_type: str
    citizen_name: str
    citizen_id: str
    workflow_name: str
    signature_type: str
    requires_signature_at: datetime
    deadline: Optional[datetime] = None


class ManualReviewResponse(BaseModel):
    review_id: str
    type: str
    citizen_name: str
    citizen_id: str
    workflow_name: str
    issue_description: str
    severity: str
    created_at: datetime
    context: Dict[str, Any]


class AdminStatsResponse(BaseModel):
    pending_approvals: int
    pending_documents: int
    pending_signatures: int
    manual_reviews: int
    total_pending: int


class WorkflowMetrics(BaseModel):
    workflow_id: str
    workflow_name: str
    total_instances: int
    active_instances: int
    completed_instances: int
    failed_instances: int
    average_processing_time_hours: float
    success_rate: float
    pending_approvals: int


class SystemMetrics(BaseModel):
    total_active_citizens: int
    total_workflow_instances: int
    instances_created_today: int
    instances_completed_today: int
    instances_created_this_week: int
    instances_completed_this_week: int


class PendingItemsBreakdown(BaseModel):
    pending_approvals: int
    pending_documents: int
    pending_signatures: int
    manual_reviews: int
    total_pending: int
    pending_by_priority: Dict[str, int]


class PerformanceMetrics(BaseModel):
    average_processing_time_hours: float
    median_processing_time_hours: float
    success_rate: float
    failure_rate: float
    abandonment_rate: float
    bottleneck_steps: List[Dict[str, Any]]


class TimeSeriesMetric(BaseModel):
    timestamp: datetime
    value: int
    label: Optional[str] = None


class DashboardResponse(BaseModel):
    """Comprehensive dashboard response with all metrics"""
    system_metrics: SystemMetrics
    pending_items: PendingItemsBreakdown
    workflow_metrics: List[WorkflowMetrics]
    performance_metrics: PerformanceMetrics
    recent_activity: List[TimeSeriesMetric]
    top_workflows: List[Dict[str, Any]]
    staff_workload: Dict[str, int]
    system_health: Dict[str, Any]
    last_updated: datetime = Field(default_factory=datetime.utcnow)