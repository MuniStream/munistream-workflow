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
from ...services.visualizers.visualizer_factory import VisualizerFactory


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
        visualization_config: Optional[Dict[str, Any]] = None,  # Field-level visualization hints
        entity_display_config: Optional[Dict[str, Any]] = None,  # Entity-level display config
        user_id_source: Optional[str] = None,  # Optional: context field to get user_id from (admin only)
        visualizer: Optional[str] = None,  # Visualizer type for entity PDF generation
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
            visualization_config: Field-level visualization hints (e.g., {"field": {"type": "qr_code"}})
            entity_display_config: Entity-level display configuration (e.g., {"default_view": "pdf_report", "base_url": "https://..."})
            user_id_source: Optional context field to get user_id from (admin workflows only)
            visualizer: Visualizer type for entity PDF generation (e.g., "pdf_report", "signed_pdf")
        """
        super().__init__(task_id, **kwargs)
        self.entity_type = entity_type
        self.name_source = name_source
        self.data_mapping = data_mapping or {}
        self.static_data = static_data or {}
        self.visualization_config = visualization_config or {}
        self.user_id_source = user_id_source
        self.visualizer = visualizer

        # Merge visualizer into entity_display_config
        self.entity_display_config = entity_display_config or {
            "default_view": "card",  # card | pdf_report | table
            "pdf_template": None,     # Template name for PDF generation
            "preview_fields": [],     # Fields to show in preview
        }

        # Add visualizer to display config if provided
        if self.visualizer:
            self.entity_display_config["visualizer"] = self.visualizer
    
    def execute(self, context: Dict[str, Any]) -> TaskResult:
        """Create the entity based on workflow context"""
        try:
            print(f"🔍 EntityCreationOperator: Starting entity creation")
            print(f"   Context keys: {list(context.keys())}")
            print(f"   Entity type: {self.entity_type}")
            print(f"   Name source: {self.name_source}")
            
            # Get user ID from context - prioritize user_id_source if specified (admin workflows)
            user_id = None
            if self.user_id_source:
                user_id = context.get(self.user_id_source)
                print(f"   Using admin user_id_source '{self.user_id_source}': {user_id}")

            # Fallback to standard user_id or customer_id
            if not user_id:
                user_id = context.get("user_id") or context.get("customer_id")

            if not user_id:
                print(f"❌ EntityCreationOperator: No user_id, customer_id, or {self.user_id_source or 'user_id_source'} in context")
                return TaskResult(
                    status="failed",
                    error="No user_id, customer_id, or user_id_source in context"
                )
            
            # Build entity data from context automatically first
            entity_data = dict(self.static_data)  # Start with static data

            # Auto-collect all task outputs from context
            for key, value in context.items():
                # Skip system/internal fields
                if key.startswith(('_', 'instance', 'workflow', 'task_instance')):
                    continue

                # Include all task outputs that contain actual form data
                if isinstance(value, dict):
                    entity_data.update(value)
                elif value is not None and not isinstance(value, (list, dict)):
                    entity_data[key] = value

            # Apply explicit mapping overrides if provided
            if self.data_mapping:
                for context_key, data_field in self.data_mapping.items():
                    value = context
                    for key_part in context_key.split("."):
                        if isinstance(value, dict):
                            value = value.get(key_part)
                            if value is None:
                                break

                    if value is not None:
                        entity_data[data_field] = value

            # Get entity name - can be from entity_data, context or static
            entity_name = None
            if self.name_source in entity_data:
                entity_name = entity_data[self.name_source]
                print(f"   Found entity name in entity_data: {entity_name}")
            else:
                entity_name = self._extract_value_from_context(context, self.name_source)
                if entity_name is not None:
                    print(f"   Found entity name in context: {entity_name}")
                else:
                    entity_name = self.name_source  # Use as static string
                    print(f"   Using static entity name: {entity_name}")

            # For async operations, we'll handle this in execute_async
            # Store the parameters for async execution
            self._entity_params = {
                "entity_type": self.entity_type,
                "owner_user_id": user_id,
                "name": entity_name,
                "data": entity_data,
                "created_by_workflow": context.get("instance_id"),
                "visualization_config": self.visualization_config,
                "entity_display_config": self.entity_display_config
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

                # Generate visualization if visualizer is specified
                pdf_generated = False
                if self.visualizer:
                    try:
                        print(f"   🎨 Generating visualization with {self.visualizer}...")

                        # Get the visualizer instance
                        visualizer = VisualizerFactory.get_visualizer(
                            visualizer_type=self.visualizer,
                            config=self.entity_display_config
                        )

                        if visualizer:
                            # Generate PDF for the entity
                            pdf_data = await visualizer.generate_pdf(entity)

                            if pdf_data:
                                # Store PDF reference in entity data
                                entity.data["_pdf_generated"] = {
                                    "visualizer": self.visualizer,
                                    "generated_at": datetime.utcnow().isoformat(),
                                    "size_bytes": len(pdf_data)
                                }

                                # Update entity with PDF info
                                await entity.save()
                                pdf_generated = True

                                await self.log_info(
                                    f"PDF visualization generated for entity {entity.entity_id}",
                                    details={
                                        "visualizer": self.visualizer,
                                        "pdf_size": len(pdf_data)
                                    }
                                )
                                print(f"   ✅ PDF generated successfully ({len(pdf_data)} bytes)")
                            else:
                                await self.log_warning(
                                    f"PDF generation returned empty data for entity {entity.entity_id}",
                                    details={"visualizer": self.visualizer}
                                )
                        else:
                            await self.log_warning(
                                f"Visualizer '{self.visualizer}' not found or not available",
                                details={"available_visualizers": VisualizerFactory.get_available_visualizers()}
                            )

                    except Exception as e:
                        await self.log_error(
                            f"Failed to generate PDF visualization for entity {entity.entity_id}",
                            error=e,
                            details={"visualizer": self.visualizer}
                        )
                        print(f"   ⚠️ PDF generation failed: {e}")

                # Update the output data
                output_data = {
                    f"{self.task_id}_entity_id": entity.entity_id,
                    f"{self.task_id}_entity_type": entity.entity_type,
                    f"created_entity_{self.entity_type}": entity.entity_id,
                    f"{self.task_id}_pdf_generated": pdf_generated
                }

                if pdf_generated:
                    output_data[f"{self.task_id}_visualizer"] = self.visualizer

                self.state.output_data = output_data
                return TaskResult(
                    status="continue",
                    data=output_data
                )
            except Exception as e:
                print(f"   ❌ Failed to create entity: {e}")
                
                # Log operator error
                await self.log_error(
                    f"Failed to create {self.entity_type} entity",
                    error=e,
                    details=self._entity_params
                )

                error_msg = str(e)
                self.state.error_message = error_msg
                return TaskResult(
                    status="failed",
                    error=error_msg
                )
        
        # If regular execute didn't need async, return its result
        return result

    def _extract_value_from_context(self, context: Dict[str, Any], key: str) -> Any:
        """Extract value from context using dot notation (e.g., 'collect_data.field_name')"""
        if not key:
            return None

        # Try direct key first
        if key in context:
            return context[key]

        # Try nested key with dot notation
        if '.' in key:
            value = context
            for key_part in key.split('.'):
                if isinstance(value, dict):
                    value = value.get(key_part)
                    if value is None:
                        break
                else:
                    return None
            return value

        return None


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

        # Store parameters for async execution
        self._validation_params = {
            "entity_id": entity_id,
            "user_id": user_id,
            "require_verified": self.require_verified,
            "require_fields": self.require_fields,
            "require_relationships": self.require_relationships
        }

        # Return pending_async status - will be handled by execute_async
        return TaskResult(
            status="pending_async",
            data={}
        )

    async def execute_async(self, context: Dict[str, Any]) -> TaskResult:
        """Async execution method for entity validation"""
        try:
            # First run the regular execute to prepare parameters
            result = self.execute(context)

            # If we have validation params, do the async operation
            if hasattr(self, '_validation_params') and self._validation_params:
                params = self._validation_params

                entity = await EntityService.get_entity(params["entity_id"], params["user_id"])
                if not entity:
                    error_msg = "Entity not found or not owned by user"
                    self.state.error_message = error_msg
                    return TaskResult(
                        status="failed",
                        error=error_msg
                    )

                # Check verification if required
                if params["require_verified"] and not entity.verified:
                    error_msg = "Entity is not verified"
                    self.state.error_message = error_msg
                    return TaskResult(
                        status="failed",
                        error=error_msg
                    )

                # Check required fields in data
                for field in params["require_fields"]:
                    if field not in entity.data or entity.data[field] is None:
                        error_msg = f"Entity missing required field: {field}"
                        self.state.error_message = error_msg
                        return TaskResult(
                            status="failed",
                            error=error_msg
                        )

                # Check required relationships
                for rel_type in params["require_relationships"]:
                    rels = entity.get_relationships(rel_type)
                    if not rels:
                        error_msg = f"Entity missing required relationship: {rel_type}"
                        self.state.error_message = error_msg
                        return TaskResult(
                            status="failed",
                            error=error_msg
                        )

                # All validations passed
                output_data = {
                    f"{self.task_id}_validated": True,
                    f"{self.task_id}_entity_id": params["entity_id"]
                }
                self.state.output_data = output_data

                return TaskResult(
                    status="continue",
                    data=output_data
                )

            # If regular execute didn't need async, return its result
            return result

        except Exception as e:
            error_msg = f"Validation error: {str(e)}"
            self.state.error_message = error_msg
            return TaskResult(
                status="failed",
                error=error_msg
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
        on_missing: str = "failed",  # What to return when requirements not met: "failed" or "retry"
        retry_delay: int = 5,  # Seconds to wait before retry (default 5 seconds)
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
            on_missing: What to return when requirements not met: "failed" or "retry"
        """
        super().__init__(task_id, **kwargs)
        self.entity_type = entity_type
        self.min_count = min_count
        self.filters = filters or {}
        self.store_as = store_as or f"{task_id}_entities"
        self.on_missing = on_missing
        self.retry_delay = retry_delay
    
    def execute(self, context: Dict[str, Any]) -> TaskResult:
        """Check for required entities"""
        user_id = context.get("user_id")
        print(f"🔍 EntityRequirementOperator DEBUG:")
        print(f"   Task ID: {self.task_id}")
        print(f"   Entity type: {self.entity_type}")
        print(f"   Filters: {self.filters}")
        print(f"   Context user_id: {user_id}")
        print(f"   Context keys: {list(context.keys())}")

        if not user_id:
            return TaskResult(
                status="failed",
                error="No user_id in context"
            )

        # Store parameters for async execution
        self._check_params = {
            "user_id": user_id,
            "entity_type": self.entity_type,
            "filters": self.filters,
            "min_count": self.min_count,
            "store_as": self.store_as
        }

        # Return pending_async status - will be handled by execute_async
        return TaskResult(
            status="pending_async",
            data={}
        )

    async def execute_async(self, context: Dict[str, Any]) -> TaskResult:
        """Async execution method for entity requirement checking"""
        try:
            # First run the regular execute to prepare parameters
            result = self.execute(context)

            # If we have check params, do the async operation
            if hasattr(self, '_check_params') and self._check_params:
                params = self._check_params

                print(f"🔍 EntityRequirementOperator ASYNC DEBUG:")
                print(f"   Checking entities for user: {params['user_id']}")
                print(f"   Entity type: {params['entity_type']}")
                print(f"   Filters: {params['filters']}")

                # Find user's entities of this type
                entities = await EntityService.find_entities(
                    owner_user_id=params["user_id"],
                    entity_type=params["entity_type"],
                    filters=params["filters"]
                )

                print(f"   Found {len(entities)} entities")
                if entities:
                    for entity in entities:
                        print(f"   - {entity.entity_id}: {entity.name}")

                if len(entities) >= params["min_count"]:
                    # Requirements met - store entity IDs in context
                    entity_ids = [e.entity_id for e in entities]

                    # Log success
                    await self.log_info(
                        f"Found {len(entities)} required {params['entity_type']} entities",
                        details={
                            "entity_ids": entity_ids,
                            "filters": params["filters"]
                        }
                    )

                    output_data = {
                        params["store_as"]: entity_ids,
                        f"{self.task_id}_count": len(entity_ids),
                        f"{self.task_id}_first": entity_ids[0] if entity_ids else None
                    }
                    self.state.output_data = output_data

                    return TaskResult(
                        status="continue",
                        data=output_data
                    )
                else:
                    # Not enough entities
                    error_msg = f"Requires at least {params['min_count']} {params['entity_type']} entities, found {len(entities)}"

                    # Log warning or error based on on_missing setting
                    if self.on_missing == "retry":
                        await self.log_warning(
                            f"Entity requirement not met for {params['entity_type']}, will retry",
                            details={**params, "found": len(entities)}
                        )
                    else:
                        await self.log_error(
                            f"Entity requirement not met for {params['entity_type']}",
                            error=error_msg,
                            details=params
                        )

                    self.state.error_message = error_msg
                    return TaskResult(
                        status=self.on_missing,  # Will be "failed" or "retry"
                        error=error_msg,
                        retry_delay=self.retry_delay if self.on_missing == "retry" else None
                    )

            # If regular execute didn't need async, return its result
            return result

        except Exception as e:
            error_msg = f"Entity requirement check failed: {str(e)}"

            # Log error
            await self.log_error(
                "Entity requirement check failed",
                error=e,
                details=getattr(self, '_check_params', {})
            )

            self.state.error_message = error_msg
            return TaskResult(
                status="failed",
                error=error_msg
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


class MultiEntityRequirementOperator(BaseOperator):
    """
    Operator that checks if user has multiple types of required entities.
    Validates all entity requirements simultaneously before proceeding.
    """

    def __init__(
        self,
        task_id: str,
        requirements: List[Dict[str, Any]],  # List of entity requirements
        on_missing: str = "failed",  # What to return when ANY requirement is not met
        retry_delay: int = 5,  # Seconds to wait before retry (default 5 seconds)
        **kwargs
    ):
        """
        Initialize multi-entity requirement operator.

        Args:
            task_id: Unique task identifier
            requirements: List of entity requirements, each containing:
                - entity_type: Type of entity required
                - min_count: Minimum number required (default 1)
                - filters: Optional filters to apply
                - store_as: Key to store found entity IDs
                - info: Optional display information for citizen portal
                  - instructions: User-friendly explanation of requirement
                  - workflow_id: ID of workflow that helps obtain this entity
                  - display_name: Display name for this requirement
                  - description: Additional description text
            on_missing: What to return when requirements not met: "failed" or "retry"

        Example:
            requirements=[
                {
                    "entity_type": "person",
                    "min_count": 1,
                    "store_as": "person_ids",
                    "info": {
                        "instructions": "You need to register as a verified citizen",
                        "workflow_id": "citizen_registration",
                        "display_name": "Citizen Registration",
                        "description": "Complete citizen registration with verified identity"
                    }
                },
                {
                    "entity_type": "property",
                    "min_count": 2,
                    "filters": {"verified": True},
                    "info": {
                        "instructions": "You need verified property ownership documents",
                        "workflow_id": "property_verification",
                        "display_name": "Property Documents",
                        "description": "Official property ownership documentation"
                    }
                }
            ]
        """
        super().__init__(task_id, **kwargs)
        self.requirements = requirements
        self.on_missing = on_missing
        self.retry_delay = retry_delay

    def execute(self, context: Dict[str, Any]) -> TaskResult:
        """Check for all required entities"""
        user_id = context.get("user_id")

        if not user_id:
            return TaskResult(
                status="failed",
                error="No user_id in context"
            )

        # Store parameters for async execution
        self._check_params = {
            "user_id": user_id,
            "requirements": self.requirements,
            "on_missing": self.on_missing
        }

        return TaskResult(
            status="pending_async",
            data={}
        )

    async def execute_async(self, context: Dict[str, Any]) -> TaskResult:
        """Async execution method for multi-entity requirement checking"""
        try:
            # First run the regular execute to prepare parameters
            result = self.execute(context)

            if hasattr(self, '_check_params') and self._check_params:
                params = self._check_params

                # Track results for all requirements
                all_met = True
                missing_requirements = []
                output_data = {}

                # Check each requirement
                for req in params["requirements"]:
                    entity_type = req["entity_type"]
                    min_count = req.get("min_count", 1)
                    filters = req.get("filters", {})
                    store_as = req.get("store_as", f"{entity_type}_entities")

                    # Log checking requirement
                    await self.log_info(
                        f"Checking requirement for {entity_type}",
                        details={
                            "entity_type": entity_type,
                            "min_count": min_count,
                            "filters": filters
                        }
                    )

                    # Find entities matching this requirement
                    entities = await EntityService.find_entities(
                        owner_user_id=params["user_id"],
                        entity_type=entity_type,
                        filters=filters
                    )

                    entity_ids = [e.entity_id for e in entities]

                    # Store results for this entity type
                    output_data[store_as] = entity_ids
                    output_data[f"{entity_type}_count"] = len(entity_ids)
                    if entity_ids:
                        output_data[f"{entity_type}_first"] = entity_ids[0]

                    # Check if requirement is met
                    if len(entities) < min_count:
                        all_met = False
                        missing_requirements.append({
                            "entity_type": entity_type,
                            "required": min_count,
                            "found": len(entities),
                            "filters": filters
                        })

                        await self.log_warning(
                            f"Requirement not met for {entity_type}",
                            details={
                                "required": min_count,
                                "found": len(entities),
                                "filters": filters
                            }
                        )
                    else:
                        await self.log_info(
                            f"Requirement met for {entity_type}",
                            details={
                                "found": len(entities),
                                "entity_ids": entity_ids[:5]  # Show first 5 IDs
                            }
                        )

                # Store output data even if not all requirements met
                # This allows workflows to see partial results
                self.state.output_data = output_data

                if all_met:
                    # All requirements satisfied
                    await self.log_info(
                        f"All {len(params['requirements'])} entity requirements met",
                        details={"entity_counts": {
                            req["entity_type"]: output_data.get(f"{req['entity_type']}_count", 0)
                            for req in params["requirements"]
                        }}
                    )

                    return TaskResult(
                        status="continue",
                        data=output_data
                    )
                else:
                    # Some requirements not met
                    error_msg = f"Missing entity requirements: {', '.join([r['entity_type'] for r in missing_requirements])}"

                    # Log based on on_missing setting
                    if params["on_missing"] == "retry":
                        await self.log_warning(
                            f"Not all entity requirements met, will retry",
                            details={"missing": missing_requirements}
                        )
                    else:
                        await self.log_error(
                            f"Entity requirements not satisfied",
                            error=error_msg,
                            details={"missing": missing_requirements}
                        )

                    self.state.error_message = error_msg
                    return TaskResult(
                        status=params["on_missing"],  # Will be "failed" or "retry"
                        error=error_msg,
                        data=output_data,  # Include partial results
                        retry_delay=self.retry_delay if params["on_missing"] == "retry" else None
                    )

            # If regular execute didn't need async, return its result
            return result

        except Exception as e:
            error_msg = f"Multi-entity requirement check failed: {str(e)}"

            await self.log_error(
                "Multi-entity requirement check failed",
                error=e,
                details=getattr(self, '_check_params', {})
            )

            self.state.error_message = error_msg
            return TaskResult(
                status="failed",
                error=error_msg
            )


