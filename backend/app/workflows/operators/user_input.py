"""
Simple, stateless User Input Operator.
"""
from typing import Dict, Any, List, Optional
from .base import BaseOperator, TaskResult


class UserInputOperator(BaseOperator):
    """
    Stateless operator that waits for user input.
    Input is provided through context, making it simple and predictable.
    """
    
    def __init__(
        self,
        task_id: str,
        form_config: Dict[str, Any],
        required_fields: Optional[List[str]] = None,
        **kwargs
    ):
        """
        Initialize user input operator.
        
        Args:
            task_id: Unique identifier for this task
            form_config: Configuration for the input form
            required_fields: List of required field names
        """
        # Store config in kwargs for API visibility
        kwargs['form_config'] = form_config
        kwargs['required_fields'] = required_fields or []
        
        super().__init__(task_id, **kwargs)
        self.form_config = form_config
        self.required_fields = required_fields or []
    
    def execute(self, context: Dict[str, Any]) -> TaskResult:
        """
        Check for user input in context and validate it.
        Simple logic: if we have valid input, continue; otherwise, wait.
        """
        # Look for input data in context
        input_key = f"{self.task_id}_input"
        
        if input_key in context:
            # We have input - validate it
            user_input = context[input_key]
            errors = self._validate_input(user_input)
            
            if not errors:
                # Valid input - continue workflow
                return TaskResult(
                    status="continue",
                    data={
                        f"{self.task_id}_validated": True,
                        f"{self.task_id}_data": user_input
                    }
                )
            else:
                # Invalid input - wait for correction
                return TaskResult(
                    status="waiting",
                    data={
                        "waiting_for": "user_input",
                        "form_config": self.form_config,
                        "validation_errors": errors,
                        "previous_input": user_input
                    }
                )
        
        # No input yet - wait for it
        return TaskResult(
            status="waiting",
            data={
                "waiting_for": "user_input",
                "form_config": self.form_config,
                "required_fields": self.required_fields
            }
        )
    
    def _validate_input(self, user_input: Dict[str, Any]) -> List[str]:
        """
        Simple validation - check required fields are present.
        
        Returns:
            List of error messages (empty if valid)
        """
        errors = []
        
        for field in self.required_fields:
            if field not in user_input or not user_input[field]:
                errors.append(f"Required field: {field}")
        
        return errors