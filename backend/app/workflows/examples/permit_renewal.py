"""
Permit Renewal Workflow
This workflow demonstrates automated renewals, expiry checks, and payment processing.
"""

from datetime import datetime, timedelta
from typing import Dict, Any

from ..base import ActionStep, ConditionalStep, ApprovalStep, IntegrationStep, TerminalStep, ValidationResult
from ..workflow import Workflow


# Validation functions
def validate_permit_info(inputs: Dict[str, Any]) -> ValidationResult:
    """Validate permit renewal information"""
    errors = []
    
    permit_number = inputs.get("permit_number", "")
    if not permit_number:
        errors.append("Permit number is required")
    
    # Check permit number format
    if permit_number and not permit_number.startswith(("BL-", "BP-", "OP-")):
        errors.append("Invalid permit number format")
    
    return ValidationResult(is_valid=len(errors) == 0, errors=errors)


# Step action functions
def lookup_permit(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Look up existing permit information"""
    permit_number = inputs.get("permit_number")
    
    # Simulate database lookup
    permit_info = {
        "permit_number": permit_number,
        "permit_type": "business_license",
        "holder_name": "Acme Corporation",
        "business_address": "123 Main St, City, State",
        "issue_date": "2023-01-15",
        "expiry_date": "2024-01-15",
        "status": "active",
        "previous_violations": [],
        "last_inspection_date": "2023-06-15",
        "last_inspection_result": "passed"
    }
    
    return {
        "permit_found": True,
        "permit_info": permit_info,
        "lookup_date": datetime.utcnow().isoformat()
    }


def check_permit_status(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Check current permit status and eligibility for renewal"""
    permit_info = context.get("permit_info", {})
    expiry_date_str = permit_info.get("expiry_date")
    
    if expiry_date_str:
        expiry_date = datetime.strptime(expiry_date_str, "%Y-%m-%d")
        days_until_expiry = (expiry_date - datetime.utcnow()).days
        
        # Check if permit is eligible for renewal (within 60 days of expiry)
        eligible_for_renewal = -30 <= days_until_expiry <= 60
        is_expired = days_until_expiry < 0
        
        # Check for violations or issues
        violations = permit_info.get("previous_violations", [])
        has_violations = len(violations) > 0
        
        return {
            "permit_status_checked": True,
            "days_until_expiry": days_until_expiry,
            "is_expired": is_expired,
            "eligible_for_renewal": eligible_for_renewal,
            "has_violations": has_violations,
            "violation_count": len(violations),
            "check_date": datetime.utcnow().isoformat()
        }
    
    return {
        "permit_status_checked": False,
        "error": "Invalid expiry date"
    }


def check_compliance_history(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Check compliance history and outstanding issues"""
    permit_info = context.get("permit_info", {})
    
    # Simulate compliance check
    compliance_issues = []
    
    # Check for outstanding fines
    outstanding_fines = 0.00  # Simulate no outstanding fines
    
    # Check inspection history
    last_inspection = permit_info.get("last_inspection_result", "unknown")
    
    compliance_score = 85  # Simulate compliance score
    
    return {
        "compliance_checked": True,
        "compliance_score": compliance_score,
        "outstanding_fines": outstanding_fines,
        "compliance_issues": compliance_issues,
        "last_inspection_result": last_inspection,
        "compliance_status": "good" if compliance_score >= 70 else "poor"
    }


def calculate_renewal_fee(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Calculate renewal fees"""
    permit_info = context.get("permit_info", {})
    permit_type = permit_info.get("permit_type", "unknown")
    
    # Base renewal fees
    base_fees = {
        "business_license": 125.00,
        "building_permit": 200.00,
        "operating_permit": 150.00
    }
    
    base_fee = base_fees.get(permit_type, 100.00)
    
    # Additional fees
    processing_fee = 15.00
    
    # Late fee if expired
    is_expired = context.get("is_expired", False)
    late_fee = 50.00 if is_expired else 0.00
    
    # Outstanding fines
    outstanding_fines = context.get("outstanding_fines", 0.00)
    
    total_fee = base_fee + processing_fee + late_fee + outstanding_fines
    
    return {
        "base_fee": base_fee,
        "processing_fee": processing_fee,
        "late_fee": late_fee,
        "outstanding_fines": outstanding_fines,
        "total_renewal_fee": total_fee,
        "fee_calculation_date": datetime.utcnow().isoformat()
    }


def update_permit_information(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Update permit information if provided"""
    updates = inputs.get("permit_updates", {})
    
    updated_fields = []
    for field, value in updates.items():
        if value:  # Only update non-empty values
            updated_fields.append(field)
    
    return {
        "information_updated": len(updated_fields) > 0,
        "updated_fields": updated_fields,
        "update_date": datetime.utcnow().isoformat()
    }


def process_renewal_payment(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Process renewal fee payment"""
    total_fee = context.get("total_renewal_fee", 0.00)
    payment_method = inputs.get("payment_method", "credit_card")
    
    # Simulate payment processing
    transaction_id = f"REN-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    
    return {
        "payment_processed": True,
        "amount_paid": total_fee,
        "payment_method": payment_method,
        "transaction_id": transaction_id,
        "payment_date": datetime.utcnow().isoformat()
    }


def issue_renewed_permit(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Issue the renewed permit"""
    permit_info = context.get("permit_info", {})
    
    # Calculate new expiry date (1 year from now)
    new_issue_date = datetime.utcnow()
    new_expiry_date = new_issue_date + timedelta(days=365)
    
    # Generate new permit number or keep existing
    old_permit_number = permit_info.get("permit_number", "")
    new_permit_number = old_permit_number  # Keep same number for renewals
    
    return {
        "permit_renewed": True,
        "new_permit_number": new_permit_number,
        "new_issue_date": new_issue_date.isoformat(),
        "new_expiry_date": new_expiry_date.isoformat(),
        "permit_type": permit_info.get("permit_type"),
        "renewal_type": "standard"
    }


def schedule_inspection(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Schedule required inspection for renewal"""
    inspection_date = datetime.utcnow() + timedelta(days=14)
    
    return {
        "inspection_scheduled": True,
        "inspection_date": inspection_date.isoformat(),
        "inspection_type": "renewal_compliance",
        "inspector_assigned": "inspector_002"
    }


def send_denial_notice(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Send renewal denial notice"""
    denial_reason = context.get("denial_reason", "Renewal requirements not met")
    
    return {
        "denial_notice_sent": True,
        "denial_reason": denial_reason,
        "notice_date": datetime.utcnow().isoformat(),
        "appeal_deadline": (datetime.utcnow() + timedelta(days=30)).isoformat()
    }


# Condition functions
def permit_found(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if permit was found in system"""
    return context.get("permit_found", False)


def eligible_for_renewal(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if permit is eligible for renewal"""
    return context.get("eligible_for_renewal", False)


def has_good_compliance(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if compliance history is acceptable"""
    compliance_status = context.get("compliance_status", "poor")
    return compliance_status == "good"


def requires_manual_review(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if manual review is required"""
    has_violations = context.get("has_violations", False)
    compliance_score = context.get("compliance_score", 0)
    return has_violations or compliance_score < 70


def renewal_approved(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if renewal was approved"""
    return context.get("renewal_decision") == "approved"


def renewal_denied(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if renewal was denied"""
    return context.get("renewal_decision") == "denied"


def payment_successful(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if payment was successful"""
    return context.get("payment_processed", False)


def create_permit_renewal_workflow() -> Workflow:
    """Create the permit renewal workflow"""
    
    workflow = Workflow(
        workflow_id="permit_renewal_v1",
        name="Permit Renewal",
        description="Automated workflow for permit renewals with compliance checking"
    )
    
    # Steps
    step_lookup = ActionStep(
        step_id="lookup_permit",
        name="Lookup Permit",
        action=lookup_permit,
        description="Look up existing permit information",
        required_inputs=["permit_number"]
    ).add_validation(validate_permit_info)
    
    step_permit_check = ConditionalStep(
        step_id="permit_existence_check",
        name="Permit Existence Check",
        description="Check if permit exists in system"
    )
    
    step_status_check = ActionStep(
        step_id="check_permit_status",
        name="Check Permit Status",
        action=check_permit_status,
        description="Check current permit status and renewal eligibility"
    )
    
    step_eligibility_check = ConditionalStep(
        step_id="renewal_eligibility_check",
        name="Renewal Eligibility Check",
        description="Check if permit is eligible for renewal"
    )
    
    step_compliance_check = ActionStep(
        step_id="check_compliance",
        name="Check Compliance History",
        action=check_compliance_history,
        description="Review compliance history and outstanding issues"
    )
    
    step_compliance_review = ConditionalStep(
        step_id="compliance_review",
        name="Compliance Review",
        description="Review compliance status"
    )
    
    step_manual_review_check = ConditionalStep(
        step_id="manual_review_check",
        name="Manual Review Requirement",
        description="Determine if manual review is needed"
    )
    
    step_manual_approval = ApprovalStep(
        step_id="manual_renewal_review",
        name="Manual Renewal Review",
        description="Manual review for complex renewals",
        approvers=["renewal_officer", "supervisor"],
        approval_type="any"
    )
    
    step_process_manual_decision = ActionStep(
        step_id="process_manual_decision",
        name="Process Manual Decision",
        action=lambda inputs, context: {"renewal_decision": context.get("approval_status", "approved")},
        description="Process manual review decision"
    )
    
    step_manual_decision_check = ConditionalStep(
        step_id="manual_decision_check",
        name="Manual Decision Check",
        description="Check manual review decision"
    )
    
    step_update_info = ActionStep(
        step_id="update_permit_info",
        name="Update Permit Information",
        action=update_permit_information,
        description="Update permit information if needed"
    )
    
    step_calculate_fees = ActionStep(
        step_id="calculate_renewal_fee",
        name="Calculate Renewal Fee",
        action=calculate_renewal_fee,
        description="Calculate total renewal fees"
    )
    
    step_process_payment = ActionStep(
        step_id="process_payment",
        name="Process Payment",
        action=process_renewal_payment,
        description="Process renewal fee payment"
    )
    
    step_payment_check = ConditionalStep(
        step_id="payment_verification",
        name="Payment Verification",
        description="Verify payment completion"
    )
    
    step_issue_renewal = ActionStep(
        step_id="issue_renewed_permit",
        name="Issue Renewed Permit",
        action=issue_renewed_permit,
        description="Issue the renewed permit"
    )
    
    step_schedule_inspection = ActionStep(
        step_id="schedule_inspection",
        name="Schedule Inspection",
        action=schedule_inspection,
        description="Schedule post-renewal inspection if required"
    )
    
    step_send_denial = ActionStep(
        step_id="send_denial_notice",
        name="Send Denial Notice",
        action=send_denial_notice,
        description="Send renewal denial notice"
    )
    
    # Terminal steps
    terminal_success = TerminalStep(
        step_id="renewal_completed",
        name="Permit Renewed Successfully",
        terminal_status="SUCCESS",
        description="Permit renewal completed successfully"
    )
    
    terminal_not_found = TerminalStep(
        step_id="permit_not_found",
        name="Permit Not Found",
        terminal_status="FAILURE",
        description="Permit not found in system"
    )
    
    terminal_not_eligible = TerminalStep(
        step_id="not_eligible_for_renewal",
        name="Not Eligible for Renewal",
        terminal_status="REJECTED",
        description="Permit not eligible for renewal at this time"
    )
    
    terminal_compliance_failure = TerminalStep(
        step_id="compliance_issues",
        name="Compliance Issues",
        terminal_status="FAILURE",
        description="Outstanding compliance issues prevent renewal"
    )
    
    terminal_denied = TerminalStep(
        step_id="renewal_denied",
        name="Renewal Denied",
        terminal_status="REJECTED",
        description="Permit renewal application denied"
    )
    
    terminal_payment_failed = TerminalStep(
        step_id="payment_failed",
        name="Payment Failed",
        terminal_status="FAILURE",
        description="Payment processing failed"
    )
    
    # Define workflow flow
    
    # Initial permit lookup
    step_lookup >> step_permit_check
    step_permit_check.when(permit_found) >> step_status_check
    step_permit_check.when(lambda i, c: not permit_found(i, c)) >> terminal_not_found
    
    # Eligibility check
    step_status_check >> step_eligibility_check
    step_eligibility_check.when(eligible_for_renewal) >> step_compliance_check
    step_eligibility_check.when(lambda i, c: not eligible_for_renewal(i, c)) >> terminal_not_eligible
    
    # Compliance review
    step_compliance_check >> step_compliance_review
    step_compliance_review.when(has_good_compliance) >> step_manual_review_check
    step_compliance_review.when(lambda i, c: not has_good_compliance(i, c)) >> terminal_compliance_failure
    
    # Manual review decision
    step_manual_review_check.when(requires_manual_review) >> step_manual_approval >> step_process_manual_decision >> step_manual_decision_check
    step_manual_review_check.when(lambda i, c: not requires_manual_review(i, c)) >> step_update_info
    
    # Manual review outcomes
    step_manual_decision_check.when(renewal_approved) >> step_update_info
    step_manual_decision_check.when(renewal_denied) >> step_send_denial >> terminal_denied
    
    # Fee calculation and payment
    step_update_info >> step_calculate_fees >> step_process_payment >> step_payment_check
    
    # Payment outcomes
    step_payment_check.when(payment_successful) >> step_issue_renewal >> step_schedule_inspection >> terminal_success
    step_payment_check.when(lambda i, c: not payment_successful(i, c)) >> terminal_payment_failed
    
    # Build and validate
    workflow.build_graph()
    workflow.validate()
    
    return workflow


# Example usage
if __name__ == "__main__":
    workflow = create_permit_renewal_workflow()
    print(workflow.to_mermaid())