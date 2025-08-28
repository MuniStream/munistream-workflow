"""
DAG Operators for working with Legal Entities.
These operators allow workflows to create, validate, and require entities.
"""
from typing import Dict, Any, List, Optional
from datetime import datetime
from enum import Enum

from .base import BaseOperator, TaskResult
from ...services.entity_service import EntityService
from ...models.legal_entity import LegalEntity


class EntityCreationOperator(BaseOperator):
    """
    Operator that creates a new legal entity during workflow execution.
    Completely agnostic - the workflow defines what data to store.
    """
    
    def __init__(
        self,
        task_id: str,
        entity_type: str,
        name_source: str = "name",  # Where to get the entity name from context
        data_mapping: Optional[Dict[str, str]] = None,  # Map context fields to entity data
        static_data: Optional[Dict[str, Any]] = None,  # Static data to include
        **kwargs
    ):
        """
        Initialize entity creation operator.
        
        Args:
            task_id: Unique task identifier
            entity_type: Type of entity to create (just a string like "person", "property")
            name_source: Context field or static string for entity name
            data_mapping: Map context keys to entity data fields
            static_data: Static data to always include
        """
        super().__init__(task_id, **kwargs)
        self.entity_type = entity_type
        self.name_source = name_source
        self.data_mapping = data_mapping or {}
        self.static_data = static_data or {}
    
    def execute(self, context: Dict[str, Any]) -> TaskResult:
        """Create the entity based on workflow context"""
        try:
            print(f"🔍 EntityCreationOperator: Starting entity creation")
            print(f"   Context keys: {list(context.keys())}")
            print(f"   Entity type: {self.entity_type}")
            print(f"   Name source: {self.name_source}")
            
            # Get user ID from context - could be user_id or customer_id
            user_id = context.get("user_id") or context.get("customer_id")
            if not user_id:
                print(f"❌ EntityCreationOperator: No user_id or customer_id in context")
                return TaskResult(
                    status="failed",
                    error="No user_id or customer_id in context"
                )
            
            # Get entity name - can be from context or static
            if self.name_source in context:
                entity_name = context[self.name_source]
                print(f"   Found entity name in context: {entity_name}")
            else:
                entity_name = self.name_source  # Use as static string
                print(f"   Using static entity name: {entity_name}")
            
            # Build entity data from mappings and static data
            entity_data = dict(self.static_data)  # Start with static data
            
            # Map context fields to entity data
            for context_key, data_field in self.data_mapping.items():
                # Support nested context keys like "user_input.field"
                value = context
                for key_part in context_key.split("."):
                    if isinstance(value, dict):
                        value = value.get(key_part)
                        if value is None:
                            break
                
                if value is not None:
                    entity_data[data_field] = value
            
            # For async operations, we'll handle this in execute_async
            # Store the parameters for async execution
            self._entity_params = {
                "entity_type": self.entity_type,
                "owner_user_id": user_id,
                "name": entity_name,
                "data": entity_data,
                "created_by_workflow": context.get("instance_id")
            }
            
            # Return pending status - will be handled by execute_async
            return TaskResult(
                status="pending_async",
                data={}
            )
            
        except Exception as e:
            return TaskResult(
                status="failed",
                error=f"Failed to create entity: {str(e)}"
            )
    
    async def execute_async(self, context: Dict[str, Any]) -> str:
        """Async execution method for entity creation"""
        # First run the regular execute to prepare parameters
        result = self.execute(context)
        
        # If we have entity params to create, do it now
        if hasattr(self, '_entity_params') and self._entity_params:
            # Log operator action - this is what the user will see
            await self.log_info(
                f"Creating {self.entity_type} entity: {self._entity_params.get('name')}",
                details={
                    "entity_type": self.entity_type,
                    "data_fields": list(self._entity_params.get('data', {}).keys())
                }
            )
            
            print(f"   Creating entity with async executor...")
            try:
                entity = await EntityService.create_entity(**self._entity_params)
                print(f"   ✅ Entity created successfully: {entity.entity_id}")
                
                # Log successful creation - operator-level log
                await self.log_info(
                    f"Successfully created {self.entity_type}: {entity.entity_id}",
                    details={
                        "entity_id": entity.entity_id,
                        "entity_name": entity.name,
                        "data": entity.data
                    }
                )
                
                # Update the output data
                self.state.output_data = {
                    f"{self.task_id}_entity_id": entity.entity_id,
                    f"{self.task_id}_entity_type": entity.entity_type,
                    f"created_entity_{self.entity_type}": entity.entity_id
                }
                return "continue"
            except Exception as e:
                print(f"   ❌ Failed to create entity: {e}")
                
                # Log operator error
                await self.log_error(
                    f"Failed to create {self.entity_type} entity",
                    error=e,
                    details=self._entity_params
                )
                
                self.state.error_message = str(e)
                return "failed"
        
        # If regular execute didn't need async, return its result
        return result.status


