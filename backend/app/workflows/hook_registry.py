"""
Hook Registration System for Workflow Types
Allows registering event-driven hooks alongside workflow definitions.
"""
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime

from ..models.workflow import WorkflowHook, HookTriggerType
from .hook_engine import WorkflowHookEngine

logger = logging.getLogger(__name__)


class HookRegistry:
    """
    Registry for workflow hooks that integrates with DAG definitions.
    Allows programmatic registration of hooks alongside workflow creation.
    """

    def __init__(self):
        self.registered_hooks: List[WorkflowHook] = []
        self.hook_engine: Optional[WorkflowHookEngine] = None

    def set_hook_engine(self, hook_engine: WorkflowHookEngine):
        """Set the hook engine reference"""
        self.hook_engine = hook_engine

    def register_hook(
        self,
        hook_id: str,
        listener_workflow_id: str,
        event_pattern: str,
        trigger_type: HookTriggerType = HookTriggerType.ALWAYS,
        priority: int = 0,
        enabled: bool = True,
        conditions: Optional[Dict[str, Any]] = None,
        required_entities: Optional[List[str]] = None,
        user_filters: Optional[Dict[str, Any]] = None,
        pass_event_context: bool = True,
        context_mapping: Optional[Dict[str, str]] = None,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None
    ) -> WorkflowHook:
        """
        Register a workflow hook programmatically.

        Args:
            hook_id: Unique hook identifier
            listener_workflow_id: Workflow that will be triggered
            event_pattern: Event pattern to listen for (supports wildcards)
            trigger_type: Type of trigger condition
            priority: Execution priority (higher = first)
            enabled: Whether this hook is active
            conditions: Conditional expressions for triggering
            required_entities: Required entity types for entity-based triggers
            user_filters: User attribute filters
            pass_event_context: Whether to pass event context to triggered workflow
            context_mapping: Map event context keys to workflow context keys
            description: Hook description
            tags: Hook tags for organization

        Returns:
            WorkflowHook instance
        """
        hook = WorkflowHook(
            hook_id=hook_id,
            listener_workflow_id=listener_workflow_id,
            event_pattern=event_pattern,
            trigger_type=trigger_type,
            priority=priority,
            enabled=enabled,
            conditions=conditions,
            required_entities=required_entities,
            user_filters=user_filters,
            pass_event_context=pass_event_context,
            context_mapping=context_mapping,
            description=description,
            tags=tags,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )

        self.registered_hooks.append(hook)
        logger.info(f"Registered hook: {hook_id} -> {listener_workflow_id} on pattern '{event_pattern}'")

        return hook

    def register_on_workflow_completed(
        self,
        hook_id: str,
        source_workflow_id: str,
        listener_workflow_id: str,
        priority: int = 0,
        **kwargs
    ) -> WorkflowHook:
        """
        Register a hook that triggers when a specific workflow completes.

        Args:
            hook_id: Unique hook identifier
            source_workflow_id: Workflow to listen for completion
            listener_workflow_id: Workflow to trigger
            priority: Execution priority
            **kwargs: Additional hook parameters

        Returns:
            WorkflowHook instance
        """
        event_pattern = f"COMPLETED.{source_workflow_id}"
        return self.register_hook(
            hook_id=hook_id,
            listener_workflow_id=listener_workflow_id,
            event_pattern=event_pattern,
            priority=priority,
            description=f"Triggered when {source_workflow_id} completes",
            **kwargs
        )

    def register_on_workflow_failed(
        self,
        hook_id: str,
        source_workflow_id: str,
        listener_workflow_id: str,
        priority: int = 0,
        **kwargs
    ) -> WorkflowHook:
        """
        Register a hook that triggers when a specific workflow fails.

        Args:
            hook_id: Unique hook identifier
            source_workflow_id: Workflow to listen for failures
            listener_workflow_id: Workflow to trigger
            priority: Execution priority
            **kwargs: Additional hook parameters

        Returns:
            WorkflowHook instance
        """
        event_pattern = f"FAILED.{source_workflow_id}"
        return self.register_hook(
            hook_id=hook_id,
            listener_workflow_id=listener_workflow_id,
            event_pattern=event_pattern,
            priority=priority,
            description=f"Triggered when {source_workflow_id} fails",
            **kwargs
        )

    def register_on_entity_created(
        self,
        hook_id: str,
        entity_type: str,
        listener_workflow_id: str,
        priority: int = 0,
        **kwargs
    ) -> WorkflowHook:
        """
        Register a hook that triggers when entities of a specific type are created.

        Args:
            hook_id: Unique hook identifier
            entity_type: Entity type to listen for
            listener_workflow_id: Workflow to trigger
            priority: Execution priority
            **kwargs: Additional hook parameters

        Returns:
            WorkflowHook instance
        """
        event_pattern = f"ENTITY_CREATED.*"
        conditions = {"entity_type": entity_type}
        return self.register_hook(
            hook_id=hook_id,
            listener_workflow_id=listener_workflow_id,
            event_pattern=event_pattern,
            trigger_type=HookTriggerType.CONDITIONAL,
            conditions=conditions,
            priority=priority,
            description=f"Triggered when {entity_type} entities are created",
            **kwargs
        )

    def register_on_approval_requested(
        self,
        hook_id: str,
        listener_workflow_id: str,
        workflow_pattern: str = "*",
        priority: int = 0,
        **kwargs
    ) -> WorkflowHook:
        """
        Register a hook that triggers when approval is requested.

        Args:
            hook_id: Unique hook identifier
            listener_workflow_id: Workflow to trigger
            workflow_pattern: Pattern for source workflows (default: all)
            priority: Execution priority
            **kwargs: Additional hook parameters

        Returns:
            WorkflowHook instance
        """
        event_pattern = f"APPROVAL_REQUESTED.{workflow_pattern}"
        return self.register_hook(
            hook_id=hook_id,
            listener_workflow_id=listener_workflow_id,
            event_pattern=event_pattern,
            priority=priority,
            description=f"Triggered when approval is requested for {workflow_pattern}",
            **kwargs
        )

    async def persist_hooks(self):
        """
        Persist all registered hooks to the database.
        This should be called during system initialization.
        """
        try:
            for hook in self.registered_hooks:
                # Check if hook already exists
                existing_hook = await WorkflowHook.find_one({"hook_id": hook.hook_id})
                if existing_hook:
                    # Update existing hook
                    existing_hook.listener_workflow_id = hook.listener_workflow_id
                    existing_hook.event_pattern = hook.event_pattern
                    existing_hook.trigger_type = hook.trigger_type
                    existing_hook.priority = hook.priority
                    existing_hook.enabled = hook.enabled
                    existing_hook.conditions = hook.conditions
                    existing_hook.required_entities = hook.required_entities
                    existing_hook.user_filters = hook.user_filters
                    existing_hook.pass_event_context = hook.pass_event_context
                    existing_hook.context_mapping = hook.context_mapping
                    existing_hook.description = hook.description
                    existing_hook.tags = hook.tags
                    existing_hook.updated_at = datetime.utcnow()
                    await existing_hook.save()
                    logger.info(f"Updated existing hook: {hook.hook_id}")
                else:
                    # Insert new hook
                    await hook.insert()
                    logger.info(f"Persisted new hook: {hook.hook_id}")

        except Exception as e:
            logger.error(f"Error persisting hooks: {str(e)}")
            raise

    def clear_registry(self):
        """Clear all registered hooks from memory"""
        self.registered_hooks.clear()

    def get_registered_hooks(self) -> List[WorkflowHook]:
        """Get all registered hooks"""
        return self.registered_hooks.copy()


