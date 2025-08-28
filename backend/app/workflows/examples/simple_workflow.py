"""
Example workflow showing the new DAG syntax with self-contained operators.
This demonstrates how multiple citizens can run the same workflow simultaneously.
"""
from datetime import datetime

from ..dag import DAG
from ..operators.python import PythonOperator
from ..operators.user_input import UserInputOperator
from ..operators.approval import ApprovalOperator
from ..operators.external_api import ExternalAPIOperator


def validate_user_data(context):
    """Example function to validate user data - completely agnostic"""
    user_data = context.get("user_data", {})
    
    # Simple validation logic
    validation_result = {
        "valid": True,
        "errors": []
    }
    
    if not user_data.get("nombre"):
        validation_result["valid"] = False
        validation_result["errors"].append("Nombre requerido")
    
    if not user_data.get("email"):
        validation_result["valid"] = False 
        validation_result["errors"].append("Email requerido")
    
    return {"validation": validation_result}


def generate_certificate(context):
    """Generate certificate based on all collected data - agnostic function"""
    user_data = context.get("user_data", {})
    validation = context.get("validation", {})
    approval = context.get("approval_status", {})
    
    # Only generate if everything is valid and approved
    if validation.get("valid") and approval.get("approved"):
        certificate = {
            "certificate_id": f"CERT-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "issued_to": user_data.get("nombre"),
            "issued_date": datetime.now().isoformat(),
            "status": "active"
        }
        return {"certificate": certificate}
    else:
        return {"certificate": None, "error": "No se puede generar certificado"}


def create_simple_workflow() -> DAG:
    """
    Creates a simple workflow that can be instantiated by multiple users.
    Each user gets their own instance with isolated context.
    """
    
    with DAG(
        dag_id="simple_certificate_workflow",
        description="Workflow simple para generar certificados",
        start_date=datetime(2024, 1, 1)
    ) as dag:
        
        # Step 1: Collect user data (self-contained, doesn't know what comes next)
        collect_data = UserInputOperator(
            task_id="collect_user_data",
            form_config={
                "title": "Datos Personales",
                "fields": [
                    {"name": "nombre", "type": "text", "required": True},
                    {"name": "email", "type": "email", "required": True},
                    {"name": "telefono", "type": "tel", "required": False}
                ]
            },
            required_fields=["nombre", "email"]
        )
        
        # Step 2: Validate the collected data (agnostic function)
        validate_data = PythonOperator(
            task_id="validate_data",
            python_callable=validate_user_data
        )
        
        # Step 3: Mock external verification (for demo purposes)
        verify_external = PythonOperator(
            task_id="verify_external_system",
            python_callable=lambda context: {
                "api_response": {
                    "verified": True,
                    "verification_id": f"VERIFY-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                    "status": "approved"
                },
                "api_status_code": 200,
                "api_endpoint": "https://api.example.com/verify/mock",
                "api_timestamp": datetime.now().isoformat()
            }
        )
        
        # Step 4: Human approval (self-contained, uses context to show what to review)
        approval = ApprovalOperator(
            task_id="human_approval",
            approver_role="validator",
            approval_message="Revisar datos del usuario para generar certificado",
            context_keys_to_review=["user_data", "validation", "api_response"],
            timeout_hours=24
        )
        
        # Step 5: Generate certificate (uses all accumulated context)
        generate_cert = PythonOperator(
            task_id="generate_certificate",
            python_callable=generate_certificate
        )
        
        # Define the flow using >> operator (just like Airflow)
        collect_data >> validate_data >> verify_external >> approval >> generate_cert
    
    return dag


def create_parallel_workflow() -> DAG:
    """
    Example of a workflow with parallel steps.
    Shows how the >> operator works with lists.
    """
    
    with DAG(
        dag_id="parallel_validation_workflow", 
        description="Workflow con validaciones paralelas"
    ) as dag:
        
        # Collect initial data
        collect_data = UserInputOperator(
            task_id="collect_data",
            form_config={"title": "Datos", "fields": [{"name": "documento", "type": "text"}]}
        )
        
        # Parallel validations (each operator is self-contained)
        validate_format = PythonOperator(
            task_id="validate_format",
            python_callable=lambda context: {"format_valid": True}
        )
        
        validate_checksum = PythonOperator(
            task_id="validate_checksum", 
            python_callable=lambda context: {"checksum_valid": True}
        )
        
        verify_registry = ExternalAPIOperator(
            task_id="verify_registry",
            endpoint="https://registry.example.com/check",
            method="POST",
            context_to_payload={"documento": "document_number"}
        )
        
        # Final approval that waits for all parallel tasks
        final_approval = ApprovalOperator(
            task_id="final_approval",
            approver_role="supervisor"
        )
        
        # Define parallel flow: one to many, then many to one
        collect_data >> [validate_format, validate_checksum, verify_registry] >> final_approval
    
    return dag


# Factory function to get available workflows
def get_available_workflows():
    """Get all available workflow definitions"""
    # Test workflows disabled - only PUENTE workflows via plugin system
    return {
        # "simple_certificate": create_simple_workflow(),
        # "parallel_validation": create_parallel_workflow(),
        # "test_entity": create_test_entity_workflow()
    }