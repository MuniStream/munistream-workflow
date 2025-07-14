from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Callable
from enum import Enum
from datetime import datetime
from pydantic import BaseModel, Field
import uuid


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ValidationResult(BaseModel):
    is_valid: bool
    errors: List[str] = Field(default_factory=list)


class StepResult(BaseModel):
    step_id: str
    status: StepStatus
    outputs: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    # Performance metrics
    execution_duration_ms: Optional[int] = None
    queue_time_ms: Optional[int] = None  # Time waiting in queue before execution
    validation_duration_ms: Optional[int] = None
    retry_count: int = 0
    memory_usage_mb: Optional[float] = None
    
    # Execution context
    executed_by: Optional[str] = None  # User or system that executed the step
    execution_environment: Optional[str] = None  # Environment info
    step_version: Optional[str] = None
    
    # Bottleneck analysis
    waiting_for_approval: bool = False
    waiting_for_external_service: bool = False
    blocking_dependencies: List[str] = Field(default_factory=list)
    
    def calculate_duration(self) -> Optional[int]:
        """Calculate execution duration in milliseconds"""
        if self.started_at and self.completed_at:
            duration = (self.completed_at - self.started_at).total_seconds() * 1000
            self.execution_duration_ms = int(duration)
            return self.execution_duration_ms
        return None


class BaseStep(ABC):
    def __init__(self, 
                 step_id: str,
                 name: str,
                 description: str = "",
                 required_inputs: Optional[List[str]] = None,
                 optional_inputs: Optional[List[str]] = None,
                 requires_citizen_input: bool = False,
                 input_form: Optional[Dict[str, Any]] = None,
                 **kwargs):
        self.step_id = step_id
        self.name = name
        self.description = description
        self.required_inputs = required_inputs or []
        self.optional_inputs = optional_inputs or []
        self.next_steps: List['BaseStep'] = []
        self.validations: List[Callable] = []
        self.requires_citizen_input = requires_citizen_input
        self.input_form = input_form or {}
    
    def add_validation(self, validation_func: Callable) -> 'BaseStep':
        self.validations.append(validation_func)
        return self
    
    def validate_inputs(self, inputs: Dict[str, Any]) -> ValidationResult:
        errors = []
        
        # Check required inputs
        for required in self.required_inputs:
            if required not in inputs:
                errors.append(f"Missing required input: {required}")
        
        # Run custom validations
        for validation in self.validations:
            try:
                result = validation(inputs)
                if isinstance(result, ValidationResult):
                    if not result.is_valid:
                        errors.extend(result.errors)
                elif not result:
                    errors.append(f"Validation failed for {validation.__name__}")
            except Exception as e:
                errors.append(f"Validation error: {str(e)}")
        
        return ValidationResult(is_valid=len(errors) == 0, errors=errors)
    
    @abstractmethod
    async def execute(self, inputs: Dict[str, Any], context: Dict[str, Any]) -> StepResult:
        pass
    
    def __rshift__(self, other: 'BaseStep') -> 'BaseStep':
        """Overload >> operator for flow definition"""
        self.next_steps.append(other)
        return other
    
    def __repr__(self):
        return f"{self.__class__.__name__}(id={self.step_id}, name={self.name})"


class ActionStep(BaseStep):
    def __init__(self, 
                 step_id: str,
                 name: str,
                 action: Callable,
                 **kwargs):
        super().__init__(step_id, name, **kwargs)
        self.action = action
    
    async def execute(self, inputs: Dict[str, Any], context: Dict[str, Any]) -> StepResult:
        result = StepResult(
            step_id=self.step_id,
            status=StepStatus.RUNNING,
            started_at=datetime.utcnow()
        )
        
        try:
            # Validate inputs
            validation = self.validate_inputs(inputs)
            if not validation.is_valid:
                result.status = StepStatus.FAILED
                result.error = f"Validation failed: {', '.join(validation.errors)}"
                result.completed_at = datetime.utcnow()
                return result
            
            # Execute action
            if asyncio.iscoroutinefunction(self.action):
                outputs = await self.action(inputs, context)
            else:
                outputs = self.action(inputs, context)
            
            result.outputs = outputs
            result.status = StepStatus.COMPLETED
            result.completed_at = datetime.utcnow()
            
        except Exception as e:
            result.status = StepStatus.FAILED
            result.error = str(e)
            result.completed_at = datetime.utcnow()
        
        return result


