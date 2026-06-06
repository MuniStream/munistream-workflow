"""
Simplified DAG Executor that always uses database as source of truth.
No in-memory instance caching - always fetch fresh from database.
"""
import copy
from typing import Optional
from datetime import datetime
import asyncio
import logging
from enum import Enum

from .dag import InstanceStatus
from .operators.base import TaskStatus
from .polling_strategy import OperatorPollingStrategy
from ..models.workflow import WorkflowInstance, EventType
from .event_manager import WorkflowEventManager
from ..core.config import settings
from ..core.logging_config import set_workflow_context, clear_workflow_context

logger = logging.getLogger(__name__)


# Heuristic threshold above which a string in the context is considered an
# oversized blob and a candidate for stripping. 100 KB is well above any
# reasonable id, URL, JSON payload or transcript we persist legitimately, and
# small enough that the cumulative budget across keys stays clear of the 16 MB
# Mongo BSON cap.
_OVERSIZED_BLOB_THRESHOLD = 100 * 1024

# Magic prefixes (in raw bytes after base64-decoding the start of the string)
# that mark a string as an image/PDF blob. base64 encodes 3 bytes into 4
# characters so we only need a few characters of the string to detect the
# format reliably.
_BASE64_IMAGE_PREFIXES = (
    "/9j/",      # JPEG
    "iVBORw0",   # PNG
    "R0lGOD",    # GIF
    "UklGR",     # WebP
    "JVBERi0",   # PDF
    "data:",     # data URL wrapper
)


def _looks_like_image_base64(value: str) -> bool:
    """Cheap check: does this string start with a known image/PDF base64 prefix?"""
    if len(value) < _OVERSIZED_BLOB_THRESHOLD:
        return False
    head = value.lstrip()[:16]
    return any(head.startswith(prefix) for prefix in _BASE64_IMAGE_PREFIXES)


def _strip_oversized_base64_blobs(node, _depth: int = 0) -> None:
    """Recursively remove oversized image/PDF base64 strings from a context tree.

    Mutates the input in place. Bounded recursion depth keeps us safe against
    pathological structures.
    """
    if _depth > 8 or node is None:
        return

    if isinstance(node, dict):
        for key in list(node.keys()):
            value = node[key]
            if isinstance(value, str) and _looks_like_image_base64(value):
                del node[key]
                continue
            _strip_oversized_base64_blobs(value, _depth + 1)
        return

    if isinstance(node, list):
        for i in range(len(node) - 1, -1, -1):
            value = node[i]
            if isinstance(value, str) and _looks_like_image_base64(value):
                del node[i]
                continue
            _strip_oversized_base64_blobs(value, _depth + 1)


class ExecutorStatus(str, Enum):
    """Executor status"""
    IDLE = "idle"
    RUNNING = "running"
    STOPPED = "stopped"


