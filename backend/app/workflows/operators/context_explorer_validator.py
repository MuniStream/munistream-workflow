"""
ContextExplorerValidator - Operator for exploring and validating workflow context.

This operator displays comprehensive information from any specified workflow context,
including selected entities with their visualizers, form data, and other contextual information.
Designed for administrative workflows to provide full visibility into any workflow data.
"""

from typing import Dict, Any, List, Optional
import logging
from datetime import datetime

from .base import BaseOperator, TaskResult
from ...services.entity_service import EntityService
from ...core.logging_config import get_workflow_logger

logger = get_workflow_logger(__name__)


class ContextExplorerValidator(BaseOperator):
    """
    Operator that displays specified workflow context information for validation.

    Shows selected entities with their visualizers, form data, and other contextual
    information from the specified context path. Provides a comprehensive view for
    administrative validation and review.
    """

    def __init__(
        self,
        task_id: str,
        context_path: str = "_parent_context",
        title: str = "Context Explorer - Workflow Information",
        description: str = "Review and validate workflow information",
        show_raw_context: bool = True,
        show_selected_entities: bool = True,
        show_form_data: bool = True,
        show_user_info: bool = True,
        entity_display_fields: Optional[List[str]] = None,
        **kwargs
    ):
        """
        Initialize the ContextExplorerValidator.

        Args:
            task_id: Unique task identifier
            context_path: Path to context data (e.g., "_parent_context", "original_context")
            title: Title for the explorer interface
            description: Description of what's being reviewed
            show_raw_context: Whether to show raw context data
            show_selected_entities: Whether to show selected entities with visualizers
            show_form_data: Whether to show form submission data
            show_user_info: Whether to show user/customer information
            entity_display_fields: Fields to display for each entity
            **kwargs: Additional arguments passed to BaseOperator
        """
        super().__init__(task_id=task_id, **kwargs)
        self.context_path = context_path
        self.title = title
        self.description = description
        self.show_raw_context = show_raw_context
        self.show_selected_entities = show_selected_entities
        self.show_form_data = show_form_data
        self.show_user_info = show_user_info
        self.entity_display_fields = entity_display_fields or [
            "name", "entity_type", "upload_date", "file_size", "status"
        ]

    def execute(self, context: Dict[str, Any]) -> TaskResult:
        """
        Synchronous wrapper for async execution.
        Used when the executor doesn't call execute_async directly.
        """
        import asyncio
        return asyncio.run(self.execute_async(context))

    async def execute_async(self, context: Dict[str, Any]) -> TaskResult:
        """Async execution for entity loading"""

        logger.info("ContextExplorerValidator async execution started",
                   context_path=self.context_path)

        # Check if user has submitted validation decision
        input_key = f"{self.task_id}_input"
        if input_key in context:
            return self._process_validation_decision(context)

        # Display context explorer interface with async entity loading
        return await self._display_context_explorer(context)

    def _process_validation_decision(self, context: Dict[str, Any]) -> TaskResult:
        """Process the validation decision from the user"""

        input_data = context.get(f"{self.task_id}_input", {})
        decision = input_data.get("validation_decision")
        comments = input_data.get("validation_comments", "")

        logger.info("Processing validation decision",
                   decision=decision,
                   has_comments=bool(comments))

        if decision == "approved":
            return TaskResult(
                status="continue",
                data={
                    "validation_decision": "approved",
                    "validation_comments": comments,
                    "validated_at": datetime.utcnow().isoformat(),
                    "validated_by": context.get("user_id", "system")
                }
            )
        elif decision == "rejected":
            return TaskResult(
                status="failed",
                data={
                    "validation_decision": "rejected",
                    "validation_comments": comments,
                    "rejected_at": datetime.utcnow().isoformat(),
                    "rejected_by": context.get("user_id", "system"),
                    "error": f"Context validation rejected: {comments}"
                }
            )
        else:
            # Invalid decision, show form again
            return TaskResult(
                status="waiting",
                data={
                    "waiting_for": "context_validation",
                    "validation_errors": ["Please select a validation decision"]
                }
            )

    async def _display_context_explorer(
        self,
        context: Dict[str, Any],
        validation_errors: Optional[List[str]] = None
    ) -> TaskResult:
        """Display the context explorer interface"""

        logger.info("Displaying context explorer interface",
                   context_path=self.context_path)

        # Get specified context
        target_context = self._get_context_data(context)

        if not target_context:
            logger.warning("No context found at specified path",
                          context_path=self.context_path)
            return TaskResult(
                status="failed",
                data={
                    "error": f"No workflow context available at path: {self.context_path}",
                    "available_keys": list(context.keys())
                }
            )

        # Build the explorer interface
        form_config = {
            "title": self.title,
            "description": f"{self.description} (Context: {self.context_path})",
            "type": "context_explorer_validation",
            "sections": []
        }

        # Add validation errors if any
        if validation_errors:
            form_config["validation_errors"] = validation_errors

        # Section 1: User Information
        if self.show_user_info:
            user_section = self._build_user_info_section(target_context)
            if user_section:
                form_config["sections"].append(user_section)

        # Section 2: Selected Entities
        if self.show_selected_entities:
            entities_section = await self._build_selected_entities_section(target_context)
            if entities_section:
                form_config["sections"].append(entities_section)

        # Section 3: Form Data
        if self.show_form_data:
            form_section = self._build_form_data_section(target_context)
            if form_section:
                form_config["sections"].append(form_section)

        # Section 4: Raw Context (optional)
        if self.show_raw_context:
            context_section = self._build_raw_context_section(target_context)
            if context_section:
                form_config["sections"].append(context_section)

        # Add validation decision fields
        form_config["validation_fields"] = [
            {
                "name": "validation_decision",
                "label": "Validation Decision",
                "type": "radio",
                "required": True,
                "options": [
                    {"value": "approved", "label": "Approve - Context is valid and complete"},
                    {"value": "rejected", "label": "Reject - Context has issues or is incomplete"}
                ]
            },
            {
                "name": "validation_comments",
                "label": "Validation Comments",
                "type": "textarea",
                "required": False,
                "placeholder": "Optional comments about the validation decision..."
            }
        ]

        return TaskResult(
            status="waiting",
            data={
                "waiting_for": "context_validation",
                "form_config": form_config
            }
        )

    def _get_context_data(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Get context data from the specified path"""

        # Support nested paths like "parent.grandparent.context"
        if "." in self.context_path:
            current = context
            for part in self.context_path.split("."):
                if isinstance(current, dict) and part in current:
                    current = current[part]
                else:
                    return None
            return current if isinstance(current, dict) else None
        else:
            # Simple path
            return context.get(self.context_path)

    def _build_user_info_section(self, target_context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Build user information section"""

        user_info = {}

        # Collect user-related fields
        user_fields = [
            ("customer_name", "Customer Name"),
            ("customer_email", "Customer Email"),
            ("customer_id", "Customer ID"),
            ("user_id", "User ID"),
            ("parent_user_id", "Parent User ID"),
            ("parent_customer_email", "Parent Customer Email")
        ]

        for field_key, field_label in user_fields:
            if field_key in target_context:
                user_info[field_label] = target_context[field_key]

        if not user_info:
            return None

        return {
            "title": "👤 User Information",
            "type": "info_display",
            "data": user_info
        }

    async def _build_selected_entities_section(
        self,
        target_context: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Build selected entities section with visualizers"""

        selected_entities = target_context.get("selected_entities", {})

        if not selected_entities:
            return {
                "title": "📎 Selected Entities",
                "type": "info_display",
                "data": {"message": "No entities were selected in this workflow"}
            }

        entities_data = {}

        for entity_group, entity_ids in selected_entities.items():
            if not entity_ids:
                continue

            # Get entity details
            try:
                entities = []
                for entity_id in entity_ids:
                    entity = await EntityService.get_entity(entity_id)
                    if entity:
                        entity_info = {
                            "entity_id": entity.entity_id,
                            "name": entity.name,
                            "entity_type": entity.entity_type,
                            "created_at": entity.created_at.isoformat() if entity.created_at else None
                        }

                        # Add display fields
                        for field in self.entity_display_fields:
                            if hasattr(entity, field):
                                value = getattr(entity, field)
                                if value is not None:
                                    entity_info[field] = value
                            elif field in entity.data:
                                entity_info[field] = entity.data[field]

                        # Add visualizer if available
                        if hasattr(entity, 'visualizer') and entity.visualizer:
                            entity_info["visualizer"] = entity.visualizer

                        entities.append(entity_info)

                if entities:
                    entities_data[entity_group] = entities

            except Exception as e:
                logger.error(f"Error loading entities for {entity_group}: {str(e)}")
                entities_data[entity_group] = [{"error": f"Failed to load entities: {str(e)}"}]

        return {
            "title": "📎 Selected Entities",
            "type": "entities_display",
            "data": entities_data
        }

    def _build_form_data_section(self, target_context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Build form data section"""

        form_data = {}

        # Look for common form data patterns
        form_patterns = [
            ("collect_concession_data_data", "Concession Data"),
            ("collect_property_data_data", "Property Data"),
            ("collect_permit_data_data", "Permit Data"),
            ("form_data", "Form Data"),
            ("user_input", "User Input")
        ]

        for pattern_key, pattern_label in form_patterns:
            if pattern_key in target_context:
                data = target_context[pattern_key]
                if isinstance(data, dict) and data:
                    form_data[pattern_label] = data

        # Also look for any field ending with '_data'
        for key, value in target_context.items():
            if key.endswith('_data') and isinstance(value, dict) and value:
                if key not in [p[0] for p in form_patterns]:
                    label = key.replace('_', ' ').title()
                    form_data[label] = value

        if not form_data:
            return None

        return {
            "title": "📝 Form Submission Data",
            "type": "form_data_display",
            "data": form_data
        }

    def _build_raw_context_section(self, target_context: Dict[str, Any]) -> Dict[str, Any]:
        """Build raw context section"""

        # Filter out sensitive or unnecessary fields
        filtered_context = {}
        skip_fields = {
            '_parent_context', 'password', 'secret', 'token', 'key',
            'digital_signature_private_key', 'digital_signature_password'
        }

        for key, value in target_context.items():
            if not any(skip in key.lower() for skip in skip_fields):
                filtered_context[key] = value

        return {
            "title": f"🔍 Raw Context Data ({self.context_path})",
            "type": "json_display",
            "data": filtered_context,
            "collapsible": True,
            "collapsed": True
        }