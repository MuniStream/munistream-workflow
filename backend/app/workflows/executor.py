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
    
    async def start(self):
        """Start the executor"""
        if self.status == ExecutorStatus.RUNNING:
            print("⚠️ Executor already running")
            return
        
        self.status = ExecutorStatus.RUNNING
        self._should_stop = False
        self._execution_task = asyncio.create_task(self._execution_loop())
        print("✅ DAG Executor started - background loop running")
        logger.info("DAG Executor started")
    
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
        if instance_id not in self.execution_queue:
            self.execution_queue.append(instance_id)
            logger.info(f"Instance {instance_id} queued for execution")
    
    async def execute_instance(self, instance_id: str) -> bool:
        """
        Execute a single instance by fetching it fresh from database.
        
        Args:
            instance_id: ID of instance to execute
            
        Returns:
            True if instance can continue, False if completed/failed
        """
        logger.info(f"Executing instance {instance_id}")
        
        # Get fresh instance from database
        db_instance = await WorkflowInstance.find_one(
            WorkflowInstance.instance_id == instance_id
        )
        if not db_instance:
            logger.error(f"Instance {instance_id} not found in database")
            return False
        
        # Get DAG instance from bag, or recreate it from database
        dag_instance = self.workflow_service.dag_bag.get_instance(instance_id)
        if not dag_instance:
            # Try to recreate from database
            print(f"🔄 Recreating instance {instance_id} from database")
            dag = self.workflow_service.dag_bag.get_dag(db_instance.workflow_id)
            if not dag:
                logger.error(f"DAG {db_instance.workflow_id} not found for instance {instance_id}")
                return False
            
            # Recreate the DAG instance from database state
            print(f"DEBUG: Recreating instance with context: {list(db_instance.context.keys()) if db_instance.context else 'None'}")
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
            print(f"✅ Instance {instance_id} recreated successfully")
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
        print(f"DEBUG: Before overwrite, dag_instance.context keys: {list(dag_instance.context.keys())}")
        print(f"DEBUG: Overwriting with db_instance.context keys: {list(db_instance.context.keys()) if db_instance.context else 'None'}")
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
        print(f"🔍 Instance {instance_id} has {len(executable_tasks)} executable tasks: {executable_tasks}")
        # Debug: print task states
        for task_id, state in dag_instance.task_states.items():
            print(f"   DEBUG Task {task_id}: status={state.get('status')}")
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
                    print(f"⏱️ Task {task_id} waiting {time_remaining:.1f}s before retry")
                    continue  # Skip this task for now
                else:
                    # Clear the retry timestamp, we can proceed
                    task._retry_after = None

            # Execute task with current context
            dag_instance.update_task_status(task_id, "executing")
            
            # Run the task
            print(f"▶️ Running task {task_id}")
            
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
                    if task_result.data:
                        task.state.output_data = task_result.data
                else:
                    # Regular synchronous execution
                    result = task.run(dag_instance.context)
                    # Note: for sync tasks, _last_result is already set in the run() method

                print(f"📊 Task {task_id} returned: {result}")
                
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
                print(f"❌ Task {task_id} failed with error: {e}")
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
            print(f"🔄 Task {task_id} returned result: '{result}'")
            if result == "continue":
                dag_instance.update_task_status(task_id, "completed")
                # Update context with task output - this is critical for data flow
                output = task.get_output()
                if output:
                    dag_instance.context.update(output)
                    logger.debug(f"Task {task_id} added to context: {list(output.keys())}")
            elif result == "waiting":
                print(f"⏳ Task {task_id} is waiting - setting status and saving context")
                dag_instance.update_task_status(task_id, "waiting")
                # CRITICAL: Save task output/context even when waiting (for state tracking)
                output = task.get_output()
                if output:
                    dag_instance.context.update(output)
                    print(f"💾 Saved waiting task output: {list(output.keys())}")
                print(f"📋 Completed tasks after waiting: {dag_instance.completed_tasks}")
                # Schedule re-check after a delay for polling
                # The instance will be re-queued in the main loop since it's PAUSED
                break  # Stop processing for now, will resume via polling
            elif result == "failed":
                dag_instance.update_task_status(task_id, "failed")
                break  # Stop processing, task failed
            elif result == "retry":
                # Don't mark as completed, keep it ready for retry
                print(f"🔁 Task {task_id} requesting retry - keeping as pending")
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
                print(f"⏱️ Task {task_id} will retry after {retry_delay} seconds")
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

        print(f"DEBUG: Saving context with keys: {list(dag_instance.context.keys())}")
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
        print("🔄 Execution loop started")
        while not self._should_stop:
            try:
                # Process queue
                if self.execution_queue:
                    instance_id = self.execution_queue.pop(0)
                    print(f"📋 Processing instance from queue: {instance_id}")
                    
                    # Execute instance
                    can_continue = await self.execute_instance(instance_id)

                    # Re-queue if still running or waiting
                    if can_continue:
                        # Check if instance is paused (waiting for external system)
                        db_instance = await WorkflowInstance.find_one(
                            WorkflowInstance.instance_id == instance_id
                        )
                        if db_instance and db_instance.status == "paused":
                            # Add delay for polling external systems
                            print(f"⏳ Instance {instance_id} is waiting, will re-check in 5 seconds...")
                            await asyncio.sleep(5)

                        self.execution_queue.append(instance_id)
                        print(f"♻️ Re-queued instance {instance_id} for continued processing")
                else:
                    # Only sleep if queue is empty to avoid CPU spinning
                    await asyncio.sleep(0.1)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Execution loop error: {str(e)}")
                await asyncio.sleep(1)
    
    def resume_instance(self, instance_id: str):
        """
        Resume a paused instance - execute immediately and add to queue.
        The fresh context will be loaded from database when executed.

        Args:
            instance_id: Instance to resume
        """
        logger.info(f"Resume requested for instance {instance_id}")

        # Execute immediately without waiting for the loop
        asyncio.create_task(self._execute_immediate(instance_id))

        # Also add to queue for continued processing if needed
        if instance_id not in self.execution_queue:
            self.execution_queue.append(instance_id)
            logger.info(f"Instance {instance_id} executing immediately and queued for continuation")
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
        """Get executor statistics"""
        return {
            "status": self.status.value,
            "queued_instances": len(self.execution_queue),
            "queue": self.execution_queue[:10]  # Show first 10 in queue
        }