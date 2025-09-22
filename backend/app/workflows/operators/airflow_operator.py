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
        self._state_key = f"airflow_{task_id}_state"

    def _waiting_result(self, message: str, **metadata) -> TaskResult:
        """Create a waiting TaskResult with state preservation."""
        # Always include the state if it exists
        data = {}
        if hasattr(self, '_airflow_state') and self._airflow_state:
            # IMPORTANT: Update the context with the current state
            data[self._state_key] = self._airflow_state.copy()
        return TaskResult(
            status="waiting",
            data=data,
            metadata={"message": message, **metadata}
        )

    def _success_result(self, data: Dict[str, Any]) -> TaskResult:
        """Create a success TaskResult."""
        return TaskResult(status="continue", data=data)

    def _failed_result(self, error: str) -> TaskResult:
        """Create a failed TaskResult."""
        return TaskResult(status="failed", error=error)

    def _get_auth_headers(self) -> Dict[str, str]:
        """Get authentication headers for Airflow API."""
        auth_str = f"{self.airflow_username}:{self.airflow_password}"
        auth_b64 = b64encode(auth_str.encode('ascii')).decode('ascii')
        return {
            "Authorization": f"Basic {auth_b64}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

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
        # Get or initialize Airflow state
        self._airflow_state = context.get(self._state_key, {})

        logger.debug(f"Airflow operator state for {self._state_key}: {self._airflow_state}")
        logger.info(f"Context keys available: {list(context.keys())}")
        logger.info(f"Looking for dag_conf at key: {self.task_id}_dag_conf")
        if f"{self.task_id}_dag_conf" in context:
            logger.info(f"Found dag_conf: {context[f'{self.task_id}_dag_conf']}")

        if not self._airflow_state:
            # First execution - trigger the DAG
            return await self._trigger_dag(context)
        else:
            # Subsequent execution - check DAG status
            return await self._check_dag_status()

    async def _trigger_dag(self, context: Dict[str, Any]) -> TaskResult:
        """Trigger an Airflow DAG and store tracking information."""
        try:
            # Generate unique run ID
            dag_run_id = f"munistream_{self.task_id}_{int(time.time())}"

            # Prepare DAG configuration
            dag_conf = {**self.dag_conf, **context.get(f"{self.task_id}_dag_conf", {})}

            await self.log_info(
                f"Triggering Airflow DAG: {self.dag_id}",
                {"dag_run_id": dag_run_id, "conf": dag_conf}
            )

            # Make API call to trigger DAG
            url = f"{self.airflow_base_url}/dags/{self.dag_id}/dagRuns"
            payload = {"dag_run_id": dag_run_id, "conf": dag_conf}

            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=self._get_auth_headers()) as response:
                    if response.status in [200, 201]:
                        await self.log_info(
                            f"Successfully triggered DAG {self.dag_id}",
                            {"dag_run_id": dag_run_id, "status_code": response.status}
                        )

                        # Store state for polling
                        self._airflow_state = {
                            "dag_run_id": dag_run_id,
                            "triggered_at": datetime.utcnow().isoformat(),
                            "last_check": datetime.utcnow().isoformat(),
                            "status": "triggered"
                        }
                        context[self._state_key] = self._airflow_state

                        # Return with the state data explicitly
                        return TaskResult(
                            status="waiting",
                            data={self._state_key: self._airflow_state},
                            metadata={
                                "message": f"Airflow DAG {self.dag_id} triggered",
                                "dag_run_id": dag_run_id
                            }
                        )
                    else:
                        response_text = await response.text()
                        await self.log_error(
                            f"Failed to trigger DAG {self.dag_id}",
                            details={"status_code": response.status, "response": response_text[:500]}
                        )
                        return self._failed_result(f"Failed to trigger DAG: HTTP {response.status}")

        except Exception as e:
            await self.log_error(f"Error triggering DAG {self.dag_id}", error=e)
            return self._failed_result(f"Error triggering DAG: {str(e)}")

    async def _check_dag_status(self) -> TaskResult:
        """Check the status of a running DAG."""
        dag_run_id = self._airflow_state["dag_run_id"]
        triggered_at = datetime.fromisoformat(self._airflow_state["triggered_at"])
        last_check = datetime.fromisoformat(self._airflow_state["last_check"])

        # Rate limiting
        time_since_last_check = (datetime.utcnow() - last_check).total_seconds()
        logger.info(f"Time since last check: {time_since_last_check}s, poll interval: {self.poll_interval_seconds}s")
        if time_since_last_check < self.poll_interval_seconds:
            wait_time = self.poll_interval_seconds - int(time_since_last_check)
            logger.info(f"Rate limiting: waiting {wait_time}s more")
            return self._waiting_result(
                f"Waiting {wait_time}s before next check",
                dag_run_id=dag_run_id
            )

        # Timeout check
        elapsed_minutes = (datetime.utcnow() - triggered_at).total_seconds() / 60
        if elapsed_minutes > self.timeout_minutes:
            await self.log_error(
                f"DAG {self.dag_id} timed out after {self.timeout_minutes} minutes",
                details={"dag_run_id": dag_run_id}
            )
            return self._failed_result(f"DAG execution timed out after {self.timeout_minutes} minutes")

        try:
            # Check DAG status via API
            url = f"{self.airflow_base_url}/dags/{self.dag_id}/dagRuns/{dag_run_id}"

            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self._get_auth_headers()) as response:
                    if response.status != 200:
                        response_text = await response.text()
                        await self.log_error(
                            f"Failed to check DAG status",
                            details={"status_code": response.status, "response": response_text[:500]}
                        )
                        return self._waiting_result(
                            f"Error checking status (HTTP {response.status}), will retry",
                            dag_run_id=dag_run_id
                        )

                    data = await response.json()
                    dag_state = data.get("state", "unknown")

                    # Update state - this needs to be persisted!
                    self._airflow_state["last_check"] = datetime.utcnow().isoformat()
                    self._airflow_state["status"] = dag_state

                    # Debug logging
                    logger.info(f"DAG {self.dag_id} state: {dag_state}, last_check updated to {self._airflow_state['last_check']}")

                    # Handle different DAG states
                    if dag_state == "success":
                        await self.log_info(
                            f"DAG {self.dag_id} completed successfully",
                            {"dag_run_id": dag_run_id, "duration_minutes": round(elapsed_minutes, 1)}
                        )
                        return self._success_result({
                            "dag_run_id": dag_run_id,
                            "dag_id": self.dag_id,
                            "execution_time_minutes": round(elapsed_minutes, 1),
                            "final_state": dag_state
                        })

                    elif dag_state == "failed":
                        await self.log_error(
                            f"DAG {self.dag_id} failed",
                            details={"dag_run_id": dag_run_id}
                        )
                        return self._failed_result(f"Airflow DAG {self.dag_id} failed")

                    elif dag_state in ["running", "queued"]:
                        await self.log_debug(
                            f"DAG {self.dag_id} still {dag_state}",
                            {"dag_run_id": dag_run_id, "elapsed_minutes": round(elapsed_minutes, 1)}
                        )
                        return self._waiting_result(
                            f"DAG is {dag_state} ({round(elapsed_minutes, 1)} minutes elapsed)",
                            dag_run_id=dag_run_id,
                            state=dag_state
                        )

                    else:
                        await self.log_warning(
                            f"DAG {self.dag_id} in unexpected state: {dag_state}",
                            {"dag_run_id": dag_run_id}
                        )
                        return self._waiting_result(
                            f"DAG in state: {dag_state}",
                            dag_run_id=dag_run_id
                        )

        except Exception as e:
            await self.log_error(f"Error checking DAG status", error=e)
            return self._waiting_result(
                f"Error checking status: {str(e)[:100]}, will retry",
                dag_run_id=dag_run_id
            )