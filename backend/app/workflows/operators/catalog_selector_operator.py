"""
Simple CatalogSelectorOperator following UserInputOperator pattern.
KISS and DRY principles - minimal, stateless implementation.
"""

from typing import Dict, Any, List, Optional
from datetime import datetime

from .base import BaseOperator, TaskResult, TaskState
from ...core.logging_config import get_workflow_logger

logger = get_workflow_logger(__name__)


class CatalogSelectorOperator(BaseOperator):
    """
    Simple, stateless operator for catalog selection.
    Follows the exact same pattern as UserInputOperator - simple and predictable.

    Supports multiple interface modes:
    - "table": Traditional table-based selection with pagination, search, and filters
    - "exact_match": Input fields for exact match on specific columns
    - "hierarchical": Cascading dropdowns for hierarchical navigation (e.g., estado→municipio→colonia)
    """

    def __init__(
        self,
        task_id: str,
        catalog_id: str,
        selection_mode: str = "single",
        interface_mode: str = "table",
        min_selections: int = 1,
        max_selections: Optional[int] = None,
        display_columns: Optional[List[str]] = None,
        selection_fields: Optional[List[str]] = None,
        store_as: str = None,
        required_message: str = None,
        search_enabled: bool = True,
        filters_enabled: bool = True,
        sorting_enabled: bool = True,
        pagination_enabled: bool = True,
        page_size: int = 20,
        default_filters: Optional[Dict[str, Any]] = None,
        exact_match_columns: Optional[List[str]] = None,
        hierarchical_columns: Optional[List[str]] = None,
        **kwargs
    ):
        """
        Initialize catalog selector operator.

        Args:
            task_id: Unique identifier for this task
            catalog_id: ID of the catalog to select from
            selection_mode: "single" or "multiple"
            interface_mode: Interface type - "table", "exact_match", "hierarchical"
            min_selections: Minimum number of selections required
            max_selections: Maximum number of selections allowed
            display_columns: Columns to show in the interface
            selection_fields: Fields to store from selected items
            store_as: Key name to store selection in context
            required_message: Message to show when selection is required
            exact_match_columns: For exact_match mode - columns to show as inputs
            hierarchical_columns: For hierarchical mode - columns for dropdown cascade (e.g. ["estado", "municipio", "colonia"])
        """
        super().__init__(task_id=task_id, **kwargs)

        self.catalog_id = catalog_id
        self.selection_mode = selection_mode
        self.interface_mode = interface_mode
        self.min_selections = min_selections
        self.max_selections = max_selections or (1 if selection_mode == "single" else 10)
        self.display_columns = display_columns or []
        self.selection_fields = selection_fields or []
        self.store_as = store_as or f"{task_id}_selection"
        self.required_message = required_message or f"Please select from {catalog_id}"
        self.search_enabled = search_enabled
        self.filters_enabled = filters_enabled
        self.sorting_enabled = sorting_enabled
        self.pagination_enabled = pagination_enabled
        self.page_size = page_size
        self.default_filters = default_filters or {}
        self.exact_match_columns = exact_match_columns or []
        self.hierarchical_columns = hierarchical_columns or []

    def execute(self, context: Dict[str, Any]) -> TaskResult:
        """
        Execute catalog selection - follows simple stateless pattern like UserInputOperator.
        Simple logic: if we have valid input, continue; otherwise, wait.
        """
        logger.info(f"CatalogSelectorOperator executing for catalog: {self.catalog_id}")

        # Look for input data in context (same pattern as UserInputOperator)
        input_key = f"{self.task_id}_input"

        if input_key in context:
            # We have input - validate and process it
            user_input = context[input_key]

            # Check if we have catalog_selection (JSON string) or selected_items (array)
            if "catalog_selection" in user_input:
                import json
                try:
                    selected_items = json.loads(user_input["catalog_selection"])
                except (json.JSONDecodeError, TypeError):
                    selected_items = []
            else:
                selected_items = user_input.get("selected_items", [])

                # Handle case where selected_items is a JSON string (from FormData)
                if isinstance(selected_items, str):
                    import json
                    try:
                        selected_items = json.loads(selected_items)
                    except (json.JSONDecodeError, TypeError):
                        selected_items = []

                # Handle case where selected_items is a single object (single mode)
                if not isinstance(selected_items, list):
                    selected_items = [selected_items]

            # Validate selection
            errors = self._validate_selection(selected_items)

            if not errors:
                # Valid selection - process and continue workflow
                return self._process_valid_selection(selected_items)
            else:
                # Invalid selection - wait for correction
                self.state.waiting_for = "catalog_selection"
                return TaskResult(
                    status="waiting",
                    data={
                        "waiting_for": "catalog_selection",
                        "form_config": self._build_form_config(),
                        "validation_errors": errors,
                        "previous_input": user_input
                    }
                )

        # No input yet - wait for it
        logger.info(f"No user input found, showing catalog selection interface")

        # Set waiting_for in state (same pattern as other operators)
        self.state.waiting_for = "catalog_selection"

        return TaskResult(
            status="waiting",
            data={
                "waiting_for": "catalog_selection",
                "form_config": self._build_form_config()
            }
        )

    def _build_form_config(self) -> Dict[str, Any]:
        """Build form configuration for catalog selection interface"""
        # Resolve dynamic filters using current context
        resolved_filters = self._resolve_dynamic_filters(self.default_filters)

        return {
            "title": f"Select from {self.catalog_id}",
            "description": self.required_message,
            "type": "catalog_selector",
            "catalog_config": {
                "catalog_id": self.catalog_id,
                "catalog_name": self.catalog_id.replace("_", " ").title(),
                "selection_mode": self.selection_mode,
                "interface_mode": self.interface_mode,
                "min_selections": self.min_selections,
                "max_selections": self.max_selections,
                "display_columns": self.display_columns,
                "search_enabled": self.search_enabled,
                "filters_enabled": self.filters_enabled,
                "sorting_enabled": self.sorting_enabled,
                "pagination_enabled": self.pagination_enabled,
                "page_size": self.page_size,
                "default_filters": resolved_filters,
                "exact_match_columns": self.exact_match_columns,
                "hierarchical_columns": self.hierarchical_columns
            },
            "validation": {
                "required": True,
                "min_selections": self.min_selections,
                "max_selections": self.max_selections
            }
        }

    def _resolve_dynamic_filters(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve dynamic filter templates like {{selected_clave.clave_catastral}}"""
        if not filters:
            return {}

        resolved = {}
        for key, value in filters.items():
            if isinstance(value, str) and value.startswith("{{") and value.endswith("}}"):
                # Extract the variable path: {{selected_clave.clave_catastral}} -> selected_clave.clave_catastral
                var_path = value[2:-2].strip()
                resolved_value = self._get_context_value(var_path)
                if resolved_value is not None:
                    resolved[key] = resolved_value
                # If can't resolve, skip the filter
            else:
                resolved[key] = value

        return resolved

    def _get_context_value(self, var_path: str) -> Any:
        """Get value from workflow context using dot notation (e.g., 'selected_clave.clave_catastral')"""
        if not hasattr(self, 'context'):
            return None

        parts = var_path.split('.')
        current = self.context

        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None

        return current

    def _validate_selection(self, selected_items: List[Dict[str, Any]]) -> List[str]:
        """
        Simple validation - check selection constraints.

        Returns:
            List of error messages (empty if valid)
        """
        errors = []

        # Check minimum selections
        if len(selected_items) < self.min_selections:
            errors.append(f"Please select at least {self.min_selections} item(s)")

        # Check maximum selections
        if len(selected_items) > self.max_selections:
            errors.append(f"Please select no more than {self.max_selections} item(s)")

        # For single mode, ensure only one item
        if self.selection_mode == "single" and len(selected_items) > 1:
            errors.append("Only one selection allowed in single mode")

        return errors

    def _process_valid_selection(self, selected_items: List[Dict[str, Any]]) -> TaskResult:
        """Process valid selection and store in context"""
        # Filter selected data to only include requested fields
        processed_items = []
        for item in selected_items:
            if isinstance(item, dict):
                if self.selection_fields:
                    # Only keep specified fields
                    filtered_item = {
                        field: item.get(field)
                        for field in self.selection_fields
                        if field in item
                    }
                else:
                    # Keep all fields
                    filtered_item = item.copy()
                processed_items.append(filtered_item)

        # Store selection in context
        if self.selection_mode == "single":
            selection_value = processed_items[0] if processed_items else None
        else:
            selection_value = processed_items

        result_data = {
            self.store_as: selection_value,
            f"{self.store_as}_count": len(processed_items),
            f"{self.store_as}_catalog_id": self.catalog_id,
            "selection_timestamp": datetime.utcnow().isoformat()
        }

        logger.info(f"User selected {len(processed_items)} items from catalog {self.catalog_id}")

        return TaskResult(
            status="continue",
            data=result_data
        )