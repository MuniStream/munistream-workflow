"""
Simplified DAG Executor that always uses database as source of truth.
No in-memory instance caching - always fetch fresh from database.
"""
from typing import Optional
from datetime import datetime
import asyncio
import logging
from enum import Enum

from .dag import InstanceStatus
from ..models.instance_log import InstanceLog, LogLevel, LogType
from ..models.workflow import WorkflowInstance, EventType
from .event_manager import WorkflowEventManager
from ..core.logging_config import set_workflow_context, clear_workflow_context

logger = logging.getLogger(__name__)


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
        self.THROTTLE_DELAY_SECONDS = 3.0  # Minimum seconds between executions per instance
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
            dag_instance.current_task = db_instance.current_step
            
            # Initialize task states for all tasks
            for task_id in dag.tasks.keys():
                if task_id == db_instance.current_step and task_id not in dag_instance.completed_tasks:
                    # Current task that's not completed must be waiting
                    dag_instance.task_states[task_id] = {"status": "waiting"}
                elif task_id in dag_instance.completed_tasks:
                    dag_instance.task_states[task_id] = {"status": "completed"}
                elif task_id in dag_instance.failed_tasks:
                    dag_instance.task_states[task_id] = {"status": "failed"}
                else:
                    dag_instance.task_states[task_id] = {"status": "pending"}
            
            # Add to DAG bag for future reference
            self.workflow_service.dag_bag.instances[instance_id] = dag_instance
            logger.info(f"✅ Instance {instance_id} recreated successfully")
        else:
            # Instance exists in memory, but we need to sync task states with database
            dag_instance.completed_tasks = set(db_instance.completed_steps or [])
            dag_instance.failed_tasks = set(db_instance.failed_steps or [])
            dag_instance.current_task = db_instance.current_step

            # Update task states for waiting tasks
            if db_instance.current_step and db_instance.current_step not in dag_instance.completed_tasks:
                if dag_instance.task_states.get(db_instance.current_step):
                    dag_instance.task_states[db_instance.current_step]["status"] = "waiting"

        # ALWAYS use database context as source of truth
        dag_instance.context = db_instance.context or {}
        logger.debug(f"Loaded context for {instance_id}: {list(dag_instance.context.keys())}")
        
        # Log execution start
        await InstanceLog.log(
            instance_id=instance_id,
            workflow_id=dag_instance.dag.dag_id,
            level=LogLevel.INFO,
            log_type=LogType.SYSTEM,
            message=f"Starting execution cycle",
            details={"status": dag_instance.status.value if hasattr(dag_instance.status, 'value') else str(dag_instance.status)}
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
        
        await InstanceLog.log(
            instance_id=instance_id,
            workflow_id=dag_instance.dag.dag_id,
            level=LogLevel.DEBUG,
            log_type=LogType.SYSTEM,
            message=f"Found {len(executable_tasks)} executable tasks",
            details={"tasks": executable_tasks}
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

            await InstanceLog.log(
                instance_id=instance_id,
                workflow_id=dag_instance.dag.dag_id,
                level=LogLevel.INFO,
                log_type=LogType.TASK_START,
                task_id=task_id,
                message=f"Starting task execution: {task_id}"
            )
            
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

                await InstanceLog.log(
                    instance_id=instance_id,
                    workflow_id=dag_instance.dag.dag_id,
                    level=LogLevel.INFO,
                    log_type=LogType.TASK_COMPLETE if result == "continue" else LogType.TASK_FAILED,
                    task_id=task_id,
                    message=f"Task completed with status: {result}",
                    details={"result": result, "output": task.get_output() if hasattr(task, 'get_output') else None}
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

                await InstanceLog.log(
                    instance_id=instance_id,
                    workflow_id=dag_instance.dag.dag_id,
                    level=LogLevel.ERROR,
                    log_type=LogType.ERROR,
                    task_id=task_id,
                    message=f"Task execution failed",
                    error=e
                )
                result = "failed"
            
            # Update task status based on result
            # DEBUG: Log result handling
            logger.info(f"DEBUG: Handling result '{result}' for task {task_id}")
            if result == "continue":
                dag_instance.update_task_status(task_id, "completed")
                # Update context with task output - this is critical for data flow
                output = task.get_output()
                if output:
                    dag_instance.context.update(output)
                    logger.debug(f"Task {task_id} added to context: {list(output.keys())}")
            elif result == "waiting":
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
            elif result == "failed":
                dag_instance.update_task_status(task_id, "failed")
                break  # Stop processing, task failed
            elif result == "retry":
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
        
        # Save everything back to database
        old_status = db_instance.status
        new_status = self._map_status(dag_instance.status)

        # Ensure instance_id and workflow_id are always saved in context
        dag_instance.context["instance_id"] = instance_id
        dag_instance.context["workflow_id"] = dag_instance.dag.dag_id

        db_instance.status = new_status
        db_instance.context = dag_instance.context
        db_instance.current_step = dag_instance.current_task
        db_instance.completed_steps = list(dag_instance.completed_tasks)
        db_instance.failed_steps = list(dag_instance.failed_tasks)
        db_instance.updated_at = datetime.utcnow()
        
        if dag_instance.completed_at:
            db_instance.completed_at = dag_instance.completed_at
        
        await db_instance.save()
        
        # Log status change
        if old_status != new_status:
            await InstanceLog.log(
                instance_id=instance_id,
                workflow_id=dag_instance.dag.dag_id,
                level=LogLevel.INFO,
                log_type=LogType.STATUS_CHANGE,
                message=f"Instance status changed from {old_status} to {new_status}",
                details={
                    "old_status": old_status,
                    "new_status": new_status,
                    "completed_tasks": list(dag_instance.completed_tasks),
                    "failed_tasks": list(dag_instance.failed_tasks)
                }
            )
        
        # Return whether instance can continue processing
        # PAUSED instances need to continue for polling waiting tasks
        return dag_instance.status in [InstanceStatus.RUNNING, InstanceStatus.PAUSED]
    
    def _has_waiting_tasks(self, instance) -> bool:
        """Check if instance has any waiting tasks"""
        for state in instance.task_states.values():
            if state.get("status") in ["waiting", "waiting_input", "waiting_approval"]:
                return True
        return False
    
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
    
    async def _execution_loop(self):
        """Background execution loop - process queue continuously"""
        logger.info("🔄 Execution loop started (optimized with event-driven approach)")
        import time
        import random

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

                    # Set next allowed execution time with random delay (2-5 seconds)
                    random_delay = random.uniform(2.0, 5.0)
                    self._instance_next_execution_time[instance_id] = time.time() + random_delay
                    logger.debug(f"Instance {instance_id} throttled for {random_delay:.1f}s")

                    if execution_time > 1.0:  # Log slow executions
                        logger.warning(f"⚠️ Slow execution detected for {instance_id}: {execution_time:.2f}s")

                    # Re-queue if still running or waiting
                    if can_continue:
                        # ALL instances get throttled the same way - 3 seconds minimum between executions
                        next_exec_time = self._instance_next_execution_time[instance_id]
                        self.throttled_queue[instance_id] = next_exec_time
                        wait_time = next_exec_time - current_time
                        logger.debug(f"⏰ Instance {instance_id} will execute again in {wait_time:.1f}s")

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

                    # Apply random throttling for next execution (2-5 seconds)
                    random_delay = random.uniform(2.0, 5.0)
                    self._instance_next_execution_time[instance_id] = time.time() + random_delay
                    logger.debug(f"Legacy instance {instance_id} throttled for {random_delay:.1f}s")

                    if can_continue:
                        # Re-queue with throttling
                        next_exec_time = self._instance_next_execution_time[instance_id]
                        self.throttled_queue[instance_id] = next_exec_time
                        logger.debug(f"⏰ Legacy instance {instance_id} throttled for next execution")

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
        Resume a paused instance - execute immediately and add to queue.
        The fresh context will be loaded from database when executed.

        Args:
            instance_id: Instance to resume
        """
        logger.info(f"Resume requested for instance {instance_id}")

        # Remove from waiting queue if present
        if instance_id in self.waiting_queue:
            del self.waiting_queue[instance_id]
            logger.debug(f"Removed {instance_id} from waiting queue")

        # Execute immediately without waiting for the loop
        asyncio.create_task(self._execute_immediate(instance_id))

        # Add to active queue for high priority processing
        if instance_id not in self.active_queue:
            # Insert at beginning for priority processing
            self.active_queue.insert(0, instance_id)
            # Legacy support
            if instance_id not in self.execution_queue:
                self.execution_queue.insert(0, instance_id)
            # Signal work available immediately
            self._work_available.set()
            logger.info(f"Instance {instance_id} executing immediately and prioritized in active queue")
        else:
            logger.info(f"Instance {instance_id} executing immediately (already in queue)")

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
            "throttle_delay": "2-5s (random)",
            "performance_metrics": {
                "slow_instances": dict(slow_instances),
                "total_instances_tracked": len(self._task_execution_times)
            },
            "optimizations": {
                "event_driven": True,
                "separate_queues": True,
                "throttling_enabled": True,
                "throttle_delay": "2-5s random per instance",
                "non_blocking": "No sleeps, timestamp-based checks"
            }
        }