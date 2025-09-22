"""
Example workflow demonstrating Airflow integration.
Shows how to trigger Airflow DAGs from MuniStream workflows.
"""

import os
from ..dag import DAG
from ..operators.python import PythonOperator
from ..operators.airflow_operator import AirflowOperator
from ..operators.base import TaskResult


def prepare_pdf_data(context):
    """
    Prepare PDF data for processing.
    This could fetch from database, generate, or receive from user.
    """
    print("📄 Preparing PDF data...")

    # For demo, use a public PDF URL
    pdf_url = "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf"

    # Return a dict - PythonOperator will wrap it in TaskResult
    # The AirflowOperator looks for {task_id}_dag_conf in the context
    return {
        "pdf_url": pdf_url,
        "pdf_source": "W3C test PDF",
        "trigger_airflow_dag_dag_conf": {  # This will be used by the AirflowOperator
            "pdf_url": pdf_url
        }
    }


def process_airflow_results(context):
    """
    Process results from Airflow DAG execution.
    """
    print("📊 Processing Airflow results...")

    # Extract Airflow execution results
    dag_run_id = context.get("dag_run_id")
    dag_id = context.get("dag_id")
    execution_time = context.get("execution_time_minutes")

    print(f"✅ Airflow DAG '{dag_id}' completed")
    print(f"   Run ID: {dag_run_id}")
    print(f"   Execution time: {execution_time} minutes")

    # Return a dict - PythonOperator will wrap it in TaskResult
    return {
        "processing_complete": True,
        "summary": f"Successfully processed via Airflow DAG {dag_id}"
    }


def create_airflow_integration_workflow():
    """
    Create a workflow that integrates with Apache Airflow.

    This workflow:
    1. Prepares data for Airflow processing
    2. Triggers an Airflow DAG (asynchronously)
    3. Waits for DAG completion without blocking
    4. Processes the results

    Note: Set environment variables for Airflow authentication:
    - AIRFLOW_API_URL (default: http://localhost:8080/api/v1)
    - AIRFLOW_USERNAME
    - AIRFLOW_PASSWORD
    """

    with DAG(
        dag_id="airflow_integration_workflow",
        description="Demonstrates async Airflow DAG integration"
    ) as dag:

        # Step 1: Prepare data
        prepare_task = PythonOperator(
            task_id="prepare_data",
            python_callable=prepare_pdf_data
        )

        # Step 2: Trigger Airflow DAG (non-blocking)
        # Get credentials from environment variables
        airflow_task = AirflowOperator(
            task_id="trigger_airflow_dag",
            dag_id="pdf_to_s3_pipeline",
            airflow_base_url=os.getenv("AIRFLOW_API_URL", "http://host.docker.internal:8080/api/v1"),
            airflow_username=os.getenv("AIRFLOW_USERNAME", "admin"),
            airflow_password=os.getenv("AIRFLOW_PASSWORD", "admin123"),
            timeout_minutes=10,
            poll_interval_seconds=5,
            dag_conf={
                "s3_bucket": os.getenv("S3_BUCKET", "munistream-files"),
                "s3_prefix": "workflow-integration-test",
                "dpi": 150
            }
        )

        # Step 3: Process results
        process_task = PythonOperator(
            task_id="process_results",
            python_callable=process_airflow_results
        )

        # Define flow
        prepare_task >> airflow_task >> process_task

    return dag


def create_multi_dag_workflow():
    """
    Create a workflow that triggers multiple Airflow DAGs.

    This demonstrates parallel DAG execution where multiple
    Airflow DAGs can run simultaneously without blocking each other.
    """

    with DAG(
        dag_id="multi_airflow_workflow",
        description="Triggers multiple Airflow DAGs in parallel"
    ) as dag:

        # Prepare data for multiple operations
        prepare = PythonOperator(
            task_id="prepare",
            python_callable=lambda context: TaskResult(
                status="continue",
                data={
                    "pdf_url": "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf",
                    "ready": True
                }
            )
        )

        # Trigger multiple Airflow DAGs
        # These will run asynchronously and won't block each other

        pdf_dag = AirflowOperator(
            task_id="pdf_conversion",
            dag_id="pdf_to_s3_pipeline",
            airflow_base_url=os.getenv("AIRFLOW_API_URL", "http://host.docker.internal:8080/api/v1"),
            airflow_username=os.getenv("AIRFLOW_USERNAME", "admin"),
            airflow_password=os.getenv("AIRFLOW_PASSWORD", "admin123"),
            dag_conf={
                "s3_bucket": os.getenv("S3_BUCKET", "munistream-files"),
                "s3_prefix": "multi-workflow/pdf"
            }
        )

        # You could add more Airflow DAGs here
        # Example: data_processing_dag, ml_pipeline_dag, etc.

        # Combine results
        combine = PythonOperator(
            task_id="combine_results",
            python_callable=lambda context: TaskResult(
                status="continue",
                data={"all_dags_complete": True}
            )
        )

        # Define parallel flow
        prepare >> [pdf_dag] >> combine

    return dag


# Register workflows
if __name__ == "__main__":
    # These workflows will be auto-registered when imported
    workflow1 = create_airflow_integration_workflow()
    workflow2 = create_multi_dag_workflow()

    print(f"✅ Created workflow: {workflow1.dag_id}")
    print(f"✅ Created workflow: {workflow2.dag_id}")