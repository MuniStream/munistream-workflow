"""
Asynchronous Airflow Operator for MuniStream
Triggers Airflow DAGs without blocking MuniStream execution
"""

import aiohttp
import asyncio
import json
import time
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from base64 import b64encode

from .base import BaseOperator, TaskResult

logger = logging.getLogger(__name__)


class AirflowOperator(BaseOperator):
    """
    Asynchronous operator for triggering and monitoring Airflow DAGs.

    This operator triggers an Airflow DAG and monitors its completion
    without blocking MuniStream's execution. It uses a polling mechanism
    to check DAG status on subsequent executions.
    """

    def __init__(
        self,
        task_id: str,
        dag_id: str,
        airflow_base_url: str = "http://localhost:8080/api/v1",
        airflow_username: str = "admin",
        airflow_password: str = "admin123",
        dag_conf: Optional[Dict[str, Any]] = None,
        timeout_minutes: int = 30,
        poll_interval_seconds: int = 10,
        **kwargs
    ):
        """
        Initialize Airflow Operator.

        Args:
            task_id: Unique task identifier
            dag_id: ID of the Airflow DAG to trigger
            airflow_base_url: Base URL for Airflow API
            airflow_username: Airflow username for authentication
            airflow_password: Airflow password for authentication
            dag_conf: Static configuration for the DAG (can be overridden by context)
            timeout_minutes: Maximum time to wait for DAG completion
            poll_interval_seconds: Minimum seconds between status checks
        """
        super().__init__(task_id, **kwargs)
        self.dag_id = dag_id
        self.airflow_base_url = airflow_base_url.rstrip('/')
        self.airflow_username = airflow_username
        self.airflow_password = airflow_password
        self.dag_conf = dag_conf or {}
        self.timeout_minutes = timeout_minutes
        self.poll_interval_seconds = poll_interval_seconds

    def execute(self, context: Dict[str, Any]) -> TaskResult:
        """
        Synchronous wrapper for async execution.
        Used when the executor doesn't call execute_async directly.
        """
        return asyncio.run(self.execute_async(context))

    async def execute_async(self, context: Dict[str, Any]) -> TaskResult:
        """
        Execute the Airflow operator asynchronously.

        This method handles both initial triggering and subsequent polling:
        1. First call: Triggers the DAG and returns "waiting"
        2. Subsequent calls: Checks DAG status and returns appropriate status

        Args:
            context: Workflow execution context

        Returns:
            TaskResult with status "waiting", "continue", or "failed"
        """
        # Check if we're already monitoring a DAG run
        airflow_state_key = f"airflow_{self.task_id}_state"
        airflow_state = context.get(airflow_state_key, {})

        print(f"DEBUG: Airflow operator checking state for key {airflow_state_key}")
        print(f"DEBUG: Found state: {airflow_state}")
        print(f"DEBUG: Full context keys: {list(context.keys())}")

        if not airflow_state:
            # First execution - trigger the DAG
            return await self._trigger_dag(context, airflow_state_key)
        else:
            # Subsequent execution - check DAG status
            return await self._check_dag_status(context, airflow_state_key, airflow_state)

    async def _trigger_dag(self, context: Dict[str, Any], state_key: str) -> TaskResult:
        """
        Trigger an Airflow DAG and store tracking information.

        Args:
            context: Workflow execution context
            state_key: Key to store Airflow state in context

        Returns:
            TaskResult with status "waiting" or "failed"
        """
        try:
            # Generate unique run ID
            dag_run_id = f"munistream_{self.task_id}_{int(time.time())}"

            # Prepare DAG configuration
            # Merge static config with dynamic context values
            dag_conf = self.dag_conf.copy()

            # Allow context to override or add to dag_conf
            context_dag_conf = context.get(f"{self.task_id}_dag_conf", {})
            dag_conf.update(context_dag_conf)

            # Log the trigger attempt
            await self.log_info(
                f"Triggering Airflow DAG: {self.dag_id}",
                {"dag_run_id": dag_run_id, "conf": dag_conf}
            )

            # Make API call to trigger DAG
            url = f"{self.airflow_base_url}/dags/{self.dag_id}/dagRuns"
            payload = {
                "dag_run_id": dag_run_id,
                "conf": dag_conf
            }

            # Create auth header
            auth_str = f"{self.airflow_username}:{self.airflow_password}"
            auth_bytes = auth_str.encode('ascii')
            auth_b64 = b64encode(auth_bytes).decode('ascii')
            headers = {
                "Authorization": f"Basic {auth_b64}",
                "Content-Type": "application/json"
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers) as response:
                    response_text = await response.text()

                    if response.status in [200, 201]:
                        # Successfully triggered
                        await self.log_info(
                            f"Successfully triggered DAG {self.dag_id}",
                            {"dag_run_id": dag_run_id, "status_code": response.status}
                        )

                        # Store state for polling
                        context[state_key] = {
                            "dag_run_id": dag_run_id,
                            "triggered_at": datetime.utcnow().isoformat(),
                            "last_check": datetime.utcnow().isoformat(),
                            "status": "triggered"
                        }

                        return TaskResult(
                            status="waiting",
                            metadata={
                                "message": f"Airflow DAG {self.dag_id} triggered",
                                "dag_run_id": dag_run_id
                            }
                        )
                    else:
                        # Failed to trigger
                        await self.log_error(
                            f"Failed to trigger DAG {self.dag_id}",
                            details={"status_code": response.status, "response": response_text[:500]}
                        )

                        return TaskResult(
                            status="failed",
                            error=f"Failed to trigger DAG: HTTP {response.status}"
                        )

        except Exception as e:
            await self.log_error(
                f"Error triggering DAG {self.dag_id}",
                error=e
            )
            return TaskResult(
                status="failed",
                error=f"Error triggering DAG: {str(e)}"
            )

    async def _check_dag_status(
        self,
        context: Dict[str, Any],
        state_key: str,
        airflow_state: Dict[str, Any]
    ) -> TaskResult:
        """
        Check the status of a running DAG.

        Args:
            context: Workflow execution context
            state_key: Key where Airflow state is stored
            airflow_state: Current Airflow tracking state

        Returns:
            TaskResult with status "waiting", "continue", or "failed"
        """
        dag_run_id = airflow_state["dag_run_id"]
        triggered_at = datetime.fromisoformat(airflow_state["triggered_at"])
        last_check = datetime.fromisoformat(airflow_state["last_check"])

        # Check if we should poll yet (rate limiting)
        time_since_last_check = (datetime.utcnow() - last_check).total_seconds()
        if time_since_last_check < self.poll_interval_seconds:
            # Too soon to check again
            return TaskResult(
                status="waiting",
                metadata={
                    "message": f"Waiting {self.poll_interval_seconds - int(time_since_last_check)}s before next check",
                    "dag_run_id": dag_run_id
                }
            )

        # Check for timeout
        time_since_trigger = (datetime.utcnow() - triggered_at).total_seconds() / 60
        if time_since_trigger > self.timeout_minutes:
            await self.log_error(
                f"DAG {self.dag_id} timed out after {self.timeout_minutes} minutes",
                details={"dag_run_id": dag_run_id}
            )

            # Clean up state
            del context[state_key]

            return TaskResult(
                status="failed",
                error=f"DAG execution timed out after {self.timeout_minutes} minutes"
            )

        try:
            # Check DAG status via API
            url = f"{self.airflow_base_url}/dags/{self.dag_id}/dagRuns/{dag_run_id}"

            # Create auth header
            auth_str = f"{self.airflow_username}:{self.airflow_password}"
            auth_bytes = auth_str.encode('ascii')
            auth_b64 = b64encode(auth_bytes).decode('ascii')
            headers = {
                "Authorization": f"Basic {auth_b64}",
                "Accept": "application/json"
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        dag_state = data.get("state", "unknown")

                        # Update last check time
                        airflow_state["last_check"] = datetime.utcnow().isoformat()
                        airflow_state["status"] = dag_state
                        context[state_key] = airflow_state

                        # Check if DAG is complete
                        if dag_state == "success":
                            await self.log_info(
                                f"DAG {self.dag_id} completed successfully",
                                {"dag_run_id": dag_run_id, "duration_minutes": round(time_since_trigger, 1)}
                            )

                            # Clean up state and return success
                            del context[state_key]

                            return TaskResult(
                                status="continue",
                                data={
                                    "dag_run_id": dag_run_id,
                                    "dag_id": self.dag_id,
                                    "execution_time_minutes": round(time_since_trigger, 1),
                                    "final_state": dag_state
                                }
                            )

                        elif dag_state == "failed":
                            await self.log_error(
                                f"DAG {self.dag_id} failed",
                                details={"dag_run_id": dag_run_id}
                            )

                            # Clean up state and return failure
                            del context[state_key]

                            return TaskResult(
                                status="failed",
                                error=f"Airflow DAG {self.dag_id} failed"
                            )

                        elif dag_state in ["running", "queued"]:
                            # Still running - continue waiting
                            await self.log_debug(
                                f"DAG {self.dag_id} still {dag_state}",
                                {"dag_run_id": dag_run_id, "elapsed_minutes": round(time_since_trigger, 1)}
                            )

                            return TaskResult(
                                status="waiting",
                                metadata={
                                    "message": f"DAG is {dag_state} ({round(time_since_trigger, 1)} minutes elapsed)",
                                    "dag_run_id": dag_run_id,
                                    "state": dag_state
                                }
                            )

                        else:
                            # Unexpected state
                            await self.log_warning(
                                f"DAG {self.dag_id} in unexpected state: {dag_state}",
                                {"dag_run_id": dag_run_id}
                            )

                            return TaskResult(
                                status="waiting",
                                metadata={
                                    "message": f"DAG in state: {dag_state}",
                                    "dag_run_id": dag_run_id
                                }
                            )

                    else:
                        # Error checking status
                        response_text = await response.text()
                        await self.log_error(
                            f"Failed to check DAG status",
                            details={"status_code": response.status, "response": response_text[:500]}
                        )

                        # Continue waiting - might be temporary issue
                        return TaskResult(
                            status="waiting",
                            metadata={
                                "message": f"Error checking status (HTTP {response.status}), will retry",
                                "dag_run_id": dag_run_id
                            }
                        )

        except Exception as e:
            await self.log_error(
                f"Error checking DAG status",
                error=e
            )

            # Continue waiting - might be temporary network issue
            return TaskResult(
                status="waiting",
                metadata={
                    "message": f"Error checking status: {str(e)[:100]}, will retry",
                    "dag_run_id": dag_run_id
                }
            )