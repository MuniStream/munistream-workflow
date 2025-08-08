"""
User Input Operator for collecting data from users.
"""
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime, timedelta

from .base import BaseOperator, TaskResult, TaskStatus


class UserInputOperator(BaseOperator):
    """
    Collects input from users - completely self-contained.
    This operator doesn't know about other steps, it only knows
    it needs to collect specific data from a user.
    """
    
    def __init__(
        self,
        task_id: str,
        form_config: Dict[str, Any],
        required_fields: Optional[List[str]] = None,
        validators: Optional[Dict[str, Callable]] = None,
        timeout_hours: Optional[int] = 24,
        **kwargs
    ):
        """
        Initialize user input operator.
        
        Args:
            task_id: Unique identifier for this task
            form_config: Configuration for the input form
            required_fields: List of required field names
            validators: Dictionary of field validators
            timeout_hours: Hours to wait for input before timeout
            **kwargs: Additional configuration
        """
        super().__init__(task_id, **kwargs)
        self.form_config = form_config
        self.required_fields = required_fields or []
        self.validators = validators or {}
        self.timeout_hours = timeout_hours
        self.request_sent_at: Optional[datetime] = None
    
    def execute(self, context: Dict[str, Any]) -> TaskResult:
        """
        Request and validate user input.
        
        Args:
            context: Execution context
            
        Returns:
            TaskResult with status and collected data
        """
        # Check if we already have input
        if self.state.has_input and self.state.user_input:
            # Validate the received input
            validation_result = self.validate_input(self.state.user_input)
            
            if validation_result["is_valid"]:
                # Input is valid, continue
                return TaskResult(
                    status="continue",
                    data=self.state.user_input
                )
            else:
                # Input is invalid, retry
                self.state.has_input = False
                self.state.user_input = None
                return TaskResult(
                    status="retry",
                    error=f"Validation failed: {', '.join(validation_result['errors'])}"
                )
        
        # Check for timeout
        if self.request_sent_at and self.timeout_hours:
            timeout_at = self.request_sent_at + timedelta(hours=self.timeout_hours)
            if datetime.utcnow() > timeout_at:
                return TaskResult(
                    status="failed",
                    error=f"User input timeout after {self.timeout_hours} hours"
                )
        
        # Request user input if not already done
        if not self.request_sent_at:
            self.request_user_input()
            self.request_sent_at = datetime.utcnow()
        
        # Still waiting for input
        self.state.status = TaskStatus.WAITING_INPUT
        self.state.waiting_for = "user_input"
        
        return TaskResult(
            status="waiting",
            data={"waiting_for": "user_input", "form_config": self.form_config}
        )
    
    def request_user_input(self):
        """Send request for user input"""
        # This would typically trigger a notification or update UI
        # The actual implementation would depend on the notification system
        self.state.metadata["input_requested_at"] = datetime.utcnow().isoformat()
        self.state.metadata["form_config"] = self.form_config
        self.state.metadata["required_fields"] = self.required_fields
    
    def validate_input(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate user input.
        
        Args:
            input_data: Data provided by user
            
        Returns:
            Dictionary with is_valid and errors
        """
        errors = []
        
        # Check required fields
        for field in self.required_fields:
            if field not in input_data or input_data[field] is None:
                errors.append(f"Campo requerido: {field}")
        
        # Run custom validators
        for field, validator in self.validators.items():
            if field in input_data:
                try:
                    if not validator(input_data[field]):
                        errors.append(f"Validación fallida para: {field}")
                except Exception as e:
                    errors.append(f"Error validando {field}: {str(e)}")
        
        return {
            "is_valid": len(errors) == 0,
            "errors": errors
        }
    
    def receive_input(self, input_data: Dict[str, Any]):
        """
        Receive input from user (called by external system).
        
        Args:
            input_data: Data provided by user
        """
        self.state.has_input = True
        self.state.user_input = input_data
        self.state.metadata["input_received_at"] = datetime.utcnow().isoformat()


class FormOperator(UserInputOperator):
    """
    Specialized operator for form-based user input with predefined templates.
    """
    
    def __init__(
        self,
        task_id: str,
        form_template: str,
        **kwargs
    ):
        """
        Initialize form operator with a template.
        
        Args:
            task_id: Unique identifier
            form_template: Name of the form template to use
            **kwargs: Additional configuration
        """
        # Load form configuration from template
        form_config = self.load_form_template(form_template)
        
        super().__init__(
            task_id=task_id,
            form_config=form_config,
            **kwargs
        )
        self.form_template = form_template
    
    def load_form_template(self, template_name: str) -> Dict[str, Any]:
        """
        Load form configuration from template.
        
        Args:
            template_name: Name of the template
            
        Returns:
            Form configuration dictionary
        """
        # Templates for common forms
        templates = {
            "identity_form": {
                "title": "Datos de Identidad",
                "fields": [
                    {"name": "nombre", "type": "text", "required": True},
                    {"name": "rfc", "type": "text", "required": True, "pattern": "^[A-Z]{4}[0-9]{6}[A-Z0-9]{3}$"},
                    {"name": "curp", "type": "text", "required": True, "pattern": "^[A-Z]{4}[0-9]{6}[HM][A-Z]{5}[0-9]{2}$"},
                    {"name": "direccion", "type": "text", "required": True},
                    {"name": "telefono", "type": "tel", "required": False},
                    {"name": "email", "type": "email", "required": False}
                ]
            },
            "property_form": {
                "title": "Datos del Inmueble",
                "fields": [
                    {"name": "clave_catastral", "type": "text", "required": True},
                    {"name": "direccion_inmueble", "type": "text", "required": True},
                    {"name": "superficie", "type": "number", "required": True},
                    {"name": "uso_suelo", "type": "select", "required": True, "options": ["Habitacional", "Comercial", "Industrial", "Mixto"]},
                    {"name": "valor_catastral", "type": "number", "required": False}
                ]
            },
            "document_upload": {
                "title": "Carga de Documentos",
                "fields": [
                    {"name": "tipo_documento", "type": "select", "required": True, "options": ["Escritura", "Comprobante domicilio", "Identificación", "Otro"]},
                    {"name": "archivo", "type": "file", "required": True, "accept": ".pdf,.jpg,.png"},
                    {"name": "descripcion", "type": "textarea", "required": False}
                ]
            }
        }
        
        return templates.get(template_name, {
            "title": "Formulario",
            "fields": []
        })