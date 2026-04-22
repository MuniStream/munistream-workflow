"""
Simple, stateless User Input Operator.
"""
from typing import Dict, Any, List, Optional
from .base import BaseOperator, TaskResult, TaskStatus


def _item_field_visible(item_field: Dict[str, Any], item: Dict[str, Any]) -> bool:
    """Evaluate show_if condition: an item field is visible when no show_if is set,
    or when the referenced sibling field's value matches the expected value(s)."""
    cond = item_field.get("show_if")
    if not cond:
        return True
    ref = item.get(cond.get("field"))
    expected = cond.get("value")
    if isinstance(expected, list):
        return ref in expected
    return ref == expected


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
                    status=TaskStatus.CONTINUE,
                    data={
                        f"{self.task_id}_validated": True,
                        f"{self.task_id}_data": user_input
                    }
                )
            else:
                # Invalid input - wait for correction
                return TaskResult(
                    status=TaskStatus.WAITING,
                    data={
                        "waiting_for": "user_input",
                        "form_config": self.form_config,
                        "validation_errors": errors,
                        "previous_input": user_input
                    }
                )
        
        # No input yet - wait for it
        return TaskResult(
            status=TaskStatus.WAITING,
            data={
                "waiting_for": "user_input",
                "form_config": self.form_config,
                "required_fields": self.required_fields
            }
        )
    
    def _validate_input(self, user_input: Dict[str, Any]) -> List[str]:
        """
        Validate input: required fields, array item_fields, sum_field constraints.

        Returns:
            List of error messages (empty if valid)
        """
        errors = []

        for field in self.required_fields:
            if field not in user_input or user_input[field] in (None, "", []):
                errors.append(f"Required field: {field}")

        fields_schema = self.form_config.get("fields", []) if isinstance(self.form_config, dict) else []
        for field_cfg in fields_schema:
            if field_cfg.get("type") != "array":
                continue

            name = field_cfg.get("name")
            value = user_input.get(name)
            if value is None:
                continue

            if not isinstance(value, list):
                errors.append(f"Field '{name}' must be a list")
                continue

            min_items = field_cfg.get("min_items")
            max_items = field_cfg.get("max_items")
            if min_items is not None and len(value) < min_items:
                errors.append(f"Field '{name}' requires at least {min_items} item(s)")
            if max_items is not None and len(value) > max_items:
                errors.append(f"Field '{name}' accepts at most {max_items} item(s)")

            item_fields = field_cfg.get("item_fields", [])
            for idx, item in enumerate(value):
                if not isinstance(item, dict):
                    errors.append(f"{name}[{idx}] must be an object")
                    continue
                for item_field in item_fields:
                    if not _item_field_visible(item_field, item):
                        continue
                    fname = item_field.get("name")
                    if item_field.get("required") and item.get(fname) in (None, "", []):
                        errors.append(f"{name}[{idx}].{fname} is required")

            sum_field = field_cfg.get("sum_field")
            sum_equals = field_cfg.get("sum_equals")
            if sum_field is not None and sum_equals is not None and value:
                try:
                    total = sum(float(item.get(sum_field, 0) or 0) for item in value if isinstance(item, dict))
                except (TypeError, ValueError):
                    errors.append(f"{name}: all '{sum_field}' values must be numeric")
                else:
                    if abs(total - float(sum_equals)) > 0.01:
                        errors.append(
                            f"{name}: sum of '{sum_field}' must equal {sum_equals} (got {total})"
                        )

        return errors