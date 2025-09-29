"""
OpenProject Assignment Operator for MuniStream
Assigns workflow tasks to OpenProject work packages and tracks their completion
"""

import os
import json
import base64
import aiohttp
import logging

from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta

from .base import BaseOperator, TaskResult

logger = logging.getLogger(__name__)


class OpenProjectAssignmentOperator(BaseOperator):
    """
    Operator that creates and tracks OpenProject work packages.

    This operator:
    1. Creates a work package in OpenProject with workflow data
    2. Uploads any files found in the workflow context
    3. Polls for work package completion
    4. Captures activities and comments for notifications
    5. Returns different outcomes based on final status

    Following the AirflowOperator pattern for async execution and state persistence.
    """

    def __init__(
        self,
        task_id: str,
        project_key: str,
        work_package_type: str,
        team_id: Optional[str] = None,
        assignee_id: Optional[str] = None,
        priority: str = "Normal",
        due_date_days: int = 5,

        # Context data handling (agnostic to source)
        context_keys_for_files: Optional[List[str]] = None,
        context_keys_for_description: Optional[List[str]] = None,
        custom_field_mapping: Optional[Dict[str, str]] = None,

        # Status configuration
        completion_statuses: Optional[List[str]] = None,
        approval_statuses: Optional[List[str]] = None,
        rejection_statuses: Optional[List[str]] = None,

        # Activity tracking
        capture_activities: bool = True,

        # API Configuration
        openproject_url: Optional[str] = None,
        api_key: Optional[str] = None,

        # Polling configuration
        poll_interval_seconds: int = 60,
        timeout_hours: int = 72,
        **kwargs
    ):
        """
        Initialize OpenProject Assignment Operator.

        Args:
            task_id: Unique task identifier
            project_key: OpenProject project identifier
            work_package_type: Type of work package to create
            team_id: Team to assign to (optional)
            assignee_id: User to assign to (optional)
            priority: Priority level (High, Normal, Low)
            due_date_days: Days from creation for due date
            context_keys_for_files: Context keys that may contain files
            context_keys_for_description: Context keys to include in description
            custom_field_mapping: Map context keys to custom fields
            completion_statuses: Statuses that indicate completion
            approval_statuses: Statuses that indicate approval
            rejection_statuses: Statuses that indicate rejection
            capture_activities: Whether to capture activities/comments
            openproject_url: OpenProject API URL
            api_key: OpenProject API key
            poll_interval_seconds: Seconds between status checks
            timeout_hours: Maximum hours to wait for completion
        """
        super().__init__(task_id, **kwargs)

        self.project_key = project_key
        self.work_package_type = work_package_type
        self.team_id = team_id
        self.assignee_id = assignee_id
        self.priority = priority
        self.due_date_days = due_date_days

        # Auto-detect file keys if not specified
        self.context_keys_for_files = context_keys_for_files or [
            "files", "uploaded_files", "documents", "attachments",
            "s3_upload_results", "processed_files", "validation_reports"
        ]

        self.context_keys_for_description = context_keys_for_description or []
        self.custom_field_mapping = custom_field_mapping or {}

        # Status configuration
        self.completion_statuses = completion_statuses or ["Closed", "Resolved", "Done"]
        self.approval_statuses = approval_statuses or ["Approved", "Accepted"]
        self.rejection_statuses = rejection_statuses or ["Rejected", "Denied"]

        # API config
        self.openproject_url = openproject_url or os.getenv("OPENPROJECT_BASE_URL", "http://localhost:8080")
        self.api_key = api_key or os.getenv("OPENPROJECT_API_KEY")

        self.capture_activities = capture_activities
        self.poll_interval_seconds = poll_interval_seconds
        self.timeout_hours = timeout_hours

        # State key for persistence (following Airflow pattern)
        self._state_key = f"openproject_{task_id}_state"

    def _waiting_result(self, message: str, **metadata) -> TaskResult:
        """Create a waiting TaskResult with state preservation."""
        data = {}
        if hasattr(self, '_openproject_state') and self._openproject_state:
            # Update the context with the current state
            data[self._state_key] = self._openproject_state.copy()
        logger.debug(f"Returning waiting result: {message}, state_key={self._state_key}, has_state={bool(data.get(self._state_key))}")
        return TaskResult(
            status="waiting",
            data=data,
            metadata={"message": message, **metadata}
        )

    def _success_result(self, data: Dict[str, Any]) -> TaskResult:
        """Create a success TaskResult."""
        return TaskResult(status="continue", data=data)

    def _failed_result(self, error: str, **context_data) -> TaskResult:
        """Create a failed TaskResult with optional context data."""
        return TaskResult(status="failed", error=error, data=context_data)

    def _get_auth_headers(self) -> Dict[str, str]:
        """Get authentication headers for OpenProject API."""
        import base64
        auth_str = f"apikey:{self.api_key}"
        auth_b64 = base64.b64encode(auth_str.encode('ascii')).decode('ascii')
        return {
            "Authorization": f"Basic {auth_b64}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

    def execute(self, context: Dict[str, Any]) -> TaskResult:
        """
        Required by base class. Since the executor prefers execute_async,
        this is only a fallback for compatibility.
        """
        import asyncio
        return asyncio.run(self.execute_async(context))

    async def execute_async(self, context: Dict[str, Any]) -> TaskResult:
        """
        Execute the OpenProject operator asynchronously.

        1. First call: Creates work package and returns "waiting"
        2. Subsequent calls: Checks work package status and returns appropriate status
        """
        logger.debug(f"OpenProject operator started for task {self.task_id}")
        logger.debug(f"API Key present: {bool(self.api_key)}, Project: {self.project_key}, Type: {self.work_package_type}")
        logger.debug(f"State key: {self._state_key}")
        logger.debug(f"Context keys: {list(context.keys())}")

        # Check API key configuration
        if not self.api_key:
            logger.warning("No OpenProject API key configured")
            return self._failed_result("OpenProject API key not configured")

        # Read dag_conf from context to override parameters (following AirflowOperator pattern)
        dag_conf = context.get(f"{self.task_id}_dag_conf", {})
        if dag_conf:
            logger.debug(f"Found dag_conf with keys: {list(dag_conf.keys())}")
            # Override completion_statuses if provided in dag_conf
            if "completion_statuses" in dag_conf:
                comp_statuses = dag_conf["completion_statuses"]
                # Handle both dictionary format (from workflow) and list format
                if isinstance(comp_statuses, dict):
                    # Preserve the categorization for proper result determination
                    self.approval_statuses = comp_statuses.get("approved", [])
                    self.rejection_statuses = comp_statuses.get("rejected", [])
                    self.revision_statuses = comp_statuses.get("needs_revision", [])
                    # Create a flat list of all completion statuses
                    all_statuses = []
                    for status_list in comp_statuses.values():
                        if isinstance(status_list, list):
                            all_statuses.extend(status_list)
                    self.completion_statuses = all_statuses
                    logger.debug(f"Updated completion_statuses: {len(self.completion_statuses)} statuses configured")
                    logger.debug(f"Status categories - Approved: {len(self.approval_statuses)}, Rejected: {len(self.rejection_statuses)}, Revision: {len(getattr(self, 'revision_statuses', []))}")
                elif isinstance(comp_statuses, list):
                    self.completion_statuses = comp_statuses
                    # With a list, we can't differentiate - all are treated as approved
                    self.approval_statuses = comp_statuses
                    self.rejection_statuses = []
                    self.revision_statuses = []
                    logger.debug(f"Updated completion_statuses from list: {len(self.completion_statuses)} statuses")

        # Get or initialize OpenProject state
        self._openproject_state = context.get(self._state_key, {})

        logger.debug(f"OpenProject state retrieved: {bool(self._openproject_state)}")
        if self._openproject_state:
            logger.debug(f"State contains: work_package_id={self._openproject_state.get('work_package_id')}, status={self._openproject_state.get('status')}")

        if not self._openproject_state:
            # First execution - create work package
            logger.debug("No state found, creating new work package")
            return await self._create_work_package(context)
        else:
            # Subsequent execution - check work package status
            wp_id = self._openproject_state.get('work_package_id')
            logger.debug(f"State found, checking work package #{wp_id} status")
            return await self._check_work_package_status()

    async def _create_work_package(self, context: Dict[str, Any]) -> TaskResult:
        """Create work package with whatever data is available in context."""
        try:
            # Scan context for all available data
            available_files = self._scan_context_for_files(context)
            description = self._build_description_from_context(context)
            custom_fields = self._extract_custom_fields(context)

            # Prepare work package data
            wp_data = {
                "subject": self._generate_subject(context),
                "description": {
                    "format": "markdown",
                    "raw": description
                },
                "_links": {
                    "project": {"href": f"/api/v3/projects/{self.project_key}"},
                    "type": {"href": f"/api/v3/types/{self.work_package_type}"},
                    "priority": {"href": f"/api/v3/priorities/{self._get_priority_id()}"},
                    "status": {"href": "/api/v3/statuses/1"}  # New/Open
                }
            }

            # Add assignee if specified
            if self.assignee_id:
                wp_data["_links"]["assignee"] = {"href": f"/api/v3/users/{self.assignee_id}"}

            # Add due date
            if self.due_date_days:
                due_date = datetime.utcnow() + timedelta(days=self.due_date_days)
                wp_data["dueDate"] = due_date.strftime("%Y-%m-%d")

            # Add custom fields
            wp_data.update(custom_fields)

            logger.debug(
                f"Creating OpenProject work package in project {self.project_key}, "
                f"type: {self.work_package_type}, files_found: {len(available_files)}"
            )

            # Create work package
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.openproject_url}/api/v3/work_packages",
                    json=wp_data,
                    headers=self._get_auth_headers()
                ) as response:
                    if response.status in [200, 201]:
                        data = await response.json()
                        wp_id = data["id"]

                        logger.debug(
                            f"Created OpenProject work package #{wp_id}, "
                            f"url: {self.openproject_url}/work_packages/{wp_id}"
                        )

                        # Upload any files found in context
                        upload_results = []
                        for file_info in available_files:
                            result = await self._upload_file_to_work_package(wp_id, file_info)
                            if result:
                                upload_results.append(result)

                        # Store state for polling
                        self._openproject_state = {
                            "work_package_id": wp_id,
                            "created_at": datetime.utcnow().isoformat(),
                            "last_check": datetime.utcnow().isoformat(),
                            "status": "created",
                            "status_name": data["_embedded"]["status"]["name"],
                            "files_uploaded": len(upload_results),
                            "context_keys_used": list(set([f.get("source") for f in available_files]))
                        }

                        return TaskResult(
                            status="waiting",
                            data={self._state_key: self._openproject_state},
                            metadata={
                                "message": f"Created OpenProject work package #{wp_id} with {len(upload_results)} files",
                                "work_package_id": wp_id,
                                "url": f"{self.openproject_url}/work_packages/{wp_id}"
                            }
                        )
                    else:
                        error_text = await response.text()
                        logger.error(
                            f"Failed to create work package - HTTP {response.status}: {error_text[:500]}"
                        )
                        return self._failed_result(f"Failed to create work package: HTTP {response.status}")

        except Exception as e:
            logger.error(f"Error creating work package: {str(e)}", exc_info=True)
            return self._failed_result(f"Error creating work package: {str(e)}")

    async def _check_work_package_status(self) -> TaskResult:
        """Check the status of a work package."""
        wp_id = self._openproject_state["work_package_id"]
        logger.debug(f"Checking status for work package #{wp_id}")
        created_at = datetime.fromisoformat(self._openproject_state["created_at"])
        last_check = datetime.fromisoformat(self._openproject_state["last_check"])

        # Rate limiting
        time_since_last_check = (datetime.utcnow() - last_check).total_seconds()
        if time_since_last_check < self.poll_interval_seconds:
            wait_time = self.poll_interval_seconds - int(time_since_last_check)
            return self._waiting_result(
                f"Waiting {wait_time}s before next check",
                work_package_id=wp_id
            )

        # Timeout check
        elapsed_hours = (datetime.utcnow() - created_at).total_seconds() / 3600
        if elapsed_hours > self.timeout_hours:
            await self.log_error(
                f"Work package #{wp_id} timed out after {self.timeout_hours} hours"
            )
            return self._failed_result(f"Work package timed out after {self.timeout_hours} hours")

        try:
            # Check work package status
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.openproject_url}/api/v3/work_packages/{wp_id}",
                    headers=self._get_auth_headers()
                ) as response:
                    if response.status != 200:
                        response_text = await response.text()
                        await self.log_error(
                            f"Failed to check work package status - HTTP {response.status}: {response_text[:500]}"
                        )
                        return self._waiting_result(
                            f"Error checking status (HTTP {response.status}), will retry",
                            work_package_id=wp_id
                        )

                    data = await response.json()
                    wp_status = data["_embedded"]["status"]["name"]

                    logger.debug(f"Work package #{wp_id} status check: {wp_status} (completion statuses: {len(self.completion_statuses)})")

                    # Update state
                    self._openproject_state["last_check"] = datetime.utcnow().isoformat()
                    self._openproject_state["status"] = wp_status
                    self._openproject_state["status_name"] = wp_status

                    # Capture activities if enabled
                    if self.capture_activities:
                        activities = await self._fetch_activities(wp_id)
                        self._openproject_state["activities"] = activities

                        # Extract important events for notifications
                        notification_data = {
                            "new_comments": self._extract_new_comments(activities),
                            "assignments": self._extract_assignments(activities),
                            "status_changes": self._extract_status_changes(activities),
                            "last_activity_check": datetime.utcnow().isoformat()
                        }
                        self._openproject_state["notification_data"] = notification_data

                    await self.log_info(f"Work package #{wp_id} status: {wp_status}")

                    # Handle different work package states
                    if wp_status in self.completion_statuses:
                        logger.info(f"Work package #{wp_id} completed with status: {wp_status}")
                        # Determine result type based on status category
                        result_type = "completed"
                        if hasattr(self, 'approval_statuses') and wp_status in self.approval_statuses:
                            result_type = "approved"
                        elif hasattr(self, 'rejection_statuses') and wp_status in self.rejection_statuses:
                            result_type = "rejected"
                        elif hasattr(self, 'revision_statuses') and wp_status in self.revision_statuses:
                            result_type = "needs_revision"

                        logger.debug(f"Result type determined: {result_type}")

                        # Get resolution/comment if available
                        resolution_comment = data.get("description", {}).get("raw", "")

                        await self.log_info(
                            f"Work package #{wp_id} completed with status: {wp_status} - Result: {result_type}, Elapsed: {round(elapsed_hours, 1)}h"
                        )

                        # Return failed result if rejected, success otherwise
                        if result_type == "rejected":
                            return self._failed_result(
                                f"Work package #{wp_id} was rejected with status: {wp_status}",
                                **{
                                    f"{self.task_id}_work_package_id": wp_id,
                                    f"{self.task_id}_result": result_type,
                                    f"{self.task_id}_status": wp_status,
                                    f"{self.task_id}_resolution": resolution_comment,
                                    f"{self.task_id}_completed_at": datetime.utcnow().isoformat(),
                                    f"{self.task_id}_execution_hours": round(elapsed_hours, 1),
                                    f"{self.task_id}_notification_data": self._openproject_state.get("notification_data", {})
                                }
                            )
                        else:
                            return self._success_result({
                                f"{self.task_id}_work_package_id": wp_id,
                                f"{self.task_id}_result": result_type,
                                f"{self.task_id}_status": wp_status,
                                f"{self.task_id}_resolution": resolution_comment,
                                f"{self.task_id}_completed_at": datetime.utcnow().isoformat(),
                                f"{self.task_id}_execution_hours": round(elapsed_hours, 1),
                                f"{self.task_id}_notification_data": self._openproject_state.get("notification_data", {})
                            })

                    else:
                        # Still in progress
                        await self.log_debug(
                            f"Work package #{wp_id} still {wp_status} - Elapsed: {round(elapsed_hours, 1)}h"
                        )
                        return self._waiting_result(
                            f"Work package is {wp_status} ({round(elapsed_hours, 1)} hours elapsed)",
                            work_package_id=wp_id,
                            status=wp_status
                        )

        except Exception as e:
            logger.error(f"Error checking work package status: {str(e)}", exc_info=True)
            await self.log_error(f"Error checking work package status: {str(e)}")
            return self._waiting_result(
                f"Error checking status: {str(e)[:100]}, will retry",
                work_package_id=wp_id
            )

    def _scan_context_for_files(self, context: Dict[str, Any]) -> List[Dict]:
        """Scan context for files from any previous operator."""
        files = []

        for key in self.context_keys_for_files:
            if key not in context:
                continue

            value = context[key]

            # Handle different formats that operators might use
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        file_info = self._normalize_file_info(item, source_key=key)
                        if file_info:
                            files.append(file_info)
                    elif isinstance(item, str):
                        # Could be URL or path
                        files.append({
                            "type": "url" if item.startswith("http") else "path",
                            "location": item,
                            "source_key": key
                        })

            elif isinstance(value, dict):
                # Could be a result object from an operator
                if "files" in value or "urls" in value or "results" in value:
                    # Recursively scan the dict
                    nested_files = self._scan_context_for_files(value)
                    files.extend(nested_files)
                else:
                    # Might be a single file info
                    file_info = self._normalize_file_info(value, source_key=key)
                    if file_info:
                        files.append(file_info)

        return files

    def _normalize_file_info(self, data: Dict, source_key: str) -> Optional[Dict]:
        """Normalize file info from various operator formats."""
        if "content" in data or "base64" in data:
            return {
                "type": "base64",
                "content": data.get("content") or data.get("base64"),
                "filename": data.get("filename") or data.get("name") or f"file_from_{source_key}",
                "content_type": data.get("content_type") or data.get("mime_type") or "application/octet-stream",
                "source": source_key
            }
        elif "url" in data or "href" in data or "location" in data:
            return {
                "type": "url",
                "location": data.get("url") or data.get("href") or data.get("location"),
                "filename": data.get("filename") or data.get("name") or "document",
                "source": source_key
            }
        elif "path" in data or "file_path" in data:
            return {
                "type": "path",
                "location": data.get("path") or data.get("file_path"),
                "filename": data.get("filename") or os.path.basename(data.get("path", "document")),
                "source": source_key
            }

        return None

    def _generate_subject(self, context: Dict[str, Any]) -> str:
        """Generate work package subject from context."""
        workflow_name = context.get("workflow_name", "Workflow Task")
        instance_id = context.get("instance_id", "Unknown")
        return f"{workflow_name} - {instance_id}"

    def _build_description_from_context(self, context: Dict[str, Any]) -> str:
        """Build description from whatever is in context."""
        parts = [
            f"## Workflow Instance: {context.get('instance_id', 'N/A')}",
            f"**Created:** {datetime.utcnow().isoformat()}",
            ""
        ]

        # Add customer/user info if available
        if "customer_name" in context or "user_name" in context:
            parts.append(f"**Submitted by:** {context.get('customer_name') or context.get('user_name')}")
        if "customer_email" in context or "user_email" in context:
            parts.append(f"**Email:** {context.get('customer_email') or context.get('user_email')}")

        parts.append("")

        # Add specified context keys
        if self.context_keys_for_description:
            parts.append("## Workflow Data")
            for key in self.context_keys_for_description:
                if key in context:
                    value = context[key]
                    parts.append(f"\n### {key.replace('_', ' ').title()}")
                    parts.append(self._format_value_for_description(value))

        # Auto-add validation/approval results from previous operators
        for key in context:
            if any(keyword in key for keyword in ["validation", "approval", "review", "result"]):
                if key not in self.context_keys_for_description:
                    parts.append(f"\n### {key.replace('_', ' ').title()}")
                    parts.append(self._format_value_for_description(context[key]))

        return "\n".join(parts)

    def _format_value_for_description(self, value: Any) -> str:
        """Format any value type for markdown description."""
        if isinstance(value, dict):
            lines = []
            for k, v in value.items():
                if isinstance(v, (dict, list)):
                    lines.append(f"- **{k}:** `{json.dumps(v, indent=2)}`")
                else:
                    lines.append(f"- **{k}:** {v}")
            return "\n".join(lines)
        elif isinstance(value, list):
            return "\n".join([f"- {item}" for item in value])
        else:
            return str(value)

    def _extract_custom_fields(self, context: Dict[str, Any]) -> Dict:
        """Map workflow context to OpenProject custom fields."""
        custom_fields = {}

        for context_key, field_name in self.custom_field_mapping.items():
            if context_key in context:
                value = context[context_key]

                # Format value based on type
                if isinstance(value, bool):
                    formatted_value = "Yes" if value else "No"
                elif isinstance(value, (list, dict)):
                    formatted_value = json.dumps(value)
                else:
                    formatted_value = str(value)

                # OpenProject custom field format
                custom_fields[f"customField{field_name}"] = formatted_value

        return custom_fields

    def _get_priority_id(self) -> int:
        """Map priority name to OpenProject priority ID."""
        priority_map = {
            "Low": 1,
            "Normal": 2,
            "High": 3,
            "Immediate": 4
        }
        return priority_map.get(self.priority, 2)

    async def _upload_file_to_work_package(self, wp_id: int, file_info: Dict) -> Optional[Dict]:
        """Upload file to OpenProject based on normalized file info."""
        try:
            file_content = None
            filename = file_info.get("filename", "document")

            # Get file content based on type
            if file_info["type"] == "base64":
                file_content = base64.b64decode(file_info["content"])
            elif file_info["type"] == "url":
                # Download from URL
                async with aiohttp.ClientSession() as session:
                    async with session.get(file_info["location"]) as response:
                        if response.status == 200:
                            file_content = await response.read()
            elif file_info["type"] == "path":
                # Read from local path
                with open(file_info["location"], "rb") as f:
                    file_content = f.read()

            if not file_content:
                return None

            # Upload to OpenProject
            form_data = aiohttp.FormData()
            metadata = {
                "fileName": filename,
                "description": {
                    "raw": f"Uploaded from workflow step: {file_info.get('source', 'unknown')}"
                }
            }
            form_data.add_field("metadata", json.dumps(metadata), content_type="application/json")
            form_data.add_field(
                "file",
                file_content,
                filename=filename,
                content_type=file_info.get("content_type", "application/octet-stream")
            )

            headers = {"Authorization": f"apikey {self.api_key}"}

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.openproject_url}/api/v3/work_packages/{wp_id}/attachments",
                    data=form_data,
                    headers=headers
                ) as response:
                    if response.status in [200, 201]:
                        data = await response.json()
                        await self.log_info(f"Uploaded file {filename} to work package #{wp_id}")
                        return {
                            "id": data.get("id"),
                            "filename": filename,
                            "source": file_info.get("source"),
                            "uploaded": True
                        }
                    else:
                        error_text = await response.text()
                        await self.log_error(
                            f"Failed to upload file {filename} - HTTP {response.status}: {error_text[:200]}"
                        )

        except Exception as e:
            await self.log_error(f"Error uploading file: {str(e)}")

        return None

    async def _fetch_activities(self, wp_id: int) -> List[Dict]:
        """Fetch all activities/comments for a work package."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.openproject_url}/api/v3/work_packages/{wp_id}/activities",
                    headers=self._get_auth_headers()
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get("_embedded", {}).get("elements", [])
        except Exception as e:
            await self.log_error(f"Error fetching activities: {str(e)}")

        return []

    def _extract_new_comments(self, activities: List[Dict]) -> List[Dict]:
        """Extract comments from activities."""
        comments = []
        last_check = self._openproject_state.get("last_activity_check")

        for activity in activities:
            if activity.get("comment", {}).get("raw"):
                created_at = activity.get("createdAt")
                if not last_check or created_at > last_check:
                    comments.append({
                        "id": activity.get("id"),
                        "author": activity.get("_embedded", {}).get("user", {}).get("name"),
                        "comment": activity.get("comment", {}).get("raw"),
                        "created_at": created_at,
                        "internal": activity.get("internal", False)
                    })
        return comments

    def _extract_assignments(self, activities: List[Dict]) -> List[Dict]:
        """Extract assignment changes from activities."""
        assignments = []
        for activity in activities:
            details = activity.get("details", [])
            for detail in details:
                if detail.get("property") == "assignee":
                    assignments.append({
                        "from": detail.get("old", {}).get("name"),
                        "to": detail.get("new", {}).get("name"),
                        "changed_at": activity.get("createdAt"),
                        "changed_by": activity.get("_embedded", {}).get("user", {}).get("name")
                    })
        return assignments

    def _extract_status_changes(self, activities: List[Dict]) -> List[Dict]:
        """Extract status changes from activities."""
        status_changes = []
        for activity in activities:
            details = activity.get("details", [])
            for detail in details:
                if detail.get("property") == "status":
                    status_changes.append({
                        "from": detail.get("old", {}).get("name"),
                        "to": detail.get("new", {}).get("name"),
                        "changed_at": activity.get("createdAt"),
                        "changed_by": activity.get("_embedded", {}).get("user", {}).get("name")
                    })
        return status_changes