"""
Example workflow for citizen registration process.
This demonstrates various step types and conditional flows.
All paths end with terminal status steps (SUCCESS, FAILURE, REJECTED).
"""

import re
from datetime import datetime, date
from typing import Dict, Any

from ..base import ActionStep, ConditionalStep, ApprovalStep, IntegrationStep, TerminalStep, ValidationResult
from ..workflow import Workflow


# Validation functions
def validate_email(inputs: Dict[str, Any]) -> ValidationResult:
    email = inputs.get("email", "")
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    
    if not re.match(pattern, email):
        return ValidationResult(is_valid=False, errors=["Invalid email format"])
    return ValidationResult(is_valid=True)


def validate_age(inputs: Dict[str, Any]) -> ValidationResult:
    birth_date = inputs.get("birth_date")
    if not birth_date:
        return ValidationResult(is_valid=False, errors=["Birth date is required"])
    
    try:
        birth = datetime.strptime(birth_date, "%Y-%m-%d").date()
        today = date.today()
        age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
        
        if age < 0:
            return ValidationResult(is_valid=False, errors=["Invalid birth date"])
        
        inputs["calculated_age"] = age
        return ValidationResult(is_valid=True)
    except ValueError:
        return ValidationResult(is_valid=False, errors=["Invalid date format. Use YYYY-MM-DD"])


