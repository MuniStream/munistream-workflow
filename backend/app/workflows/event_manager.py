"""
Workflow Event Manager for publishing and handling workflow events.
Integrates with the hook engine to trigger event-driven workflows.
"""
import uuid
from typing import Dict, Any, Optional, List
from datetime import datetime
import logging
import asyncio

from ..models.workflow import WorkflowEvent, EventType
from .hook_engine import WorkflowHookEngine

logger = logging.getLogger(__name__)


class WorkflowEventManager:
    """
    Central event manager for workflow events.
    Handles event publishing, storage, and hook triggering.
    """

    def __init__(self, hook_engine: Optional[WorkflowHookEngine] = None):
        """
        Initialize event manager.

        Args:
            hook_engine: Hook engine for triggering workflows
        """
        self.hook_engine = hook_engine or WorkflowHookEngine()
        self._subscribers = {}  # For direct subscribers (non-hook based)

    async def publish_event(
        self,
        event_type: EventType,
        workflow_id: str,
        instance_id: Optional[str] = None,
        user_id: Optional[str] = None,
        event_data: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Publish a workflow event.

        Args:
            event_type: Type of event
            workflow_id: ID of workflow that generated the event
            instance_id: ID of instance that generated the event
            user_id: User associated with the event
            event_data: Event-specific data
            context: Additional context data

        Returns:
            Event ID
        """
        try:
            # Create event
            event = WorkflowEvent(
                event_id=str(uuid.uuid4()),
                workflow_id=workflow_id,
                instance_id=instance_id,
                event_type=event_type,
                event_data=event_data or {},
                user_id=user_id,
                context=context or {},
                timestamp=datetime.utcnow()
            )

            # Store event in database
            await event.insert()
            logger.info(f"📤 Published event: {event_type} for {workflow_id}")

            # Process hooks asynchronously
            asyncio.create_task(self._process_hooks_async(event))

            # Notify direct subscribers
            await self._notify_subscribers(event)

            return event.event_id

        except Exception as e:
            logger.error(f"❌ Error publishing event: {str(e)}")
            raise

    async def _process_hooks_async(self, event: WorkflowEvent):
        """
        Process hooks for an event asynchronously.

        Args:
            event: Event to process
        """
        try:
            triggered_instances = await self.hook_engine.process_event(event)

            # Update event with triggered workflows
            if triggered_instances:
                event.triggered_admin_workflows = triggered_instances
                event.processed_at = datetime.utcnow()
                await event.save()

                logger.info(f"🔗 Event {event.event_id} triggered {len(triggered_instances)} workflows")

        except Exception as e:
            logger.error(f"❌ Error processing hooks for event {event.event_id}: {str(e)}")

    async def _notify_subscribers(self, event: WorkflowEvent):
        """
        Notify direct subscribers of an event.

        Args:
            event: Event to notify about
        """
        try:
            # Get subscribers for this event type
            subscribers = self._subscribers.get(event.event_type.value, [])

            # Notify each subscriber
            for subscriber in subscribers:
                try:
                    await subscriber(event)
                except Exception as e:
                    logger.error(f"❌ Error notifying subscriber: {str(e)}")

        except Exception as e:
            logger.error(f"❌ Error notifying subscribers: {str(e)}")

    def subscribe(self, event_type: EventType, handler):
        """
        Subscribe to events of a specific type.

        Args:
            event_type: Type of event to subscribe to
            handler: Async function to call when event occurs
        """
        if event_type.value not in self._subscribers:
            self._subscribers[event_type.value] = []

        self._subscribers[event_type.value].append(handler)
        logger.info(f"📧 Subscribed to {event_type} events")

    def unsubscribe(self, event_type: EventType, handler):
        """
        Unsubscribe from events.

        Args:
            event_type: Type of event to unsubscribe from
            handler: Handler to remove
        """
        if event_type.value in self._subscribers:
            try:
                self._subscribers[event_type.value].remove(handler)
                logger.info(f"📧 Unsubscribed from {event_type} events")
            except ValueError:
                pass

    async def publish_workflow_started(
        self,
        workflow_id: str,
        instance_id: str,
        user_id: str,
        initial_context: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Publish workflow started event.

        Args:
            workflow_id: Workflow that started
            instance_id: Instance that started
            user_id: User who started the workflow
            initial_context: Initial workflow context

        Returns:
            Event ID
        """
        return await self.publish_event(
            event_type=EventType.STARTED,
            workflow_id=workflow_id,
            instance_id=instance_id,
            user_id=user_id,
            event_data={
                "initial_context": initial_context or {},
                "started_at": datetime.utcnow().isoformat()
            }
        )

    async def publish_workflow_completed(
        self,
        workflow_id: str,
        instance_id: str,
        user_id: str,
        final_context: Optional[Dict[str, Any]] = None,
        entities_created: Optional[List[str]] = None
    ) -> str:
        """
        Publish workflow completed event.

        Args:
            workflow_id: Workflow that completed
            instance_id: Instance that completed
            user_id: User whose workflow completed
            final_context: Final workflow context
            entities_created: List of entity IDs created during workflow

        Returns:
            Event ID
        """
        return await self.publish_event(
            event_type=EventType.COMPLETED,
            workflow_id=workflow_id,
            instance_id=instance_id,
            user_id=user_id,
            event_data={
                "final_context": final_context or {},
                "entities_created": entities_created or [],
                "completed_at": datetime.utcnow().isoformat()
            }
        )

    async def publish_workflow_failed(
        self,
        workflow_id: str,
        instance_id: str,
        user_id: str,
        error_message: str,
        failed_step: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Publish workflow failed event.

        Args:
            workflow_id: Workflow that failed
            instance_id: Instance that failed
            user_id: User whose workflow failed
            error_message: Error description
            failed_step: Step that failed
            context: Context at time of failure

        Returns:
            Event ID
        """
        return await self.publish_event(
            event_type=EventType.FAILED,
            workflow_id=workflow_id,
            instance_id=instance_id,
            user_id=user_id,
            event_data={
                "error_message": error_message,
                "failed_step": failed_step,
                "context_at_failure": context or {},
                "failed_at": datetime.utcnow().isoformat()
            }
        )

    async def publish_entity_created(
        self,
        workflow_id: str,
        instance_id: str,
        user_id: str,
        entity_id: str,
        entity_type: str,
        entity_data: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Publish entity created event.

        Args:
            workflow_id: Workflow that created the entity
            instance_id: Instance that created the entity
            user_id: User who owns the entity
            entity_id: ID of created entity
            entity_type: Type of entity created
            entity_data: Entity data

        Returns:
            Event ID
        """
        return await self.publish_event(
            event_type=EventType.ENTITY_CREATED,
            workflow_id=workflow_id,
            instance_id=instance_id,
            user_id=user_id,
            event_data={
                "entity_id": entity_id,
                "entity_type": entity_type,
                "entity_data": entity_data or {},
                "created_at": datetime.utcnow().isoformat()
            }
        )

    async def publish_approval_requested(
        self,
        workflow_id: str,
        instance_id: str,
        user_id: str,
        approval_step: str,
        approver_roles: List[str],
        approval_data: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Publish approval requested event.

        Args:
            workflow_id: Workflow requesting approval
            instance_id: Instance requesting approval
            user_id: User whose workflow needs approval
            approval_step: Step that requires approval
            approver_roles: Roles that can approve
            approval_data: Data for approval

        Returns:
            Event ID
        """
        return await self.publish_event(
            event_type=EventType.APPROVAL_REQUESTED,
            workflow_id=workflow_id,
            instance_id=instance_id,
            user_id=user_id,
            event_data={
                "approval_step": approval_step,
                "approver_roles": approver_roles,
                "approval_data": approval_data or {},
                "requested_at": datetime.utcnow().isoformat()
            }
        )

    async def publish_approval_completed(
        self,
        workflow_id: str,
        instance_id: str,
        user_id: str,
        approval_step: str,
        approver_id: str,
        decision: str,
        comments: Optional[str] = None
    ) -> str:
        """
        Publish approval completed event.

        Args:
            workflow_id: Workflow with completed approval
            instance_id: Instance with completed approval
            user_id: User whose workflow was approved/rejected
            approval_step: Step that was approved
            approver_id: User who made the decision
            decision: "approved" or "rejected"
            comments: Approver comments

        Returns:
            Event ID
        """
        return await self.publish_event(
            event_type=EventType.APPROVAL_COMPLETED,
            workflow_id=workflow_id,
            instance_id=instance_id,
            user_id=user_id,
            event_data={
                "approval_step": approval_step,
                "approver_id": approver_id,
                "decision": decision,
                "comments": comments,
                "completed_at": datetime.utcnow().isoformat()
            }
        )

    async def get_events(
        self,
        workflow_id: Optional[str] = None,
        instance_id: Optional[str] = None,
        event_type: Optional[EventType] = None,
        user_id: Optional[str] = None,
        limit: int = 100,
        skip: int = 0
    ) -> List[WorkflowEvent]:
        """
        Get workflow events with filtering.

        Args:
            workflow_id: Filter by workflow ID
            instance_id: Filter by instance ID
            event_type: Filter by event type
            user_id: Filter by user ID
            limit: Maximum number of events
            skip: Number of events to skip

        Returns:
            List of workflow events
        """
        try:
            filters = {}
            if workflow_id:
                filters["workflow_id"] = workflow_id
            if instance_id:
                filters["instance_id"] = instance_id
            if event_type:
                filters["event_type"] = event_type
            if user_id:
                filters["user_id"] = user_id

            events = await WorkflowEvent.find(filters)\
                .sort([("timestamp", -1)])\
                .skip(skip)\
                .limit(limit)\
                .to_list()

            return events

        except Exception as e:
            logger.error(f"❌ Error getting events: {str(e)}")
            return []

    async def get_event_statistics(self) -> Dict[str, Any]:
        """
        Get event statistics.

        Returns:
            Event statistics
        """
        try:
            total_events = await WorkflowEvent.count()

            # Count by event type
            event_types = {}
            all_events = await WorkflowEvent.find().to_list()
            for event in all_events:
                event_type = event.event_type.value
                event_types[event_type] = event_types.get(event_type, 0) + 1

            # Recent activity (last 24 hours)
            from datetime import timedelta
            yesterday = datetime.utcnow() - timedelta(days=1)
            recent_events = await WorkflowEvent.find({"timestamp": {"$gte": yesterday}}).count()

            return {
                "total_events": total_events,
                "recent_events_24h": recent_events,
                "events_by_type": event_types,
                "last_updated": datetime.utcnow().isoformat()
            }

        except Exception as e:
            logger.error(f"❌ Error getting event statistics: {str(e)}")
            return {}


# Global event manager instance
event_manager = WorkflowEventManager()