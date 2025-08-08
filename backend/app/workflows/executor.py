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
        heartbeat_interval: int = 30
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
            return
        
        self.status = ExecutorStatus.RUNNING
        self.started_at = datetime.utcnow()
        self._should_stop = False
        
        # Start background execution loop
        self._execution_task = asyncio.create_task(self._execution_loop())
        
        logger.info(f"DAG Executor started with max {self.max_concurrent_instances} concurrent instances")
    
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
        Execute a single DAG instance completely.
        
        Args:
            instance: DAG instance to execute
            
        Returns:
            True if completed successfully, False if failed
        """
        try:
            instance.status = InstanceStatus.RUNNING
            instance.started_at = datetime.utcnow()
            
            logger.info(f"Starting execution of instance {instance.instance_id}")
            
            # Execute tasks in sequence
            while not instance.is_completed() and not instance.has_failed():
                # Get next executable tasks
                executable_tasks = instance.get_executable_tasks()
                
                if not executable_tasks:
                    # No executable tasks - check if waiting or stuck
                    waiting_tasks = self._get_waiting_tasks(instance)
                    
                    if waiting_tasks:
                        # Tasks are waiting for external input/approval
                        logger.info(f"Instance {instance.instance_id} waiting for: {waiting_tasks}")
                        break  # Exit execution loop, will resume when input arrives
                    else:
                        # No waiting tasks but not completed - might be stuck
                        logger.error(f"Instance {instance.instance_id} appears stuck")
                        instance.status = InstanceStatus.FAILED
                        break
                
                # Execute each ready task
                for task_id in executable_tasks:
                    success = await self._execute_task(instance, task_id)
                    if not success:
                        instance.status = InstanceStatus.FAILED
                        break
                
                # Small delay to prevent tight loops
                await asyncio.sleep(0.1)
            
            # Determine final status
            if instance.is_completed():
                instance.status = InstanceStatus.COMPLETED
                instance.completed_at = datetime.utcnow()
                self.total_executed += 1
                logger.info(f"Instance {instance.instance_id} completed successfully")
                return True
            
            elif instance.has_failed():
                instance.status = InstanceStatus.FAILED
                instance.completed_at = datetime.utcnow()
                self.total_failed += 1
                logger.error(f"Instance {instance.instance_id} failed")
                return False
            
            else:
                # Paused/waiting state
                instance.status = InstanceStatus.PAUSED
                logger.info(f"Instance {instance.instance_id} paused - waiting for input")
                return False
                
        except Exception as e:
            logger.error(f"Error executing instance {instance.instance_id}: {str(e)}")
            instance.status = InstanceStatus.FAILED
            instance.completed_at = datetime.utcnow()
            self.total_failed += 1
            return False
        
        finally:
            # Remove from running instances if completed or failed
            if instance.status in [InstanceStatus.COMPLETED, InstanceStatus.FAILED]:
                self.running_instances.pop(instance.instance_id, None)
    
    async def _execute_task(self, instance: DAGInstance, task_id: str) -> bool:
        """
        Execute a single task within an instance.
        
        Args:
            instance: DAG instance
            task_id: Task to execute
            
        Returns:
            True if task succeeded
        """
        task = instance.dag.tasks[task_id]
        
        try:
            logger.debug(f"Executing task {task_id} in instance {instance.instance_id}")
            
            # Update task status to executing
            instance.update_task_status(task_id, "executing")
            
            # Execute the task (task is self-contained and agnostic)
            result_status = task.run(instance.context)
            
            # Process result based on status
            if result_status == "continue":
                # Task completed successfully
                task_result = task.get_output()
                instance.update_task_status(task_id, "completed", result=task_result)
                return True
                
            elif result_status == "waiting":
                # Task is waiting for external input/approval
                instance.update_task_status(task_id, "waiting")
                return True  # Not failed, just waiting
                
            elif result_status == "retry":
                # Task needs to retry
                instance.update_task_status(task_id, "retry")
                # For now, treat as waiting (could implement retry logic)
                return True
                
            elif result_status == "skip":
                # Task was skipped
                instance.update_task_status(task_id, "skipped")
                return True
                
            elif result_status == "failed":
                # Task failed
                error_msg = task.state.error_message or "Task failed without error message"
                instance.update_task_status(task_id, "failed", error=error_msg)
                return False
            
            else:
                # Unknown status
                instance.update_task_status(task_id, "failed", error=f"Unknown task status: {result_status}")
                return False
                
        except asyncio.TimeoutError:
            instance.update_task_status(task_id, "failed", error="Task timeout")
            logger.error(f"Task {task_id} in instance {instance.instance_id} timed out")
            return False
            
        except Exception as e:
            instance.update_task_status(task_id, "failed", error=str(e))
            logger.error(f"Error executing task {task_id} in instance {instance.instance_id}: {str(e)}")
            return False
    
    def _get_waiting_tasks(self, instance: DAGInstance) -> List[str]:
        """Get tasks that are waiting for external input"""
        return [
            task_id for task_id, state in instance.task_states.items()
            if state["status"] in ["waiting", "waiting_input", "waiting_approval"]
        ]
    
    async def _execution_loop(self):
        """Background execution loop"""
        while not self._should_stop:
            try:
                # Process execution queue
                if (len(self.running_instances) < self.max_concurrent_instances 
                    and self.execution_queue):
                    
                    instance_id = self.execution_queue.pop(0)
                    instance = self.running_instances.get(instance_id)
                    
                    if instance:
                        # Execute instance in background
                        asyncio.create_task(self.execute_instance(instance))
                
                # Heartbeat delay
                await asyncio.sleep(self.heartbeat_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in execution loop: {str(e)}")
                await asyncio.sleep(5)  # Wait before retrying
    
    def resume_instance(self, instance_id: str):
        """
        Resume a paused instance.
        
        Args:
            instance_id: Instance to resume
        """
        instance = self.running_instances.get(instance_id)
        if instance and instance.status == InstanceStatus.PAUSED:
            # Add back to execution queue
            if instance_id not in self.execution_queue:
                self.execution_queue.append(instance_id)
                logger.info(f"Instance {instance_id} resumed")
    
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