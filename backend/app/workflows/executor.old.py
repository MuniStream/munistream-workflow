"""
DAG Executor for orchestrating sequential execution of self-contained operators.
Manages multiple concurrent DAG instances while maintaining context isolation.
"""
from typing import Dict, Any, List, Optional, Set
from datetime import datetime
import asyncio
import logging
from enum import Enum

from .dag import DAGInstance, InstanceStatus
from .operators.base import BaseOperator, TaskStatus


logger = logging.getLogger(__name__)


class ExecutorStatus(str, Enum):
    """Executor status"""
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"


class DAGExecutor:
    """
    Orchestrates execution of DAG instances.
    Each instance runs independently with isolated context.
    Only the executor knows the sequence - operators remain agnostic.
    """
    
    def __init__(
        self,
        max_concurrent_instances: int = 10,
        task_timeout_seconds: int = 3600,
        heartbeat_interval: int = 30,
        workflow_service=None
    ):
        """
        Initialize DAG executor.
        
        Args:
            max_concurrent_instances: Maximum number of instances to run concurrently
            task_timeout_seconds: Timeout for individual task execution
            heartbeat_interval: Interval for status updates
        """
        self.max_concurrent_instances = max_concurrent_instances
        self.task_timeout_seconds = task_timeout_seconds
        self.heartbeat_interval = heartbeat_interval
        self.workflow_service = workflow_service  # For database synchronization
        
        # Execution state
        self.status = ExecutorStatus.IDLE
        self.running_instances: Dict[str, DAGInstance] = {}
        self.execution_queue: List[str] = []  # Queue of instance IDs
        
        # Monitoring
        self.total_executed = 0
        self.total_failed = 0
        self.started_at: Optional[datetime] = None
        
        # Background task for continuous execution
        self._execution_task: Optional[asyncio.Task] = None
        self._should_stop = False
    
    async def start(self):
        """Start the executor"""
        if self.status == ExecutorStatus.RUNNING:
            logger.warning("Executor already running")
            return
        
        self.status = ExecutorStatus.RUNNING
        self.started_at = datetime.utcnow()
        self._should_stop = False
        
        # Start background execution loop
        self._execution_task = asyncio.create_task(self._execution_loop())
        logger.info(f"DAG Executor started (max {self.max_concurrent_instances} concurrent instances)")
    
    async def stop(self):
        """Stop the executor gracefully"""
        self._should_stop = True
        
        if self._execution_task:
            self._execution_task.cancel()
            try:
                await self._execution_task
            except asyncio.CancelledError:
                pass
        
        self.status = ExecutorStatus.STOPPED
        logger.info("DAG Executor stopped")
    
    def submit_instance(self, instance: DAGInstance):
        """
        Submit a DAG instance for execution.
        
        Args:
            instance: DAG instance to execute
        """
        if instance.instance_id in self.running_instances:
            raise ValueError(f"Instance {instance.instance_id} is already running")
        
        # Add to execution queue
        self.execution_queue.append(instance.instance_id)
        self.running_instances[instance.instance_id] = instance
        
        logger.info(f"Instance {instance.instance_id} submitted for execution")
    
    async def execute_instance(self, instance: DAGInstance) -> bool:
        """
        Execute a single DAG instance.
        Instance status is derived from task states, not manually set.
        """
        try:
            # Mark instance as started if first time
            if not instance.started_at:
                instance.started_at = datetime.utcnow()
            
            # Process all executable tasks
            executable_tasks = instance.get_executable_tasks()
            
            for task_id in executable_tasks:
                success = await self._execute_task(instance, task_id)
                if not success:
                    # Task failed - stop processing
                    break
            
            # Update instance status based on current state
            await self._update_instance_status(instance)
            
            # Sync with database
            if self.workflow_service:
                await self.workflow_service.update_instance_from_dag(instance)
            
            # Return true if instance is still processable (not failed/completed)
            return instance.status not in [InstanceStatus.FAILED, InstanceStatus.COMPLETED]
                
        except Exception as e:
            logger.error(f"Error executing instance {instance.instance_id}: {str(e)}")
            instance.status = InstanceStatus.FAILED
            if self.workflow_service:
                await self.workflow_service.update_instance_from_dag(instance)
            return False
    
    async def _update_instance_status(self, instance: DAGInstance):
        """
        Intelligently determine instance status based on task states.
        Single source of truth for status determination.
        """
        # Check if all tasks are completed
        if instance.is_completed():
            instance.status = InstanceStatus.COMPLETED
            instance.completed_at = datetime.utcnow()
            self.total_executed += 1
            logger.info(f"Instance {instance.instance_id} completed")
            return
        
        # Check if any task has failed
        if instance.has_failed():
            instance.status = InstanceStatus.FAILED
            instance.completed_at = datetime.utcnow()
            self.total_failed += 1
            logger.error(f"Instance {instance.instance_id} failed")
            return
        
        # Check if tasks are waiting for input
        waiting_tasks = self._get_waiting_tasks(instance)
        if waiting_tasks:
            instance.status = InstanceStatus.PAUSED
            logger.info(f"Instance {instance.instance_id} waiting for: {waiting_tasks}")
            return
        
        # Check if there are executable tasks
        if instance.get_executable_tasks():
            instance.status = InstanceStatus.RUNNING
            return
        
        # No executable tasks and not waiting - might be stuck
        logger.warning(f"Instance {instance.instance_id} has no executable tasks")
        instance.status = InstanceStatus.PENDING
    
    async def _execute_task(self, instance: DAGInstance, task_id: str) -> bool:
        """
        Execute a single task - simplified and clear.
        """
        task = instance.dag.tasks.get(task_id)
        if not task:
            logger.error(f"Task {task_id} not found in DAG")
            return False
        
        try:
            # Mark as executing
            instance.update_task_status(task_id, "executing")
            
            # Execute the task
            result_status = task.run(instance.context)
            
            # Map result to task state
            status_mapping = {
                "continue": ("completed", True),
                "waiting": ("waiting", True),
                "retry": ("retry", True),
                "skip": ("skipped", True),
                "failed": ("failed", False)
            }
            
            task_state, success = status_mapping.get(result_status, ("failed", False))
            
            # Update task status
            if task_state == "completed":
                instance.update_task_status(task_id, task_state, result=task.get_output())
            elif task_state == "failed":
                error_msg = getattr(task.state, 'error_message', 'Task failed') if hasattr(task, 'state') else 'Task failed'
                instance.update_task_status(task_id, task_state, error=error_msg)
            else:
                instance.update_task_status(task_id, task_state)
            
            return success
            
        except Exception as e:
            instance.update_task_status(task_id, "failed", error=str(e))
            logger.error(f"Task {task_id} error: {str(e)}")
            return False
    
    def _get_waiting_tasks(self, instance: DAGInstance) -> List[str]:
        """Get tasks that are waiting for external input"""
        return [
            task_id for task_id, state in instance.task_states.items()
            if state["status"] in ["waiting", "waiting_input", "waiting_approval"]
        ]
    
    async def _execution_loop(self):
        """Background execution loop - simplified and efficient"""
        logger.info("Starting execution loop")
        
        while not self._should_stop:
            try:
                # Process all queued instances
                while self.execution_queue and len(self.running_instances) < self.max_concurrent_instances:
                    instance_id = self.execution_queue.pop(0)
                    instance = self.running_instances.get(instance_id)
                    
                    if instance:
                        # Execute in background and continue processing if needed
                        task = asyncio.create_task(self._process_instance(instance))
                        # Don't await - let it run in background
                
                # Short delay for responsiveness
                await asyncio.sleep(1)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Execution loop error: {str(e)}")
                await asyncio.sleep(1)
    
    async def _process_instance(self, instance: DAGInstance):
        """Process a single instance until it needs to wait or completes"""
        try:
            # Keep processing while instance can make progress
            while True:
                can_continue = await self.execute_instance(instance)
                
                if not can_continue:
                    # Instance is paused, failed, or completed
                    break
                
                # Small delay between iterations
                await asyncio.sleep(0.1)
                
        finally:
            # Only clean up completed/failed instances (keep paused ones for resumption)
            if instance.status in [InstanceStatus.COMPLETED, InstanceStatus.FAILED]:
                self.running_instances.pop(instance.instance_id, None)
    
    def resume_instance(self, instance_id: str):
        """
        Resume a paused instance by re-queuing it for execution.
        
        Args:
            instance_id: Instance to resume
        """
        # First check if instance is already running
        if instance_id in self.running_instances:
            instance = self.running_instances[instance_id]
            if instance.status == InstanceStatus.PAUSED:
                # Add back to execution queue
                if instance_id not in self.execution_queue:
                    self.execution_queue.append(instance_id)
                    logger.info(f"Instance {instance_id} resumed from running_instances")
        else:
            # Instance not in running_instances, need to get it from workflow_service
            if self.workflow_service:
                instance = self.workflow_service.dag_bag.get_instance(instance_id)
                if instance:
                    # Add to running instances and queue
                    self.running_instances[instance_id] = instance
                    if instance_id not in self.execution_queue:
                        self.execution_queue.append(instance_id)
                        logger.info(f"Instance {instance_id} resumed from DAGBag")
    
    def cancel_instance(self, instance_id: str):
        """
        Cancel a running instance.
        
        Args:
            instance_id: Instance to cancel
        """
        instance = self.running_instances.get(instance_id)
        if instance:
            instance.status = InstanceStatus.CANCELLED
            instance.completed_at = datetime.utcnow()
            self.running_instances.pop(instance_id, None)
            
            # Remove from queue if present
            if instance_id in self.execution_queue:
                self.execution_queue.remove(instance_id)
                
            logger.info(f"Instance {instance_id} cancelled")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get executor statistics"""
        return {
            "status": self.status,
            "running_instances": len(self.running_instances),
            "queued_instances": len(self.execution_queue),
            "max_concurrent": self.max_concurrent_instances,
            "total_executed": self.total_executed,
            "total_failed": self.total_failed,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "uptime_seconds": (
                (datetime.utcnow() - self.started_at).total_seconds()
                if self.started_at else 0
            )
        }