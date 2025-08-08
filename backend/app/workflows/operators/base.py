"""
Base Operator for self-contained workflow steps.
Inspired by Apache Airflow's operator pattern.
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Union
from enum import Enum
from datetime import datetime
from pydantic import BaseModel, Field
import uuid


class TaskList:
    """Helper class to handle list operations in DAG flow"""
    
    def __init__(self, tasks: List['BaseOperator']):
        self.tasks = tasks
    
    def __rshift__(self, other: Union['BaseOperator', List['BaseOperator']]) -> Union['BaseOperator', List['BaseOperator']]:
        """Connect all tasks in this list to the next task(s)"""
        if isinstance(other, list):
            # Many to many connection
            for task in self.tasks:
                for next_task in other:
                    task.downstream_tasks.append(next_task)
                    next_task.upstream_tasks.append(task)
            return other
        else:
            # Many to one connection
            for task in self.tasks:
                task.downstream_tasks.append(other)
                other.upstream_tasks.append(task)
            return other


class TaskStatus(str, Enum):
    """Status of a task/operator execution"""
    # Initial states
    PENDING = "pending"
    READY = "ready"
    
    # Execution states
    EXECUTING = "executing"
    WAITING_INPUT = "waiting_input"
    WAITING_APPROVAL = "waiting_approval"
    VALIDATING = "validating"
    
    # Result states
    CONTINUE = "continue"
    FAILED = "failed"
    RETRY = "retry"
    SKIP = "skip"
    WAITING = "waiting"


class TaskState(BaseModel):
    """Internal state of a task"""
    status: TaskStatus = TaskStatus.PENDING
    input_data: Dict[str, Any] = Field(default_factory=dict)
    output_data: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    # Assignment and approval
    assigned_to: Optional[str] = None
    assigned_team: Optional[str] = None
    waiting_for: Optional[str] = None  # "user_input", "approval", "external_system"
    
    # Execution tracking
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    retry_count: int = 0
    error_message: Optional[str] = None
    
    # User interactions
    has_input: bool = False
    user_input: Optional[Dict[str, Any]] = None
    approval_decision: Optional[str] = None  # "approved", "rejected"
    rejection_reason: Optional[str] = None


class TaskResult(BaseModel):
    """Result of a task execution"""
    status: str  # "continue", "failed", "waiting", "retry", "skip"
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    next_task: Optional[str] = None  # For branching operations
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BaseOperator(ABC):
    """
    Base operator class - self-contained and agnostic to other steps.
    Each operator only knows about its own task, not the workflow structure.
    """
    
    def __init__(self, task_id: str, **kwargs):
        """
        Initialize base operator.
        
        Args:
            task_id: Unique identifier for this task
            **kwargs: Additional configuration parameters
        """
        self.task_id = task_id
        self.downstream_tasks: List['BaseOperator'] = []
        self.upstream_tasks: List['BaseOperator'] = []
        self.state = TaskState()
        self.kwargs = kwargs
        
        # Auto-register with current DAG context if available
        self._auto_register_with_dag()
    
    def _auto_register_with_dag(self):
        """Auto-register this operator with the current DAG context"""
        from ..dag import DAGContext
        
        current_dag = DAGContext.current_dag
        if current_dag is not None:
            current_dag.add_task(self)
    
    @abstractmethod
    def execute(self, context: Dict[str, Any]) -> TaskResult:
        """
        Execute the operator's task.
        This method must be implemented by each operator.
        
        Args:
            context: Execution context with data from previous steps
            
        Returns:
            TaskResult with status and output data
        """
        pass
    
    def run(self, context: Dict[str, Any]) -> str:
        """
        Run the operator and return execution status.
        
        Args:
            context: Execution context
            
        Returns:
            Status string: "continue", "failed", "waiting", "retry", "skip"
        """
        try:
            self.state.started_at = datetime.utcnow()
            self.state.status = TaskStatus.EXECUTING
            
            # Execute the operator
            result = self.execute(context)
            
            # Update state based on result
            if result.status == "continue":
                self.state.status = TaskStatus.CONTINUE
                self.state.output_data = result.data or {}
                self.state.completed_at = datetime.utcnow()
            elif result.status == "waiting":
                self.state.status = TaskStatus.WAITING_INPUT
            elif result.status == "failed":
                self.state.status = TaskStatus.FAILED
                self.state.error_message = result.error
                self.state.completed_at = datetime.utcnow()
            elif result.status == "retry":
                self.state.status = TaskStatus.RETRY
                self.state.retry_count += 1
            elif result.status == "skip":
                self.state.status = TaskStatus.SKIP
                self.state.completed_at = datetime.utcnow()
            
            return result.status
            
        except Exception as e:
            self.state.status = TaskStatus.FAILED
            self.state.error_message = str(e)
            self.state.completed_at = datetime.utcnow()
            return "failed"
    
    def __rshift__(self, other: Union['BaseOperator', List['BaseOperator']]) -> Union['BaseOperator', 'TaskList']:
        """
        Implement >> operator for defining task flow.
        
        Examples:
            task1 >> task2
            task1 >> [task2, task3]
        """
        if isinstance(other, list):
            for task in other:
                self.downstream_tasks.append(task)
                task.upstream_tasks.append(self)
            return TaskList(other)
        else:
            self.downstream_tasks.append(other)
            other.upstream_tasks.append(self)
            return other
    
    def __lshift__(self, other: Union['BaseOperator', List['BaseOperator']]) -> 'BaseOperator':
        """
        Implement << operator for reverse flow definition.
        
        Examples:
            task2 << task1  (equivalent to task1 >> task2)
        """
        if isinstance(other, list):
            for task in other:
                task >> self
        else:
            other >> self
        return self
    
    def __repr__(self):
        return f"{self.__class__.__name__}(task_id='{self.task_id}')"
    
    def get_status(self) -> TaskStatus:
        """Get current task status"""
        return self.state.status
    
    def set_input(self, data: Dict[str, Any]):
        """Set input data for the task"""
        self.state.input_data = data
    
    def get_output(self) -> Dict[str, Any]:
        """Get output data from the task"""
        return self.state.output_data
    
    def reset(self):
        """Reset the operator state for re-execution"""
        self.state = TaskState()