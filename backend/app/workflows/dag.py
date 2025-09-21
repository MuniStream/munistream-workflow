"""
DAG (Directed Acyclic Graph) implementation with context manager support.
Supports multiple concurrent instances of the same DAG definition.
"""
from typing import Dict, Any, List, Optional, Set
from datetime import datetime
from enum import Enum
import networkx as nx
import uuid

from .operators.base import BaseOperator


class DAGStatus(str, Enum):
    """DAG definition status"""
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class InstanceStatus(str, Enum):
    """DAG instance execution status"""
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused" 
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DAG:
    """
    DAG Definition - template that can be instantiated multiple times.
    Each citizen/user gets their own DAGInstance with isolated context.
    """
    
    def __init__(
        self,
        dag_id: str,
        name: Optional[str] = None,
        description: str = "",
        category: Optional[str] = None,
        schedule: Optional[str] = None,
        start_date: Optional[datetime] = None,
        default_args: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
        **kwargs
    ):
        """
        Initialize DAG definition.
        
        Args:
            dag_id: Unique identifier for the DAG type
            name: Human-readable name for the workflow
            description: Description of the workflow
            category: Workflow category for grouping
            schedule: Schedule interval (for automated runs)
            start_date: When the DAG starts being available
            default_args: Default arguments for all operators
            tags: Tags for categorization
            **kwargs: Additional metadata
        """
        self.dag_id = dag_id
        self.name = name or dag_id
        self.description = description
        self.category = category or "general"
        self.schedule = schedule
        self.start_date = start_date
        self.default_args = default_args or {}
        self.tags = tags or []
        self.metadata = kwargs
        
        # DAG structure (template)
        self.tasks: Dict[str, BaseOperator] = {}
        self.graph = nx.DiGraph()
        
        # Status and versioning
        self.status = DAGStatus.DRAFT
        self.version = "1.0.0"
        self.created_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()
    
    def __enter__(self) -> 'DAG':
        """Enter context manager for task definition"""
        DAGContext.push(self)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit context manager with validation"""
        try:
            if exc_type is None:
                self.build_graph()
                self.validate()
                self.status = DAGStatus.ACTIVE
        finally:
            DAGContext.pop()
    
    def add_task(self, task: BaseOperator) -> 'DAG':
        """Add task to DAG definition"""
        if task.task_id in self.tasks:
            raise ValueError(f"Task {task.task_id} already exists in DAG {self.dag_id}")
        
        self.tasks[task.task_id] = task
        self.graph.add_node(task.task_id, operator=task)
        self.updated_at = datetime.utcnow()
        
        return self
    
    def build_graph(self) -> 'DAG':
        """Build graph from task connections"""
        for task_id, task in self.tasks.items():
            for downstream_task in task.downstream_tasks:
                if downstream_task.task_id not in self.tasks:
                    self.add_task(downstream_task)
                self.graph.add_edge(task_id, downstream_task.task_id)
        return self
    
    def validate(self) -> bool:
        """Validate DAG structure"""
        if not self.tasks:
            raise ValueError("DAG must have at least one task")
        
        if not nx.is_directed_acyclic_graph(self.graph):
            cycles = list(nx.simple_cycles(self.graph))
            raise ValueError(f"DAG contains cycles: {cycles}")
        
        return True
    
    def create_instance(
        self,
        user_id: str,
        instance_data: Optional[Dict[str, Any]] = None
    ) -> 'DAGInstance':
        """
        Create a new instance of this DAG for a specific user.
        
        Args:
            user_id: ID of the user/citizen running this workflow
            instance_data: Initial data for the instance
            
        Returns:
            New DAG instance with isolated context
        """
        return DAGInstance(
            dag=self,
            user_id=user_id,
            initial_data=instance_data or {}
        )
    
    def get_root_tasks(self) -> List[BaseOperator]:
        """Get tasks with no upstream dependencies"""
        return [
            self.tasks[task_id] 
            for task_id in self.tasks.keys()
            if self.graph.in_degree(task_id) == 0
        ]
    
    def get_execution_order(self) -> List[str]:
        """Get topological sort of tasks"""
        return list(nx.topological_sort(self.graph))
    
    def to_mermaid(self) -> str:
        """Generate Mermaid diagram"""
        lines = ["graph TD"]
        
        for task_id, task in self.tasks.items():
            operator_type = task.__class__.__name__.replace("Operator", "")
            lines.append(f"    {task_id}[{operator_type}: {task_id}]")
        
        for edge in self.graph.edges():
            from_task, to_task = edge
            lines.append(f"    {from_task} --> {to_task}")
        
        return "\n".join(lines)


class DAGInstance:
    """
    Instance of a DAG being executed by a specific user.
    Maintains isolated context and state for concurrent execution.
    """
    
    def __init__(
        self,
        dag: DAG,
        user_id: str,
        initial_data: Dict[str, Any]
    ):
        """
        Initialize DAG instance.
        
        Args:
            dag: DAG definition template
            user_id: User executing this instance
            initial_data: Initial context data
        """
        self.instance_id = str(uuid.uuid4())
        self.dag = dag
        self.user_id = user_id
        
        # Isolated instance state
        self.status = InstanceStatus.PENDING
        self.context: Dict[str, Any] = initial_data.copy()
        self.current_task: Optional[str] = None
        
        # Task execution tracking
        self.task_states: Dict[str, Any] = {}
        self.completed_tasks: Set[str] = set()
        self.failed_tasks: Set[str] = set()
        
        # Timestamps
        self.created_at = datetime.utcnow()
        self.started_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None
        self.updated_at = datetime.utcnow()
        
        # Initialize task states
        for task_id in dag.tasks.keys():
            self.task_states[task_id] = {
                "status": "pending",
                "started_at": None,
                "completed_at": None,
                "result": None,
                "error": None
            }
    
    def get_executable_tasks(self) -> List[str]:
        """
        Get tasks that can be executed now.
        
        Returns:
            List of task IDs ready for execution
        """
        executable = []
        
        for task_id in self.dag.tasks.keys():
            # Skip if already completed or failed
            if task_id in self.completed_tasks or task_id in self.failed_tasks:
                continue
            
            # Skip if currently executing (but NOT if waiting - waiting tasks can be resumed)
            if self.task_states[task_id]["status"] == "executing":
                continue

            # If this task is waiting, it can be resumed
            if self.task_states[task_id]["status"] == "waiting":
                executable.append(task_id)
                continue

            # Check if all upstream dependencies are completed (not waiting!)
            upstream_tasks = list(self.dag.graph.predecessors(task_id))
            if all(upstream in self.completed_tasks for upstream in upstream_tasks):
                executable.append(task_id)
        
        return executable
    
    def update_task_status(
        self,
        task_id: str,
        status: str,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None
    ):
        """
        Update status of a specific task.
        
        Args:
            task_id: Task identifier
            status: New status
            result: Task result data
            error: Error message if failed
        """
        if task_id not in self.task_states:
            raise ValueError(f"Task {task_id} not found in instance")
        
        self.task_states[task_id]["status"] = status
        self.task_states[task_id]["result"] = result
        self.task_states[task_id]["error"] = error
        self.updated_at = datetime.utcnow()
        
        if status == "executing":
            self.task_states[task_id]["started_at"] = datetime.utcnow()
            self.current_task = task_id
            
        elif status in ["completed", "continue"]:
            self.task_states[task_id]["completed_at"] = datetime.utcnow()
            self.completed_tasks.add(task_id)
            
            # Merge task result into instance context
            if result:
                self.context.update(result)
            
        elif status == "waiting":
            # Keep waiting task as current task for persistence
            self.current_task = task_id

        elif status == "failed":
            self.task_states[task_id]["completed_at"] = datetime.utcnow()
            self.failed_tasks.add(task_id)
    
    def is_completed(self) -> bool:
        """Check if all tasks are completed"""
        return len(self.completed_tasks) == len(self.dag.tasks)
    
    def has_failed(self) -> bool:
        """Check if any task has failed"""
        return len(self.failed_tasks) > 0
    
    def get_progress_percentage(self) -> float:
        """Get completion percentage"""
        if not self.dag.tasks:
            return 0.0
        return (len(self.completed_tasks) / len(self.dag.tasks)) * 100
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert instance to dictionary"""
        return {
            "instance_id": self.instance_id,
            "dag_id": self.dag.dag_id,
            "user_id": self.user_id,
            "status": self.status,
            "current_task": self.current_task,
            "progress_percentage": self.get_progress_percentage(),
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "updated_at": self.updated_at.isoformat(),
            "task_states": self.task_states,
            "context_keys": list(self.context.keys())  # Don't expose full context for security
        }


