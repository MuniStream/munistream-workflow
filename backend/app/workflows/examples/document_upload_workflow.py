"""
Document Upload Workflow Example
This workflow demonstrates citizen document upload with S3 storage.
"""

from typing import Dict, Any
from app.workflows.dag import DAG
from app.workflows.operators import (
    PythonOperator,
    UserInputOperator,
    S3UploadOperator,
    ApprovalOperator
)
from app.workflows.operators.document_operators import DocumentCreationOperator


def validate_documents(context: Dict[str, Any]) -> Dict[str, Any]:
    """Validate uploaded documents"""
    print("📋 Validating uploaded documents")

    # Get upload results from S3
    s3_results = context.get("s3_upload_results", {})
    uploaded_files = s3_results.get("uploaded_files", [])

    if not uploaded_files:
        return {
            "validation_status": "failed",
            "error": "No files were uploaded successfully"
        }

    # Perform validation checks
    validation_results = []
    for file in uploaded_files:
        # Check file properties
        result = {
            "filename": file.get("filename"),
            "s3_key": file.get("s3_key"),
            "size": file.get("size"),
            "valid": True,
            "issues": []
        }

        # Example validation rules
        if file.get("size", 0) > 50 * 1024 * 1024:  # 50MB
            result["valid"] = False
            result["issues"].append("File size exceeds 50MB")

        validation_results.append(result)

    # Check if all files are valid
    all_valid = all(r["valid"] for r in validation_results)

    return {
        "validation_status": "success" if all_valid else "partial",
        "validation_results": validation_results,
        "total_files": len(uploaded_files),
        "valid_files": sum(1 for r in validation_results if r["valid"]),
        "s3_urls": s3_results.get("s3_urls", [])
    }


def process_approved_documents(context: Dict[str, Any]) -> Dict[str, Any]:
    """Process documents after approval"""
    print("✅ Processing approved documents")

    # Get S3 URLs and validation results
    s3_urls = context.get("s3_urls", [])
    validation_results = context.get("validation_results", [])

    # Here you could:
    # - Update database records
    # - Send notifications
    # - Trigger downstream processes
    # - Generate certificates

    return {
        "processing_status": "completed",
        "processed_count": len(s3_urls),
        "message": f"Successfully processed {len(s3_urls)} documents"
    }


def create_document_upload_workflow() -> DAG:
    """
    Create a workflow for citizen document uploads with S3 storage.

    Flow:
    1. Collect documents from citizen
    2. Upload to S3
    3. Validate documents
    4. Admin approval
    5. Process approved documents
    6. Create document record
    """

    # Create the DAG
    dag = DAG(
        dag_id="document_upload_workflow",
        default_args={
            "owner": "munistream",
            "retries": 1
        }
    )

    # Step 1: Collect documents from citizen
    collect_documents = UserInputOperator(
        task_id="collect_documents",
        form_fields=[
            {
                "name": "document_type",
                "label": "Document Type",
                "type": "select",
                "required": True,
                "options": ["identity", "proof_of_address", "income_statement", "other"]
            },
            {
                "name": "description",
                "label": "Document Description",
                "type": "text",
                "required": True
            },
            {
                "name": "files",
                "label": "Upload Documents",
                "type": "file",
                "required": True,
                "multiple": True,
                "accept": ".pdf,.jpg,.jpeg,.png,.doc,.docx"
            }
        ],
        instructions="Please upload the required documents for your application.",
        timeout_hours=48
    )

    # Step 2: Upload documents to S3
    upload_to_s3 = S3UploadOperator(
        task_id="upload_to_s3",
        bucket_name=None,  # Uses environment variable S3_BUCKET_NAME
        s3_prefix="citizen-uploads/{instance_id}",
        file_source="files",  # Gets files from user input
        metadata_tags={
            "upload_type": "citizen_document",
            "workflow": "document_upload"
        },
        allowed_extensions=['.pdf', '.jpg', '.jpeg', '.png', '.doc', '.docx'],
        max_file_size=10 * 1024 * 1024  # 10MB
    )

    # Step 3: Validate uploaded documents
    validate_docs = PythonOperator(
        task_id="validate_documents",
        python_callable=validate_documents,
        provide_context=True
    )

    # Step 4: Admin approval
    admin_approval = ApprovalOperator(
        task_id="admin_approval",
        approver_role="admin",
        approval_message="Please review the uploaded documents",
        include_context_fields=["validation_results", "s3_urls"],
        timeout_hours=24
    )

    # Step 5: Process approved documents
    process_docs = PythonOperator(
        task_id="process_documents",
        python_callable=process_approved_documents,
        provide_context=True
    )

    # Step 6: Create document record in system
    create_record = DocumentCreationOperator(
        task_id="create_document_record",
        document_type="citizen_upload",
        document_name="Citizen Document - {document_type}",
        metadata_mapping={
            "s3_urls": "file_urls",
            "document_type": "type",
            "description": "description",
            "validation_results": "validation"
        },
        expiry_days=365,
        issuing_authority="Citizen Portal"
    )

    # Define the workflow
    collect_documents >> upload_to_s3 >> validate_docs >> admin_approval >> process_docs >> create_record

    return dag


# Register the workflow
if __name__ == "__main__":
    workflow = create_document_upload_workflow()
    print(f"Created workflow: {workflow.dag_id}")
    print(f"Tasks: {[task.task_id for task in workflow.tasks.values()]}")