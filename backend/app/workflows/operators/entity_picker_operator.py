"""
EntityPickerOperator for interactive entity selection in workflows.

This operator allows users to interactively pick specific entities that match criteria.
Extends MultiEntityRequirementOperator to add selection interface for found entities.
"""
from typing import Dict, Any, List
import logging

from .base import BaseOperator, TaskResult, OperatorRequirement, RequirementStatus
from .entity_operators import MultiEntityRequirementOperator
from ...services.entity_service import EntityService
from ...core.logging_config import get_workflow_logger, set_workflow_context

# Initialize workflow-aware logger
logger = get_workflow_logger(__name__)


class EntityPickerOperator(MultiEntityRequirementOperator):
    """
    Operator that allows users to interactively pick specific entities that match criteria.
    Extends MultiEntityRequirementOperator to add selection interface for found entities.
    """

    def __init__(
        self,
        task_id: str,
        requirements: List[Dict[str, Any]],  # Same as parent, with additional display options
        on_missing: str = "failed",
        retry_delay: int = 5,
        **kwargs
    ):
        """
        Initialize entity picker operator.

        Args:
            task_id: Unique task identifier
            requirements: List of entity requirements, each containing:
                - entity_type: Type of entity required (e.g., "document", "person")
                - min_count: Minimum number required (default 1)
                - max_count: Maximum number selectable (default same as min_count)
                - filters: Optional filters (e.g., {"document_type": "licencias_construccion"})
                - store_as: Key to store selected entity IDs
                - display_title: Title for this requirement section (optional)
                - display_fields: Fields to show in entity cards (optional)
                - info: Optional display information for citizen portal (optional)
                  - instructions: User-friendly explanation of requirement
                  - workflow_id: ID of workflow that helps obtain this entity
                  - display_name: Display name for this requirement
                  - description: Additional description text
            on_missing: What to return when no entities found: "failed" or "retry"

        Example:
            requirements=[
                {
                    "entity_type": "document",
                    "min_count": 1,
                    "max_count": 1,
                    "filters": {"document_type": "licencias_construccion"},
                    "store_as": "selected_licencias_ids",
                    "display_title": "Select Construction License",
                    "display_fields": ["name", "document_type", "upload_date"],
                    "info": {
                        "instructions": "You need a valid construction license issued by the municipal authority",
                        "workflow_id": "obtain_construction_license",
                        "display_name": "Construction License",
                        "description": "Official permit required for construction work"
                    }
                },
                {
                    "entity_type": "person",
                    "min_count": 1,
                    "max_count": 2,
                    "filters": {"verified": True},
                    "store_as": "selected_person_ids",
                    "display_title": "Select Verified Person(s)",
                    "display_fields": ["name", "curp", "email"],
                    "info": {
                        "instructions": "Select verified citizens who are authorized for this process",
                        "workflow_id": "citizen_verification",
                        "display_name": "Verified Citizens",
                        "description": "Citizens with verified identity and authorization"
                    }
                }
            ]
        """
        super().__init__(task_id, requirements, on_missing, retry_delay, **kwargs)

        # Process requirements to add default display config
        for req in self.requirements:
            if "max_count" not in req:
                req["max_count"] = req.get("min_count", 1)
            if "display_title" not in req:
                entity_type = req["entity_type"]
                req["display_title"] = f"{entity_type.title()}"
            if "display_fields" not in req:
                # Default display fields based on entity type
                if req["entity_type"] == "document":
                    req["display_fields"] = ["name", "document_type", "upload_date"]
                elif req["entity_type"] == "person":
                    req["display_fields"] = ["name", "email"]
                else:
                    req["display_fields"] = ["name"]

    def get_requirements(self) -> List[OperatorRequirement]:
        """
        Define entity requirements for this operator.
        Converts the entity-specific requirements into generic OperatorRequirement objects.
        """
        operator_requirements = []

        for req_config in self.requirements:
            # Extract requirement details
            entity_type = req_config["entity_type"]
            min_count = req_config.get("min_count", 1)
            store_as = req_config.get("store_as", f"{entity_type}_entities")
            display_title = req_config.get("display_title", f"{entity_type.title()}")
            info = req_config.get("info", {})

            # Create a generic requirement
            operator_requirements.append(OperatorRequirement(
                requirement_id=store_as,
                type="entity",  # This is an entity requirement
                name=display_title,
                description=info.get("instructions", f"At least {min_count} {entity_type} required"),
                critical=min_count > 0,  # Critical if at least one is required
                metadata={
                    "entity_type": entity_type,
                    "min_count": min_count,
                    "max_count": req_config.get("max_count", min_count),
                    "filters": req_config.get("filters", {}),
                    "workflow_id": info.get("workflow_id"),
                    "display_name": info.get("display_name"),
                    "original_config": req_config  # Store full config for reference
                }
            ))

        return operator_requirements

    async def check_requirement(self,
                               requirement: OperatorRequirement,
                               context: Dict[str, Any]) -> RequirementStatus:
        """
        Check if entity requirements are met.
        Queries the database to see if required entities exist for the user.
        """
        if requirement.type != "entity":
            # This operator only knows how to check entity requirements
            return await super().check_requirement(requirement, context)

        # Extract metadata
        metadata = requirement.metadata
        entity_type = metadata.get("entity_type")
        min_count = metadata.get("min_count", 1)
        filters = metadata.get("filters", {})
        workflow_id = metadata.get("workflow_id")

        # Get user ID from context
        user_id = context.get("user_id")
        if not user_id:
            return RequirementStatus(
                requirement_id=requirement.requirement_id,
                fulfilled=False,
                message="No user context available",
                details={"error": "missing_user_id"}
            )

        try:
            # Query for entities matching the requirement
            entities = await EntityService.find_entities(
                owner_user_id=user_id,
                entity_type=entity_type,
                filters=filters
            )

            # Check if requirement is fulfilled
            fulfilled = len(entities) >= min_count

            # Prepare available resources (first 5 entities for preview)
            available_resources = [
                {
                    "entity_id": entity.entity_id,
                    "name": entity.name,
                    "entity_type": entity.entity_type,
                    "data": entity.data
                }
                for entity in entities[:5]  # Limit to 5 for preview
            ]

            # Generate status message
            if fulfilled:
                message = f"Found {len(entities)} {entity_type}(s) available"
            else:
                message = f"Found {len(entities)} of {min_count} required {entity_type}(s)"

            # Determine action needed
            action_needed = None
            action_url = None

            # For required documents (min_count > 0), only show action if not fulfilled
            # For optional documents (min_count = 0), always show action to allow uploading
            if not fulfilled or min_count == 0:
                if not fulfilled:
                    action_needed = f"Upload {min_count - len(entities)} more {entity_type}(s)"
                else:
                    action_needed = f"Upload {entity_type} (optional)"

                # If a workflow_id is specified, that's the workflow to complete
                if workflow_id:
                    action_url = f"/services/{workflow_id}"
                else:
                    action_url = "/documents/upload"

            return RequirementStatus(
                requirement_id=requirement.requirement_id,
                fulfilled=fulfilled,
                message=message,
                details={
                    "found": len(entities),
                    "required": min_count,
                    "entity_type": entity_type,
                    "filters": filters
                },
                action_needed=action_needed,
                action_url=action_url,
                available_resources=available_resources
            )

        except Exception as e:
            logger.error(f"Error checking entity requirement: {str(e)}")
            return RequirementStatus(
                requirement_id=requirement.requirement_id,
                fulfilled=False,
                message=f"Error checking requirement: {str(e)}",
                details={"error": str(e)}
            )

    def execute(self, context: Dict[str, Any]) -> TaskResult:
        """Check for user selections or find entities to present for selection"""

        self._set_workflow_context(context)
        logger.info("EntityPickerOperator execution started")

        if not self._validate_user_context(context):
            return self._fail_missing_user(context)

        if self._should_validate_existing_selections(context):
            return self._validate_user_selections(context)

        return self._prepare_for_entity_discovery(context)

    def _set_workflow_context(self, context: Dict[str, Any]) -> None:
        """Set workflow context for structured logging"""
        set_workflow_context(
            user_id=context.get("user_id"),
            instance_id=context.get("instance_id"),
            workflow_id=context.get("workflow_id"),
            step=self.task_id
        )

    def _validate_user_context(self, context: Dict[str, Any]) -> bool:
        """Check if user context is valid for execution"""
        user_id = context.get("user_id")
        if not user_id:
            logger.error("Missing user_id in context",
                        context_keys=list(context.keys()))
            return False
        return True

    def _fail_missing_user(self, context: Dict[str, Any]) -> TaskResult:
        """Return failure result for missing user context"""
        return TaskResult(
            status="failed",
            error="No user_id in context"
        )

    def _should_validate_existing_selections(self, context: Dict[str, Any]) -> bool:
        """Check if user has already made selections that need validation"""
        input_key = f"{self.task_id}_input"
        has_input = input_key in context

        logger.debug("Checking for existing user input",
                    input_key=input_key,
                    context_keys=list(context.keys()),
                    has_input=has_input)

        if has_input:
            # Check if the input contains selection data
            input_data = context.get(input_key, {})
            selection_key = f"{self.task_id}_selections"
            has_selections = selection_key in input_data

            logger.debug("Checking for selections in input data",
                        selection_key=selection_key,
                        input_keys=list(input_data.keys()) if input_data else None,
                        has_selections=has_selections)

            if has_selections:
                logger.info("Found user selections in input, proceeding to validation")
                return True

        logger.info("No existing selections found, proceeding to entity discovery")
        return False

    def _validate_user_selections(self, context: Dict[str, Any]) -> TaskResult:
        """Validate user-submitted selections and return CONTINUE or WAITING"""
        return self._validate_selections(context)

    def _prepare_for_entity_discovery(self, context: Dict[str, Any]) -> TaskResult:
        """Prepare parameters for async entity discovery"""
        self._check_params = {
            "user_id": context.get("user_id"),
            "requirements": self.requirements,
            "on_missing": self.on_missing
        }

        logger.info("Prepared for entity discovery",
                   requirements_count=len(self.requirements))
        return TaskResult(
            status="pending_async",
            data={}
        )

    async def _discover_available_entities(self, requirements: List[Dict[str, Any]], user_id: str) -> Dict[str, List]:
        """Find all entities that match each requirement"""
        logger.info("Starting entity discovery for all requirements",
                   requirements_count=len(requirements))

        requirement_entities = {}

        for req in requirements:
            entity_type = req["entity_type"]
            min_count = req.get("min_count", 1)
            filters = req.get("filters", {})
            store_as = req.get("store_as")

            logger.debug("Discovering entities for requirement",
                        entity_type=entity_type,
                        filters=filters,
                        min_count=min_count,
                        store_as=store_as)

            # Find entities matching this requirement
            entities = await EntityService.find_entities(
                owner_user_id=user_id,
                entity_type=entity_type,
                filters=filters
            )

            logger.debug("Entity discovery result",
                        req_key=store_as,
                        entities_count=len(entities),
                        entity_type=entity_type)

            requirement_entities[store_as] = entities

            # Log discovery results for this requirement
            await self.log_info(
                f"Found {len(entities)} entities for {entity_type}",
                details={
                    "entity_type": entity_type,
                    "filters": filters,
                    "found_count": len(entities),
                    "min_required": min_count
                }
            )

        logger.info("Entity discovery completed",
                   total_requirements=len(requirements),
                   discovered_keys=list(requirement_entities.keys()))

        return requirement_entities

    def _check_minimum_requirements_met(self, requirement_entities: Dict[str, List], requirements: List[Dict[str, Any]]) -> bool:
        """Verify each requirement has enough entities to proceed"""
        logger.debug("Checking minimum requirements for all entity types")

        for req in requirements:
            entity_type = req["entity_type"]
            min_count = req.get("min_count", 1)
            store_as = req.get("store_as")
            entities = requirement_entities.get(store_as, [])

            if len(entities) < min_count:
                logger.warning("Minimum requirement not met",
                              entity_type=entity_type,
                              required=min_count,
                              found=len(entities),
                              store_as=store_as)
                return False

        logger.info("All minimum requirements met")
        return True

    def _can_auto_select_all_requirements(self, requirement_entities: Dict[str, List], requirements: List[Dict[str, Any]]) -> bool:
        """Check if ALL requirements can be automatically selected"""
        logger.debug("Checking auto-selection possibility for all requirements")

        for req in requirements:
            entities = requirement_entities.get(req.get("store_as"), [])
            if not self._can_auto_select_single_requirement(entities, req):
                logger.debug("Cannot auto-select requirement", entity_type=req["entity_type"])
                return False

        logger.info("All requirements can be auto-selected")
        return True

    def _can_auto_select_single_requirement(self, entities: List, requirement: Dict[str, Any]) -> bool:
        """Check if a single requirement qualifies for auto-selection"""
        entity_type = requirement["entity_type"]
        min_count = requirement.get("min_count", 1)
        max_count = requirement.get("max_count", min_count)
        auto_select = requirement.get("auto_select", False)

        # Rule 0: Never auto-select optional entities (min_count=0) - user must explicitly choose
        if min_count == 0:
            logger.debug("Optional entity requirement - user selection required",
                        entity_type=entity_type,
                        available=len(entities))
            return False

        # Rule 1: Perfect match (exactly one entity needed and available)
        if len(entities) == 1 and min_count == 1 and max_count == 1:
            logger.debug("Perfect match detected - single entity for single requirement",
                        entity_type=entity_type)
            return True

        # Rule 2: Auto-select flag enabled with sufficient entities
        if auto_select and len(entities) >= min_count:
            logger.debug("Auto-select enabled with sufficient entities",
                        entity_type=entity_type,
                        available=len(entities),
                        needed=min_count)
            return True

        # Rule 3: No auto-selection possible
        logger.debug("No auto-selection rules matched",
                    entity_type=entity_type,
                    available=len(entities),
                    min_count=min_count,
                    max_count=max_count,
                    auto_select=auto_select)
        return False

    def _auto_select_entities(self, requirement_entities: Dict[str, List], requirements: List[Dict[str, Any]]) -> TaskResult:
        """Automatically select entities and return continuation data"""
        logger.info("Performing automatic entity selection")

        selected_entities_group = {}

        for req in requirements:
            entity_type = req["entity_type"]
            min_count = req.get("min_count", 1)
            store_as = req.get("store_as")
            entities = requirement_entities.get(store_as, [])

            # Determine how many to select
            selected_entities = entities[:min_count]
            selected_ids = [e.entity_id for e in selected_entities]

            # Store in grouped format under selected_entities
            selected_entities_group[store_as] = selected_ids

            logger.info("Auto-selected entities for requirement",
                       entity_type=entity_type,
                       selected_count=len(selected_ids),
                       entity_ids=selected_ids[:3])  # Log first 3 IDs

        # Only store the grouped selected entities
        auto_selection_data = {"selected_entities": selected_entities_group}

        # Update state for tracking
        self.state.output_data = auto_selection_data

        logger.info("Auto-selection completed successfully",
                   total_requirements=len(requirements),
                   total_selected=sum(len(ids) for ids in selected_entities_group.values()),
                   grouped_entities=selected_entities_group)

        return TaskResult(
            status="continue",
            data=auto_selection_data
        )

    def _wait_for_missing_entities(self, requirement_entities: Dict[str, List], requirements: List[Dict[str, Any]], context: Dict[str, Any]) -> TaskResult:
        """User needs to upload entities before we can proceed"""
        logger.warning("Cannot proceed - missing required entities")

        form_config = self._generate_missing_entities_notification(requirement_entities, requirements, context)
        self._save_waiting_state("missing_entities", form_config)

        return TaskResult(
            status="waiting",
            data={"waiting_for": "missing_entities", "form_config": form_config}
        )

    def _wait_for_user_selection(self, requirement_entities: Dict[str, List], requirements: List[Dict[str, Any]]) -> TaskResult:
        """User needs to choose from available entities"""
        logger.info("User selection required - entities available but choice needed")

        form_config = self._generate_entity_selection_form(requirement_entities, requirements)
        self._save_waiting_state("entity_selection", form_config)

        return TaskResult(
            status="waiting",
            data={"waiting_for": "entity_selection", "form_config": form_config}
        )

    def _save_waiting_state(self, waiting_for: str, form_config: Dict[str, Any]) -> None:
        """Save waiting state to operator state for tracking"""
        self.state.output_data = {
            "waiting_for": waiting_for,
            "form_config": form_config
        }
        logger.debug("Saved waiting state",
                    waiting_for=waiting_for,
                    form_title=form_config.get("title", "Unknown"))

    def _generate_missing_entities_notification(self, requirement_entities: Dict[str, List], requirements: List[Dict[str, Any]], context: Dict[str, Any]) -> Dict[str, Any]:
        """Generate notification explaining what entities are missing"""
        return self._generate_missing_requirements_form(requirement_entities, requirements, context)

    def _generate_entity_selection_form(self, requirement_entities: Dict[str, List], requirements: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Generate form for user to select specific entities"""
        form_config = self._generate_selection_form(requirement_entities, requirements)
        self._last_form_config = form_config
        return form_config

    def _validate_selections(self, context: Dict[str, Any]) -> TaskResult:
        """Validate user selections meet requirements"""
        try:
            # Get selections from input data
            input_key = f"{self.task_id}_input"
            input_data = context.get(input_key, {})
            selection_key = f"{self.task_id}_selections"
            selections = input_data.get(selection_key, {})
            instance_id = context.get("instance_id", "unknown")

            logger.info("Validating user selections")
            print(f"DEBUG: input_key={input_key}")
            print(f"DEBUG: selection_key={selection_key}")
            print(f"DEBUG: input_data={input_data}")
            print(f"DEBUG: selections={selections}")
            print(f"DEBUG: selections_keys={list(selections.keys()) if selections else None}")
            print(f"DEBUG: requirements_count={len(self.requirements)}")

            # Validate each requirement
            selected_entities_group = {}
            validation_errors = []

            logger.info("Starting validation loop",
                        requirements_count=len(self.requirements),
                        selections_structure=selections)

            for req in self.requirements:
                entity_type = req["entity_type"]
                min_count = req.get("min_count", 1)
                max_count = req.get("max_count", min_count)
                store_as = req.get("store_as", f"{entity_type}_entities")

                # Get selected entity IDs for this requirement using store_as
                store_as = req.get("store_as")
                selected_ids = selections.get(store_as, [])

                logger.info("Validating requirement")
                print(f"DEBUG: entity_type={entity_type}")
                print(f"DEBUG: store_as={store_as}")
                print(f"DEBUG: min_count={min_count}")
                print(f"DEBUG: max_count={max_count}")
                print(f"DEBUG: selected_ids={selected_ids}")
                print(f"DEBUG: selected_count={len(selected_ids)}")
                print(f"DEBUG: selection_keys_available={list(selections.keys())}")

                # Validate count
                if len(selected_ids) < min_count:
                    error_msg = f"{req.get('display_title', entity_type)}: Select at least {min_count} item(s)"
                    validation_errors.append(error_msg)
                    logger.warning("Validation error - not enough selections",
                                 entity_type=entity_type,
                                 store_as=store_as,
                                 required=min_count,
                                 found=len(selected_ids),
                                 error=error_msg)
                elif len(selected_ids) > max_count:
                    error_msg = f"{req.get('display_title', entity_type)}: Select at most {max_count} item(s)"
                    validation_errors.append(error_msg)
                    logger.warning("Validation error - too many selections",
                                 entity_type=entity_type,
                                 store_as=store_as,
                                 max_allowed=max_count,
                                 found=len(selected_ids),
                                 error=error_msg)
                else:
                    # Store valid selections in grouped format
                    selected_entities_group[store_as] = selected_ids
                    logger.info("Requirement validation passed",
                              entity_type=entity_type,
                              store_as=store_as,
                              selections_count=len(selected_ids))

            logger.debug("Validation complete",
                        total_errors=len(validation_errors),
                        validation_errors=validation_errors,
                        selected_entities=selected_entities_group)

            if validation_errors:
                # Return to selection with validation errors
                logger.warning("Validation failed, returning to selection form",
                             error_count=len(validation_errors),
                             errors=validation_errors)
                return TaskResult(
                    status="waiting",
                    data={
                        "waiting_for": "entity_selection",
                        "form_config": self._last_form_config,
                        "validation_errors": validation_errors,
                        "previous_selections": selections
                    }
                )

            # All selections valid - store as grouped entities
            output_data = {"selected_entities": selected_entities_group}
            logger.info("All validations passed, continuing workflow",
                       selected_entities=selected_entities_group,
                       total_selected=sum(len(ids) for ids in selected_entities_group.values()))
            self.state.output_data = output_data
            return TaskResult(
                status="continue",
                data=output_data
            )

        except Exception as e:
            error_msg = f"Selection validation failed: {str(e)}"
            self.state.error_message = error_msg
            return TaskResult(
                status="failed",
                error=error_msg
            )

    async def execute_async(self, context: Dict[str, Any]) -> TaskResult:
        """Async execution method for entity discovery and decision making"""

        # Set workflow context for async logging (outside try block for error logging)
        self._set_workflow_context(context)

        try:
            logger.info("Starting async entity discovery and decision making")

            # First run the regular execute to prepare parameters
            result = self.execute(context)

            # If validation passed (status="continue"), return that result immediately
            if result.status == "continue":
                logger.info("Validation passed, returning continue result from execute_async")
                return result

            if hasattr(self, '_check_params') and self._check_params:
                params = self._check_params

                # Step 1: Discover available entities
                requirement_entities = await self._discover_available_entities(
                    params["requirements"],
                    params["user_id"]
                )

                # Step 2: Make decision based on discovery results
                return await self._determine_next_action(requirement_entities, params["requirements"], context)

            # If regular execute didn't need async, return its result
            return result

        except Exception as e:
            error_msg = f"Entity picker failed: {str(e)}"

            logger.error("Entity picker execution failed",
                        error=str(e),
                        error_type=type(e).__name__,
                        check_params=getattr(self, '_check_params', {}))

            self.state.error_message = error_msg
            return TaskResult(
                status="failed",
                error=error_msg
            )

    async def _determine_next_action(self, requirement_entities: Dict[str, List], requirements: List[Dict[str, Any]], context: Dict[str, Any]) -> TaskResult:
        """Crystal clear decision tree showing all possible outcomes"""

        logger.info("Determining next action based on entity discovery results")

        # First check: Do we have minimum entities?
        if not self._check_minimum_requirements_met(requirement_entities, requirements):
            logger.info("Minimum requirements not met - waiting for missing entities")
            return self._wait_for_missing_entities(requirement_entities, requirements, context)

        # Second check: Can we auto-select everything?
        if self._can_auto_select_all_requirements(requirement_entities, requirements):
            logger.info("All requirements can be auto-selected - proceeding automatically")
            return self._auto_select_entities(requirement_entities, requirements)

        # Third check: Need user to choose
        logger.info("User selection required - generating selection form")
        return self._wait_for_user_selection(requirement_entities, requirements)

    def _generate_missing_requirements_form(self, requirement_entities: Dict[str, List], requirements: List[Dict[str, Any]], context: Dict[str, Any]) -> Dict[str, Any]:
        """Generate notification (not form) explaining missing entity requirements"""
        missing_info = []

        for req in requirements:
            entity_type = req["entity_type"]
            min_count = req.get("min_count", 1)
            filters = req.get("filters", {})
            # Use store_as as the key (same as in BUILD)
            req_key = req.get("store_as")

            logger.debug("Processing missing requirement",
                        req_key=req_key,
                        available_keys=list(requirement_entities.keys()),
                        entity_type=entity_type)
            entities = requirement_entities.get(req_key, [])
            logger.debug("Found entities for requirement",
                        req_key=req_key,
                        found_count=len(entities),
                        min_required=min_count)

            if len(entities) < min_count:
                missing_count = min_count - len(entities)
                # Build description from all filters, not just document_type
                filter_desc = ", ".join([f"{k}: {v}" for k, v in filters.items()]) if filters else "unfiltered"

                missing_info.append({
                    "title": req.get("display_title", f"Entities {entity_type}"),
                    "description": f"Necesitas subir {missing_count} entidades de tipo: {entity_type} ({filter_desc})",
                    "type": "missing_requirement",
                    "entity_type": entity_type,
                    "filters": filters,
                    "required_count": min_count,
                    "found_count": len(entities)
                })

        return {
            "title": "Documentos Requeridos Faltantes",
            "description": "Antes de continuar, necesitas subir los siguientes documentos:",
            "type": "missing_requirements_notification",
            "waiting_for": "missing_entities",
            "missing_requirements": missing_info,
            "is_notification": True,  # Mark as notification, not input form
            "actions": [
                {
                    "type": "redirect",
                    "text": "Ir a Subir Documentos",
                    "route": "/documents"
                }
            ]
        }

    def _generate_selection_form(self, requirement_entities: Dict[str, List], requirements: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Generate form configuration for entity selection"""

        # Build form sections for each requirement
        form_sections = []

        for req in requirements:
            entity_type = req["entity_type"]
            min_count = req.get("min_count", 1)
            # Use store_as as the key, consistent with _discover_available_entities
            req_key = req.get("store_as", f"{entity_type}_entities")
            entities = requirement_entities.get(req_key, [])

            # Only skip if there are no entities AND it's a required field
            if not entities and min_count > 0:
                continue
            max_count = req.get("max_count", min_count)
            display_title = req.get("display_title", f"Select {entity_type.title()}")
            display_fields = req.get("display_fields", ["name"])

            # Create entity options for this requirement
            entity_options = []
            for entity in entities:
                # Build display label from display_fields
                label_parts = []
                for field in display_fields:
                    if hasattr(entity, field):
                        value = getattr(entity, field)
                    elif isinstance(entity.data, dict) and field in entity.data:
                        value = entity.data[field]
                    else:
                        continue

                    if value:
                        label_parts.append(str(value))

                label = " - ".join(label_parts) if label_parts else entity.name

                entity_options.append({
                    "value": entity.entity_id,
                    "label": label,
                    "entity_data": {
                        "entity_id": entity.entity_id,
                        "entity_type": entity.entity_type,
                        "name": entity.name,
                        "data": entity.data
                    }
                })

            # Use store_as as field name to match validation logic
            field_name = req.get("store_as")

            # Create form field
            form_field = {
                "name": field_name,
                "label": display_title,
                "type": "entity_multi_select" if max_count > 1 else "entity_select",
                "required": min_count > 0,
                "min_count": min_count,
                "max_count": max_count,
                "options": entity_options,
                "entity_type": entity_type,
                "description": f"Select {min_count}-{max_count} item(s)" if min_count != max_count else f"Select {min_count} item(s)"
            }

            form_sections.append(form_field)

        # Build complete form configuration
        # Get title and description from kwargs or use defaults
        title = self.kwargs.get("title", "Seleccionar Documentos Requeridos")
        description = self.kwargs.get("description", "Elige los documentos específicos necesarios para este trámite")
        submit_text = self.kwargs.get("submit_button_text", "Continuar con Documentos Seleccionados")

        form_config = {
            "title": title,
            "description": description,
            "fields": form_sections,
            "submit_button_text": submit_text
        }

        return form_config