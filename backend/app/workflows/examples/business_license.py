"""
Business License Application Workflow
This workflow demonstrates complex approval chains and document verification.
"""

from datetime import datetime, date
from typing import Dict, Any

from ..base import ActionStep, ConditionalStep, ApprovalStep, IntegrationStep, TerminalStep, ValidationResult
from ..workflow import Workflow


# Validation functions
def validate_business_info(inputs: Dict[str, Any]) -> ValidationResult:
    """Validate business information"""
    errors = []
    
    required_fields = ["business_name", "business_type", "address", "owner_name", "tax_id"]
    for field in required_fields:
        if not inputs.get(field):
            errors.append(f"{field} is required")
    
    # Validate tax ID format (simplified)
    tax_id = inputs.get("tax_id", "")
    if tax_id and not tax_id.replace("-", "").isdigit():
        errors.append("Invalid tax ID format")
    
    return ValidationResult(is_valid=len(errors) == 0, errors=errors)


def validate_documents(inputs: Dict[str, Any]) -> ValidationResult:
    """Validate required documents"""
    required_docs = ["identity_document", "proof_of_address", "business_plan"]
    uploaded_docs = inputs.get("documents", [])
    
    missing_docs = [doc for doc in required_docs if doc not in uploaded_docs]
    
    if missing_docs:
        return ValidationResult(
            is_valid=False,
            errors=[f"Missing required document: {doc}" for doc in missing_docs]
        )
    
    return ValidationResult(is_valid=True)