class EntityValidationOperator(BaseOperator):
    """
    Simple operator that validates an entity exists and meets basic criteria.
    Agnostic - the workflow defines what to validate.
    """
    
    def __init__(
        self,
        task_id: str,
        entity_id_field: str,  # Context field containing entity ID
        require_fields: Optional[List[str]] = None,  # Required data fields
        require_verified: bool = False,
        require_relationships: Optional[List[str]] = None,
        **kwargs
    ):
        """
        Initialize entity validation operator.
        
        Args:
            task_id: Unique task identifier
            entity_id_field: Context field with the entity ID to validate
            require_fields: List of required fields in entity data
            require_verified: Whether entity must be verified
            require_relationships: Required relationship types
        """
        super().__init__(task_id, **kwargs)
        self.entity_id_field = entity_id_field
        self.require_fields = require_fields or []
        self.require_verified = require_verified
        self.require_relationships = require_relationships or []
    
    def execute(self, context: Dict[str, Any]) -> TaskResult:
        """Validate the entity"""
        try:
            # Get entity ID from context
            entity_id = context.get(self.entity_id_field)
            if not entity_id:
                return TaskResult(
                    status="failed",
                    error=f"No entity ID found in context field '{self.entity_id_field}'"
                )
            
            # Get user ID
            user_id = context.get("user_id")
            if not user_id:
                return TaskResult(
                    status="failed",
                    error="No user_id in context"
                )
            
            # Fetch and validate entity
            import asyncio
            
            async def validate():
                entity = await EntityService.get_entity(entity_id, user_id)
                if not entity:
                    return False, "Entity not found or not owned by user"
                
                # Check verification if required
                if self.require_verified and not entity.verified:
                    return False, "Entity is not verified"
                
                # Check required fields in data
                for field in self.require_fields:
                    if field not in entity.data or entity.data[field] is None:
                        return False, f"Entity missing required field: {field}"
                
                # Check required relationships
                for rel_type in self.require_relationships:
                    rels = entity.get_relationships(rel_type)
                    if not rels:
                        return False, f"Entity missing required relationship: {rel_type}"
                
                return True, None
            
            # Run async operation
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            is_valid, error = loop.run_until_complete(validate())
            
            if is_valid:
                return TaskResult(
                    status="continue",
                    data={
                        f"{self.task_id}_validated": True,
                        f"{self.task_id}_entity_id": entity_id
                    }
                )
            else:
                return TaskResult(
                    status="failed",
                    error=error or "Entity validation failed"
                )
            
        except Exception as e:
            return TaskResult(
                status="failed",
                error=f"Validation error: {str(e)}"
            )


