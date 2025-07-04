"""
Building Permit workflow with comprehensive document management.
Demonstrates document reuse, verification, generation, and wallet storage.
"""

from datetime import datetime, date, timedelta
from typing import Dict, Any, List
import uuid

from ..base import ActionStep, ConditionalStep, ApprovalStep, IntegrationStep, TerminalStep, ValidationResult
from ..steps.document_steps import (
    DocumentExistenceCheckStep, DocumentUploadStep, DocumentVerificationStep,
    DocumentGenerationStep, DocumentSigningStep
)
from ..workflow import Workflow
from ...models.document import DocumentType, DocumentAccess


# Validation functions
def validate_permit_application(inputs: Dict[str, Any]) -> ValidationResult:
    """Validate permit application data"""
    errors = []
    
    # Check required fields
    required_fields = ["property_address", "construction_type", "estimated_value", "start_date"]
    for field in required_fields:
        if field not in inputs or not inputs[field]:
            errors.append(f"Missing required field: {field}")
    
    # Validate construction type
    valid_types = ["new_construction", "renovation", "addition", "demolition", "commercial", "residential"]
    if inputs.get("construction_type") not in valid_types:
        errors.append(f"Invalid construction type. Must be one of: {', '.join(valid_types)}")
    
    # Validate estimated value
    try:
        value = float(inputs.get("estimated_value", 0))
        if value <= 0:
            errors.append("Estimated value must be greater than 0")
    except (ValueError, TypeError):
        errors.append("Invalid estimated value format")
    
    # Validate start date
    try:
        start_date = datetime.strptime(inputs.get("start_date", ""), "%Y-%m-%d")
        if start_date < datetime.now():
            errors.append("Start date cannot be in the past")
        if start_date > datetime.now() + timedelta(days=365):
            errors.append("Start date cannot be more than 1 year in the future")
    except ValueError:
        errors.append("Invalid start date format. Use YYYY-MM-DD")
    
    return ValidationResult(is_valid=len(errors) == 0, errors=errors)


def validate_property_ownership(inputs: Dict[str, Any]) -> ValidationResult:
    """Validate property ownership information"""
    errors = []
    
    if not inputs.get("property_owner_name"):
        errors.append("Property owner name is required")
    
    if not inputs.get("property_deed_number") and not inputs.get("property_tax_id"):
        errors.append("Either property deed number or tax ID is required")
    
    return ValidationResult(is_valid=len(errors) == 0, errors=errors)


