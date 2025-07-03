"""
Complaint Handling Workflow
This workflow demonstrates case management, escalation, and resolution tracking.
"""

from datetime import datetime, timedelta
from typing import Dict, Any

from ..base import ActionStep, ConditionalStep, ApprovalStep, IntegrationStep, TerminalStep, ValidationResult
from ..workflow import Workflow


# Validation functions
def validate_complaint(inputs: Dict[str, Any]) -> ValidationResult:
    """Validate complaint submission"""
    errors = []
    
    required_fields = ["complaint_type", "description", "complainant_contact"]
    for field in required_fields:
        if not inputs.get(field):
            errors.append(f"{field} is required")
    
    # Validate description length
    description = inputs.get("description", "")
    if len(description) < 20:
        errors.append("Description must be at least 20 characters")
    
    return ValidationResult(is_valid=len(errors) == 0, errors=errors)


# Step action functions
def log_complaint(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Log the initial complaint"""
    import uuid
    
    complaint_id = f"COMP-{datetime.utcnow().strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}"
    
    return {
        "complaint_id": complaint_id,
        "submission_date": datetime.utcnow().isoformat(),
        "status": "submitted",
        "priority": "normal"
    }


def categorize_complaint(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Automatically categorize the complaint"""
    complaint_type = inputs.get("complaint_type", "").lower()
    description = inputs.get("description", "").lower()
    
    # Determine priority based on type and keywords
    priority = "normal"
    urgency_keywords = ["emergency", "urgent", "immediate", "danger", "safety"]
    
    if any(keyword in description for keyword in urgency_keywords):
        priority = "high"
    elif complaint_type in ["safety", "health", "emergency"]:
        priority = "high"
    elif complaint_type in ["noise", "parking", "minor"]:
        priority = "low"
    
    # Determine department
    department_mapping = {
        "noise": "Environmental Health",
        "parking": "Transportation",
        "safety": "Public Safety",
        "health": "Health Department",
        "maintenance": "Public Works",
        "tax": "Finance Department",
        "permit": "Planning Department"
    }
    
    assigned_department = department_mapping.get(complaint_type, "General Services")
    
    # Estimate resolution time
    resolution_timeframes = {
        "high": 2,      # 2 days
        "normal": 7,    # 7 days
        "low": 14       # 14 days
    }
    
    expected_resolution = datetime.utcnow() + timedelta(days=resolution_timeframes[priority])
    
    return {
        "categorization_completed": True,
        "priority": priority,
        "assigned_department": assigned_department,
        "expected_resolution_date": expected_resolution.isoformat(),
        "categorization_date": datetime.utcnow().isoformat()
    }


def assign_case_officer(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Assign a case officer to handle the complaint"""
    department = context.get("assigned_department", "General Services")
    priority = context.get("priority", "normal")
    
    # Simulate officer assignment logic
    officers = {
        "Environmental Health": ["officer_env_001", "officer_env_002"],
        "Transportation": ["officer_trans_001"],
        "Public Safety": ["officer_safety_001", "officer_safety_002"],
        "Health Department": ["officer_health_001"],
        "Public Works": ["officer_works_001", "officer_works_002"],
        "Finance Department": ["officer_finance_001"],
        "Planning Department": ["officer_planning_001"],
        "General Services": ["officer_general_001", "officer_general_002"]
    }
    
    available_officers = officers.get(department, ["officer_general_001"])
    assigned_officer = available_officers[0]  # Simplified assignment
    
    return {
        "officer_assigned": True,
        "assigned_officer": assigned_officer,
        "assignment_date": datetime.utcnow().isoformat(),
        "workload_priority": priority
    }


def conduct_initial_assessment(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Conduct initial assessment of the complaint"""
    complaint_type = inputs.get("complaint_type")
    
    # Simulate assessment
    requires_site_visit = complaint_type in ["noise", "safety", "maintenance", "health"]
    requires_documentation = complaint_type in ["permit", "tax", "legal"]
    estimated_effort = "high" if requires_site_visit else "medium"
    
    return {
        "initial_assessment_completed": True,
        "requires_site_visit": requires_site_visit,
        "requires_documentation": requires_documentation,
        "estimated_effort": estimated_effort,
        "assessment_date": datetime.utcnow().isoformat()
    }


def schedule_site_visit(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Schedule site visit for investigation"""
    visit_date = datetime.utcnow() + timedelta(days=3)
    
    return {
        "site_visit_scheduled": True,
        "visit_date": visit_date.isoformat(),
        "inspector": context.get("assigned_officer"),
        "visit_type": "complaint_investigation"
    }


def conduct_investigation(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Conduct detailed investigation"""
    # Simulate investigation results
    findings = {
        "complaint_substantiated": True,
        "evidence_found": True,
        "violation_identified": False,
        "action_required": True
    }
    
    return {
        "investigation_completed": True,
        "findings": findings,
        "investigation_date": datetime.utcnow().isoformat(),
        "investigator": context.get("assigned_officer"),
        "next_action": "resolution_required"
    }


def develop_resolution_plan(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Develop plan to resolve the complaint"""
    complaint_type = inputs.get("complaint_type")
    findings = context.get("findings", {})
    
    # Generate resolution plan based on type and findings
    resolution_actions = []
    
    if complaint_type == "noise":
        resolution_actions = ["Issue warning notice", "Schedule follow-up inspection"]
    elif complaint_type == "maintenance":
        resolution_actions = ["Schedule repair work", "Assign maintenance crew"]
    elif complaint_type == "safety":
        resolution_actions = ["Install safety measures", "Update signage"]
    else:
        resolution_actions = ["Address concern", "Implement corrective measures"]
    
    estimated_completion = datetime.utcnow() + timedelta(days=5)
    
    return {
        "resolution_plan_developed": True,
        "planned_actions": resolution_actions,
        "estimated_completion": estimated_completion.isoformat(),
        "plan_date": datetime.utcnow().isoformat()
    }


def implement_resolution(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Implement the resolution plan"""
    planned_actions = context.get("planned_actions", [])
    
    # Simulate implementation
    completed_actions = []
    for action in planned_actions:
        completed_actions.append({
            "action": action,
            "completed_date": datetime.utcnow().isoformat(),
            "status": "completed"
        })
    
    return {
        "resolution_implemented": True,
        "completed_actions": completed_actions,
        "implementation_date": datetime.utcnow().isoformat(),
        "resolution_status": "completed"
    }


def notify_complainant(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Notify complainant of resolution"""
    contact_method = inputs.get("preferred_contact", "email")
    complaint_id = context.get("complaint_id")
    
    return {
        "complainant_notified": True,
        "notification_method": contact_method,
        "notification_date": datetime.utcnow().isoformat(),
        "message": f"Your complaint {complaint_id} has been resolved"
    }


def escalate_complaint(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Escalate complaint to supervisor"""
    return {
        "escalated": True,
        "escalation_reason": "Requires supervisory review",
        "escalation_date": datetime.utcnow().isoformat(),
        "escalated_to": "supervisor_001"
    }


def close_complaint(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Close the complaint case"""
    return {
        "case_closed": True,
        "closure_date": datetime.utcnow().isoformat(),
        "closure_reason": "Resolved to complainant satisfaction",
        "final_status": "resolved"
    }


# Condition functions
def is_high_priority(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if complaint is high priority"""
    return context.get("priority") == "high"


def requires_site_visit(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if site visit is required"""
    return context.get("requires_site_visit", False)


def complaint_substantiated(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if complaint was substantiated"""
    findings = context.get("findings", {})
    return findings.get("complaint_substantiated", False)


def requires_escalation(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if escalation is required"""
    # Escalate if high priority with violations or complex cases
    priority = context.get("priority")
    findings = context.get("findings", {})
    violation_identified = findings.get("violation_identified", False)
    
    return priority == "high" and violation_identified


def supervisor_approved(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if supervisor approved the resolution"""
    return context.get("approval_status") == "approved"


def supervisor_rejected(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if supervisor rejected the resolution"""
    return context.get("approval_status") == "rejected"


def create_complaint_handling_workflow() -> Workflow:
    """Create the complaint handling workflow"""
    
    workflow = Workflow(
        workflow_id="complaint_handling_v1",
        name="Complaint Handling",
        description="Workflow for handling citizen complaints with investigation and resolution"
    )
    
    # Steps
    step_log_complaint = ActionStep(
        step_id="log_complaint",
        name="Log Complaint",
        action=log_complaint,
        description="Log the initial complaint submission",
        required_inputs=["complaint_type", "description", "complainant_contact"]
    ).add_validation(validate_complaint)
    
    step_categorize = ActionStep(
        step_id="categorize_complaint",
        name="Categorize Complaint",
        action=categorize_complaint,
        description="Automatically categorize and prioritize the complaint"
    )
    
    step_priority_check = ConditionalStep(
        step_id="priority_check",
        name="Priority Assessment",
        description="Check complaint priority level"
    )
    
    step_assign_officer = ActionStep(
        step_id="assign_case_officer",
        name="Assign Case Officer",
        action=assign_case_officer,
        description="Assign appropriate case officer"
    )
    
    step_initial_assessment = ActionStep(
        step_id="initial_assessment",
        name="Initial Assessment",
        action=conduct_initial_assessment,
        description="Conduct initial assessment of the complaint"
    )
    
    step_site_visit_check = ConditionalStep(
        step_id="site_visit_check",
        name="Site Visit Requirement",
        description="Check if site visit is required"
    )
    
    step_schedule_visit = ActionStep(
        step_id="schedule_site_visit",
        name="Schedule Site Visit",
        action=schedule_site_visit,
        description="Schedule site visit for investigation"
    )
    
    step_investigate = ActionStep(
        step_id="conduct_investigation",
        name="Conduct Investigation",
        action=conduct_investigation,
        description="Conduct detailed investigation"
    )
    
    step_substantiation_check = ConditionalStep(
        step_id="substantiation_check",
        name="Complaint Substantiation",
        description="Check if complaint was substantiated"
    )
    
    step_escalation_check = ConditionalStep(
        step_id="escalation_check",
        name="Escalation Assessment",
        description="Check if escalation is required"
    )
    
    step_supervisor_review = ApprovalStep(
        step_id="supervisor_review",
        name="Supervisor Review",
        description="Supervisor review for complex cases",
        approvers=["supervisor_001", "manager_001"],
        approval_type="any"
    )
    
    step_process_supervisor_decision = ActionStep(
        step_id="process_supervisor_decision",
        name="Process Supervisor Decision",
        action=lambda inputs, context: {"approval_status": context.get("approval_status", "approved")},
        description="Process supervisor review decision"
    )
    
    step_supervisor_decision_check = ConditionalStep(
        step_id="supervisor_decision_check",
        name="Supervisor Decision",
        description="Check supervisor decision"
    )
    
    step_develop_plan = ActionStep(
        step_id="develop_resolution_plan",
        name="Develop Resolution Plan",
        action=develop_resolution_plan,
        description="Develop plan to resolve the complaint"
    )
    
    step_implement_resolution = ActionStep(
        step_id="implement_resolution",
        name="Implement Resolution",
        action=implement_resolution,
        description="Implement the resolution plan"
    )
    
    step_notify_complainant = ActionStep(
        step_id="notify_complainant",
        name="Notify Complainant",
        action=notify_complainant,
        description="Notify complainant of resolution"
    )
    
    step_close_case = ActionStep(
        step_id="close_complaint",
        name="Close Case",
        action=close_complaint,
        description="Close the complaint case"
    )
    
    step_escalate = ActionStep(
        step_id="escalate_complaint",
        name="Escalate Complaint",
        action=escalate_complaint,
        description="Escalate complaint to higher authority"
    )
    
    # Terminal steps
    terminal_resolved = TerminalStep(
        step_id="complaint_resolved",
        name="Complaint Resolved",
        terminal_status="SUCCESS",
        description="Complaint successfully resolved"
    )
    
    terminal_unsubstantiated = TerminalStep(
        step_id="complaint_unsubstantiated",
        name="Complaint Unsubstantiated",
        terminal_status="CLOSED",
        description="Complaint investigation found no merit"
    )
    
    terminal_escalated = TerminalStep(
        step_id="complaint_escalated",
        name="Complaint Escalated",
        terminal_status="ESCALATED",
        description="Complaint escalated to higher authority"
    )
    
    terminal_supervisor_rejected = TerminalStep(
        step_id="supervisor_rejected_case",
        name="Supervisor Rejected",
        terminal_status="REJECTED",
        description="Supervisor rejected the case resolution"
    )
    
    # Define workflow flow
    
    # Initial processing
    step_log_complaint >> step_categorize >> step_priority_check
    
    # Priority-based routing
    step_priority_check.when(is_high_priority) >> step_assign_officer
    step_priority_check.when(lambda i, c: not is_high_priority(i, c)) >> step_assign_officer
    
    # Assessment and investigation
    step_assign_officer >> step_initial_assessment >> step_site_visit_check
    
    # Site visit path
    step_site_visit_check.when(requires_site_visit) >> step_schedule_visit >> step_investigate
    step_site_visit_check.when(lambda i, c: not requires_site_visit(i, c)) >> step_investigate
    
    # Investigation results
    step_investigate >> step_substantiation_check
    step_substantiation_check.when(complaint_substantiated) >> step_escalation_check
    step_substantiation_check.when(lambda i, c: not complaint_substantiated(i, c)) >> step_notify_complainant >> terminal_unsubstantiated
    
    # Escalation decision
    step_escalation_check.when(requires_escalation) >> step_supervisor_review >> step_process_supervisor_decision >> step_supervisor_decision_check
    step_escalation_check.when(lambda i, c: not requires_escalation(i, c)) >> step_develop_plan
    
    # Supervisor review outcomes
    step_supervisor_decision_check.when(supervisor_approved) >> step_develop_plan
    step_supervisor_decision_check.when(supervisor_rejected) >> step_escalate >> terminal_escalated
    
    # Resolution implementation
    step_develop_plan >> step_implement_resolution >> step_notify_complainant >> step_close_case >> terminal_resolved
    
    # Build and validate
    workflow.build_graph()
    workflow.validate()
    
    return workflow


# Example usage
if __name__ == "__main__":
    workflow = create_complaint_handling_workflow()
    print(workflow.to_mermaid())