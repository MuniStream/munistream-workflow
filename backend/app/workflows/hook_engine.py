"""
Workflow Hook Engine for event-driven workflow triggering.
Provides pattern matching and conditional triggering of workflows based on events.
"""
import re
import fnmatch
from typing import List, Dict, Any, Optional
from datetime import datetime
import logging
import uuid

from ..models.workflow import WorkflowHook, WorkflowEvent, HookTriggerType
from ..services.entity_service import EntityService

logger = logging.getLogger(__name__)


class WorkflowHookEngine:
    """
    Engine for managing and executing workflow hooks.
    Handles event pattern matching and conditional triggering.
    """

    def __init__(self, workflow_service=None):
        """
        Initialize hook engine.

        Args:
            workflow_service: Reference to workflow service for triggering workflows
        """
        self.workflow_service = workflow_service
        self.entity_service = EntityService()

    async def register_hook(self, hook: WorkflowHook) -> bool:
        """
        Register a new workflow hook.

        Args:
            hook: WorkflowHook to register

        Returns:
            True if hook was registered successfully
        """
        try:
            # Validate hook configuration
            if not await self._validate_hook(hook):
                logger.error(f"Hook validation failed for {hook.hook_id}")
                return False

            # Save hook to database
            await hook.insert()
            logger.info(f"✅ Registered hook: {hook.hook_id} -> {hook.listener_workflow_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to register hook {hook.hook_id}: {str(e)}")
            return False

    async def unregister_hook(self, hook_id: str) -> bool:
        """
        Unregister a workflow hook.

        Args:
            hook_id: ID of hook to remove

        Returns:
            True if hook was removed successfully
        """
        try:
            hook = await WorkflowHook.find_one({"hook_id": hook_id})
            if hook:
                await hook.delete()
                logger.info(f"🗑️ Unregistered hook: {hook_id}")
                return True
            return False

        except Exception as e:
            logger.error(f"Failed to unregister hook {hook_id}: {str(e)}")
            return False

    async def process_event(self, event: WorkflowEvent) -> List[str]:
        """
        Process a workflow event and trigger matching hooks.

        Args:
            event: WorkflowEvent to process

        Returns:
            List of triggered workflow instance IDs
        """
        triggered_instances = []

        try:
            # Find all matching hooks
            matching_hooks = await self._find_matching_hooks(event)
            logger.info(f"🔍 Found {len(matching_hooks)} matching hooks for event {event.event_id}")

            # Sort hooks by priority (higher priority first)
            matching_hooks.sort(key=lambda h: h.priority, reverse=True)

            # Process each matching hook
            for hook in matching_hooks:
                try:
                    # Check if hook conditions are met
                    if await self._evaluate_hook_conditions(hook, event):
                        # Trigger the workflow
                        instance_id = await self._trigger_workflow(hook, event)
                        if instance_id:
                            triggered_instances.append(instance_id)
                            logger.info(f"✅ Triggered workflow {hook.listener_workflow_id} -> {instance_id}")
                        else:
                            logger.warning(f"⚠️ Failed to trigger workflow {hook.listener_workflow_id}")
                    else:
                        logger.debug(f"🚫 Hook conditions not met for {hook.hook_id}")

                except Exception as e:
                    logger.error(f"❌ Error processing hook {hook.hook_id}: {str(e)}")
                    continue

        except Exception as e:
            logger.error(f"❌ Error processing event {event.event_id}: {str(e)}")

        return triggered_instances

    async def _find_matching_hooks(self, event: WorkflowEvent) -> List[WorkflowHook]:
        """
        Find hooks with event patterns that match the given event.

        Args:
            event: Event to match against

        Returns:
            List of matching hooks
        """
        # Get all enabled hooks
        all_hooks = await WorkflowHook.find({"enabled": True}).to_list()
        matching_hooks = []

        # Create event string for pattern matching
        event_string = f"{event.event_type}.{event.workflow_id}"
        if event.instance_id:
            event_string += f".{event.instance_id}"

        for hook in all_hooks:
            if self._pattern_matches(hook.event_pattern, event_string):
                matching_hooks.append(hook)

        return matching_hooks

    def _pattern_matches(self, pattern: str, event_string: str) -> bool:
        """
        Check if an event pattern matches an event string.
        Supports wildcards (* and ?) and regex patterns.

        Args:
            pattern: Event pattern (supports wildcards)
            event_string: Event string to match

        Returns:
            True if pattern matches
        """
        try:
            # Support different pattern types
            if pattern.startswith("regex:"):
                # Regex pattern
                regex_pattern = pattern[6:]  # Remove "regex:" prefix
                return bool(re.match(regex_pattern, event_string))
            else:
                # Wildcard pattern (default)
                return fnmatch.fnmatch(event_string, pattern)

        except Exception as e:
            logger.error(f"Pattern matching error: {str(e)}")
            return False

    async def _evaluate_hook_conditions(self, hook: WorkflowHook, event: WorkflowEvent) -> bool:
        """
        Evaluate if hook conditions are met for the given event.

        Args:
            hook: Hook to evaluate
            event: Event context

        Returns:
            True if conditions are met
        """
        try:
            # Always trigger for ALWAYS type
            if hook.trigger_type == HookTriggerType.ALWAYS:
                return True

            # Entity-based conditions
            if hook.trigger_type == HookTriggerType.ENTITY_BASED:
                return await self._check_entity_conditions(hook, event)

            # User-based conditions
            if hook.trigger_type == HookTriggerType.USER_BASED:
                return await self._check_user_conditions(hook, event)

            # Conditional evaluation
            if hook.trigger_type == HookTriggerType.CONDITIONAL:
                return await self._evaluate_conditions(hook.conditions, event)

            return True

        except Exception as e:
            logger.error(f"Error evaluating hook conditions: {str(e)}")
            return False

    async def _check_entity_conditions(self, hook: WorkflowHook, event: WorkflowEvent) -> bool:
        """
        Check if required entities exist for entity-based hooks.

        Args:
            hook: Hook with entity requirements
            event: Event context

        Returns:
            True if entity conditions are met
        """
        if not hook.required_entities or not event.user_id:
            return True

        try:
            # Check if user has all required entity types
            for entity_type in hook.required_entities:
                entities = await self.entity_service.get_entities_by_type(
                    owner_user_id=event.user_id,
                    entity_type=entity_type
                )
                if not entities:
                    logger.debug(f"Missing required entity type: {entity_type}")
                    return False

            return True

        except Exception as e:
            logger.error(f"Error checking entity conditions: {str(e)}")
            return False

    async def _check_user_conditions(self, hook: WorkflowHook, event: WorkflowEvent) -> bool:
        """
        Check user-based filter conditions.

        Args:
            hook: Hook with user filters
            event: Event context

        Returns:
            True if user conditions are met
        """
        if not hook.user_filters or not event.user_id:
            return True

        try:
            # Simple user attribute filtering
            # Could be extended to check user roles, groups, etc.
            user_attributes = event.event_data.get("user_attributes", {})

            for filter_key, filter_value in hook.user_filters.items():
                if user_attributes.get(filter_key) != filter_value:
                    return False

            return True

        except Exception as e:
            logger.error(f"Error checking user conditions: {str(e)}")
            return False

    async def _evaluate_conditions(self, conditions: Dict[str, Any], event: WorkflowEvent) -> bool:
        """
        Evaluate conditional expressions against event data.

        Args:
            conditions: Condition expressions
            event: Event context

        Returns:
            True if all conditions are met
        """
        if not conditions:
            return True

        try:
            # Simple condition evaluation
            # Could be extended with more sophisticated expression evaluation
            for condition_key, expected_value in conditions.items():
                actual_value = event.event_data.get(condition_key)

                if isinstance(expected_value, dict):
                    # Support operators like {"gt": 5}, {"in": [1,2,3]}
                    if "eq" in expected_value:
                        if actual_value != expected_value["eq"]:
                            return False
                    elif "gt" in expected_value:
                        if not (actual_value and actual_value > expected_value["gt"]):
                            return False
                    elif "in" in expected_value:
                        if actual_value not in expected_value["in"]:
                            return False
                else:
                    # Simple equality check
                    if actual_value != expected_value:
                        return False

            return True

        except Exception as e:
            logger.error(f"Error evaluating conditions: {str(e)}")
            return False

    async def _trigger_workflow(self, hook: WorkflowHook, event: WorkflowEvent) -> Optional[str]:
        """
        Trigger a workflow based on a hook and event.

        Args:
            hook: Hook configuration
            event: Triggering event

        Returns:
            Instance ID of triggered workflow, or None if failed
        """
        try:
            if not self.workflow_service:
                logger.error("No workflow service available for triggering")
                return None

            # Prepare initial context
            initial_context = {}

            # Pass event context if configured
            if hook.pass_event_context:
                initial_context.update(event.event_data)
                initial_context["triggering_event"] = {
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "workflow_id": event.workflow_id,
                    "instance_id": event.instance_id,
                    "timestamp": event.timestamp.isoformat()
                }

            # Apply context mapping
            if hook.context_mapping:
                mapped_context = {}
                for source_key, target_key in hook.context_mapping.items():
                    if source_key in event.event_data:
                        mapped_context[target_key] = event.event_data[source_key]
                initial_context.update(mapped_context)

            # Use user from event or system user for admin workflows
            user_id = event.user_id or "system"

            # Create workflow instance
            instance = await self.workflow_service.create_instance(
                workflow_id=hook.listener_workflow_id,
                user_id=user_id,
                initial_data=initial_context
            )

            return instance.instance_id

        except Exception as e:
            logger.error(f"Error triggering workflow: {str(e)}")
            return None

    async def _validate_hook(self, hook: WorkflowHook) -> bool:
        """
        Validate hook configuration.

        Args:
            hook: Hook to validate

        Returns:
            True if hook is valid
        """
        try:
            # Check required fields
            if not hook.hook_id or not hook.listener_workflow_id or not hook.event_pattern:
                return False

            # Validate event pattern
            if hook.event_pattern.startswith("regex:"):
                # Test regex compilation
                regex_pattern = hook.event_pattern[6:]
                re.compile(regex_pattern)

            # Check if target workflow exists
            if self.workflow_service:
                dag = await self.workflow_service.get_dag(hook.listener_workflow_id)
                if not dag:
                    logger.error(f"Target workflow not found: {hook.listener_workflow_id}")
                    return False

            return True

        except Exception as e:
            logger.error(f"Hook validation error: {str(e)}")
            return False

    async def list_hooks(self, workflow_id: Optional[str] = None, enabled_only: bool = True) -> List[WorkflowHook]:
        """
        List workflow hooks.

        Args:
            workflow_id: Filter by workflow ID
            enabled_only: Only return enabled hooks

        Returns:
            List of hooks
        """
        try:
            filters = {}
            if workflow_id:
                filters["listener_workflow_id"] = workflow_id
            if enabled_only:
                filters["enabled"] = True

            hooks = await WorkflowHook.find(filters).to_list()
            return hooks

        except Exception as e:
            logger.error(f"Error listing hooks: {str(e)}")
            return []

    async def get_hook_statistics(self) -> Dict[str, Any]:
        """
        Get hook engine statistics.

        Returns:
            Statistics about hooks and triggering
        """
        try:
            total_hooks = await WorkflowHook.count()
            enabled_hooks = await WorkflowHook.find({"enabled": True}).count()

            # Group by workflow type
            hooks_by_workflow = {}
            all_hooks = await WorkflowHook.find().to_list()
            for hook in all_hooks:
                workflow_id = hook.listener_workflow_id
                if workflow_id not in hooks_by_workflow:
                    hooks_by_workflow[workflow_id] = 0
                hooks_by_workflow[workflow_id] += 1

            return {
                "total_hooks": total_hooks,
                "enabled_hooks": enabled_hooks,
                "disabled_hooks": total_hooks - enabled_hooks,
                "hooks_by_workflow": hooks_by_workflow,
                "last_updated": datetime.utcnow().isoformat()
            }

        except Exception as e:
            logger.error(f"Error getting hook statistics: {str(e)}")
            return {}


# Global hook engine instance
hook_engine = WorkflowHookEngine()