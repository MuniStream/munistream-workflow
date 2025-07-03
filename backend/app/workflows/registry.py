"""
Step registry for managing and looking up workflow steps.
"""

from typing import Dict, Optional, List
from .base import BaseStep
from .workflow import Workflow


class StepRegistry:
    """Registry for managing workflow steps across the system"""
    
    def __init__(self):
        self._steps: Dict[str, BaseStep] = {}
        self._workflows: Dict[str, Workflow] = {}
        self._step_to_workflow: Dict[str, str] = {}
    
    def register_workflow(self, workflow: Workflow):
        """Register a workflow and all its steps"""
        self._workflows[workflow.workflow_id] = workflow
        
        for step in workflow.steps.values():
            self._steps[step.step_id] = step
            self._step_to_workflow[step.step_id] = workflow.workflow_id
    
    def get_step(self, step_id: str) -> Optional[BaseStep]:
        """Get a step by its ID"""
        return self._steps.get(step_id)
    
    def get_workflow(self, workflow_id: str) -> Optional[Workflow]:
        """Get a workflow by its ID"""
        return self._workflows.get(workflow_id)
    
    def get_workflow_for_step(self, step_id: str) -> Optional[str]:
        """Get the workflow ID that contains a specific step"""
        return self._step_to_workflow.get(step_id)
    
    def list_steps(self, workflow_id: Optional[str] = None) -> List[Dict[str, str]]:
        """List all steps, optionally filtered by workflow"""
        if workflow_id:
            workflow = self._workflows.get(workflow_id)
            if not workflow:
                return []
            return [
                {
                    "step_id": step.step_id,
                    "name": step.name,
                    "type": step.__class__.__name__,
                    "workflow_id": workflow_id
                }
                for step in workflow.steps.values()
            ]
        else:
            return [
                {
                    "step_id": step_id,
                    "name": step.name,
                    "type": step.__class__.__name__,
                    "workflow_id": self._step_to_workflow.get(step_id, "unknown")
                }
                for step_id, step in self._steps.items()
            ]
    
    def list_workflows(self) -> List[Dict[str, str]]:
        """List all registered workflows"""
        return [
            {
                "workflow_id": workflow_id,
                "name": workflow.name,
                "description": workflow.description,
                "step_count": len(workflow.steps)
            }
            for workflow_id, workflow in self._workflows.items()
        ]


# Global step registry instance
step_registry = StepRegistry()


def register_example_workflows():
    """Register example workflows with the registry"""
    from .examples.citizen_registration import create_citizen_registration_workflow
    
    # Register citizen registration workflow
    citizen_workflow = create_citizen_registration_workflow()
    step_registry.register_workflow(citizen_workflow)
    
    # You can add more example workflows here
    # from .examples.business_license import create_business_license_workflow
    # business_workflow = create_business_license_workflow()
    # step_registry.register_workflow(business_workflow)


# Auto-register example workflows on import
register_example_workflows()