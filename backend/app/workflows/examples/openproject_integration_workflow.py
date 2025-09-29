"""
Example workflow demonstrating OpenProject integration.
Shows how to create work packages and wait for team decisions.
"""

import os
from app.workflows.dag import DAG
from app.workflows.operators.python import PythonOperator
from app.workflows.operators.openproject_operator import OpenProjectAssignmentOperator
from app.workflows.operators.base import TaskResult


def prepare_request_data(context):
    """
    Prepare data for OpenProject work package creation.
    This simulates data coming from a citizen request or form submission.
    """
    print("Preparing request data for OpenProject...")

    # Simulate citizen request data
    request_data = {
        "request_type": "building_permit",
        "description": "Request for building permit approval for residential construction",
        "citizen_name": "John Doe",
        "citizen_email": "john.doe@example.com",
        "property_address": "123 Main St, City, State",
        "urgency": "medium",
        "submitted_date": "2025-01-27",
        "attachments": [
            {
                "filename": "building_plans.pdf",
                "url": "https://example.com/files/building_plans.pdf",
                "size": 2048576
            },
            {
                "filename": "site_survey.pdf",
                "url": "https://example.com/files/site_survey.pdf",
                "size": 1024000
            }
        ]
    }

    # Prepare OpenProject configuration for the operator
    openproject_config = {
        "assign_to_openproject_dag_conf": {
            "project_id": "3",  # Permisos project ID
            "subject": f"Building Permit Request - {request_data['property_address']}",
            "description": request_data["description"],
            "type": "Task",
            "priority": "Normal" if request_data["urgency"] == "medium" else "High",
            "custom_fields": {
                "request_type": request_data["request_type"],
                "citizen_email": request_data["citizen_email"],
                "property_address": request_data["property_address"]
            },
            "attachments": request_data["attachments"],
            "completion_statuses": {
                "approved": ["Closed", "Resolved"],
                "rejected": ["Rejected", "Won't Fix"],
                "needs_revision": ["Feedback", "In Progress"]
            }
        }
    }

    print(f"Prepared request for: {request_data['request_type']}")
    print(f"   Property: {request_data['property_address']}")
    print(f"   Attachments: {len(request_data['attachments'])} files")

    return {**request_data, **openproject_config}


def process_team_decision(context):
    """
    Process the team's decision from OpenProject.
    """
    print("Processing team decision...")

    # Extract OpenProject results - operator saves with task_id prefix
    outcome = context.get("assign_to_openproject_result", "unknown")
    work_package_id = context.get("assign_to_openproject_work_package_id")
    final_status = context.get("assign_to_openproject_status")
    notification_data = context.get("assign_to_openproject_notification_data", {})
    activities = notification_data.get("new_comments", [])

    print(f"Team decision received: {outcome}")
    print(f"   Work Package ID: {work_package_id}")
    print(f"   Final Status: {final_status}")
    print(f"   Activities logged: {len(activities)}")

    # Extract comments from activities (already processed by operator)
    comments = activities  # Activities are already in the right format from operator

    # Prepare notification data based on outcome
    notification_data = {
        "recipient": context.get("citizen_email", "john.doe@example.com"),
        "outcome": outcome,
        "work_package_id": work_package_id,
        "team_comments": comments
    }

    if outcome == "approved":
        notification_data["message"] = "Your building permit request has been approved!"
        notification_data["next_steps"] = "You can proceed with construction. Please schedule an inspection."
    elif outcome == "rejected":
        notification_data["message"] = "Your building permit request has been rejected."
        notification_data["next_steps"] = "Please review the team's comments for rejection reasons."
    else:  # needs_revision
        notification_data["message"] = "Your request needs additional information."
        notification_data["next_steps"] = "Please provide the requested information and resubmit."

    print(f"Notification prepared for: {notification_data['recipient']}")

    return notification_data


def create_openproject_integration_workflow():
    """
    Create a workflow that integrates with OpenProject.

    This workflow:
    1. Prepares request data (simulating citizen submission)
    2. Creates a work package in OpenProject
    3. Waits for team decision (non-blocking)
    4. Processes the decision and prepares notifications

    Note: Set environment variables for OpenProject:
    - OPENPROJECT_BASE_URL
    - OPENPROJECT_API_KEY
    - OPENPROJECT_DEFAULT_PROJECT_ID
    """

    with DAG(
        dag_id="openproject_integration_workflow",
        description="Demonstrates OpenProject work package creation and team assignment"
    ) as dag:

        # Step 1: Prepare request data
        prepare_task = PythonOperator(
            task_id="prepare_request",
            python_callable=prepare_request_data
        )

        # Step 2: Create work package and wait for decision (non-blocking)
        openproject_task = OpenProjectAssignmentOperator(
            task_id="assign_to_openproject",
            project_key=os.getenv("OPENPROJECT_PROJECT_KEY"),  # Must be set in environment
            work_package_type="1",  # Task type work package (ID=1)
            openproject_url=os.getenv("OPENPROJECT_BASE_URL"),  # Must be set in environment
            api_key=os.getenv("OPENPROJECT_API_KEY"),  # Must be set in environment
            timeout_minutes=60,  # Wait up to 60 minutes for team decision
            poll_interval_seconds=30,  # Check every 30 seconds
            capture_activities=True  # Capture all activities for notification service
        )

        # Step 3: Process team decision
        process_task = PythonOperator(
            task_id="process_decision",
            python_callable=process_team_decision
        )

        # Define workflow
        prepare_task >> openproject_task >> process_task

    return dag