# Action functions
def check_identity_documents(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Check what identity documents are available and needed"""
    # Get document check results from previous step
    found_docs = context.get("found_documents", {})
    missing_docs = context.get("missing_documents", [])
    
    # Analyze what we have and what we need
    has_verified_id = DocumentType.NATIONAL_ID.value in found_docs or DocumentType.PASSPORT.value in found_docs
    needs_proof_of_address = DocumentType.PROOF_OF_ADDRESS.value in missing_docs
    
    return {
        "has_verified_identity": has_verified_id,
        "needs_proof_of_address": needs_proof_of_address,
        "identity_verification_method": "document_based" if has_verified_id else "pending",
        "can_proceed": has_verified_id,
        "missing_critical_documents": not has_verified_id
    }


def calculate_permit_fee(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Calculate permit fee based on construction type and value"""
    construction_type = inputs.get("construction_type")
    estimated_value = float(inputs.get("estimated_value", 0))
    
    # Base fee structure
    base_fees = {
        "new_construction": 500,
        "renovation": 250,
        "addition": 300,
        "demolition": 200,
        "commercial": 750,
        "residential": 400
    }
    
    # Calculate percentage-based fee (0.5% of construction value)
    percentage_fee = estimated_value * 0.005
    
    # Get base fee
    base_fee = base_fees.get(construction_type, 400)
    
    # Total fee is base + percentage, with min and max limits
    total_fee = base_fee + percentage_fee
    total_fee = max(100, min(total_fee, 10000))  # Min $100, Max $10,000
    
    # Add expedited processing option
    expedited_available = total_fee < 5000
    expedited_fee = total_fee * 0.5 if expedited_available else 0
    
    return {
        "base_fee": base_fee,
        "percentage_fee": round(percentage_fee, 2),
        "total_fee": round(total_fee, 2),
        "expedited_available": expedited_available,
        "expedited_fee": round(expedited_fee, 2),
        "fee_breakdown": {
            "base": f"${base_fee}",
            "percentage": f"${percentage_fee:.2f} (0.5% of value)",
            "total": f"${total_fee:.2f}"
        },
        "payment_due_date": (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    }


def verify_property_ownership(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Verify property ownership through external systems"""
    property_address = inputs.get("property_address")
    owner_name = inputs.get("property_owner_name")
    deed_number = inputs.get("property_deed_number")
    tax_id = inputs.get("property_tax_id")
    
    # Simulate property verification (in production, this would call real APIs)
    verification_successful = True
    confidence_score = 0.95
    
    property_details = {
        "verified": verification_successful,
        "confidence_score": confidence_score,
        "property_id": f"PROP-{uuid.uuid4().hex[:8].upper()}",
        "owner_verified": True,
        "owner_name": owner_name,
        "property_address": property_address,
        "zoning": "R1-Residential",
        "lot_size": "7,500 sq ft",
        "existing_permits": [],
        "restrictions": ["Height limit: 35ft", "Setback: 20ft front, 5ft sides"],
        "verification_timestamp": datetime.utcnow().isoformat()
    }
    
    return property_details


def check_zoning_compliance(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Check if construction complies with zoning regulations"""
    construction_type = inputs.get("construction_type")
    property_details = context.get("property_details", {})
    zoning = property_details.get("zoning", "Unknown")
    
    # Simplified zoning rules
    compliant = True
    issues = []
    recommendations = []
    
    if construction_type == "commercial" and "Residential" in zoning:
        compliant = False
        issues.append("Commercial construction not allowed in residential zone")
        recommendations.append("Apply for zoning variance or choose different location")
    
    if construction_type in ["new_construction", "addition"]:
        recommendations.append("Ensure compliance with setback requirements")
        recommendations.append("Maximum height restriction applies")
    
    return {
        "zoning_compliant": compliant,
        "zoning_type": zoning,
        "compliance_issues": issues,
        "recommendations": recommendations,
        "requires_variance": not compliant,
        "special_conditions": property_details.get("restrictions", [])
    }


def schedule_inspection(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Schedule property inspection"""
    # Get available inspection slots
    available_slots = []
    start_date = datetime.now() + timedelta(days=3)  # Inspections start 3 days out
    
    for i in range(10):  # Next 10 business days
        date = start_date + timedelta(days=i)
        if date.weekday() < 5:  # Monday-Friday only
            available_slots.append({
                "date": date.strftime("%Y-%m-%d"),
                "time_slots": ["09:00 AM", "11:00 AM", "02:00 PM", "04:00 PM"]
            })
    
    return {
        "inspection_required": True,
        "inspection_type": "initial_site_inspection",
        "available_slots": available_slots[:5],  # Show first 5 available days
        "inspection_fee": 150.00,
        "estimated_duration": "1-2 hours",
        "inspector_will_check": [
            "Property boundaries",
            "Existing structures",
            "Utility locations",
            "Environmental concerns",
            "Zoning compliance"
        ]
    }


def prepare_permit_data(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Prepare data for permit document generation"""
    permit_number = f"BP-{datetime.now().year}-{uuid.uuid4().hex[:6].upper()}"
    
    return {
        "permit_number": permit_number,
        "issue_date": datetime.now().strftime("%Y-%m-%d"),
        "expiry_date": (datetime.now() + timedelta(days=365)).strftime("%Y-%m-%d"),
        "holder_name": context.get("citizen_name", inputs.get("property_owner_name")),
        "holder_id": context.get("citizen_id"),
        "property_address": inputs.get("property_address"),
        "property_id": context.get("property_id"),
        "construction_type": inputs.get("construction_type"),
        "project_description": inputs.get("project_description", ""),
        "estimated_value": f"${float(inputs.get('estimated_value', 0)):,.2f}",
        "permit_fee_paid": f"${context.get('total_fee', 0):.2f}",
        "zoning": context.get("zoning_type"),
        "special_conditions": context.get("special_conditions", []),
        "inspection_required": True,
        "issuing_authority": "CivicStream Building Department",
        "issuing_officer": "Sarah Johnson",
        "qr_code_data": f"https://civicstream.gov/verify/permit/{permit_number}"
    }


def save_to_citizen_wallet(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Save permit to citizen's document wallet"""
    permit_id = context.get("generated_document_id")
    citizen_id = context.get("citizen_id")
    
    return {
        "saved_to_wallet": True,
        "wallet_category": "permits",
        "document_id": permit_id,
        "access_level": DocumentAccess.WORKFLOW.value,
        "shareable": True,
        "wallet_path": f"/citizens/{citizen_id}/documents/permits/{permit_id}",
        "quick_access_code": uuid.uuid4().hex[:8].upper(),
        "mobile_available": True
    }


def send_permit_notification(inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Send permit approval notification"""
    return {
        "notification_sent": True,
        "channels": ["email", "sms", "mobile_app"],
        "email_sent_to": inputs.get("email"),
        "sms_sent_to": inputs.get("phone"),
        "includes_permit": True,
        "includes_instructions": True,
        "next_steps": [
            "Schedule mandatory inspection",
            "Display permit at construction site",
            "Begin construction within 30 days",
            "Request inspections at milestone stages"
        ],
        "sent_at": datetime.utcnow().isoformat()
    }


# Condition functions
def has_identity_documents(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if citizen has verified identity documents"""
    return context.get("has_verified_identity", False)


def missing_identity_documents(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if identity documents are missing"""
    return not context.get("has_verified_identity", True)


def property_verified(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if property ownership is verified"""
    return context.get("verified", False) and context.get("owner_verified", False)


def property_not_verified(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if property verification failed"""
    return not context.get("verified", True)


def zoning_compliant(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if construction is zoning compliant"""
    return context.get("zoning_compliant", False)


def zoning_not_compliant(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if zoning compliance failed"""
    return not context.get("zoning_compliant", True)


def payment_completed(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if permit fee payment is completed"""
    return context.get("payment_status") == "completed"


def payment_pending(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if payment is still pending"""
    return context.get("payment_status") != "completed"


def approved(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if permit is approved"""
    return context.get("approval_decision") == "approved"


def rejected(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if permit is rejected"""
    return context.get("approval_decision") == "rejected"


# Create the workflow
def create_building_permit_workflow() -> Workflow:
    """Create building permit workflow with document management"""
    
    # Initialize workflow
    workflow = Workflow(
        workflow_id="building_permit_v1",
        name="Building Permit Application",
        description="Apply for building permit with automated document verification and digital permit issuance"
    )
    
    # Document checking steps
    step_check_identity = DocumentExistenceCheckStep(
        step_id="check_identity_documents",
        name="Check Identity Documents",
        required_document_types=[DocumentType.NATIONAL_ID, DocumentType.PASSPORT, DocumentType.PROOF_OF_ADDRESS],
        require_verified=True,
        description="Check if citizen has verified identity documents"
    )
    
    step_analyze_documents = ActionStep(
        step_id="analyze_identity_documents",
        name="Analyze Document Requirements",
        action=check_identity_documents,
        description="Determine what documents are needed"
    )
    
    step_identity_decision = ConditionalStep(
        step_id="identity_document_decision",
        name="Identity Documents Decision",
        description="Route based on document availability"
    )
    
    # Document upload step (if needed)
    step_upload_identity = DocumentUploadStep(
        step_id="upload_identity_document",
        name="Upload Identity Document",
        required_document_type=DocumentType.NATIONAL_ID,
        description="Upload missing identity document"
    )
    
    step_verify_identity = DocumentVerificationStep(
        step_id="verify_identity_document",
        name="Verify Identity Document",
        verifier_roles=["permit_clerk", "administrator"],
        auto_verify_threshold=0.90,
        description="Verify uploaded identity document"
    )
    
    # Application validation
    step_validate_application = ActionStep(
        step_id="validate_application",
        name="Validate Permit Application",
        action=lambda inputs, context: {
            "validation_passed": True,
            "application_id": f"APP-{uuid.uuid4().hex[:8].upper()}",
            "submitted_at": datetime.utcnow().isoformat()
        },
        description="Validate permit application data",
        required_inputs=["property_address", "construction_type", "estimated_value", "start_date"]
    ).add_validation(validate_permit_application)
    
    # Property verification
    step_verify_property = ActionStep(
        step_id="verify_property_ownership",
        name="Verify Property Ownership",
        action=verify_property_ownership,
        description="Verify property ownership through government databases",
        required_inputs=["property_owner_name"]
    ).add_validation(validate_property_ownership)
    
    step_property_check = ConditionalStep(
        step_id="property_verification_check",
        name="Property Verification Check",
        description="Check property verification result"
    )
    
    # Zoning compliance
    step_check_zoning = ActionStep(
        step_id="check_zoning_compliance",
        name="Check Zoning Compliance",
        action=check_zoning_compliance,
        description="Verify construction complies with zoning laws"
    )
    
    step_zoning_decision = ConditionalStep(
        step_id="zoning_compliance_decision",
        name="Zoning Compliance Decision",
        description="Route based on zoning compliance"
    )
    
    # Fee calculation and payment
    step_calculate_fee = ActionStep(
        step_id="calculate_permit_fee",
        name="Calculate Permit Fee",
        action=calculate_permit_fee,
        description="Calculate permit fee based on construction type and value"
    )
    
    step_payment_gateway = IntegrationStep(
        step_id="process_payment",
        name="Process Permit Fee Payment",
        service_name="payment_gateway",
        endpoint="https://api.civicstream.gov/payments/process",
        description="Process permit fee payment through payment gateway"
    )
    
    step_payment_check = ConditionalStep(
        step_id="payment_verification",
        name="Payment Verification",
        description="Verify payment was successful"
    )
    
    # Inspection scheduling
    step_schedule_inspection = ActionStep(
        step_id="schedule_inspection",
        name="Schedule Property Inspection",
        action=schedule_inspection,
        description="Schedule mandatory property inspection"
    )
    
    # Approval process
    step_technical_review = ApprovalStep(
        step_id="technical_review",
        name="Technical Review",
        approvers=["building_inspector", "city_planner"],
        approval_type="all",
        description="Technical review by building department"
    )
    
    step_final_approval = ApprovalStep(
        step_id="final_approval",
        name="Final Permit Approval",
        approvers=["permit_supervisor"],
        approval_type="any",
        description="Final approval by permit supervisor"
    )
    
    step_approval_decision = ConditionalStep(
        step_id="approval_decision",
        name="Approval Decision",
        description="Check final approval decision"
    )
    
    # Permit generation
    step_prepare_permit = ActionStep(
        step_id="prepare_permit_data",
        name="Prepare Permit Data",
        action=prepare_permit_data,
        description="Prepare data for permit generation"
    )
    
    step_generate_permit = DocumentGenerationStep(
        step_id="generate_permit",
        name="Generate Building Permit",
        template_id="building_permit_template",
        output_document_type=DocumentType.PERMIT,
        description="Generate official building permit document"
    )
    
    step_sign_permit = DocumentSigningStep(
        step_id="sign_permit",
        name="Sign Building Permit",
        required_signers=["permit_supervisor", "building_commissioner"],
        signature_type="digital",
        description="Digitally sign the building permit"
    )
    
    # Save to wallet
    step_save_wallet = ActionStep(
        step_id="save_to_wallet",
        name="Save Permit to Citizen Wallet",
        action=save_to_citizen_wallet,
        description="Save approved permit to citizen's document wallet"
    )
    
    # Notification
    step_send_notification = ActionStep(
        step_id="send_notification",
        name="Send Permit Notification",
        action=send_permit_notification,
        description="Send permit approval notification with document"
    )
    
    # Blockchain recording
    step_blockchain = IntegrationStep(
        step_id="blockchain_record",
        name="Record on Blockchain",
        service_name="blockchain_service",
        endpoint="https://api.blockchain.civicstream.gov/record",
        description="Record permit issuance on blockchain"
    )
    
    # Terminal steps
    terminal_success = TerminalStep(
        step_id="permit_issued",
        name="Permit Successfully Issued",
        terminal_status="SUCCESS",
        description="Building permit issued and saved to citizen wallet"
    )
    
    terminal_missing_docs = TerminalStep(
        step_id="missing_documents",
        name="Missing Required Documents",
        terminal_status="PENDING",
        description="Required identity documents not provided"
    )
    
    terminal_property_failed = TerminalStep(
        step_id="property_verification_failed",
        name="Property Verification Failed",
        terminal_status="FAILURE",
        description="Could not verify property ownership"
    )
    
    terminal_zoning_failed = TerminalStep(
        step_id="zoning_non_compliant",
        name="Zoning Non-Compliant",
        terminal_status="REJECTED",
        description="Construction does not comply with zoning regulations"
    )
    
    terminal_payment_failed = TerminalStep(
        step_id="payment_failed",
        name="Payment Failed",
        terminal_status="PENDING",
        description="Permit fee payment was not completed"
    )
    
    terminal_rejected = TerminalStep(
        step_id="permit_rejected",
        name="Permit Rejected",
        terminal_status="REJECTED",
        description="Building permit application was rejected"
    )
    
    # Define workflow flow
    
    # Start with identity check
    step_check_identity >> step_analyze_documents >> step_identity_decision
    
    # Identity document routing
    step_identity_decision.when(has_identity_documents) >> step_validate_application
    step_identity_decision.when(missing_identity_documents) >> step_upload_identity
    step_upload_identity >> step_verify_identity >> step_validate_application
    
    # Property verification flow
    step_validate_application >> step_verify_property >> step_property_check
    step_property_check.when(property_verified) >> step_check_zoning
    step_property_check.when(property_not_verified) >> terminal_property_failed
    
    # Zoning check flow
    step_check_zoning >> step_zoning_decision
    step_zoning_decision.when(zoning_compliant) >> step_calculate_fee
    step_zoning_decision.when(zoning_not_compliant) >> terminal_zoning_failed
    
    # Payment flow
    step_calculate_fee >> step_payment_gateway >> step_payment_check
    step_payment_check.when(payment_completed) >> step_schedule_inspection
    step_payment_check.when(payment_pending) >> terminal_payment_failed
    
    # Approval flow
    step_schedule_inspection >> step_technical_review >> step_final_approval >> step_approval_decision
    step_approval_decision.when(approved) >> step_prepare_permit
    step_approval_decision.when(rejected) >> terminal_rejected
    
    # Permit generation and delivery
    step_prepare_permit >> step_generate_permit >> step_sign_permit
    step_sign_permit >> step_save_wallet >> step_send_notification
    step_send_notification >> step_blockchain >> terminal_success
    
    # Add all steps to workflow
    workflow.add_step(step_check_identity)
    workflow.add_step(step_analyze_documents)
    workflow.add_step(step_identity_decision)
    workflow.add_step(step_upload_identity)
    workflow.add_step(step_verify_identity)
    workflow.add_step(step_validate_application)
    workflow.add_step(step_verify_property)
    workflow.add_step(step_property_check)
    workflow.add_step(step_check_zoning)
    workflow.add_step(step_zoning_decision)
    workflow.add_step(step_calculate_fee)
    workflow.add_step(step_payment_gateway)
    workflow.add_step(step_payment_check)
    workflow.add_step(step_schedule_inspection)
    workflow.add_step(step_technical_review)
    workflow.add_step(step_final_approval)
    workflow.add_step(step_approval_decision)
    workflow.add_step(step_prepare_permit)
    workflow.add_step(step_generate_permit)
    workflow.add_step(step_sign_permit)
    workflow.add_step(step_save_wallet)
    workflow.add_step(step_send_notification)
    workflow.add_step(step_blockchain)
    workflow.add_step(terminal_success)
    workflow.add_step(terminal_property_failed)
    workflow.add_step(terminal_zoning_failed)
    workflow.add_step(terminal_payment_failed)
    workflow.add_step(terminal_rejected)
    
    # Set start step
    workflow.set_start(step_check_identity)
    
    # Build and validate
    workflow.build_graph()
    workflow.validate()
    
    return workflow


# Example usage
async def example_permit_application():
    """Example of building permit application"""
    from ..workflow import WorkflowInstance
    
    # Create workflow
    workflow = create_building_permit_workflow()
    
    # Create instance with application data
    instance = WorkflowInstance(
        instance_id=str(uuid.uuid4()),
        workflow_id=workflow.workflow_id,
        user_id="citizen123",
        context={
            "citizen_id": "citizen123",
            "citizen_name": "John Smith",
            "email": "john.smith@email.com",
            "phone": "+1234567890",
            
            # Permit application data
            "property_address": "123 Main St, Cityville, ST 12345",
            "property_owner_name": "John Smith",
            "property_deed_number": "DEED-2023-4567",
            "property_tax_id": "TAX-123-456-789",
            
            "construction_type": "renovation",
            "estimated_value": "75000",
            "start_date": "2024-03-01",
            "project_description": "Kitchen and bathroom renovation with structural modifications",
            
            # Assuming identity documents exist
            "found_documents": {
                "national_id": {
                    "document_id": "doc_20231201120000",
                    "relevance_score": 0.95
                }
            },
            "missing_documents": ["proof_of_address"]
        }
    )
    
    # Execute workflow
    completed_instance = await workflow.execute_instance(instance)
    
    # Check results
    final_step = completed_instance.step_results.get(completed_instance.current_step)
    if final_step:
        terminal_status = final_step.outputs.get("terminal_status", "UNKNOWN")
        print(f"Permit application completed with status: {terminal_status}")
        
        if terminal_status == "SUCCESS":
            print(f"Permit number: {final_step.outputs.get('permit_number')}")
            print(f"Saved to wallet: {final_step.outputs.get('wallet_path')}")
    
    return completed_instance


if __name__ == "__main__":
    workflow = create_building_permit_workflow()
    print(f"Building Permit Workflow: {workflow.workflow_id}")
    print(f"Total steps: {len(workflow.steps)}")
    print("\nWorkflow diagram:")
    print(workflow.to_mermaid())