class DAGExecutor:
    """
    Simple executor that always fetches fresh instances from database.
    No caching, no sync issues - database is the single source of truth.
    """

    def __init__(self, workflow_service=None):
        """
        Initialize executor.

        Args:
            workflow_service: Reference to workflow service for database access
        """
        self.workflow_service = workflow_service
        # Initialize event manager with hook engine that references this workflow service
        from .hook_engine import WorkflowHookEngine
        hook_engine = WorkflowHookEngine(workflow_service=workflow_service)
        self.event_manager = WorkflowEventManager(hook_engine=hook_engine)
        self.status = ExecutorStatus.IDLE
        self.execution_queue: list[str] = []  # Queue of instance IDs to process
        self._execution_task: Optional[asyncio.Task] = None
        self._should_stop = False
        # Event-driven optimization: notify when work is available
        self._work_available = asyncio.Event()
        # Separate queues for different states
        self.active_queue: list[str] = []  # Ready to execute
        self.waiting_queue: dict[str, float] = {}  # Instance ID -> next check time
        # Throttling: prevent continuous execution without delays
        self.throttled_queue: dict[str, float] = {}  # Instance ID -> next allowed execution time
        self._instance_next_execution_time: dict[str, float] = {}  # Track next allowed execution per instance
        # Performance metrics
        self._task_execution_times: dict[str, list[float]] = {}
        self._last_execution_time: dict[str, datetime] = {}
    
    async def start(self):
        """Start the executor"""
        if self.status == ExecutorStatus.RUNNING:
            logger.warning("⚠️ Executor already running")
            return

        self.status = ExecutorStatus.RUNNING
        self._should_stop = False

        # Load incomplete instances from database
        resumed_count = await self.load_incomplete_instances()
        if resumed_count > 0:
            logger.info(f"📥 Loaded {resumed_count} incomplete instances for processing")

        self._execution_task = asyncio.create_task(self._execution_loop())
        logger.info("✅ DAG Executor started - background loop running")
    
    async def load_incomplete_instances(self):
        """Load incomplete instances from database and queue them for processing"""
        from ..models.workflow import WorkflowInstance
        from beanie.operators import In

        try:
            # Find all instances that are not completed, failed, or cancelled
            # Exclude pending_assignment and waiting_for_start (these require manual intervention)
            incomplete_instances = await WorkflowInstance.find(
                In(WorkflowInstance.status, ["pending", "running", "paused"])
            ).to_list()

            queued_count = 0
            for instance in incomplete_instances:
                # Skip instances that need assignment or manual start
                if instance.status in ["pending_assignment", "waiting_for_start"]:
                    continue

                if instance.instance_id not in self.execution_queue:
                    self.execution_queue.append(instance.instance_id)
                    queued_count += 1

            return queued_count

        except Exception as e:
            logger.error(f"Error loading incomplete instances: {e}")
            logger.error(f"❌ Error loading incomplete instances: {e}")
            return 0

    async def stop(self):
        """Stop the executor"""
        self._should_stop = True
        if self._execution_task:
            self._execution_task.cancel()
            try:
                await self._execution_task
            except asyncio.CancelledError:
                pass
        self.status = ExecutorStatus.STOPPED
        logger.info("DAG Executor stopped")
    
    def submit_instance(self, instance_id: str):
        """
        Submit an instance for execution by its ID.
        We'll fetch the actual instance from database when processing.

        Args:
            instance_id: ID of instance to execute
        """
        if instance_id not in self.execution_queue and instance_id not in self.active_queue:
            self.active_queue.append(instance_id)
            # Legacy support - also add to old queue
            if instance_id not in self.execution_queue:
                self.execution_queue.append(instance_id)
            # Signal that work is available (wake up the executor loop)
            self._work_available.set()
            logger.info(f"Instance {instance_id} queued for execution (active queue)")
    
    async def execute_instance(self, instance_id: str) -> bool:
        """
        Execute a single instance by fetching it fresh from database.

        Args:
            instance_id: ID of instance to execute

        Returns:
            True if instance can continue, False if completed/failed
        """
        # Get fresh instance from database
        db_instance = await WorkflowInstance.find_one(
            WorkflowInstance.instance_id == instance_id
        )
        if not db_instance:
            logger.error(f"Instance {instance_id} not found in database")
            return False

        # Set workflow context for all subsequent logging
        set_workflow_context(
            user_id=getattr(db_instance, 'user_id', None) or db_instance.context.get('customer_email'),
            workflow_id=db_instance.workflow_id,
            instance_id=instance_id,
            tenant=getattr(db_instance, 'tenant', None) or db_instance.context.get('tenant')
        )

        # Check for special statuses that should not be executed
        if db_instance.status == "pending_assignment":
            logger.info("Instance is pending assignment, skipping execution",
                       instance_id=instance_id,
                       workflow_type=db_instance.workflow_type.value if db_instance.workflow_type else None)
            return False  # Don't execute until assigned

        if db_instance.status == "waiting_for_start":
            logger.info("Instance is waiting for manual start by assigned user",
                       instance_id=instance_id,
                       assigned_to=db_instance.assigned_user_id or db_instance.assigned_team_id)
            return False  # Don't execute until manually started

        logger.info("🚀 Starting workflow instance execution", extra={
            "workflow_status": db_instance.status,
            "workflow_progress": getattr(db_instance, 'progress', 0)
        })

        # Get DAG instance from bag, or recreate it from database
        dag_instance = self.workflow_service.dag_bag.get_instance(instance_id)
        if not dag_instance:
            # Try to recreate from database
            logger.info(f"🔄 Recreating instance {instance_id} from database")
            dag = self.workflow_service.dag_bag.get_dag(db_instance.workflow_id)
            if not dag:
                logger.error(f"DAG {db_instance.workflow_id} not found for instance {instance_id}")
                return False
            
            # Recreate the DAG instance from database state
            dag_instance = dag.create_instance(db_instance.user_id, db_instance.context)
            dag_instance.instance_id = instance_id  # Keep the original ID
            dag_instance.created_at = db_instance.created_at
            dag_instance.started_at = db_instance.started_at
            
            # Restore task states from database
            dag_instance.completed_tasks = set(db_instance.completed_steps or [])
            dag_instance.failed_tasks = set(db_instance.failed_steps or [])
            dag_instance.skipped_tasks = set(getattr(db_instance, "skipped_steps", None) or [])
            dag_instance.current_task = db_instance.current_step

            # Initialize task states for all tasks. Terminal states
            # (completed/skipped/failed) win over the "current_step waiting"
            # heuristic — a skipped gate must stay skipped, not be
            # resurrected as waiting just because it was the last step the
            # previous save persisted as current.
            for task_id in dag.tasks.keys():
                if task_id in dag_instance.completed_tasks:
                    dag_instance.task_states[task_id] = {"status": "completed"}
                elif task_id in dag_instance.failed_tasks:
                    dag_instance.task_states[task_id] = {"status": "failed"}
                elif task_id in dag_instance.skipped_tasks:
                    dag_instance.task_states[task_id] = {"status": "skipped"}
                elif task_id == db_instance.current_step:
                    dag_instance.task_states[task_id] = {"status": "waiting"}
                else:
                    dag_instance.task_states[task_id] = {"status": "pending"}
            
            # Add to DAG bag for future reference
            self.workflow_service.dag_bag.instances[instance_id] = dag_instance
            logger.info(f"✅ Instance {instance_id} recreated successfully")
        else:
            # Instance exists in memory, but we need to sync task states with database
            dag_instance.completed_tasks = set(db_instance.completed_steps or [])
            dag_instance.failed_tasks = set(db_instance.failed_steps or [])
            dag_instance.skipped_tasks = set(getattr(db_instance, "skipped_steps", None) or [])
            dag_instance.current_task = db_instance.current_step

            # Only force the current step back to "waiting" when it's still
            # pending. Skipping this guard meant a skipped gate task
            # (current_step recorded by the previous save) was mis-marked as
            # waiting, leaving the instance stuck in PAUSED forever.
            if (
                db_instance.current_step
                and db_instance.current_step not in dag_instance.completed_tasks
                and db_instance.current_step not in dag_instance.skipped_tasks
                and db_instance.current_step not in dag_instance.failed_tasks
            ):
                if dag_instance.task_states.get(db_instance.current_step):
                    dag_instance.task_states[db_instance.current_step]["status"] = "waiting"

        # ALWAYS use database context as source of truth
        dag_instance.context = db_instance.context or {}
        logger.debug(f"Loaded context for {instance_id}: {list(dag_instance.context.keys())}")
        
        # Log execution start
        logger.debug(
            "Starting execution cycle",
            extra={
                "instance_id": instance_id,
                "workflow_id": dag_instance.dag.dag_id,
                "status": dag_instance.status.value if hasattr(dag_instance.status, 'value') else str(dag_instance.status),
            },
        )

        # Publish workflow started event if this is the first execution
        if not dag_instance.started_at:
            dag_instance.started_at = datetime.utcnow()
            try:
                await self.event_manager.publish_workflow_started(
                    workflow_id=dag_instance.dag.dag_id,
                    instance_id=instance_id,
                    user_id=dag_instance.user_id,
                    initial_context=dag_instance.context
                )
            except Exception as e:
                # Log error but don't fail workflow execution due to event publishing issues
                logger.warning(f"Failed to publish workflow started event for {instance_id}: {str(e)}")
        
        # Process executable tasks
        executable_tasks = dag_instance.get_executable_tasks()
        logger.info(f"Instance {instance_id} has {len(executable_tasks)} executable tasks: {executable_tasks}")
        
        logger.debug(
            "Found %d executable tasks",
            len(executable_tasks),
            extra={
                "instance_id": instance_id,
                "workflow_id": dag_instance.dag.dag_id,
                "tasks": executable_tasks,
            },
        )

        for task_id in executable_tasks:
            task = dag_instance.dag.tasks.get(task_id)
            if not task:
                continue

            # Check if this task has a retry delay that hasn't expired yet
            if hasattr(task, '_retry_after') and task._retry_after:
                if datetime.utcnow() < task._retry_after:
                    time_remaining = (task._retry_after - datetime.utcnow()).total_seconds()
                    continue  # Skip this task for now
                else:
                    # Clear the retry timestamp, we can proceed
                    task._retry_after = None

            # Snapshot context BEFORE this task starts producing output, so we can rewind to it
            # later if a downstream confirmation step requests editing this task's data.
            # Only snapshot the first time we transition into executing (subsequent re-entries
            # for waiting → continue should not overwrite the snapshot).
            if db_instance.pre_task_context_snapshots is None:
                db_instance.pre_task_context_snapshots = {}
            if task_id not in db_instance.pre_task_context_snapshots:
                snapshot = copy.deepcopy(dag_instance.context)
                # The snapshot is kept so that a downstream confirmation step
                # can rewind to this point. We strip oversized image blobs from
                # it: by the time a rewind happens the binaries already live in
                # S3, and keeping the base64 in every snapshot multiplies a
                # single image's footprint by the number of remaining tasks and
                # blows past Mongo's 16 MB BSON cap.
                _strip_oversized_base64_blobs(snapshot)
                db_instance.pre_task_context_snapshots[task_id] = snapshot

            # Execute task with current context
            dag_instance.update_task_status(task_id, "executing")

            # Run the task

            # Set step context for task execution logging
            set_workflow_context(step=task_id)
            logger.info(f"▶️ Executing workflow step: {task_id}", extra={
                "workflow_step": task_id,
                "workflow_action": "step_started",
                "user_id": getattr(db_instance, 'user_id', None) or db_instance.context.get('customer_email'),
                "workflow_id": db_instance.workflow_id,
                "instance_id": instance_id,
                "tenant": getattr(db_instance, 'tenant', None) or db_instance.context.get('tenant')
            })

            try:
                # Set instance and workflow IDs on the task for logging
                task._instance_id = instance_id
                task._workflow_id = dag_instance.dag.dag_id
                
                # Check if task has an async execute method
                if hasattr(task, 'execute_async'):
                    # Task can handle async operations
                    task_result = await task.execute_async(dag_instance.context)
                    # Store the result on the task for retry delay access
                    task._last_result = task_result
                    # Extract status string and update task's output data
                    result = task_result.status
                    # DEBUG: Log the result mapping
                    logger.info(f"DEBUG: Task {task_id} returned result = '{result}'")
                    if task_result.data:
                        task.state.output_data = task_result.data
                        # Also update dag_instance task_states for tracking endpoint
                        dag_instance.task_states[task_id]["output_data"] = task_result.data
                    # Always sync waiting_for from task state (not just when there's data)
                    if hasattr(task, 'state') and hasattr(task.state, 'waiting_for') and task.state.waiting_for:
                        dag_instance.task_states[task_id]["waiting_for"] = task.state.waiting_for
                else:
                    # Regular synchronous execution
                    result = task.run(dag_instance.context)
                    # Note: for sync tasks, _last_result is already set in the run() method


                # DEBUG: Log result handling
                logger.info(f"DEBUG: Task {task_id} returned result = '{result}'")

                logger.info(
                    "Task %s completed with status: %s",
                    task_id, result,
                    extra={
                        "instance_id": instance_id,
                        "workflow_id": dag_instance.dag.dag_id,
                        "task_id": task_id,
                        "result": str(result),
                    },
                )
            except Exception as e:

                logger.error(f"❌ Step failed: {task_id}", extra={
                    "workflow_step": task_id,
                    "workflow_action": "step_failed",
                    "error_message": str(e),
                    "error_type": type(e).__name__,
                    "user_id": getattr(db_instance, 'user_id', None) or db_instance.context.get('customer_email'),
                    "workflow_id": db_instance.workflow_id,
                    "instance_id": instance_id,
                    "tenant": getattr(db_instance, 'tenant', None) or db_instance.context.get('tenant')
                })

                result = TaskStatus.FAILED
            
            # Update task status based on result
            # DEBUG: Log result handling
            logger.info(f"DEBUG: Handling result '{result}' for task {task_id}")
            if result == TaskStatus.CONTINUE:
                dag_instance.update_task_status(task_id, "completed")
                # Update context with task output - this is critical for data flow
                output = task.get_output()
                if output:
                    dag_instance.context.update(output)
                    logger.debug(f"Task {task_id} added to context: {list(output.keys())}")
            elif result == TaskStatus.WAITING:
                dag_instance.update_task_status(task_id, "waiting")
                # CRITICAL: Save task output/context even when waiting (for state tracking)
                output = task.get_output()
                if output:
                    dag_instance.context.update(output)

                # Ensure output_data is available in task_states for tracking endpoint
                if hasattr(task, 'state') and task.state.output_data:
                    dag_instance.task_states[task_id]["output_data"] = task.state.output_data
                # Sync waiting_for from task state
                if hasattr(task, 'state') and hasattr(task.state, 'waiting_for') and task.state.waiting_for:
                    dag_instance.task_states[task_id]["waiting_for"] = task.state.waiting_for
                # Schedule re-check after a delay for polling
                # The instance will be re-queued in the main loop since it's PAUSED
                break  # Stop processing for now, will resume via polling
            elif result == TaskStatus.FAILED:
                print(f"[EXECUTOR] TASK FAILED: {task_id} in instance {instance_id}")
                if hasattr(task, '_last_result') and task._last_result:
                    print(f"[EXECUTOR] Task failure data: {task._last_result.data}")
                    print(f"[EXECUTOR] Task failure error: {getattr(task._last_result, 'error', 'No error message')}")
                dag_instance.update_task_status(task_id, "failed")
                break  # Stop processing, task failed
            elif result == TaskStatus.SKIP:
                # Short-circuit: this task and its skip-only descendants drop out
                # of the run. Downstream tasks that also have a completed upstream
                # still execute (convergence), so this implements Airflow-like
                # trigger semantics for ShortCircuitOperator branching.
                dag_instance.update_task_status(task_id, "skipped")
                newly_skipped = dag_instance.propagate_skips()
                if newly_skipped:
                    logger.info(
                        f"Cascaded skip from {task_id} to descendants: {sorted(newly_skipped)}",
                        extra={"instance_id": instance_id, "source_task": task_id},
                    )
            elif result == TaskStatus.RETRY:
                # Handle workflow recovery logic if next_task is specified
                if hasattr(task, '_last_result') and task._last_result and hasattr(task._last_result, 'next_task') and task._last_result.next_task:
                    next_task_id = task._last_result.next_task
                    recovery_data = task._last_result.data or {}
                    clear_tasks = recovery_data.get("clear_tasks", [])
                    recovery_action = recovery_data.get("recovery_action", "unknown")

                    logger.info(f"Handling workflow recovery: {recovery_action} - instance: {instance_id}, current_task: {task_id}, next_task: {next_task_id}")

                    # Execute complete retry workflow reset
                    success = await self._execute_retry_workflow_reset(
                        dag_instance, db_instance, instance_id,
                        next_task_id, task_id, recovery_data, recovery_action
                    )

                    if success:
                        logger.info(f"Breaking execution loop to restart from {next_task_id}")
                        return await self.execute_instance(instance_id)
                    else:
                        logger.error(f"Invalid next_task specified: {next_task_id} - instance: {instance_id}, available_tasks: {list(dag_instance.dag.tasks.keys())}")

                else:
                    # Standard retry logic
                    # Don't mark as completed, keep it ready for retry
                    dag_instance.update_task_status(task_id, "pending")
                    # Clear from completed tasks if it was there
                    if task_id in dag_instance.completed_tasks:
                        dag_instance.completed_tasks.remove(task_id)

                    # Get retry delay from task result if available
                    retry_delay = 5  # Default delay (seconds)
                    if hasattr(task, '_last_result') and task._last_result:
                        if hasattr(task._last_result, 'retry_delay') and task._last_result.retry_delay:
                            retry_delay = task._last_result.retry_delay

                    # Set the retry timestamp on the task
                    from datetime import timedelta
                    task._retry_after = datetime.utcnow() + timedelta(seconds=retry_delay)
                    # Don't break - instance will continue to be re-queued and checked
            else:
                dag_instance.update_task_status(task_id, "completed")
        
        # Determine instance status based on task states
        old_instance_status = dag_instance.status
        if dag_instance.is_completed():
            dag_instance.status = InstanceStatus.COMPLETED
            dag_instance.completed_at = datetime.utcnow()

            # Publish workflow completed event
            if old_instance_status != InstanceStatus.COMPLETED:
                try:
                    await self.event_manager.publish_workflow_completed(
                        workflow_id=dag_instance.dag.dag_id,
                        instance_id=instance_id,
                        user_id=dag_instance.user_id,
                        final_context=dag_instance.context
                    )
                except Exception as e:
                    # Log error but don't fail workflow completion due to event publishing issues
                    logger.warning(f"Failed to publish workflow completed event for {instance_id}: {str(e)}")

        elif dag_instance.has_failed():
            dag_instance.status = InstanceStatus.FAILED
            dag_instance.completed_at = datetime.utcnow()

            # Publish workflow failed event
            if old_instance_status != InstanceStatus.FAILED:
                failed_task = next(iter(dag_instance.failed_tasks), None)
                try:
                    await self.event_manager.publish_workflow_failed(
                        workflow_id=dag_instance.dag.dag_id,
                        instance_id=instance_id,
                        user_id=dag_instance.user_id,
                        error_message="Workflow failed during execution",
                        failed_step=failed_task,
                        context=dag_instance.context
                    )
                except Exception as e:
                    # Log error but don't fail workflow processing due to event publishing issues
                    logger.warning(f"Failed to publish workflow failed event for {instance_id}: {str(e)}")

        elif self._has_waiting_tasks(dag_instance):
            dag_instance.status = InstanceStatus.PAUSED
        else:
            dag_instance.status = InstanceStatus.RUNNING

        # If this instance just transitioned to a terminal state and it is a
        # child workflow, wake the parent so it can advance past its
        # WorkflowStartOperator without polling.
        terminal_states = (
            InstanceStatus.COMPLETED, InstanceStatus.FAILED, InstanceStatus.CANCELLED
        )
        if (
            old_instance_status != dag_instance.status
            and dag_instance.status in terminal_states
        ):
            parent_id = (
                getattr(db_instance, "parent_instance_id", None)
                or dag_instance.context.get("parent_instance_id")
            )
            if parent_id:
                logger.info(
                    "Child workflow %s reached %s; resuming parent %s",
                    instance_id, dag_instance.status.value, parent_id,
                )
                try:
                    self.resume_instance(parent_id)
                except Exception as e:
                    logger.warning(
                        "Failed to resume parent %s from child %s: %s",
                        parent_id, instance_id, e,
                    )

        # Save everything back to database
        old_status = db_instance.status
        new_status = self._map_status(dag_instance.status)

        await self._save_instance_state(dag_instance, db_instance, instance_id, new_status)
        
        # Log status change
        if old_status != new_status:
            logger.info(
                "Instance status changed from %s to %s",
                old_status, new_status,
                extra={
                    "instance_id": instance_id,
                    "workflow_id": dag_instance.dag.dag_id,
                    "old_status": str(old_status),
                    "new_status": str(new_status),
                    "completed_tasks": list(dag_instance.completed_tasks),
                    "failed_tasks": list(dag_instance.failed_tasks),
                },
            )
        
        # Return whether instance can continue processing.
        # RUNNING -> more executable tasks; the loop will keep advancing.
        # PAUSED  -> at least one waiting task. The scheduling loop inspects
        # each waiting operator's PollingConfig to decide whether to re-queue
        # for polling or sit idle until resume_instance() is called.
        return dag_instance.status in [InstanceStatus.RUNNING, InstanceStatus.PAUSED]

    def _has_waiting_tasks(self, instance) -> bool:
        """Check if instance has any waiting tasks"""
        for state in instance.task_states.values():
            if state.get("status") in ["waiting", "waiting_input", "waiting_approval"]:
                return True
        return False

    def _get_instance_polling_decision(self, dag_instance) -> Optional[float]:
        """Decide when to wake this PAUSED instance again.

        Returns:
            None  -> No waiting task needs polling. The instance will only be
                    revived by resume_instance() (or by the safety net sweep).
            float -> Seconds until the next wake-up. Equals the minimum
                    polling interval declared across all POLLING waiting
                    tasks of the instance.
        """
        waiting_intervals = []
        for task_id, state in dag_instance.task_states.items():
            if state.get("status") not in ("waiting", "waiting_input", "waiting_approval"):
                continue
            task = dag_instance.dag.tasks.get(task_id)
            if task is None:
                continue
            cfg = task.get_polling_config()
            if cfg.strategy == OperatorPollingStrategy.POLLING:
                waiting_intervals.append(cfg.polling_interval_seconds)
        if not waiting_intervals:
            return None
        return float(min(waiting_intervals))

    def _schedule_next_wakeup(self, instance_id: str) -> None:
        """Decide where (and when) to re-queue an instance after it executed.

        RUNNING instances get a short throttle to avoid hot-looping inside
        a single tick. PAUSED instances are routed by their waiting tasks'
        PollingConfig:
          - All EVENT_DRIVEN -> wait for resume_instance(); safety net
                                catches orphans every EXECUTOR_SAFETY_NET_SECONDS.
          - Any POLLING       -> wake up at the minimum declared interval.
        """
        import time as _time
        dag_instance = self.workflow_service.dag_bag.get_instance(instance_id)
        is_paused = (
            dag_instance is not None
            and dag_instance.status == InstanceStatus.PAUSED
        )

        if not is_paused:
            throttle = settings.EXECUTOR_RUNNING_THROTTLE_SECONDS
            next_exec_time = _time.time() + throttle
            self._instance_next_execution_time[instance_id] = next_exec_time
            self.throttled_queue[instance_id] = next_exec_time
            return

        poll_delay = self._get_instance_polling_decision(dag_instance)
        if poll_delay is None:
            safety_net = settings.EXECUTOR_SAFETY_NET_SECONDS
            if safety_net > 0:
                self.waiting_queue[instance_id] = _time.time() + safety_net
                logger.debug(
                    "Instance %s event-driven; awaiting resume_instance "
                    "(safety net %ds)",
                    instance_id, safety_net,
                )
            else:
                logger.debug(
                    "Instance %s event-driven; awaiting resume_instance (no safety net)",
                    instance_id,
                )
            return

        self.waiting_queue[instance_id] = _time.time() + poll_delay
        logger.debug(
            "Instance %s scheduled to poll in %.1fs", instance_id, poll_delay
        )
    
    def _map_status(self, dag_status) -> str:
        """Map DAG status to database status string"""
        mapping = {
            InstanceStatus.PENDING: "pending",
            InstanceStatus.RUNNING: "running",
            InstanceStatus.PAUSED: "paused",
            InstanceStatus.COMPLETED: "completed",
            InstanceStatus.FAILED: "failed",
            InstanceStatus.CANCELLED: "cancelled"
        }
        return mapping.get(dag_status, "running")

    async def _save_instance_state(self, dag_instance, db_instance, instance_id, new_status=None):
        """Save instance state to database"""
        # Ensure instance_id and workflow_id are always saved in context
        dag_instance.context["instance_id"] = instance_id
        dag_instance.context["workflow_id"] = dag_instance.dag.dag_id

        previous_step = db_instance.current_step
        new_step = dag_instance.current_task

        # Defensive sweep: capture/upload operators have a long history of
        # leaving multiple aliases of the same base64 blob in the context
        # (raw _input payload, _<image>.content, captured_document.*, etc.).
        # Cumulatively those copies can push a single instance past Mongo's
        # 16 MB BSON cap, after which findAndModify fails and the instance
        # gets stuck. Strip image/PDF base64 strings before every save so
        # we never depend on every operator remembering to clean up.
        _strip_oversized_base64_blobs(dag_instance.context)
        if db_instance.pre_task_context_snapshots:
            for snapshot in db_instance.pre_task_context_snapshots.values():
                _strip_oversized_base64_blobs(snapshot)

        db_instance.context = dag_instance.context
        db_instance.current_step = new_step
        db_instance.completed_steps = list(dag_instance.completed_tasks)
        db_instance.failed_steps = list(dag_instance.failed_tasks)
        db_instance.skipped_steps = list(dag_instance.skipped_tasks)
        db_instance.updated_at = datetime.utcnow()

        if new_status:
            db_instance.status = new_status

        if dag_instance.completed_at:
            db_instance.completed_at = dag_instance.completed_at

        await db_instance.save()

        if new_step and new_step != previous_step:
            try:
                await self.event_manager.publish_event(
                    event_type=EventType.STEP_ADVANCED,
                    workflow_id=db_instance.workflow_id,
                    instance_id=instance_id,
                    user_id=db_instance.user_id,
                    event_data={
                        "previous_step": previous_step,
                        "current_step": new_step,
                    },
                )
            except Exception:
                logger.exception(
                    "Failed to publish STEP_ADVANCED for instance %s", instance_id
                )

    async def _execute_retry_workflow_reset(self, dag_instance, db_instance, instance_id,
                                          next_task_id, failed_task_id, recovery_data, recovery_action):
        """
        Execute complete workflow retry reset procedure.

        Returns:
            bool: True if reset successful, False if next_task is invalid
        """
        if next_task_id not in dag_instance.dag.tasks:
            return False

        clear_tasks = recovery_data.get("clear_tasks", [])

        # Clear specified tasks from completed/skipped to allow re-execution
        for clear_task in clear_tasks:
            if clear_task in dag_instance.completed_tasks:
                dag_instance.completed_tasks.remove(clear_task)
                logger.debug(f"Cleared task {clear_task} from completed tasks")
            if clear_task in dag_instance.skipped_tasks:
                dag_instance.skipped_tasks.remove(clear_task)
                logger.debug(f"Cleared task {clear_task} from skipped tasks")

            # Reset task state to pending
            if clear_task in dag_instance.task_states:
                dag_instance.task_states[clear_task] = {
                    "status": "pending",
                    "started_at": None,
                    "completed_at": None,
                    "result": None,
                    "error": None
                }

            # Reset the operator if it exists
            if clear_task in dag_instance.dag.tasks:
                task_operator = dag_instance.dag.tasks[clear_task]
                if hasattr(task_operator, 'reset'):
                    task_operator.reset()
                    logger.debug(f"Reset operator state for task {clear_task}")

        # Setup next_task as current task
        dag_instance.current_task = next_task_id
        dag_instance.task_states[next_task_id] = {
            "status": "pending",
            "started_at": None,
            "completed_at": None,
            "result": None,
            "error": None
        }

        # Remove next_task from completed, failed, and skipped tasks
        if next_task_id in dag_instance.completed_tasks:
            dag_instance.completed_tasks.remove(next_task_id)
        if next_task_id in dag_instance.failed_tasks:
            dag_instance.failed_tasks.remove(next_task_id)
        if next_task_id in dag_instance.skipped_tasks:
            dag_instance.skipped_tasks.remove(next_task_id)

        # Preserve retry context history
        if "retries" not in dag_instance.context:
            dag_instance.context["retries"] = []

        # Create retry snapshot
        retry_entry = {
            "attempt": len(dag_instance.context["retries"]) + 1,
            "timestamp": datetime.utcnow().isoformat(),
            "failed_task": failed_task_id,
            "next_task": next_task_id,
            "reason": recovery_data.get("message", "Retry initiated"),
            "recovery_action": recovery_action,
            "context_snapshot": {k: v for k, v in dag_instance.context.items() if k != "retries"}
        }
        dag_instance.context["retries"].append(retry_entry)

        # Update context with recovery information
        dag_instance.context.update(recovery_data)

        logger.info(f"Workflow recovery completed: jumping to {next_task_id} - instance: {instance_id}, recovery_action: {recovery_action}")

        # Configure upstream dependencies as completed
        if hasattr(dag_instance.dag, 'graph'):
            upstream_tasks = list(dag_instance.dag.graph.predecessors(next_task_id))
            for upstream in upstream_tasks:
                if upstream not in dag_instance.completed_tasks:
                    dag_instance.completed_tasks.add(upstream)
                    if upstream in dag_instance.task_states:
                        dag_instance.task_states[upstream]["status"] = "completed"
                        dag_instance.task_states[upstream]["completed_at"] = datetime.utcnow()

            # Reset ALL downstream tasks reachable from next_task
            import networkx as nx
            all_downstream_tasks = set(nx.descendants(dag_instance.dag.graph, next_task_id))
            all_downstream_tasks.add(next_task_id)  # Include next_task itself

            for task_to_reset in all_downstream_tasks:
                # Remove from completed/failed/skipped sets
                if task_to_reset in dag_instance.completed_tasks:
                    dag_instance.completed_tasks.remove(task_to_reset)
                if task_to_reset in dag_instance.failed_tasks:
                    dag_instance.failed_tasks.remove(task_to_reset)
                if task_to_reset in dag_instance.skipped_tasks:
                    dag_instance.skipped_tasks.remove(task_to_reset)

                # Reset task state
                dag_instance.task_states[task_to_reset] = {
                    "status": "pending",
                    "started_at": None,
                    "completed_at": None,
                    "result": None,
                    "error": None
                }

            # Clean context keys for ALL tasks being reset
            # Limpiar cualquier clave que contenga cualquier tarea que estamos reseteando
            context_keys_to_remove = [
                key for key in dag_instance.context.keys()
                if any(task in key for task in all_downstream_tasks)
            ]

            # Also clean WorkflowStartOperator global context keys
            workflow_start_global_keys = [
                "child_instance_id", "child_workflow_id", "child_status",
                "waiting_for", "child_failure_count", "completed_at", "message"
            ]
            for key in workflow_start_global_keys:
                if key in dag_instance.context:
                    context_keys_to_remove.append(key)

            for key in context_keys_to_remove:
                del dag_instance.context[key]

        # Save state to database
        await self._save_instance_state(dag_instance, db_instance, instance_id)
        return True

    async def _execution_loop(self):
        """Background execution loop - process queue continuously"""
        logger.info("🔄 Execution loop started (optimized with event-driven approach)")
        import time

        while not self._should_stop:
            try:
                current_time = time.time()

                # First, check throttled queue for instances that can now execute
                if self.throttled_queue:
                    ready_from_throttle = [
                        inst_id for inst_id, next_time in self.throttled_queue.items()
                        if next_time <= current_time
                    ]
                    for instance_id in ready_from_throttle:
                        del self.throttled_queue[instance_id]
                        # Add to front of active queue for immediate processing
                        self.active_queue.insert(0, instance_id)
                        logger.debug(f"Instance {instance_id} ready after throttle delay")

                # Check active queue for work
                if self.active_queue:
                    instance_id = self.active_queue.pop(0)

                    # Check if this instance is throttled (needs to wait before next execution)
                    if instance_id in self._instance_next_execution_time:
                        next_allowed_time = self._instance_next_execution_time[instance_id]
                        if current_time < next_allowed_time:
                            # Not ready yet, add to throttled queue
                            self.throttled_queue[instance_id] = next_allowed_time
                            time_to_wait = next_allowed_time - current_time
                            logger.info(f"⏳ Throttling {instance_id}, wait {time_to_wait:.1f}s more")
                            continue  # Skip this instance for now
                    else:
                        logger.debug(f"First execution of {instance_id}, no throttling yet")

                    # Remove from legacy queue too
                    if instance_id in self.execution_queue:
                        self.execution_queue.remove(instance_id)

                    # Track execution time for metrics
                    start_time = time.time()

                    # Execute instance
                    can_continue = await self.execute_instance(instance_id)

                    # Log execution time
                    execution_time = time.time() - start_time
                    if instance_id not in self._task_execution_times:
                        self._task_execution_times[instance_id] = []
                    self._task_execution_times[instance_id].append(execution_time)
                    self._last_execution_time[instance_id] = datetime.utcnow()

                    if execution_time > 1.0:  # Log slow executions
                        logger.warning(f"⚠️ Slow execution detected for {instance_id}: {execution_time:.2f}s")

                    # Re-queue based on the instance's current scheduling profile.
                    if can_continue:
                        self._schedule_next_wakeup(instance_id)

                # Check waiting queue for instances ready to retry
                elif self.waiting_queue:
                    ready_instances = [
                        inst_id for inst_id, check_time in self.waiting_queue.items()
                        if check_time <= current_time
                    ]

                    if ready_instances:
                        # Move ready instances to throttled or active queue based on their throttle status
                        for instance_id in ready_instances:
                            del self.waiting_queue[instance_id]

                            # Check if instance is still throttled
                            if instance_id in self._instance_next_execution_time:
                                next_allowed = self._instance_next_execution_time[instance_id]
                                if current_time < next_allowed:
                                    # Still throttled, add to throttled queue
                                    self.throttled_queue[instance_id] = next_allowed
                                    logger.debug(f"Moving {instance_id} from waiting to throttled queue")
                                    continue

                            # Not throttled, can execute immediately
                            self.active_queue.append(instance_id)
                            self.execution_queue.append(instance_id)
                            logger.debug(f"Moving {instance_id} from waiting to active queue")
                        # Signal work available if we added to active queue
                        if self.active_queue:
                            self._work_available.set()
                    else:
                        # No instance ready yet. Sleep until the earliest one is
                        # due, or until resume_instance() signals new work.
                        next_due = min(self.waiting_queue.values())
                        sleep_for = max(0.05, min(next_due - current_time, 1.0))
                        try:
                            await asyncio.wait_for(
                                self._work_available.wait(), timeout=sleep_for
                            )
                            self._work_available.clear()
                        except asyncio.TimeoutError:
                            pass

                # Fallback to legacy queue processing
                elif self.execution_queue:
                    instance_id = self.execution_queue.pop(0)

                    # Check throttling for legacy queue too
                    if instance_id in self._instance_next_execution_time:
                        next_allowed_time = self._instance_next_execution_time[instance_id]
                        if current_time < next_allowed_time:
                            # Not ready yet, add to throttled queue
                            self.throttled_queue[instance_id] = next_allowed_time
                            time_to_wait = next_allowed_time - current_time
                            logger.info(f"⏳ Throttling {instance_id} from legacy queue, wait {time_to_wait:.1f}s more")
                            continue  # Skip this instance for now

                    # Execute
                    start_time = time.time()
                    can_continue = await self.execute_instance(instance_id)
                    execution_time = time.time() - start_time

                    if can_continue:
                        self._schedule_next_wakeup(instance_id)

                # Check if there are throttled instances waiting
                elif self.throttled_queue:
                    # No sleep - just wait for event with timeout
                    try:
                        await asyncio.wait_for(
                            self._work_available.wait(),
                            timeout=0.05  # 50ms timeout while throttled instances are waiting
                        )
                        # Clear the event for next wait
                        self._work_available.clear()
                    except asyncio.TimeoutError:
                        # Normal - continue to check throttled queue
                        pass

                else:
                    # No work available - wait for signal or check periodically
                    try:
                        await asyncio.wait_for(
                            self._work_available.wait(),
                            timeout=0.1  # 100ms timeout when truly idle
                        )
                        # Clear the event for next wait
                        self._work_available.clear()
                    except asyncio.TimeoutError:
                        # Normal - just continue checking
                        pass

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Execution loop error: {str(e)}")
                # On error, add small delay but don't block for long
                await asyncio.sleep(0.1)
    
    def resume_instance(self, instance_id: str):
        """
        Resume a paused instance: drop any pending wait timers and prioritize
        the instance at the front of the active queue. The execution loop wakes
        up via _work_available.set() and runs it on the next tick.

        We deliberately do NOT spawn an extra asyncio task to execute right
        now — the loop is already running and will process the instance
        immediately. A parallel immediate task creates a race where two
        executions of the same instance can clobber each other's Mongo writes.
        """
        logger.info(f"Resume requested for instance {instance_id}")

        # Drop any pending wait timers; this instance has new work.
        if instance_id in self.waiting_queue:
            del self.waiting_queue[instance_id]
            logger.debug(f"Removed {instance_id} from waiting queue")
        if instance_id in self.throttled_queue:
            del self.throttled_queue[instance_id]
            logger.debug(f"Removed {instance_id} from throttled queue")
        # Resume bypasses the RUNNING throttle: clear any pending deadline so
        # the active-queue branch executes this instance straight away.
        self._instance_next_execution_time.pop(instance_id, None)

        if instance_id not in self.active_queue:
            self.active_queue.insert(0, instance_id)
            if instance_id not in self.execution_queue:
                self.execution_queue.insert(0, instance_id)
            logger.info(f"Instance {instance_id} prioritized in active queue")
        else:
            logger.info(f"Instance {instance_id} already in active queue")

        self._work_available.set()

    async def _execute_immediate(self, instance_id: str):
        """Execute an instance immediately without waiting for the loop"""
        try:
            logger.info(f"Executing instance {instance_id} immediately")
            can_continue = await self.execute_instance(instance_id)

            # If instance needs to continue but isn't in queue, add it
            if can_continue and instance_id not in self.execution_queue:
                self.execution_queue.append(instance_id)

        except Exception as e:
            logger.error(f"Error in immediate execution of {instance_id}: {e}")
    
    def cancel_instance(self, instance_id: str):
        """
        Cancel an instance.
        
        Args:
            instance_id: Instance to cancel
        """
        # Remove from queue if present
        if instance_id in self.execution_queue:
            self.execution_queue.remove(instance_id)
        
        # Mark as cancelled in database
        asyncio.create_task(self._mark_cancelled(instance_id))
        logger.info(f"Instance {instance_id} cancelled")
    
    async def _mark_cancelled(self, instance_id: str):
        """Mark instance as cancelled in database"""
        from ..models.workflow import WorkflowInstance
        db_instance = await WorkflowInstance.find_one(
            WorkflowInstance.instance_id == instance_id
        )
        if db_instance:
            db_instance.status = "cancelled"
            db_instance.completed_at = datetime.utcnow()
            await db_instance.save()
    
    def get_stats(self):
        """Get executor statistics with performance metrics"""
        import statistics

        # Calculate average execution times
        avg_times = {}
        for instance_id, times in self._task_execution_times.items():
            if times:
                avg_times[instance_id] = {
                    "avg": statistics.mean(times),
                    "max": max(times),
                    "min": min(times),
                    "count": len(times)
                }

        # Sort by average time (slowest first)
        slow_instances = sorted(
            avg_times.items(),
            key=lambda x: x[1]["avg"],
            reverse=True
        )[:5]  # Top 5 slowest

        # Calculate throttled instances and their wait times
        import time
        current_time = time.time()
        throttled_info = {}
        for instance_id, next_time in self.throttled_queue.items():
            wait_time = max(0, next_time - current_time)
            throttled_info[instance_id] = f"{wait_time:.1f}s"

        return {
            "status": self.status.value,
            "queued_instances": len(self.execution_queue),
            "active_queue_size": len(self.active_queue),
            "waiting_queue_size": len(self.waiting_queue),
            "throttled_queue_size": len(self.throttled_queue),
            "queue": self.execution_queue[:10],  # Show first 10 in queue
            "active_queue": self.active_queue[:10],
            "waiting_instances": list(self.waiting_queue.keys())[:10],
            "throttled_instances": throttled_info,
            "running_throttle_seconds": settings.EXECUTOR_RUNNING_THROTTLE_SECONDS,
            "safety_net_seconds": settings.EXECUTOR_SAFETY_NET_SECONDS,
            "performance_metrics": {
                "slow_instances": dict(slow_instances),
                "total_instances_tracked": len(self._task_execution_times)
            },
            "optimizations": {
                "event_driven_scheduling": True,
                "separate_queues": True,
                "running_throttle_seconds": settings.EXECUTOR_RUNNING_THROTTLE_SECONDS,
                "paused_safety_net_seconds": settings.EXECUTOR_SAFETY_NET_SECONDS,
                "non_blocking": "No sleeps, timestamp-based checks"
            }
        }