# Step action functions
def submit_application(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Process initial application submission"""
    import uuid
    
    application_id = f"BL-{datetime.utcnow().strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}"
    
    return {
        "application_id": application_id,
        "submission_date": datetime.utcnow().isoformat(),
        "status": "submitted",
        "initial_review_required": True
    }


def conduct_initial_review(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Conduct initial application review"""
    # Simulate review logic
    business_type = inputs.get("business_type", "").lower()
    
    # Some business types require additional scrutiny
    high_risk_types = ["restaurant", "bar", "manufacturing", "healthcare"]
    requires_inspection = business_type in high_risk_types
    
    return {
        "initial_review_completed": True,
        "requires_inspection": requires_inspection,
        "risk_level": "high" if requires_inspection else "low",
        "reviewer": "initial_review_officer",
        "review_date": datetime.utcnow().isoformat()
    }


def verify_documents(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Verify submitted documents"""
    # Simulate document verification
    documents = inputs.get("documents", [])
    
    verification_results = {}
    for doc in documents:
        # Simulate verification (in real system, this would call external services)
        verification_results[doc] = {
            "verified": True,
            "verification_method": "automated_scan",
            "confidence": 0.95
        }
    
    all_verified = all(result["verified"] for result in verification_results.values())
    
    return {
        "documents_verified": all_verified,
        "verification_results": verification_results,
        "verification_date": datetime.utcnow().isoformat()
    }


def schedule_inspection(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Schedule on-site inspection"""
    from datetime import timedelta
    
    # Schedule inspection 5-10 days from now
    inspection_date = datetime.utcnow() + timedelta(days=7)
    
    return {
        "inspection_scheduled": True,
        "inspection_date": inspection_date.isoformat(),
        "inspector_assigned": "inspector_001",
        "inspection_type": "initial_business_license"
    }


def conduct_inspection(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Record inspection results"""
    # Simulate inspection results
    return {
        "inspection_completed": True,
        "inspection_passed": True,  # Simplified - in reality this would vary
        "inspector": "inspector_001",
        "inspection_date": datetime.utcnow().isoformat(),
        "notes": "Premises meets all safety and zoning requirements"
    }


def calculate_fees(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Calculate licensing fees"""
    business_type = inputs.get("business_type", "").lower()
    
    # Base fees by business type
    fee_schedule = {
        "retail": 150.00,
        "restaurant": 350.00,
        "bar": 500.00,
        "manufacturing": 750.00,
        "healthcare": 400.00,
        "service": 100.00
    }
    
    base_fee = fee_schedule.get(business_type, 200.00)
    
    # Additional fees
    inspection_fee = 75.00 if context.get("requires_inspection") else 0.00
    processing_fee = 25.00
    
    total_fee = base_fee + inspection_fee + processing_fee
    
    return {
        "base_fee": base_fee,
        "inspection_fee": inspection_fee,
        "processing_fee": processing_fee,
        "total_fee": total_fee,
        "fee_calculation_date": datetime.utcnow().isoformat()
    }


def process_payment(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Process license fee payment"""
    # Simulate payment processing
    total_fee = context.get("total_fee", 0.00)
    
    return {
        "payment_processed": True,
        "amount_paid": total_fee,
        "payment_method": "credit_card",
        "transaction_id": f"TXN-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
        "payment_date": datetime.utcnow().isoformat()
    }


def issue_license(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Issue the business license"""
    from datetime import timedelta
    
    issue_date = datetime.utcnow()
    expiry_date = issue_date + timedelta(days=365)  # Valid for 1 year
    
    license_number = f"BL-{issue_date.strftime('%Y')}-{context.get('application_id', 'UNKNOWN').split('-')[-1]}"
    
    return {
        "license_issued": True,
        "license_number": license_number,
        "issue_date": issue_date.isoformat(),
        "expiry_date": expiry_date.isoformat(),
        "license_type": inputs.get("business_type"),
        "business_name": inputs.get("business_name")
    }


def send_rejection_notice(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Send rejection notice to applicant"""
    rejection_reason = context.get("rejection_reason", "Application did not meet requirements")
    
    return {
        "rejection_notice_sent": True,
        "rejection_reason": rejection_reason,
        "notice_date": datetime.utcnow().isoformat(),
        "appeal_deadline": (datetime.utcnow() + timedelta(days=30)).isoformat()
    }


# Condition functions
def documents_verified(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if all documents are verified"""
    return context.get("documents_verified", False)


def requires_inspection(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if inspection is required"""
    return context.get("requires_inspection", False)


def inspection_passed(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if inspection passed"""
    return context.get("inspection_passed", False)


def initial_review_approved(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if initial review was approved"""
    return context.get("initial_review_decision") == "approved"


def initial_review_rejected(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if initial review was rejected"""
    return context.get("initial_review_decision") == "rejected"


def final_approval_granted(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if final approval was granted"""
    return context.get("final_approval_decision") == "approved"


def final_approval_denied(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if final approval was denied"""
    return context.get("final_approval_decision") == "rejected"


def payment_completed(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if payment was completed"""
    return context.get("payment_processed", False)


def create_business_license_workflow() -> Workflow:
    """Create the business license application workflow"""
    
    workflow = Workflow(
        workflow_id="business_license_v1",
        name="Business License Application",
        description="Complete workflow for business license applications with inspections and approvals"
    )
    
    # Steps
    step_submit = ActionStep(
        step_id="submit_application",
        name="Submit Application",
        action=submit_application,
        description="Submit initial business license application",
        required_inputs=["business_name", "business_type", "address", "owner_name", "tax_id", "documents"]
    ).add_validation(validate_business_info).add_validation(validate_documents)
    
    step_initial_review = ActionStep(
        step_id="initial_review",
        name="Initial Review",
        action=conduct_initial_review,
        description="Conduct initial application review"
    )
    
    step_verify_docs = ActionStep(
        step_id="verify_documents",
        name="Verify Documents",
        action=verify_documents,
        description="Verify submitted documents"
    )
    
    step_doc_check = ConditionalStep(
        step_id="document_verification_check",
        name="Document Verification Check",
        description="Check if documents are verified"
    )
    
    step_initial_approval = ApprovalStep(
        step_id="initial_approval",
        name="Initial Application Approval",
        description="Initial approval by licensing officer",
        approvers=["licensing_officer", "supervisor"],
        approval_type="any"
    )
    
    step_process_initial_approval = ActionStep(
        step_id="process_initial_approval",
        name="Process Initial Approval",
        action=lambda inputs, context: {"initial_review_decision": context.get("approval_status", "approved")},
        description="Process initial approval decision"
    )
    
    step_approval_check = ConditionalStep(
        step_id="initial_approval_check",
        name="Initial Approval Decision",
        description="Check initial approval decision"
    )
    
    step_inspection_check = ConditionalStep(
        step_id="inspection_requirement_check",
        name="Inspection Requirement Check",
        description="Check if inspection is required"
    )
    
    step_schedule_inspection = ActionStep(
        step_id="schedule_inspection",
        name="Schedule Inspection",
        action=schedule_inspection,
        description="Schedule on-site inspection"
    )
    
    step_conduct_inspection = ActionStep(
        step_id="conduct_inspection",
        name="Conduct Inspection",
        action=conduct_inspection,
        description="Perform on-site inspection"
    )
    
    step_inspection_result_check = ConditionalStep(
        step_id="inspection_result_check",
        name="Inspection Result Check",
        description="Check inspection results"
    )
    
    step_calculate_fees = ActionStep(
        step_id="calculate_fees",
        name="Calculate Fees",
        action=calculate_fees,
        description="Calculate licensing fees"
    )
    
    step_final_approval = ApprovalStep(
        step_id="final_approval",
        name="Final License Approval",
        description="Final approval by department head",
        approvers=["department_head"],
        approval_type="all"
    )
    
    step_process_final_approval = ActionStep(
        step_id="process_final_approval",
        name="Process Final Approval",
        action=lambda inputs, context: {"final_approval_decision": context.get("approval_status", "approved")},
        description="Process final approval decision"
    )
    
    step_final_approval_check = ConditionalStep(
        step_id="final_approval_check",
        name="Final Approval Decision",
        description="Check final approval decision"
    )
    
    step_process_payment = ActionStep(
        step_id="process_payment",
        name="Process Payment",
        action=process_payment,
        description="Process license fee payment"
    )
    
    step_payment_check = ConditionalStep(
        step_id="payment_verification",
        name="Payment Verification",
        description="Verify payment completion"
    )
    
    step_issue_license = ActionStep(
        step_id="issue_license",
        name="Issue License",
        action=issue_license,
        description="Issue the business license"
    )
    
    step_send_rejection = ActionStep(
        step_id="send_rejection_notice",
        name="Send Rejection Notice",
        action=send_rejection_notice,
        description="Send rejection notice to applicant"
    )
    
    # Terminal steps
    terminal_success = TerminalStep(
        step_id="license_issued",
        name="License Issued Successfully",
        terminal_status="SUCCESS",
        description="Business license issued successfully"
    )
    
    terminal_document_failure = TerminalStep(
        step_id="document_verification_failed",
        name="Document Verification Failed",
        terminal_status="FAILURE",
        description="Required documents could not be verified"
    )
    
    terminal_initial_rejection = TerminalStep(
        step_id="initial_application_rejected",
        name="Initial Application Rejected",
        terminal_status="REJECTED",
        description="Application rejected at initial review"
    )
    
    terminal_inspection_failure = TerminalStep(
        step_id="inspection_failed",
        name="Inspection Failed",
        terminal_status="FAILURE",
        description="Business premises failed inspection"
    )
    
    terminal_final_rejection = TerminalStep(
        step_id="final_approval_rejected",
        name="Final Approval Rejected",
        terminal_status="REJECTED",
        description="Application rejected at final approval"
    )
    
    terminal_payment_failure = TerminalStep(
        step_id="payment_failed",
        name="Payment Processing Failed",
        terminal_status="FAILURE",
        description="Could not process license fee payment"
    )
    
    # Define workflow flow
    
    # Main application flow
    step_submit >> step_initial_review >> step_verify_docs >> step_doc_check
    
    # Document verification paths
    step_doc_check.when(documents_verified) >> step_initial_approval
    step_doc_check.when(lambda i, c: not documents_verified(i, c)) >> terminal_document_failure
    
    # Initial approval flow
    step_initial_approval >> step_process_initial_approval >> step_approval_check
    step_approval_check.when(initial_review_approved) >> step_inspection_check
    step_approval_check.when(initial_review_rejected) >> step_send_rejection >> terminal_initial_rejection
    
    # Inspection flow
    step_inspection_check.when(requires_inspection) >> step_schedule_inspection >> step_conduct_inspection >> step_inspection_result_check
    step_inspection_check.when(lambda i, c: not requires_inspection(i, c)) >> step_calculate_fees
    
    # Inspection results
    step_inspection_result_check.when(inspection_passed) >> step_calculate_fees
    step_inspection_result_check.when(lambda i, c: not inspection_passed(i, c)) >> terminal_inspection_failure
    
    # Final approval and payment
    step_calculate_fees >> step_final_approval >> step_process_final_approval >> step_final_approval_check
    step_final_approval_check.when(final_approval_granted) >> step_process_payment >> step_payment_check
    step_final_approval_check.when(final_approval_denied) >> step_send_rejection >> terminal_final_rejection
    
    # Payment and license issuance
    step_payment_check.when(payment_completed) >> step_issue_license >> terminal_success
    step_payment_check.when(lambda i, c: not payment_completed(i, c)) >> terminal_payment_failure
    
    # Build and validate
    workflow.build_graph()
    workflow.validate()
    
    return workflow


# Example usage
if __name__ == "__main__":
    workflow = create_business_license_workflow()
    print(workflow.to_mermaid())