# Step action functions
def validate_identity(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Validate citizen identity documents"""
    # In real implementation, this would verify ID documents
    return {
        "identity_verified": True,
        "verification_method": "document_scan",
        "confidence_score": 0.95
    }


def check_duplicates(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Check if citizen already exists in the system"""
    # In real implementation, this would query the database
    email = inputs.get("email")
    id_number = inputs.get("id_number")
    
    return {
        "is_duplicate": False,
        "checked_fields": ["email", "id_number"],
        "timestamp": datetime.utcnow().isoformat()
    }


def create_account(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Create citizen account"""
    return {
        "account_id": f"CIT-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
        "account_status": "active",
        "created_at": datetime.utcnow().isoformat()
    }


def send_welcome_email(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Send welcome email to new citizen"""
    return {
        "email_sent": True,
        "template": "welcome_adult",
        "recipient": inputs.get("email"),
        "sent_at": datetime.utcnow().isoformat()
    }


def send_guardian_notification(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Send notification to guardian for minor registration"""
    return {
        "notification_sent": True,
        "template": "minor_registration",
        "guardian_email": inputs.get("guardian_email"),
        "sent_at": datetime.utcnow().isoformat()
    }


def handle_approval_result(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Process approval decision"""
    # In real implementation, this would check actual approval status
    approval_status = context.get("approval_status", "approved")
    return {
        "approval_processed": True,
        "decision": approval_status,
        "processed_at": datetime.utcnow().isoformat()
    }


# Condition functions
def is_adult(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if the citizen is 18 or older"""
    age = context.get("calculated_age", 0)
    return age >= 18


def is_minor(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if the citizen is under 18"""
    age = context.get("calculated_age", 0)
    return age < 18


def identity_verified(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if identity was successfully verified"""
    return context.get("identity_verified", False)


def identity_not_verified(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if identity verification failed"""
    return not context.get("identity_verified", True)


def no_duplicates(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if no duplicate accounts were found"""
    return not context.get("is_duplicate", True)


def has_duplicates(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if duplicate accounts were found"""
    return context.get("is_duplicate", False)


def is_approved(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if registration was approved"""
    return context.get("decision", "") == "approved"


def is_rejected(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if registration was rejected"""
    return context.get("decision", "") == "rejected"


# Create the workflow
def create_citizen_registration_workflow() -> Workflow:
    """Create and configure the citizen registration workflow"""
    
    # Initialize workflow
    workflow = Workflow(
        workflow_id="citizen_registration_v1",
        name="Citizen Registration",
        description="Complete workflow for registering new citizens with age-based routing"
    )
    
    # Create steps
    step_validate_identity = ActionStep(
        step_id="validate_identity",
        name="Validate Identity",
        action=validate_identity,
        description="Verify citizen identity documents",
        required_inputs=["first_name", "last_name", "id_number", "id_document"]
    )
    
    step_identity_check = ConditionalStep(
        step_id="identity_check",
        name="Identity Verification Check",
        description="Check if identity was verified"
    )
    
    step_check_duplicates = ActionStep(
        step_id="check_duplicates",
        name="Check Duplicates",
        action=check_duplicates,
        description="Check if citizen already exists",
        required_inputs=["email", "id_number"]
    ).add_validation(validate_email)
    
    step_duplicate_check = ConditionalStep(
        step_id="duplicate_check",
        name="Duplicate Check",
        description="Check if duplicates were found"
    )
    
    step_age_check = ConditionalStep(
        step_id="age_verification",
        name="Age Verification",
        description="Route based on citizen age",
        required_inputs=["birth_date"]
    ).add_validation(validate_age)
    
    step_adult_approval = ApprovalStep(
        step_id="adult_approval",
        name="Adult Registration Approval",
        description="Approve adult citizen registration",
        approvers=["registration_officer", "supervisor"],
        approval_type="any"
    )
    
    step_process_approval = ActionStep(
        step_id="process_approval",
        name="Process Approval",
        action=handle_approval_result,
        description="Process the approval decision"
    )
    
    step_approval_check = ConditionalStep(
        step_id="approval_check",
        name="Approval Decision",
        description="Check approval status"
    )
    
    step_create_account = ActionStep(
        step_id="create_account",
        name="Create Account",
        action=create_account,
        description="Create citizen account in the system"
    )
    
    step_send_welcome = ActionStep(
        step_id="send_welcome",
        name="Send Welcome Email",
        action=send_welcome_email,
        description="Send welcome email to new citizen"
    )
    
    step_guardian_notification = ActionStep(
        step_id="guardian_notification",
        name="Notify Guardian",
        action=send_guardian_notification,
        description="Send notification to guardian for minor registration",
        required_inputs=["guardian_email", "guardian_name"]
    )
    
    step_blockchain_record = IntegrationStep(
        step_id="blockchain_record",
        name="Record on Blockchain",
        service_name="blockchain_service",
        endpoint="https://api.blockchain.example/record",
        description="Immortalize registration on blockchain"
    )
    
    # Terminal steps
    terminal_success = TerminalStep(
        step_id="registration_success",
        name="Registration Successful",
        terminal_status="SUCCESS",
        description="Citizen registration completed successfully"
    )
    
    terminal_identity_failed = TerminalStep(
        step_id="identity_failed",
        name="Identity Verification Failed",
        terminal_status="FAILURE",
        description="Could not verify citizen identity"
    )
    
    terminal_duplicate = TerminalStep(
        step_id="duplicate_found",
        name="Duplicate Registration",
        terminal_status="REJECTED",
        description="Citizen already exists in the system"
    )
    
    terminal_approval_rejected = TerminalStep(
        step_id="approval_rejected",
        name="Registration Rejected",
        terminal_status="REJECTED",
        description="Registration was rejected by approver"
    )
    
    terminal_minor_pending = TerminalStep(
        step_id="minor_pending",
        name="Minor Registration Pending",
        terminal_status="PENDING",
        description="Minor registration requires guardian approval"
    )
    
    # Define flow
    
    # Identity verification path
    step_validate_identity >> step_identity_check
    step_identity_check.when(identity_verified) >> step_check_duplicates
    step_identity_check.when(identity_not_verified) >> terminal_identity_failed
    
    # Duplicate check path
    step_check_duplicates >> step_duplicate_check
    step_duplicate_check.when(no_duplicates) >> step_age_check
    step_duplicate_check.when(has_duplicates) >> terminal_duplicate
    
    # Age-based routing
    # Adult path
    step_age_check.when(is_adult) >> step_adult_approval >> step_process_approval >> step_approval_check
    step_approval_check.when(is_approved) >> step_create_account
    step_approval_check.when(is_rejected) >> terminal_approval_rejected
    
    # Minor path
    step_age_check.when(is_minor) >> step_guardian_notification >> terminal_minor_pending
    
    # Success path
    step_create_account >> step_send_welcome >> step_blockchain_record >> terminal_success
    
    # Add all steps to workflow
    workflow.add_step(step_validate_identity)
    workflow.add_step(step_identity_check)
    workflow.add_step(step_check_duplicates)
    workflow.add_step(step_duplicate_check)
    workflow.add_step(step_age_check)
    workflow.add_step(step_adult_approval)
    workflow.add_step(step_process_approval)
    workflow.add_step(step_approval_check)
    workflow.add_step(step_create_account)
    workflow.add_step(step_send_welcome)
    workflow.add_step(step_guardian_notification)
    workflow.add_step(step_blockchain_record)
    workflow.add_step(terminal_success)
    workflow.add_step(terminal_identity_failed)
    workflow.add_step(terminal_duplicate)
    workflow.add_step(terminal_approval_rejected)
    workflow.add_step(terminal_minor_pending)
    
    # Set start step
    workflow.set_start(step_validate_identity)
    
    # Build and validate
    workflow.build_graph()
    workflow.validate()
    
    return workflow


# Example usage function
async def example_usage():
    """Example of how to use the citizen registration workflow"""
    from ..workflow import WorkflowInstance
    import uuid
    
    # Create workflow
    workflow = create_citizen_registration_workflow()
    
    # Create instance with citizen data
    instance = WorkflowInstance(
        instance_id=str(uuid.uuid4()),
        workflow_id=workflow.workflow_id,
        user_id="user123",
        context={
            "first_name": "John",
            "last_name": "Doe",
            "email": "john.doe@example.com",
            "id_number": "123456789",
            "id_document": "passport",
            "birth_date": "1990-05-15",
            "guardian_email": "guardian@example.com",
            "guardian_name": "Jane Doe"
        }
    )
    
    # Execute workflow
    completed_instance = await workflow.execute_instance(instance)
    
    # Check final status
    final_step = completed_instance.step_results.get(completed_instance.current_step)
    if final_step:
        terminal_status = final_step.outputs.get("terminal_status", "UNKNOWN")
        print(f"Workflow completed with status: {terminal_status}")
    
    return completed_instance


# Generate Mermaid diagram
if __name__ == "__main__":
    workflow = create_citizen_registration_workflow()
    print(workflow.to_mermaid())