class EntityRequirementOperator(BaseOperator):
    """
    Operator that checks if user has required entities before proceeding.
    Completely agnostic - just checks for entity types and optional filters.
    """
    
    def __init__(
        self,
        task_id: str,
        entity_type: str,
        min_count: int = 1,
        filters: Optional[Dict[str, Any]] = None,  # Optional filters on entity data
        store_as: Optional[str] = None,  # Store found entity IDs in context with this key
        **kwargs
    ):
        """
        Initialize entity requirement operator.
        
        Args:
            task_id: Unique task identifier
            entity_type: Type of entity required (e.g., "person", "property")
            min_count: Minimum number of entities required
            filters: Optional filters to apply to entity data
            store_as: Key to store found entity IDs in context
        """
        super().__init__(task_id, **kwargs)
        self.entity_type = entity_type
        self.min_count = min_count
        self.filters = filters or {}
        self.store_as = store_as or f"{task_id}_entities"
    
    def execute(self, context: Dict[str, Any]) -> TaskResult:
        """Check for required entities"""
        try:
            user_id = context.get("user_id")
            if not user_id:
                return TaskResult(
                    status="failed",
                    error="No user_id in context"
                )
            
            # Check for entities
            import asyncio
            
            async def check_entities():
                # Find user's entities of this type
                entities = await EntityService.find_entities(
                    owner_user_id=user_id,
                    entity_type=self.entity_type,
                    filters=self.filters
                )
                
                return entities
            
            # Run async operation
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            entities = loop.run_until_complete(check_entities())
            
            if len(entities) >= self.min_count:
                # Requirements met - store entity IDs in context
                entity_ids = [e.entity_id for e in entities]
                return TaskResult(
                    status="continue",
                    data={
                        self.store_as: entity_ids,
                        f"{self.task_id}_count": len(entity_ids),
                        f"{self.task_id}_first": entity_ids[0] if entity_ids else None
                    }
                )
            else:
                # Not enough entities
                return TaskResult(
                    status="failed",
                    error=f"Requires at least {self.min_count} {self.entity_type} entities, found {len(entities)}"
                )
            
        except Exception as e:
            return TaskResult(
                status="failed",
                error=f"Entity requirement check failed: {str(e)}"
            )


class EntityRelationshipOperator(BaseOperator):
    """
    Operator that creates relationships between entities.
    """
    
    def __init__(
        self,
        task_id: str,
        from_entity_field: str,  # Context field with source entity ID
        to_entity_field: str,    # Context field with target entity ID
        relationship_type: str,
        auto_verify: bool = False,
        metadata_fields: Optional[Dict[str, str]] = None,
        **kwargs
    ):
        """
        Initialize relationship creation operator.
        
        Args:
            task_id: Unique task identifier
            from_entity_field: Context field containing source entity ID
            to_entity_field: Context field containing target entity ID
            relationship_type: Type of relationship to create
            auto_verify: Whether to auto-verify the relationship
            metadata_fields: Map context fields to relationship metadata
        """
        super().__init__(task_id, **kwargs)
        self.from_entity_field = from_entity_field
        self.to_entity_field = to_entity_field
        self.relationship_type = relationship_type
        self.auto_verify = auto_verify
        self.metadata_fields = metadata_fields or {}
    
    def execute(self, context: Dict[str, Any]) -> TaskResult:
        """Create the relationship"""
        try:
            # Get entity IDs
            from_id = context.get(self.from_entity_field)
            to_id = context.get(self.to_entity_field)
            
            if not from_id or not to_id:
                return TaskResult(
                    status="failed",
                    error=f"Missing entity IDs: from={from_id}, to={to_id}"
                )
            
            user_id = context.get("user_id") or context.get("customer_id")
            if not user_id:
                return TaskResult(
                    status="failed",
                    error="No user_id or customer_id in context"
                )
            
            # Build metadata
            metadata = {}
            for context_key, meta_key in self.metadata_fields.items():
                if context_key in context:
                    metadata[meta_key] = context[context_key]
            
            # Store params for async execution
            self._relationship_params = {
                "from_entity_id": from_id,
                "to_entity_id": to_id,
                "relationship_type": self.relationship_type,
                "owner_user_id": user_id,
                "metadata": metadata
            }
            
            return TaskResult(
                status="pending_async",
                data={}
            )
            
        except Exception as e:
            return TaskResult(
                status="failed",
                error=f"Relationship creation failed: {str(e)}"
            )
    
    async def execute_async(self, context: Dict[str, Any]) -> str:
        """Async execution for relationship creation"""
        # First run regular execute to prepare params
        result = self.execute(context)
        
        # If we have relationship params, create it
        if hasattr(self, '_relationship_params') and self._relationship_params:
            try:
                await_result = await EntityService.add_relationship(**self._relationship_params)
                
                if await_result:
                    # Update output data
                    self.state.output_data = {
                        f"{self.task_id}_relationship_created": True,
                        f"{self.task_id}_from": self._relationship_params["from_entity_id"],
                        f"{self.task_id}_to": self._relationship_params["to_entity_id"],
                        f"{self.task_id}_type": self.relationship_type
                    }
                    return "continue"
                else:
                    self.state.error_message = "Failed to create relationship"
                    return "failed"
            except Exception as e:
                self.state.error_message = str(e)
                return "failed"
        
        return result.status