def create_complex_openproject_workflow():
    """
    Create a more complex workflow with multiple OpenProject assignments.

    This demonstrates:
    - Multiple team assignments based on request type
    - Parallel work package creation
    - Decision routing based on outcomes
    """

    def route_to_teams(context):
        """Route request to appropriate teams based on type"""
        print("Routing request to appropriate teams...")

        request_type = context.get("request_type", "general")

        # Prepare configurations for different teams
        teams_config = {}

        if request_type in ["building_permit", "construction"]:
            teams_config["building_team_dag_conf"] = {
                "project_id": "2",  # Building Department project
                "subject": "Building Review Required",
                "type": "Task"
            }

        if request_type in ["business_license", "commercial"]:
            teams_config["licensing_team_dag_conf"] = {
                "project_id": "3",  # Licensing Department project
                "subject": "License Review Required",
                "type": "Task"
            }

        # Always include compliance team for review
        teams_config["compliance_team_dag_conf"] = {
            "project_id": "4",  # Compliance Department project
            "subject": "Compliance Check Required",
            "type": "Review"
        }

        print(f"Routing to {len(teams_config)} teams")

        return teams_config

    def consolidate_decisions(context):
        """Consolidate decisions from multiple teams"""
        print("Consolidating team decisions...")

        # Extract results from different teams
        building_result = context.get("building_team_result", {})
        licensing_result = context.get("licensing_team_result", {})
        compliance_result = context.get("compliance_team_result", {})

        # Determine overall outcome (all must approve)
        outcomes = []
        if building_result:
            outcomes.append(building_result.get("outcome"))
        if licensing_result:
            outcomes.append(licensing_result.get("outcome"))
        if compliance_result:
            outcomes.append(compliance_result.get("outcome"))

        # Overall approval only if all teams approve
        if all(outcome == "approved" for outcome in outcomes if outcome):
            overall_outcome = "approved"
        elif any(outcome == "rejected" for outcome in outcomes if outcome):
            overall_outcome = "rejected"
        else:
            overall_outcome = "needs_revision"

        print(f"Overall decision: {overall_outcome}")
        print(f"   Team outcomes: {outcomes}")

        return {
            "overall_outcome": overall_outcome,
            "team_decisions": {
                "building": building_result,
                "licensing": licensing_result,
                "compliance": compliance_result
            }
        }

    with DAG(
        dag_id="complex_openproject_workflow",
        description="Multi-team OpenProject integration"
    ) as dag:

        # Prepare initial request
        prepare = PythonOperator(
            task_id="prepare",
            python_callable=prepare_request_data
        )

        # Route to appropriate teams
        route = PythonOperator(
            task_id="route_to_teams",
            python_callable=route_to_teams
        )

        # Create work packages for different teams (these run in parallel)
        building_team = OpenProjectAssignmentOperator(
            task_id="building_team",
            project_key=os.getenv("OPENPROJECT_BUILDING_PROJECT", os.getenv("OPENPROJECT_PROJECT_KEY")),
            work_package_type="Task",
            base_url=os.getenv("OPENPROJECT_BASE_URL"),
            api_key=os.getenv("OPENPROJECT_API_KEY"),
            timeout_minutes=120,
            poll_interval_seconds=60
        )

        licensing_team = OpenProjectAssignmentOperator(
            task_id="licensing_team",
            project_key=os.getenv("OPENPROJECT_LICENSING_PROJECT", os.getenv("OPENPROJECT_PROJECT_KEY")),
            work_package_type="Task",
            base_url=os.getenv("OPENPROJECT_BASE_URL"),
            api_key=os.getenv("OPENPROJECT_API_KEY"),
            timeout_minutes=120,
            poll_interval_seconds=60
        )

        compliance_team = OpenProjectAssignmentOperator(
            task_id="compliance_team",
            project_key=os.getenv("OPENPROJECT_COMPLIANCE_PROJECT", os.getenv("OPENPROJECT_PROJECT_KEY")),
            work_package_type="Review",
            base_url=os.getenv("OPENPROJECT_BASE_URL"),
            api_key=os.getenv("OPENPROJECT_API_KEY"),
            timeout_minutes=120,
            poll_interval_seconds=60
        )

        # Consolidate all decisions
        consolidate = PythonOperator(
            task_id="consolidate_decisions",
            python_callable=consolidate_decisions
        )

        # Define workflow with parallel team assignments
        prepare >> route
        route >> [building_team, licensing_team, compliance_team] >> consolidate

    return dag