class ConditionalStep(BaseStep):
    def __init__(self, 
                 step_id: str,
                 name: str,
                 **kwargs):
        super().__init__(step_id, name, **kwargs)
        self.conditions: Dict[Callable, BaseStep] = {}
        self.default_step: Optional[BaseStep] = None
    
    def when(self, condition: Callable) -> 'ConditionalBranch':
        return ConditionalBranch(self, condition)
    
    def otherwise(self, step: BaseStep) -> 'ConditionalStep':
        self.default_step = step
        return self
    
    async def execute(self, inputs: Dict[str, Any], context: Dict[str, Any]) -> StepResult:
        result = StepResult(
            step_id=self.step_id,
            status=StepStatus.COMPLETED,
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow()
        )
        
        # Evaluate conditions
        for condition, next_step in self.conditions.items():
            try:
                if asyncio.iscoroutinefunction(condition):
                    condition_result = await condition(inputs, context)
                else:
                    condition_result = condition(inputs, context)
                
                if condition_result:
                    result.outputs = {"next_step": next_step.step_id, "condition_met": condition.__name__}
                    return result
            except Exception as e:
                result.status = StepStatus.FAILED
                result.error = f"Condition evaluation failed: {str(e)}"
                return result
        
        # Use default if no condition matched
        if self.default_step:
            result.outputs = {"next_step": self.default_step.step_id, "condition_met": "default"}
        else:
            result.outputs = {"next_step": None, "condition_met": "none"}
        
        return result


class ConditionalBranch:
    def __init__(self, conditional_step: ConditionalStep, condition: Callable):
        self.conditional_step = conditional_step
        self.condition = condition
    
    def __rshift__(self, other: BaseStep) -> ConditionalStep:
        self.conditional_step.conditions[self.condition] = other
        self.conditional_step.next_steps.append(other)
        return self.conditional_step


class ApprovalStep(BaseStep):
    def __init__(self, 
                 step_id: str,
                 name: str,
                 approvers: List[str],
                 approval_type: str = "any",  # "any", "all", "majority"
                 **kwargs):
        super().__init__(step_id, name, **kwargs)
        self.approvers = approvers
        self.approval_type = approval_type
    
    async def execute(self, inputs: Dict[str, Any], context: Dict[str, Any]) -> StepResult:
        result = StepResult(
            step_id=self.step_id,
            status=StepStatus.PENDING,  # Approval steps start as pending
            started_at=datetime.utcnow(),
            outputs={
                "approvers": self.approvers,
                "approval_type": self.approval_type,
                "approval_request_id": str(uuid.uuid4())
            }
        )
        
        # In a real implementation, this would create an approval request
        # and wait for approvers to respond
        
        return result


class TerminalStep(BaseStep):
    """A step that represents the end of a workflow path"""
    def __init__(self, 
                 step_id: str,
                 name: str,
                 terminal_status: str = "SUCCESS",  # SUCCESS, FAILURE, REJECTED, etc.
                 **kwargs):
        super().__init__(step_id, name, **kwargs)
        self.terminal_status = terminal_status
    
    async def execute(self, inputs: Dict[str, Any], context: Dict[str, Any]) -> StepResult:
        result = StepResult(
            step_id=self.step_id,
            status=StepStatus.COMPLETED,
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
            outputs={
                "terminal_status": self.terminal_status,
                "final_context": context
            }
        )
        return result


class IntegrationStep(BaseStep):
    def __init__(self, 
                 step_id: str,
                 name: str,
                 service_name: str,
                 endpoint: str,
                 method: str = "POST",
                 **kwargs):
        super().__init__(step_id, name, **kwargs)
        self.service_name = service_name
        self.endpoint = endpoint
        self.method = method
    
    async def execute(self, inputs: Dict[str, Any], context: Dict[str, Any]) -> StepResult:
        result = StepResult(
            step_id=self.step_id,
            status=StepStatus.RUNNING,
            started_at=datetime.utcnow()
        )
        
        try:
            # In a real implementation, this would make an HTTP request
            # to the external service
            import httpx
            
            async with httpx.AsyncClient() as client:
                response = await client.request(
                    method=self.method,
                    url=self.endpoint,
                    json=inputs
                )
                
                result.outputs = {
                    "status_code": response.status_code,
                    "response": response.json() if response.headers.get("content-type") == "application/json" else response.text
                }
                result.status = StepStatus.COMPLETED
                
        except Exception as e:
            result.status = StepStatus.FAILED
            result.error = f"Integration failed: {str(e)}"
        finally:
            result.completed_at = datetime.utcnow()
        
        return result


import asyncio