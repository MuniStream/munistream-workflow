"""
Simplified Entity Service - Agnostic to entity structure.
"""
from typing import List, Optional, Dict, Any
from datetime import datetime
import uuid
import logging

from ..models.legal_entity import EntityType, LegalEntity, EntityRelationship
from ..core.logging_config import set_workflow_context, get_workflow_context


class EntityService:
    """Service for managing legal entities - completely agnostic"""
    
    @staticmethod
    async def create_entity_type(
        type_id: str,
        name: str,
        alias: Optional[str] = None,
        **kwargs
    ) -> EntityType:
        """Create a simple entity type"""
        entity_type = EntityType(
            type_id=type_id,
            name=name,
            alias=alias,
            **kwargs
        )
        await entity_type.insert()
        return entity_type
    
    @staticmethod
    async def get_entity_type(type_id: str) -> Optional[EntityType]:
        """Get an entity type by ID"""
        return await EntityType.find_one(
            EntityType.type_id == type_id,
            EntityType.is_active == True
        )
    
    @staticmethod
    async def list_entity_types() -> List[EntityType]:
        """List all active entity types"""
        return await EntityType.find(EntityType.is_active == True).to_list()
    
    @staticmethod
    async def create_entity(
        entity_type: str,
        owner_user_id: str,
        name: str,
        data: Dict[str, Any] = None,
        visualization_config: Optional[Dict[str, Any]] = None,
        entity_display_config: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> LegalEntity:
        """
        Create a new entity with any data structure.
        The workflow decides what data to store.
        """
        # Generate unique entity ID
        entity_id = f"{entity_type}_{uuid.uuid4().hex[:8]}"
        
        # Create entity
        entity = LegalEntity(
            entity_id=entity_id,
            entity_type=entity_type,
            owner_user_id=owner_user_id,
            name=name,
            data=data or {},
            visualization_config=visualization_config,
            entity_display_config=entity_display_config,
            **kwargs
        )
        
        await entity.insert()
        return entity
    
    @staticmethod
    async def get_entity(
        entity_id: str,
        owner_user_id: Optional[str] = None
    ) -> Optional[LegalEntity]:
        """Get an entity by ID"""
        query = {"entity_id": entity_id}
        if owner_user_id:
            query["owner_user_id"] = owner_user_id
        
        return await LegalEntity.find_one(query)
    
    @staticmethod
    async def find_entities(
        owner_user_id: Optional[str] = None,
        entity_type: Optional[str] = None,
        filters: Dict[str, Any] = None,
        skip: int = 0,
        limit: int = 20
    ) -> List[LegalEntity]:
        """
        Find entities with flexible filtering.
        Filters can query any field in the data dict using MongoDB syntax.
        """
        query = {}
        
        if owner_user_id:
            query["owner_user_id"] = owner_user_id
        
        if entity_type:
            query["entity_type"] = entity_type
        
        # Add custom filters for data fields
        if filters:
            for key, value in filters.items():
                if key.startswith("data."):
                    # Direct data field query
                    query[key] = value
                elif "." not in key:
                    # Assume it's a data field if no dot
                    query[f"data.{key}"] = value
                else:
                    # Other field query
                    query[key] = value

        print(f"🔍 EntityService.find_entities DEBUG:")
        print(f"   Query: {query}")
        print(f"   Owner user ID: {owner_user_id}")
        print(f"   Entity type: {entity_type}")
        print(f"   Filters: {filters}")

        results = await LegalEntity.find(query).skip(skip).limit(limit).to_list()
        print(f"   Found {len(results)} entities")
        if results:
            for i, entity in enumerate(results):
                print(f"   Result {i}: ID={entity.entity_id}, type={entity.entity_type}, owner={entity.owner_user_id}")

        return results
    
    @staticmethod
    async def update_entity(
        entity_id: str,
        updates: Dict[str, Any],
        owner_user_id: Optional[str] = None
    ) -> Optional[LegalEntity]:
        """Update an entity - completely flexible"""
        entity = await EntityService.get_entity(entity_id, owner_user_id)
        if not entity:
            return None
        
        # Handle data updates specially
        if "data" in updates:
            entity.update_data(updates["data"])
            del updates["data"]
        
        # Apply other updates
        for key, value in updates.items():
            if hasattr(entity, key) and key not in ["entity_id", "owner_user_id", "created_at"]:
                setattr(entity, key, value)
        
        entity.updated_at = datetime.utcnow()
        await entity.save()
        return entity
    
    @staticmethod
    async def add_relationship(
        from_entity_id: str,
        to_entity_id: str,
        relationship_type: str,
        metadata: Dict[str, Any] = None,
        owner_user_id: Optional[str] = None
    ) -> Optional[LegalEntity]:
        """Add a relationship between entities"""
        from_entity = await EntityService.get_entity(from_entity_id, owner_user_id)
        if not from_entity:
            return None
        
        # Verify target entity exists
        to_entity = await EntityService.get_entity(to_entity_id)
        if not to_entity:
            return None
        
        from_entity.add_relationship(
            to_entity_id=to_entity_id,
            relationship_type=relationship_type,
            metadata=metadata or {}
        )
        
        await from_entity.save()
        return from_entity
    
    @staticmethod
    async def get_related_entities(
        entity_id: str,
        relationship_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get entities related to a given entity"""
        entity = await EntityService.get_entity(entity_id)
        if not entity:
            return []
        
        related = []
        for rel in entity.get_relationships(relationship_type):
            related_entity = await EntityService.get_entity(rel.to_entity_id)
            if related_entity:
                related.append({
                    "entity": related_entity.to_display_dict(),
                    "relationship": rel.dict()
                })
        
        return related
    
    @staticmethod
    async def check_entity_exists(
        owner_user_id: str,
        entity_type: str,
        unique_field: str,
        unique_value: Any
    ) -> bool:
        """Check if an entity with a unique field value already exists"""
        existing = await EntityService.find_entities(
            owner_user_id=owner_user_id,
            entity_type=entity_type,
            filters={unique_field: unique_value}
        )
        return len(existing) > 0

    @staticmethod
    async def delete_entity(entity_id: str, owner_user_id: str) -> bool:
        """Delete an entity and all its relationships"""
        logger = logging.getLogger(__name__)

        # Set workflow context for structured logging
        set_workflow_context(
            user_id=owner_user_id,
            tenant=None  # Will be set by caller if available
        )

        logger.info("🗑️ Starting entity deletion", extra={
            "entity_id": entity_id,
            "owner_user_id": owner_user_id,
            "action": "entity_deletion_start"
        })

        try:
            # First verify ownership
            entity = await EntityService.get_entity(entity_id, owner_user_id)
            if not entity:
                logger.warning("Entity not found or access denied", extra={
                    "entity_id": entity_id,
                    "owner_user_id": owner_user_id,
                    "action": "entity_not_found"
                })
                return False

            logger.debug("Entity found, checking for active workflows", extra={
                "entity_id": entity_id,
                "entity_type": entity.entity_type,
                "action": "entity_found"
            })

            # Check for active workflows before deletion
            from ..models.workflow import WorkflowInstance
            try:
                # Debug the query construction
                query = {"context.entity_id": entity_id, "status": {"$in": ["running", "pending", "paused"]}}
                logger.debug("Checking for active workflows with query", extra={
                    "entity_id": entity_id,
                    "query": str(query),
                    "action": "workflow_check_query"
                })

                active_workflows = await WorkflowInstance.find(query).to_list()

                logger.debug("Active workflow check completed", extra={
                    "entity_id": entity_id,
                    "active_workflows_count": len(active_workflows),
                    "action": "workflow_check_result"
                })

                if active_workflows:
                    logger.warning("Cannot delete entity with active workflows", extra={
                        "entity_id": entity_id,
                        "active_workflows_count": len(active_workflows),
                        "workflow_ids": [w.instance_id for w in active_workflows],
                        "action": "deletion_blocked_active_workflows"
                    })
                    raise ValueError("Cannot delete entity with active workflows")

            except Exception as workflow_check_error:
                logger.error("Error checking for active workflows: %s", str(workflow_check_error), extra={
                    "entity_id": entity_id,
                    "error_message": str(workflow_check_error),
                    "error_type": type(workflow_check_error).__name__,
                    "action": "workflow_check_error"
                }, exc_info=True)
                raise

            # Find and clean up relationships where this entity is referenced
            logger.debug("Cleaning up entity relationships", extra={
                "entity_id": entity_id,
                "action": "relationships_cleanup_start"
            })

            # Find entities that have relationships pointing TO this entity
            entities_with_relationships = await LegalEntity.find(
                {"relationships.to_entity_id": entity_id}
            ).to_list()

            relationships_cleaned = 0
            for other_entity in entities_with_relationships:
                # Remove relationships pointing to the entity being deleted
                original_count = len(other_entity.relationships)
                other_entity.relationships = [
                    rel for rel in other_entity.relationships
                    if rel.to_entity_id != entity_id
                ]
                relationships_cleaned += original_count - len(other_entity.relationships)
                other_entity.updated_at = datetime.utcnow()
                await other_entity.save()

            logger.debug("Entity relationships cleaned up", extra={
                "entity_id": entity_id,
                "entities_updated": len(entities_with_relationships),
                "relationships_cleaned": relationships_cleaned,
                "action": "relationships_cleaned"
            })

            # Delete the entity itself
            await entity.delete()

            logger.info("✅ Entity deleted successfully", extra={
                "entity_id": entity_id,
                "owner_user_id": owner_user_id,
                "action": "entity_deletion_success"
            })

            return True

        except Exception as e:
            logger.error("❌ Failed to delete entity: %s", str(e), extra={
                "entity_id": entity_id,
                "owner_user_id": owner_user_id,
                "error_message": str(e),
                "error_type": type(e).__name__,
                "action": "entity_deletion_error"
            }, exc_info=True)
            raise