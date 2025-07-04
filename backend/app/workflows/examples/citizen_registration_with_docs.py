"""
Enhanced citizen registration workflow with document management.
Demonstrates document upload, verification, reuse, and generation.
"""

import re
from datetime import datetime, date
from typing import Dict, Any

from ..base import ActionStep, ConditionalStep, ApprovalStep, IntegrationStep, TerminalStep, ValidationResult
from ..steps.document_steps import (
    DocumentUploadStep, DocumentVerificationStep, DocumentExistenceCheckStep, 
    DocumentGenerationStep, DocumentSigningStep
)
from ..workflow import Workflow
from ...models.document import DocumentType


# Enhanced validation functions with document support
def validate_citizen_documents(inputs: Dict[str, Any]) -> ValidationResult:
    """Validate that citizen has provided required documents"""
    errors = []
    
    # Check if document existence check was performed
    if "found_documents" not in inputs:
        errors.append("Document existence check not completed")
        return ValidationResult(is_valid=False, errors=errors)
    
    found_documents = inputs["found_documents"]
    missing_documents = inputs.get("missing_documents", [])
    
    # National ID is required for registration
    if DocumentType.NATIONAL_ID.value not in found_documents:
        if DocumentType.NATIONAL_ID.value in missing_documents:
            errors.append("National ID document is required but not found")
    
    # For minors, birth certificate is also required
    age = inputs.get("calculated_age", 0)
    if age < 18 and DocumentType.BIRTH_CERTIFICATE.value not in found_documents:
        if DocumentType.BIRTH_CERTIFICATE.value in missing_documents:
            errors.append("Birth certificate is required for minor registration")
    
    return ValidationResult(is_valid=len(errors) == 0, errors=errors)


def validate_email(inputs: Dict[str, Any]) -> ValidationResult:
    """Validate email format"""
    email = inputs.get("email", "")
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    
    if not re.match(pattern, email):
        return ValidationResult(is_valid=False, errors=["Invalid email format"])
    return ValidationResult(is_valid=True)


