"""
Example workflows demonstrating different workflow types and hook registration.
Shows how to create document processing, process, and admin workflows with event-driven architecture.
"""
from datetime import datetime

from ..dag import DAG
from ..operators.python import PythonOperator
from ..operators.user_input import UserInputOperator
from ..operators.approval import ApprovalOperator
from ..operators.document_operators import DocumentProcessingOperator
from ..operators.entity_operators import EntityCreateOperator
from ..hook_registry import (
    register_workflow_hook,
    register_on_completed,
    register_on_failed,
    register_on_entity,
    register_on_approval
)
from ...models.workflow import WorkflowType, HookTriggerType


def create_document_processing_workflow() -> DAG:
    """
    Document processing workflow that analyzes documents and creates entities.
    This workflow emits events and creates entities for other workflows to consume.
    """

    with DAG(
        dag_id="document_analysis_workflow",
        name="Document Analysis and Entity Creation",
        description="Automated document processing that creates property entities",
        workflow_type=WorkflowType.DOCUMENT_PROCESSING,
        entity_outputs=["property_record", "document_analysis"],
        emit_events=True,
        listens_to_events=False
    ) as dag:

        # Step 1: Process uploaded document
        analyze_document = DocumentProcessingOperator(
            task_id="analyze_document",
            analysis_type="property_document",
            extract_fields=["property_address", "owner_name", "property_type", "area_sqm"]
        )

        # Step 2: Validate extracted data
        validate_extraction = PythonOperator(
            task_id="validate_extraction",
            python_callable=lambda context: {
                "validation_status": "passed" if context.get("property_address") else "failed",
                "validation_confidence": 0.95,
                "validation_timestamp": datetime.utcnow().isoformat()
            }
        )

        # Step 3: Create property entity if validation passes
        create_property_entity = EntityCreateOperator(
            task_id="create_property_entity",
            entity_type="property_record",
            entity_data_mapping={
                "address": "property_address",
                "owner": "owner_name",
                "type": "property_type",
                "area": "area_sqm",
                "analysis_confidence": "validation_confidence"
            },
            condition_key="validation_status",
            condition_value="passed"
        )

        # Step 4: Create document analysis record
        create_analysis_record = EntityCreateOperator(
            task_id="create_analysis_record",
            entity_type="document_analysis",
            entity_data_mapping={
                "document_id": "document_id",
                "analysis_type": "analysis_type",
                "status": "validation_status",
                "confidence": "validation_confidence",
                "processed_at": "validation_timestamp"
            }
        )

        # Define flow
        analyze_document >> validate_extraction >> [create_property_entity, create_analysis_record]

    return dag


def create_citizen_process_workflow() -> DAG:
    """
    Citizen process workflow that guides users through property registration.
    This workflow depends on entities created by document processing workflows.
    """

    with DAG(
        dag_id="property_registration_process",
        name="Property Registration Process",
        description="Citizen-facing workflow for property registration",
        workflow_type=WorkflowType.PROCESS,
        emit_events=True,
        listens_to_events=True,
        entity_outputs=["registration_application"]
    ) as dag:

        # Step 1: Collect citizen information
        collect_citizen_data = UserInputOperator(
            task_id="collect_citizen_data",
            form_config={
                "title": "Información del Solicitante",
                "fields": [
                    {"name": "full_name", "type": "text", "required": True},
                    {"name": "identification", "type": "text", "required": True},
                    {"name": "email", "type": "email", "required": True},
                    {"name": "phone", "type": "tel", "required": False}
                ]
            },
            required_fields=["full_name", "identification", "email"]
        )

        # Step 2: Select or verify property (depends on property entities existing)
        verify_property = PythonOperator(
            task_id="verify_property",
            python_callable=lambda context: {
                "property_verified": True,
                "verification_method": "entity_lookup",
                "property_id": context.get("selected_property_id", "PROP-" + str(datetime.now().timestamp()))
            }
        )

        # Step 3: Submit application
        submit_application = PythonOperator(
            task_id="submit_application",
            python_callable=lambda context: {
                "application_id": f"APP-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                "submission_date": datetime.utcnow().isoformat(),
                "status": "submitted"
            }
        )

        # Step 4: Administrative review
        admin_review = ApprovalOperator(
            task_id="admin_review",
            approver_role="property_reviewer",
            approval_message="Review property registration application",
            context_keys_to_review=["full_name", "identification", "property_id", "application_id"],
            timeout_hours=72
        )

        # Step 5: Create final registration entity
        create_registration = EntityCreateOperator(
            task_id="create_registration",
            entity_type="registration_application",
            entity_data_mapping={
                "applicant_name": "full_name",
                "applicant_id": "identification",
                "property_id": "property_id",
                "application_id": "application_id",
                "status": "approval_status",
                "created_at": "submission_date"
            }
        )

        # Define flow
        collect_citizen_data >> verify_property >> submit_application >> admin_review >> create_registration

    return dag