# Global hook registry instance
hook_registry = HookRegistry()


# Convenience functions for workflow definitions
def register_workflow_hook(
    hook_id: str,
    listener_workflow_id: str,
    event_pattern: str,
    **kwargs
) -> WorkflowHook:
    """
    Convenience function for registering hooks in workflow definitions.

    Usage in workflow files:
        register_workflow_hook(
            hook_id="cleanup_on_failure",
            listener_workflow_id="cleanup_workflow",
            event_pattern="FAILED.*",
            priority=100
        )
    """
    return hook_registry.register_hook(
        hook_id=hook_id,
        listener_workflow_id=listener_workflow_id,
        event_pattern=event_pattern,
        **kwargs
    )


def register_on_completed(
    hook_id: str,
    source_workflow_id: str,
    listener_workflow_id: str,
    **kwargs
) -> WorkflowHook:
    """Convenience function for completion hooks"""
    return hook_registry.register_on_workflow_completed(
        hook_id=hook_id,
        source_workflow_id=source_workflow_id,
        listener_workflow_id=listener_workflow_id,
        **kwargs
    )


def register_on_failed(
    hook_id: str,
    source_workflow_id: str,
    listener_workflow_id: str,
    **kwargs
) -> WorkflowHook:
    """Convenience function for failure hooks"""
    return hook_registry.register_on_workflow_failed(
        hook_id=hook_id,
        source_workflow_id=source_workflow_id,
        listener_workflow_id=listener_workflow_id,
        **kwargs
    )


def register_on_entity(
    hook_id: str,
    entity_type: str,
    listener_workflow_id: str,
    **kwargs
) -> WorkflowHook:
    """Convenience function for entity creation hooks"""
    return hook_registry.register_on_entity_created(
        hook_id=hook_id,
        entity_type=entity_type,
        listener_workflow_id=listener_workflow_id,
        **kwargs
    )


def register_on_approval(
    hook_id: str,
    listener_workflow_id: str,
    workflow_pattern: str = "*",
    **kwargs
) -> WorkflowHook:
    """Convenience function for approval hooks"""
    return hook_registry.register_on_approval_requested(
        hook_id=hook_id,
        listener_workflow_id=listener_workflow_id,
        workflow_pattern=workflow_pattern,
        **kwargs
    )