def validate_age(inputs: Dict[str, Any]) -> ValidationResult:
    """Validate age from birth date"""
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
def check_document_requirements(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Check what documents are required for registration"""
    age = context.get("calculated_age", 18)
    
    required_docs = [DocumentType.NATIONAL_ID]
    if age < 18:
        required_docs.append(DocumentType.BIRTH_CERTIFICATE)
    
    return {
        "required_document_types": [doc.value for doc in required_docs],
        "age_based_requirements": age < 18,
        "total_required": len(required_docs)
    }


def validate_identity_from_document(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Validate citizen identity using uploaded documents"""
    # Get the verified national ID document
    found_documents = inputs.get("found_documents", {})
    national_id_info = found_documents.get(DocumentType.NATIONAL_ID.value)
    
    if national_id_info:
        # In real implementation, extract data from the verified document
        return {
            "identity_verified": True,
            "verification_method": "document_based",
            "document_id": national_id_info["document_id"],
            "confidence_score": national_id_info.get("relevance_score", 0.9),
            "extracted_data": {
                "id_number": "123456789",  # Would be extracted from document
                "full_name": f"{inputs.get('first_name', '')} {inputs.get('last_name', '')}",
                "date_of_birth": inputs.get("birth_date")
            }
        }
    else:
        return {
            "identity_verified": False,
            "verification_method": "document_missing",
            "error": "National ID document not found or not verified"
        }


def check_duplicates(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Check if citizen already exists in the system"""
    # Enhanced duplicate check using document data
    email = inputs.get("email")
    id_number = context.get("extracted_data", {}).get("id_number")
    
    return {
        "is_duplicate": False,  # Simulated
        "checked_fields": ["email", "id_number", "document_fingerprint"],
        "timestamp": datetime.utcnow().isoformat(),
        "method": "enhanced_document_based"
    }


def create_account(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Create citizen account with document references"""
    account_id = f"CIT-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    
    # Reference the verified documents
    found_documents = inputs.get("found_documents", {})
    linked_documents = []
    
    for doc_type, doc_info in found_documents.items():
        linked_documents.append({
            "type": doc_type,
            "document_id": doc_info["document_id"],
            "purpose": "identity_verification"
        })
    
    return {
        "account_id": account_id,
        "account_status": "active",
        "created_at": datetime.utcnow().isoformat(),
        "linked_documents": linked_documents,
        "document_folder_created": True
    }


def prepare_certificate_data(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Prepare data for registration certificate generation"""
    return {
        "citizen_name": f"{inputs.get('first_name', '')} {inputs.get('last_name', '')}",
        "account_id": context.get("account_id"),
        "registration_date": datetime.utcnow().strftime("%Y-%m-%d"),
        "age": context.get("calculated_age"),
        "email": inputs.get("email"),
        "verification_method": context.get("verification_method"),
        "issuing_authority": "CivicStream Registration Office"
    }


def send_welcome_email_with_docs(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Send welcome email with document attachments"""
    certificate_id = context.get("generated_document_id")
    
    return {
        "email_sent": True,
        "template": "welcome_with_certificate",
        "recipient": inputs.get("email"),
        "attachments": [certificate_id] if certificate_id else [],
        "sent_at": datetime.utcnow().isoformat()
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


def documents_verified(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if all required documents are verified"""
    found_documents = inputs.get("found_documents", {})
    return len(found_documents) > 0 and DocumentType.NATIONAL_ID.value in found_documents


def documents_missing(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if required documents are missing"""
    return not documents_verified(inputs, context)


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


# Create the enhanced workflow
def create_citizen_registration_with_documents_workflow() -> Workflow:
    """Create citizen registration workflow with document management"""
    
    # Initialize workflow
    workflow = Workflow(
        workflow_id="citizen_registration_with_docs_v1",
        name="Citizen Registration with Document Management",
        description="Complete citizen registration with document upload, verification, and certificate generation"
    )
    
    # Document-related steps
    step_check_existing_docs = DocumentExistenceCheckStep(
        step_id="check_existing_documents",
        name="Check Existing Documents",
        required_document_types=[DocumentType.NATIONAL_ID, DocumentType.BIRTH_CERTIFICATE],
        require_verified=True,
        description="Check if citizen already has verified documents"
    )
    
    step_documents_decision = ConditionalStep(
        step_id="documents_decision",
        name="Documents Available Decision",
        description="Route based on document availability"
    )
    
    
    # Core workflow steps with document integration
    step_document_requirements = ActionStep(
        step_id="document_requirements",
        name="Check Document Requirements",
        action=check_document_requirements,
        description="Determine required documents based on age"
    )
    
    step_validate_identity = ActionStep(
        step_id="validate_identity",
        name="Validate Identity from Documents",
        action=validate_identity_from_document,
        description="Verify citizen identity using documents",
        required_inputs=["first_name", "last_name", "found_documents"]
    ).add_validation(validate_citizen_documents)
    
    step_identity_check = ConditionalStep(
        step_id="identity_check",
        name="Identity Verification Check",
        description="Check if identity was verified from documents"
    )
    
    step_check_duplicates = ActionStep(
        step_id="check_duplicates",
        name="Check Duplicates with Document Data",
        action=check_duplicates,
        description="Check if citizen already exists using enhanced document matching",
        required_inputs=["email"]
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
    
    # Approval steps
    step_adult_approval = ApprovalStep(
        step_id="adult_approval",
        name="Adult Registration Approval",
        description="Approve adult citizen registration",
        approvers=["registration_officer", "supervisor"],
        approval_type="any"
    )
    
    step_process_approval = ActionStep(
        step_id="process_approval",
        name="Process Approval Decision",
        action=lambda inputs, context: {
            "approval_processed": True,
            "decision": context.get("approval_status", "approved"),
            "processed_at": datetime.utcnow().isoformat()
        },
        description="Process the approval decision"
    )
    
    step_approval_check = ConditionalStep(
        step_id="approval_check",
        name="Approval Decision Check",
        description="Check approval status"
    )
    
    # Account creation with document linking
    step_create_account = ActionStep(
        step_id="create_account",
        name="Create Account with Document Links",
        action=create_account,
        description="Create citizen account and link verified documents"
    )
    
    # Document generation steps
    step_prepare_certificate = ActionStep(
        step_id="prepare_certificate_data",
        name="Prepare Certificate Data",
        action=prepare_certificate_data,
        description="Prepare data for registration certificate"
    )
    
    step_generate_certificate = DocumentGenerationStep(
        step_id="generate_certificate",
        name="Generate Registration Certificate",
        template_id="registration_certificate",
        output_document_type=DocumentType.CERTIFICATE,
        description="Generate official registration certificate"
    )
    
    step_sign_certificate = DocumentSigningStep(
        step_id="sign_certificate",
        name="Sign Registration Certificate",
        required_signers=["registration_officer"],
        signature_type="digital",
        description="Digitally sign the registration certificate"
    )
    
    # Communication steps
    step_send_welcome = ActionStep(
        step_id="send_welcome_with_docs",
        name="Send Welcome Email with Documents",
        action=send_welcome_email_with_docs,
        description="Send welcome email with certificate attachment"
    )
    
    step_guardian_notification = ActionStep(
        step_id="guardian_notification",
        name="Notify Guardian with Documents",
        action=lambda inputs, context: {
            "notification_sent": True,
            "template": "minor_registration_with_docs",
            "guardian_email": inputs.get("guardian_email"),
            "documents_attached": True,
            "sent_at": datetime.utcnow().isoformat()
        },
        description="Send notification to guardian with documents",
        required_inputs=["guardian_email", "guardian_name"]
    )
    
    # Blockchain integration
    step_blockchain_record = IntegrationStep(
        step_id="blockchain_record",
        name="Record Registration on Blockchain",
        service_name="blockchain_service",
        endpoint="https://api.blockchain.example/record",
        description="Immortalize registration and documents on blockchain"
    )
    
    # Terminal steps
    terminal_success = TerminalStep(
        step_id="registration_success",
        name="Registration Successful with Documents",
        terminal_status="SUCCESS",
        description="Citizen registration completed with all documents processed"
    )
    
    terminal_docs_missing = TerminalStep(
        step_id="documents_missing",
        name="Required Documents Missing",
        terminal_status="PENDING",
        description="Registration pending - required documents not uploaded"
    )
    
    terminal_identity_failed = TerminalStep(
        step_id="identity_failed",
        name="Identity Verification Failed",
        terminal_status="FAILURE",
        description="Could not verify citizen identity from documents"
    )
    
    terminal_duplicate = TerminalStep(
        step_id="duplicate_found",
        name="Duplicate Registration Found",
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
    
    # Define enhanced flow
    
    # Start with document requirements check
    step_document_requirements >> step_check_existing_docs
    
    # Document availability routing
    step_check_existing_docs >> step_documents_decision
    step_documents_decision.when(documents_verified) >> step_validate_identity
    step_documents_decision.when(documents_missing) >> terminal_docs_missing
    
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
    
    # Document generation and completion path
    step_create_account >> step_prepare_certificate >> step_generate_certificate
    step_generate_certificate >> step_sign_certificate >> step_send_welcome
    step_send_welcome >> step_blockchain_record >> terminal_success
    
    # Add all steps to workflow
    workflow.add_step(step_document_requirements)
    workflow.add_step(step_check_existing_docs)
    workflow.add_step(step_documents_decision)
    workflow.add_step(step_validate_identity)
    workflow.add_step(step_identity_check)
    workflow.add_step(step_check_duplicates)
    workflow.add_step(step_duplicate_check)
    workflow.add_step(step_age_check)
    workflow.add_step(step_adult_approval)
    workflow.add_step(step_process_approval)
    workflow.add_step(step_approval_check)
    workflow.add_step(step_create_account)
    workflow.add_step(step_prepare_certificate)
    workflow.add_step(step_generate_certificate)
    workflow.add_step(step_sign_certificate)
    workflow.add_step(step_send_welcome)
    workflow.add_step(step_guardian_notification)
    workflow.add_step(step_blockchain_record)
    workflow.add_step(terminal_success)
    workflow.add_step(terminal_docs_missing)
    workflow.add_step(terminal_identity_failed)
    workflow.add_step(terminal_duplicate)
    workflow.add_step(terminal_approval_rejected)
    workflow.add_step(terminal_minor_pending)
    
    # Set start step
    workflow.set_start(step_document_requirements)
    
    # Build and validate
    workflow.build_graph()
    workflow.validate()
    
    return workflow


# Example usage function
async def example_usage_with_documents():
    """Example of using the document-enhanced citizen registration workflow"""
    from ..workflow import WorkflowInstance
    import uuid
    
    # Create workflow
    workflow = create_citizen_registration_with_documents_workflow()
    
    # Create instance with citizen data
    instance = WorkflowInstance(
        instance_id=str(uuid.uuid4()),
        workflow_id=workflow.workflow_id,
        user_id="user123",
        context={
            "first_name": "John",
            "last_name": "Doe",
            "email": "john.doe@example.com",
            "birth_date": "1990-05-15",
            "guardian_email": "guardian@example.com",
            "guardian_name": "Jane Doe",
            # Document context would be populated by the document existence check step
            "found_documents": {
                "national_id": {
                    "document_id": "doc_20231201120000",
                    "relevance_score": 0.95,
                    "reason": "Recently verified, high confidence"
                }
            },
            "missing_documents": []
        }
    )
    
    # Execute workflow
    completed_instance = await workflow.execute_instance(instance)
    
    # Check final status
    final_step = completed_instance.step_results.get(completed_instance.current_step)
    if final_step:
        terminal_status = final_step.outputs.get("terminal_status", "UNKNOWN")
        print(f"Workflow completed with status: {terminal_status}")
        
        # Show document-related outputs
        if "generated_document_id" in final_step.outputs:
            print(f"Generated certificate: {final_step.outputs['generated_document_id']}")
    
    return completed_instance


if __name__ == "__main__":
    workflow = create_citizen_registration_with_documents_workflow()
    print(workflow.to_mermaid())