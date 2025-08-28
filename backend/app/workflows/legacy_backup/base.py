from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Callable
from enum import Enum
from datetime import datetime
from pydantic import BaseModel, Field
import uuid
import asyncio


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
        
        # Auto-register with current workflow context if available
        self._auto_register_with_workflow()
    
    def _auto_register_with_workflow(self):
        """Auto-register this step with the current workflow context"""
        # Import here to avoid circular imports
        from .workflow import Workflow
        
        current_workflow = Workflow.get_current()
        if current_workflow is not None:
            current_workflow.add_step(self)
            # Set as start step if it's the first step added
            if current_workflow.start_step is None:
                current_workflow.set_start(self)
    
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


# Entity validation functions (always available)
def entity_validation_successful(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Función condicional: verificar que todas las validaciones fueron exitosas"""
    return context.get("overall_status") == "valid"


def entity_validation_has_warnings_only(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Función condicional: verificar que solo hay advertencias (no errores críticos)"""
    return context.get("overall_status") == "has_warnings"


def entity_validation_has_errors(inputs: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Función condicional: verificar que hay errores críticos"""
    return context.get("overall_status") in ["has_errors", "critical_error"]


# EntityStep functionality - integrated directly in base.py
from typing import Protocol, runtime_checkable

@runtime_checkable
class EntityValidationService(Protocol):
    """Protocol que define la interfaz que debe implementar un servicio de validación de entidades"""
    
    async def create_entity(self, entity_type: str, data: Dict[str, Any]) -> 'Entity':
        """Crear una nueva instancia de entidad"""
        ...
    
    async def validate_entities(self, entities: List['Entity']) -> Dict[str, Any]:
        """Validar múltiples entidades en paralelo"""
        ...


@runtime_checkable  
class Entity(Protocol):
    """Protocol que define la interfaz que debe implementar una entidad"""
    
    validation_status: str
    validation_errors: List[str]
    auto_filled_fields: List[str]
    external_validations: Dict[str, Any]
    data: Dict[str, Any]
    
    async def validate(self) -> bool:
        """Validar la entidad"""
        ...
    
    async def auto_complete(self) -> Dict[str, Any]:
        """Auto-completar datos de la entidad"""
        ...
    
    def get_validation_rules(self) -> Dict[str, Any]:
        """Obtener reglas de validación"""
        ...
    
    def get_required_fields(self) -> List[str]:
        """Obtener campos requeridos"""
        ...


class EntityStep(ActionStep):
    """
    Step base para integrar entidades semánticas con workflows DAG.
    
    Esta clase proporciona la funcionalidad genérica para:
    - Extraer datos de entrada según configuración
    - Crear entidades usando el servicio de validación
    - Auto-completar datos
    - Validar entidades
    - Formatear resultados para el workflow
    """
    
    def __init__(
        self,
        step_id: str,
        name: str,
        entity_service: EntityValidationService,
        entity_mappings: List[Dict[str, Any]],
        **kwargs
    ):
        """
        Args:
            entity_service: Servicio de validación de entidades del plugin
            entity_mappings: Lista de configuraciones de entidades a validar
                Formato: [
                    {
                        "entity_type": "address",
                        "input_fields": ["street", "number", "postal_code"],
                        "output_key": "address_validation",
                        "config": {}  # Configuración adicional opcional
                    }
                ]
        """
        self.entity_service = entity_service
        self.entity_mappings = entity_mappings
        
        super().__init__(
            step_id=step_id,
            name=name,
            action=self._execute_entity_validation,
            description=f"Validación semántica usando {len(entity_mappings)} entidades",
            **kwargs
        )
    
    async def _execute_entity_validation(self, inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Ejecutar validación de entidades según configuración"""
        
        results = {
            "entity_validation_completed": True,
            "entity_results": {},
            "overall_status": "valid",
            "validation_errors": [],
            "validation_warnings": [],
            "auto_filled_data": {},
            "validation_timestamp": datetime.utcnow().isoformat()
        }
        
        try:
            entities_created = []
            
            # Procesar cada mapping de entidad
            for mapping in self.entity_mappings:
                entity_type = mapping["entity_type"]
                input_fields = mapping["input_fields"]
                output_key = mapping["output_key"]
                config = mapping.get("config", {})
                optional = mapping.get("optional", False)
                
                # Extraer datos de entrada
                entity_data = self._extract_entity_data(inputs, input_fields, config)
                
                # Skip si es opcional y no hay datos
                if optional and not any(entity_data.values()):
                    continue
                
                # Crear entidad
                entity = await self.entity_service.create_entity(entity_type, entity_data)
                entities_created.append((entity, output_key))
            
            # Procesar cada entidad creada
            for entity, output_key in entities_created:
                # Auto-completar
                completed_data = await entity.auto_complete()
                results["auto_filled_data"][output_key] = completed_data
                
                # Validar
                is_valid = await entity.validate()
                
                # Almacenar resultado individual
                results["entity_results"][output_key] = {
                    "valid": is_valid,
                    "status": entity.validation_status,
                    "errors": entity.validation_errors,
                    "auto_filled_fields": entity.auto_filled_fields,
                    "external_validations": entity.external_validations,
                    "data": entity.data
                }
                
                # Actualizar estado general
                self._update_overall_status(results, entity, output_key)
        
        except Exception as e:
            results.update({
                "overall_status": "critical_error",
                "validation_errors": [f"Error crítico en validación: {str(e)}"],
                "exception": str(e)
            })
        
        return results
    
    def _extract_entity_data(self, inputs: Dict[str, Any], input_fields: List[str], config: Dict[str, Any]) -> Dict[str, Any]:
        """Extraer datos de entrada para una entidad específica"""
        entity_data = {}
        
        # Extraer campos de entrada
        for field in input_fields:
            if field in inputs:
                entity_data[field] = inputs[field]
        
        # Agregar configuración adicional
        entity_data.update(config)
        
        return entity_data
    
    def _update_overall_status(self, results: Dict[str, Any], entity: Entity, output_key: str):
        """Actualizar el estado general basado en el resultado de una entidad"""
        if not entity.validation_status == "valid":
            error_prefix = f"{output_key}: "
            
            if entity.validation_status == "needs_review":
                results["validation_warnings"].extend([
                    f"{error_prefix}{err}" for err in entity.validation_errors
                ])
                if results["overall_status"] != "has_errors":
                    results["overall_status"] = "has_warnings"
            else:
                results["validation_errors"].extend([
                    f"{error_prefix}{err}" for err in entity.validation_errors
                ])
                results["overall_status"] = "has_errors"


class EntityValidationResult:
    """Clase utilitaria para manejar resultados de validación de entidades"""
    
    def __init__(self, validation_result: Dict[str, Any]):
        self.result = validation_result
    
    @property
    def is_valid(self) -> bool:
        """Verificar si todas las validaciones fueron exitosas"""
        return self.result.get("overall_status") == "valid"
    
    @property
    def has_warnings(self) -> bool:
        """Verificar si hay advertencias pero no errores críticos"""
        return self.result.get("overall_status") == "has_warnings"
    
    @property
    def has_errors(self) -> bool:
        """Verificar si hay errores críticos"""
        return self.result.get("overall_status") in ["has_errors", "critical_error"]
    
    @property
    def validation_errors(self) -> List[str]:
        """Obtener lista de errores de validación"""
        return self.result.get("validation_errors", [])
    
    @property
    def validation_warnings(self) -> List[str]:
        """Obtener lista de advertencias"""
        return self.result.get("validation_warnings", [])
    
    @property
    def auto_filled_data(self) -> Dict[str, Any]:
        """Obtener datos auto-completados"""
        return self.result.get("auto_filled_data", {})
    
    @property
    def entity_results(self) -> Dict[str, Any]:
        """Obtener resultados detallados por entidad"""
        return self.result.get("entity_results", {})

import asyncio