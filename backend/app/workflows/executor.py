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
        from ..models.workflow import WorkflowInstance
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
                if task_id in dag_instance.completed_tasks:
                    dag_instance.task_states[task_id] = {"status": "completed"}
                elif task_id in dag_instance.failed_tasks:
                    dag_instance.task_states[task_id] = {"status": "failed"}
                elif task_id == db_instance.current_step:
                    dag_instance.task_states[task_id] = {"status": "waiting"}
                else:
                    dag_instance.task_states[task_id] = {"status": "pending"}
            
            # Add to DAG bag for future reference
            self.workflow_service.dag_bag.instances[instance_id] = dag_instance
            print(f"✅ Instance {instance_id} recreated successfully")
        
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
        
        # Process executable tasks
        executable_tasks = dag_instance.get_executable_tasks()
        print(f"🔍 Instance {instance_id} has {len(executable_tasks)} executable tasks: {executable_tasks}")
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
                    result = await task.execute_async(dag_instance.context)
                else:
                    # Regular synchronous execution
                    result = task.run(dag_instance.context)
                
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
            if result == "continue":
                dag_instance.update_task_status(task_id, "completed")
                # Update context with task output - this is critical for data flow
                output = task.get_output()
                if output:
                    dag_instance.context.update(output)
                    logger.debug(f"Task {task_id} added to context: {list(output.keys())}")
            elif result == "waiting":
                dag_instance.update_task_status(task_id, "waiting")
                break  # Stop processing, we're waiting
            elif result == "failed":
                dag_instance.update_task_status(task_id, "failed")
                break  # Stop processing, task failed
            else:
                dag_instance.update_task_status(task_id, "completed")
        
        # Determine instance status based on task states
        if dag_instance.is_completed():
            dag_instance.status = InstanceStatus.COMPLETED
            dag_instance.completed_at = datetime.utcnow()
        elif dag_instance.has_failed():
            dag_instance.status = InstanceStatus.FAILED
            dag_instance.completed_at = datetime.utcnow()
        elif self._has_waiting_tasks(dag_instance):
            dag_instance.status = InstanceStatus.PAUSED
        else:
            dag_instance.status = InstanceStatus.RUNNING
        
        # Save everything back to database
        old_status = db_instance.status
        new_status = self._map_status(dag_instance.status)
        
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
        return dag_instance.status == InstanceStatus.RUNNING
    
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
                    
                    # Re-queue if still running
                    if can_continue:
                        self.execution_queue.append(instance_id)
                        print(f"♻️ Re-queued instance {instance_id} for continued processing")
                
                # Short delay between iterations
                await asyncio.sleep(1)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Execution loop error: {str(e)}")
                await asyncio.sleep(1)
    
    def resume_instance(self, instance_id: str):
        """
        Resume a paused instance - just add it back to the queue.
        The fresh context will be loaded from database when executed.
        
        Args:
            instance_id: Instance to resume
        """
        logger.info(f"Resume requested for instance {instance_id}")
        if instance_id not in self.execution_queue:
            self.execution_queue.append(instance_id)
            logger.info(f"Instance {instance_id} queued for resumption")
        else:
            logger.info(f"Instance {instance_id} already in queue")
    
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