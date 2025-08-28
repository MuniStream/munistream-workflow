from typing import Dict, Any, List, Optional
from datetime import datetime
from pydantic import BaseModel, Field
import networkx as nx
from enum import Enum

from .base import BaseStep, StepResult, StepStatus


class WorkflowStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    INACTIVE = "inactive"
    ARCHIVED = "archived"


class WorkflowInstance(BaseModel):
    instance_id: str
    workflow_id: str
    user_id: str
    status: str = "running"
    current_step: Optional[str] = None
    context: Dict[str, Any] = Field(default_factory=dict)
    step_results: Dict[str, StepResult] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None


class Workflow:
    # Class variable to track the current workflow context
    _current_workflow: Optional['Workflow'] = None
    
    def __init__(self, workflow_id: str, name: str, description: str = ""):
        self.workflow_id = workflow_id
        self.name = name
        self.description = description
        self.steps: Dict[str, BaseStep] = {}
        self.start_step: Optional[BaseStep] = None
        self.graph = nx.DiGraph()
        self.status = WorkflowStatus.DRAFT
        self.version = "1.0.0"
        self.metadata: Dict[str, Any] = {}
    
    def __enter__(self) -> 'Workflow':
        """Enter context manager - allows 'with Workflow(...) as workflow:' syntax"""
        Workflow._current_workflow = self
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit context manager - auto-build and validate workflow"""
        try:
            if exc_type is None:  # No exceptions occurred
                self.build_graph()
                self.validate()
        finally:
            Workflow._current_workflow = None
    
    @classmethod
    def get_current(cls) -> Optional['Workflow']:
        """Get the current workflow context (for use by operators)"""
        return cls._current_workflow
    
    def add_step(self, step: BaseStep) -> 'Workflow':
        self.steps[step.step_id] = step
        self.graph.add_node(step.step_id, step=step)
        
        if not self.start_step:
            self.start_step = step
        
        return self
    
    def set_start(self, step: BaseStep) -> 'Workflow':
        if step.step_id not in self.steps:
            self.add_step(step)
        self.start_step = step
        return self
    
    def build_graph(self) -> 'Workflow':
        """Build the workflow graph from step connections"""
        for step_id, step in self.steps.items():
            for next_step in step.next_steps:
                self.graph.add_edge(step_id, next_step.step_id)
                if next_step.step_id not in self.steps:
                    self.steps[next_step.step_id] = next_step
                    self.graph.add_node(next_step.step_id, step=next_step)
        
        # Validate graph
        if not nx.is_directed_acyclic_graph(self.graph):
            raise ValueError("Workflow contains cycles!")
        
        return self
    
    def validate(self) -> bool:
        """Validate the workflow structure"""
        if not self.start_step:
            raise ValueError("Workflow must have a start step")
        
        if not nx.is_directed_acyclic_graph(self.graph):
            raise ValueError("Workflow contains cycles")
        
        # Check if all nodes are reachable from start
        reachable = nx.descendants(self.graph, self.start_step.step_id)
        reachable.add(self.start_step.step_id)
        
        if len(reachable) != len(self.steps):
            unreachable = set(self.steps.keys()) - reachable
            raise ValueError(f"Unreachable steps: {unreachable}")
        
        return True
    
    def get_next_steps(self, current_step_id: str) -> List[str]:
        """Get the next steps from the current step"""
        return list(self.graph.successors(current_step_id))
    
    def to_mermaid(self) -> str:
        """Generate Mermaid diagram representation"""
        lines = ["graph TD"]
        
        # Add nodes
        for step_id, step in self.steps.items():
            shape = "[]" if isinstance(step, ConditionalStep) else "()"
            label = f"{step.name}"
            if step == self.start_step:
                lines.append(f"    {step_id}{shape[0]}{label}{shape[1]} -.->|start| {step_id}")
            else:
                lines.append(f"    {step_id}{shape[0]}{label}{shape[1]}")
        
        # Add edges
        for edge in self.graph.edges():
            from_step, to_step = edge
            step = self.steps[from_step]
            
            if isinstance(step, ConditionalStep):
                # Find which condition leads to this edge
                for condition, next_step in step.conditions.items():
                    if next_step.step_id == to_step:
                        lines.append(f"    {from_step} -->|{condition.__name__}| {to_step}")
                        break
                else:
                    if step.default_step and step.default_step.step_id == to_step:
                        lines.append(f"    {from_step} -->|default| {to_step}")
            else:
                lines.append(f"    {from_step} --> {to_step}")
        
        return "\n".join(lines)
    
    async def execute_instance(self, instance: WorkflowInstance) -> WorkflowInstance:
        """Execute a workflow instance"""
        current_step_id = instance.current_step or self.start_step.step_id
        
        while current_step_id:
            step = self.steps[current_step_id]
            
            # Execute the current step
            result = await step.execute(instance.context, instance.context)
            instance.step_results[current_step_id] = result
            
            if result.status == StepStatus.FAILED:
                instance.status = "failed"
                break
            
            # Update context with outputs
            instance.context.update(result.outputs)
            
            # Determine next step
            if isinstance(step, ConditionalStep) and "next_step" in result.outputs:
                next_step_id = result.outputs["next_step"]
                current_step_id = next_step_id
            else:
                next_steps = self.get_next_steps(current_step_id)
                current_step_id = next_steps[0] if next_steps else None
            
            instance.current_step = current_step_id
            instance.updated_at = datetime.utcnow()
        
        if not current_step_id and instance.status != "failed":
            instance.status = "completed"
            instance.completed_at = datetime.utcnow()
        
        return instance


from .base import ConditionalStep