class DAGContext:
    """Context manager for DAG creation"""
    
    _context_stack: List[DAG] = []
    
    @classmethod
    def push(cls, dag: DAG):
        cls._context_stack.append(dag)
    
    @classmethod
    def pop(cls) -> Optional[DAG]:
        return cls._context_stack.pop() if cls._context_stack else None
    
    @classmethod
    def get_current(cls) -> Optional[DAG]:
        """Get the current DAG from the stack"""
        return cls._context_stack[-1] if cls._context_stack else None
    
    # Create a class-level property for backward compatibility
    current_dag = property(lambda self: self.get_current())
    
    @classmethod
    def clear(cls):
        cls._context_stack.clear()


class DAGBag:
    """Collection of DAG definitions"""
    
    def __init__(self):
        self.dags: Dict[str, DAG] = {}
        self.instances: Dict[str, DAGInstance] = {}
    
    def add_dag(self, dag: DAG):
        """Add DAG definition"""
        if dag.dag_id in self.dags:
            raise ValueError(f"DAG {dag.dag_id} already exists")
        self.dags[dag.dag_id] = dag
    
    def get_dag(self, dag_id: str) -> Optional[DAG]:
        """Get DAG definition by ID"""
        return self.dags.get(dag_id)
    
    def create_instance(
        self,
        dag_id: str,
        user_id: str,
        initial_data: Optional[Dict[str, Any]] = None
    ) -> DAGInstance:
        """Create new DAG instance"""
        dag = self.get_dag(dag_id)
        if not dag:
            raise ValueError(f"DAG {dag_id} not found")
        
        instance = dag.create_instance(user_id, initial_data)
        self.instances[instance.instance_id] = instance
        return instance
    
    def get_instance(self, instance_id: str) -> Optional[DAGInstance]:
        """Get instance by ID"""
        return self.instances.get(instance_id)
    
    def get_user_instances(self, user_id: str) -> List[DAGInstance]:
        """Get all instances for a user"""
        return [
            instance for instance in self.instances.values()
            if instance.user_id == user_id
        ]