def create_admin_notification_workflow() -> DAG:
    """
    Admin workflow that gets triggered by events from other workflows.
    This workflow handles notifications and follow-up actions.
    """

    with DAG(
        dag_id="admin_notification_workflow",
        name="Administrative Notifications",
        description="Admin workflow for notifications and follow-up actions",
        workflow_type=WorkflowType.ADMIN,
        emit_events=False,
        listens_to_events=True
    ) as dag:

        # Step 1: Process triggering event
        process_event = PythonOperator(
            task_id="process_event",
            python_callable=lambda context: {
                "event_type": context.get("triggering_event", {}).get("event_type"),
                "source_workflow": context.get("triggering_event", {}).get("workflow_id"),
                "notification_priority": "high" if "FAILED" in str(context.get("triggering_event", {}).get("event_type", "")) else "normal"
            }
        )

        # Step 2: Send notifications
        send_notifications = PythonOperator(
            task_id="send_notifications",
            python_callable=lambda context: {
                "notification_sent": True,
                "notification_method": "email",
                "recipients": ["admin@example.com"],
                "sent_at": datetime.utcnow().isoformat()
            }
        )

        # Step 3: Create audit log entry
        create_audit_log = PythonOperator(
            task_id="create_audit_log",
            python_callable=lambda context: {
                "audit_entry_id": f"AUDIT-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                "event_processed": True,
                "processing_time": datetime.utcnow().isoformat()
            }
        )

        # Define flow
        process_event >> [send_notifications, create_audit_log]

    return dag


def create_cleanup_workflow() -> DAG:
    """
    Admin workflow for cleaning up failed processes.
    Gets triggered when other workflows fail.
    """

    with DAG(
        dag_id="cleanup_workflow",
        name="Failed Process Cleanup",
        description="Cleanup workflow for handling failed processes",
        workflow_type=WorkflowType.ADMIN,
        emit_events=True,
        listens_to_events=True
    ) as dag:

        # Step 1: Analyze failure
        analyze_failure = PythonOperator(
            task_id="analyze_failure",
            python_callable=lambda context: {
                "failure_cause": context.get("error_message", "Unknown error"),
                "failed_workflow": context.get("triggering_event", {}).get("workflow_id"),
                "cleanup_required": True,
                "analysis_timestamp": datetime.utcnow().isoformat()
            }
        )

        # Step 2: Perform cleanup
        perform_cleanup = PythonOperator(
            task_id="perform_cleanup",
            python_callable=lambda context: {
                "cleanup_actions": ["remove_temp_files", "reset_user_state", "notify_support"],
                "cleanup_completed": True,
                "cleanup_timestamp": datetime.utcnow().isoformat()
            }
        )

        # Step 3: Create cleanup report
        create_cleanup_report = PythonOperator(
            task_id="create_cleanup_report",
            python_callable=lambda context: {
                "report_id": f"CLEANUP-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                "cleanup_success": context.get("cleanup_completed", False),
                "report_generated": datetime.utcnow().isoformat()
            }
        )

        # Define flow
        analyze_failure >> perform_cleanup >> create_cleanup_report

    return dag


# Register workflow hooks
def register_workflow_hooks():
    """Register hooks for event-driven workflow interactions"""

    # Hook: Admin notification on any workflow completion
    register_on_completed(
        hook_id="notify_on_completion",
        source_workflow_id="*",  # Listen to all workflows
        listener_workflow_id="admin_notification_workflow",
        priority=10,
        description="Send admin notification when any workflow completes"
    )

    # Hook: Admin notification on any workflow failure
    register_on_failed(
        hook_id="notify_on_failure",
        source_workflow_id="*",  # Listen to all workflows
        listener_workflow_id="admin_notification_workflow",
        priority=100,  # High priority for failures
        description="Send admin notification when any workflow fails"
    )

    # Hook: Cleanup on process workflow failures
    register_on_failed(
        hook_id="cleanup_on_process_failure",
        source_workflow_id="property_registration_process",
        listener_workflow_id="cleanup_workflow",
        priority=90,
        description="Trigger cleanup when property registration fails"
    )

    # Hook: Admin notification when property entities are created
    register_on_entity(
        hook_id="notify_on_property_creation",
        entity_type="property_record",
        listener_workflow_id="admin_notification_workflow",
        priority=20,
        description="Notify admin when new property records are created"
    )

    # Hook: Admin notification when applications are submitted
    register_on_entity(
        hook_id="notify_on_application_submission",
        entity_type="registration_application",
        listener_workflow_id="admin_notification_workflow",
        priority=30,
        description="Notify admin when registration applications are submitted"
    )

    # Hook: Admin notification on approval requests
    register_on_approval(
        hook_id="notify_on_approval_request",
        listener_workflow_id="admin_notification_workflow",
        workflow_pattern="property_registration_process",
        priority=50,
        description="Notify admin when approvals are requested"
    )

    # Advanced hook: Trigger document processing when certain entities exist
    register_workflow_hook(
        hook_id="auto_process_documents",
        listener_workflow_id="document_analysis_workflow",
        event_pattern="ENTITY_CREATED.*",
        trigger_type=HookTriggerType.CONDITIONAL,
        conditions={
            "entity_type": "uploaded_document",
            "document_type": "property_deed"
        },
        priority=60,
        context_mapping={
            "entity_id": "document_id",
            "entity_data": "document_metadata"
        },
        description="Automatically process property documents when uploaded"
    )


def get_workflow_types_examples():
    """Get all workflow type examples"""

    # Register hooks first
    register_workflow_hooks()

    return {
        "document_processing": create_document_processing_workflow(),
        "citizen_process": create_citizen_process_workflow(),
        "admin_notifications": create_admin_notification_workflow(),
        "cleanup_workflow": create_cleanup_workflow()
    }