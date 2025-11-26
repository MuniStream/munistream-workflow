"""
WorkflowStartOperator - Operator for starting child workflows with dynamic assignment.

This operator allows a workflow to start another workflow (typically administrative)
and optionally wait for its completion. Supports automatic assignment to teams or users.
"""

from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from enum import Enum

from .base import BaseOperator, TaskResult, TaskStatus
from ...models.workflow import WorkflowInstance, WorkflowType, AssignmentType, AssignmentStatus
from ...core.logging_config import get_workflow_logger, set_workflow_context

logger = get_workflow_logger(__name__)


class WorkflowStartOperator(BaseOperator):
    """
    Operator for starting child workflows with dynamic assignment.

    This operator enables workflow chaining where a citizen workflow (PROCESS type)
    can initiate an administrative workflow (ADMIN type) with automatic assignment
    to a team or specific user. The parent workflow can optionally wait for the
    child to complete before continuing.

    Example:
        # Basic team assignment (original functionality)
        validate_property = WorkflowStartOperator(
            task_id="validate_property_admin",
            workflow_id="admin_property_validation",
            workflow_type=WorkflowType.ADMIN,
            assign_to={"team": "validadores_catastro"},
            wait_for_completion=True,
            timeout_minutes=2880,  # 48 hours
            context_mapping={
                "property_id": "property_id",
                "documents": "uploaded_files"
            }
        )

        # Enhanced: Auto-assignment with role and auto-start
        validate_property_enhanced = WorkflowStartOperator(
            task_id="validate_property_admin",
            workflow_id="admin_property_validation",
            workflow_type=WorkflowType.ADMIN,
            assign_to={"team": "validadores_catastro"},  # Keycloak group
            auto_assign=True,                            # Enable auto user assignment
            assignee_role="reviewer",                    # Only assign to reviewers
            assignment_strategy="round_robin",           # Use round-robin assignment
            auto_start=True,                             # Auto-start after assignment
            wait_for_completion=True
        )
    """

    def __init__(
        self,
        task_id: str,
        workflow_id: str,
        workflow_type: WorkflowType = WorkflowType.ADMIN,
        assign_to: Optional[Dict[str, str]] = None,
        wait_for_completion: bool = True,
        timeout_minutes: int = 1440,  # 24 hours default
        pass_context: bool = True,
        context_mapping: Optional[Dict[str, str]] = None,
        required_status: str = "approved",
        priority: int = 5,
        auto_assign: bool = False,
        assignee_role: Optional[str] = None,
        auto_start: bool = False,
        assignment_strategy: str = "round_robin",
        **kwargs
    ):
        """
        Initialize the WorkflowStartOperator.

        Args:
            task_id: Unique identifier for this task
            workflow_id: ID of the workflow to start
            workflow_type: Type of workflow (ADMIN, PROCESS, etc.)
            assign_to: Assignment configuration - {"team": "name"} or {"admin": "email"}
            wait_for_completion: Whether to wait for child workflow to complete
            timeout_minutes: Maximum time to wait for completion
            pass_context: Whether to pass context from parent to child
            context_mapping: Map parent context keys to child keys
            required_status: Expected terminal status for success (if waiting)
            priority: Priority level for the child workflow (1-10)
            auto_assign: Enable automatic user assignment from Keycloak group
            assignee_role: Required role for assignee (e.g., "reviewer", "approver")
            auto_start: Automatically start workflow after assignment
            assignment_strategy: Assignment strategy (round_robin, workload_based, etc.)
            **kwargs: Additional arguments passed to BaseOperator
        """
        super().__init__(task_id=task_id, **kwargs)
        self.workflow_id = workflow_id
        self.workflow_type = workflow_type
        self.assign_to = assign_to
        self.wait_for_completion = wait_for_completion
        self.timeout_minutes = timeout_minutes
        self.pass_context = pass_context
        self.context_mapping = context_mapping or {}
        self.required_status = required_status
        self.priority = priority
        self.auto_assign = auto_assign
        self.assignee_role = assignee_role
        self.auto_start = auto_start
        self.assignment_strategy = assignment_strategy

        # State key will be set per instance to ensure uniqueness
        self._state_key = None

    def execute(self, context: Dict[str, Any]) -> TaskResult:
        """
        Synchronous execute not supported - this operator requires async execution.
        """
        raise RuntimeError("WorkflowStartOperator requires async execution. This should be handled by execute_async.")

    async def execute_async(self, context: Dict[str, Any]) -> TaskResult:
        """
        Execute the operator: create child workflow, assign, and optionally wait.

        Args:
            context: Execution context from parent workflow

        Returns:
            TaskResult with status and child workflow information
        """
        # Simple print to ensure the method is called
        print(f"[WorkflowStartOperator] Execute called for task {self.task_id}")
        print(f"[WorkflowStartOperator] Context keys: {list(context.keys())}")
        print(f"[WorkflowStartOperator] Instance ID: {context.get('instance_id')}")
        print(f"[WorkflowStartOperator] Workflow ID target: {self.workflow_id}")

        try:
            logger.info("WorkflowStartOperator.execute() started",
                       task_id=self.task_id,
                       workflow_id=self.workflow_id,
                       parent_instance_id=context.get("instance_id"))
        except Exception as e:
            print(f"[WorkflowStartOperator] Error logging: {e}")
            import traceback
            traceback.print_exc()

        # Set workflow context for logging
        try:
            set_workflow_context(
                user_id=context.get("user_id"),
                workflow_id=context.get("workflow_id"),
                instance_id=context.get("instance_id"),
                tenant=context.get("tenant"),
                step=self.task_id
            )
        except Exception as e:
            print(f"[WorkflowStartOperator] Error setting context: {e}")
            import traceback
            traceback.print_exc()

        # Create unique state key per workflow instance
        instance_id = context.get("instance_id")
        self._state_key = f"workflow_start_{self.task_id}_{instance_id}_state"

        # Get or initialize operator state (persisted across executions)
        self._operator_state = context.get(self._state_key, {})

        print(f"[WorkflowStartOperator] State key: {self._state_key}")
        print(f"[WorkflowStartOperator] Current state: {self._operator_state}")

        # Check if we're resuming (already have state from previous execution)
        if self._operator_state:
            # Restore state from previous execution
            self.child_instance_id = self._operator_state.get("child_instance_id")
            start_time_str = self._operator_state.get("start_time")
            if start_time_str:
                try:
                    self.start_time = datetime.fromisoformat(start_time_str)
                except:
                    self.start_time = datetime.utcnow()
            else:
                self.start_time = datetime.utcnow()

            print(f"[WorkflowStartOperator] Resuming with child_instance_id: {self.child_instance_id}")
            logger.info("Resuming check for child workflow",
                       child_workflow_id=self.child_instance_id,
                       workflow_type=self.workflow_type.value)
            return await self._check_child_status(context)

        # Create new child workflow
        logger.info("Starting child workflow creation process",
                   target_workflow_id=self.workflow_id,
                   workflow_type=self.workflow_type.value,
                   assign_to=self.assign_to,
                   wait_for_completion=self.wait_for_completion,
                   parent_context_keys=list(context.keys()))

        try:
            # Prepare context for child
            logger.debug("Preparing child context")
            child_context = self._prepare_child_context(context)
            logger.debug("Child context prepared",
                        child_context_keys=list(child_context.keys()))

            # Create the child instance
            logger.info("Calling _create_child_workflow")
            child_instance = await self._create_child_workflow(child_context)

            if not child_instance:
                logger.error("Child instance creation returned None")
                return TaskResult(
                    status=TaskStatus.FAILED,
                    data={
                        "error": "Child workflow instance creation returned None",
                        "workflow_id": self.workflow_id
                    }
                )

            self.child_instance_id = child_instance.instance_id
            self.start_time = datetime.utcnow()

            logger.info("Child workflow instance created successfully",
                       child_instance_id=self.child_instance_id,
                       parent_instance_id=context.get("instance_id"),
                       child_status=child_instance.status)

            # Assign if configuration provided
            if self.assign_to:
                logger.debug("Starting workflow assignment",
                            assign_to=self.assign_to)
                print(f"[WorkflowStartOperator] About to call _assign_workflow")
                try:
                    await self._assign_workflow(child_instance, context)
                    print(f"[WorkflowStartOperator] _assign_workflow completed successfully")
                    logger.info("Child workflow assigned",
                               child_instance_id=self.child_instance_id,
                               assignment=self.assign_to)
                except Exception as assign_error:
                    print(f"[WorkflowStartOperator] ERROR in _assign_workflow: {assign_error}")
                    print(f"[WorkflowStartOperator] Error type: {type(assign_error).__name__}")
                    import traceback
                    traceback.print_exc()
                    logger.error("Failed to assign child workflow",
                               error=str(assign_error),
                               error_type=type(assign_error).__name__,
                               child_instance_id=self.child_instance_id,
                               exc_info=True)
                    raise
            else:
                logger.warning("No assignment configuration provided - workflow not assigned",
                             workflow_id=self.workflow_id,
                             task_id=self.task_id)

            # Store state for persistence across executions AFTER successful assignment
            self._operator_state = {
                "child_instance_id": self.child_instance_id,
                "start_time": self.start_time.isoformat(),
                "workflow_id": self.workflow_id,
                "assignment_completed": True
            }
            context[self._state_key] = self._operator_state

            logger.info("WorkflowStartOperator state persisted after assignment",
                       state_key=self._state_key,
                       child_instance_id=self.child_instance_id)

            # If not waiting for completion, return success immediately
            if not self.wait_for_completion:
                logger.info("Not waiting for completion, returning success",
                           child_instance_id=self.child_instance_id)
                return TaskResult(
                    status=TaskStatus.COMPLETED,
                    data={
                        "child_instance_id": self.child_instance_id,  # Add instance_id
                        "child_workflow_id": self.child_instance_id,  # Keep for backward compatibility
                        "child_workflow_type": self.workflow_type.value,
                        "assigned_to": self.assign_to,
                        "message": f"Started child workflow {self.workflow_id}",
                        self._state_key: self._operator_state  # Persist state
                    }
                )

            # Otherwise, check status (will return WAITING)
            logger.info("Waiting for child workflow completion",
                       child_instance_id=self.child_instance_id)
            try:
                print(f"[DEBUG] About to call _check_child_status...")
                result = await self._check_child_status(context)
                print(f"[DEBUG] _check_child_status returned: {result}")
                print(f"[DEBUG] Result status: {result.status}")
                return result
            except Exception as e:
                print(f"[DEBUG] EXCEPTION in _check_child_status: {e}")
                print(f"[DEBUG] Exception type: {type(e).__name__}")
                import traceback
                traceback.print_exc()
                raise

        except Exception as e:
            logger.error("Exception in WorkflowStartOperator.execute",
                        error=str(e),
                        error_type=type(e).__name__,
                        workflow_id=self.workflow_id,
                        parent_instance_id=context.get("instance_id"),
                        exc_info=True)
            return TaskResult(
                status=TaskStatus.FAILED,
                data={
                    "error": f"Failed to start workflow: {str(e)}",
                    "error_type": type(e).__name__,
                    "workflow_id": self.workflow_id
                }
            )

    def _prepare_child_context(self, parent_context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Prepare context for the child workflow.

        Args:
            parent_context: Context from parent workflow

        Returns:
            Prepared context for child workflow
        """
        child_context = {
            "parent_instance_id": parent_context.get("instance_id"),
            "parent_workflow_id": parent_context.get("workflow_id"),
            "parent_task_id": self.task_id,
            "initiated_by": "workflow_start_operator",
            "priority": self.priority,
            "created_at": datetime.utcnow().isoformat()
        }

        # Add user information if available
        if "user_id" in parent_context:
            child_context["parent_user_id"] = parent_context["user_id"]

        if "customer_email" in parent_context:
            child_context["parent_customer_email"] = parent_context["customer_email"]

        # ALWAYS copy the entire parent context as _parent_context
        # This allows administrative operators to access all parent workflow data
        child_context["_parent_context"] = parent_context.copy()

        # Map specific fields from parent to child
        if self.pass_context and self.context_mapping:
            for parent_key, child_key in self.context_mapping.items():
                if parent_key in parent_context:
                    child_context[child_key] = parent_context[parent_key]
                    logger.debug("Mapped context field",
                               parent_key=parent_key,
                               child_key=child_key)

        # If pass_context is True but no specific mapping, pass common fields
        elif self.pass_context:
            # Default fields to pass
            default_fields = [
                "tenant", "customer_id", "entity_id", "documents",
                "form_data", "metadata"
            ]
            for field in default_fields:
                if field in parent_context:
                    child_context[field] = parent_context[field]

        return child_context

    async def _create_child_workflow(self, context: Dict[str, Any]) -> WorkflowInstance:
        """
        Create the child workflow instance.

        Args:
            context: Context for the child workflow

        Returns:
            Created WorkflowInstance
        """
        print(f"[WorkflowStartOperator] _create_child_workflow called")
        print(f"[WorkflowStartOperator] Target workflow_id: {self.workflow_id}")
        print(f"[WorkflowStartOperator] Parent instance: {context.get('parent_instance_id')}")

        logger.info("_create_child_workflow started",
                   workflow_id=self.workflow_id,
                   parent_instance_id=context.get("parent_instance_id"))

        try:
            # Import workflow_service dynamically to avoid circular import
            print(f"[WorkflowStartOperator] About to import workflow_service")
            logger.debug("Importing workflow_service")
            from ...services.workflow_service import workflow_service
            print(f"[WorkflowStartOperator] workflow_service imported successfully")
            logger.debug("workflow_service imported successfully")

            # Get the workflow definition to verify it exists and check type
            print(f"[WorkflowStartOperator] Getting workflow definition for: {self.workflow_id}")
            logger.info("Getting workflow definition",
                       workflow_id=self.workflow_id)
            workflow_def = await workflow_service.get_workflow_definition(self.workflow_id)

            if workflow_def:
                print(f"[WorkflowStartOperator] Workflow definition found: {workflow_def}")
                print(f"[WorkflowStartOperator] Definition workflow_type: {getattr(workflow_def, 'workflow_type', 'NOT SET')}")
            else:
                print(f"[WorkflowStartOperator] Workflow definition NOT found!")
            print(f"[WorkflowStartOperator] Got workflow def: {workflow_def is not None}")
            if not workflow_def:
                logger.error("Workflow definition not found",
                           workflow_id=self.workflow_id)
                raise ValueError(f"Workflow {self.workflow_id} not found")

            logger.debug("Workflow definition found",
                        workflow_id=self.workflow_id,
                        has_workflow_type=hasattr(workflow_def, 'workflow_type'))

            # Create the instance
            print(f"[WorkflowStartOperator] About to create instance")
            print(f"[WorkflowStartOperator] User ID: {context.get('parent_user_id', 'system')}")
            logger.info("Creating workflow instance via workflow_service",
                       workflow_id=self.workflow_id,
                       user_id=context.get("parent_user_id", "system"))

            try:
                dag_instance = await workflow_service.create_instance(
                    workflow_id=self.workflow_id,
                    user_id=context.get("parent_user_id", "system"),
                    initial_data=context
                )
                print(f"[WorkflowStartOperator] create_instance returned: {dag_instance is not None}")
                if dag_instance:
                    print(f"[WorkflowStartOperator] Instance ID: {dag_instance.instance_id if hasattr(dag_instance, 'instance_id') else 'No instance_id attr'}")
                    print(f"[WorkflowStartOperator] DAG instance workflow_type: {getattr(dag_instance, 'workflow_type', 'NOT SET')}")
            except Exception as create_ex:
                print(f"[WorkflowStartOperator] ERROR creating instance: {create_ex}")
                import traceback
                traceback.print_exc()
                raise

            if not dag_instance:
                logger.error("workflow_service.create_instance returned None")
                raise RuntimeError("Failed to create workflow instance - returned None")

            logger.info("DAG instance created",
                       instance_id=dag_instance.instance_id)

            # Get the database instance to modify status
            logger.debug("Looking up database instance",
                        instance_id=dag_instance.instance_id)
            db_instance = await WorkflowInstance.find_one(
                WorkflowInstance.instance_id == dag_instance.instance_id
            )

            if not db_instance:
                logger.error("Database instance not found after creation",
                           instance_id=dag_instance.instance_id)
                raise RuntimeError(f"Failed to find created instance {dag_instance.instance_id}")

            logger.debug("Database instance found",
                        instance_id=db_instance.instance_id,
                        current_status=db_instance.status)
            print(f"[WorkflowStartOperator] DB instance workflow_type BEFORE setting: {db_instance.workflow_type}")

            # Set workflow type if available
            if hasattr(workflow_def, 'workflow_type'):
                print(f"[WorkflowStartOperator] Setting workflow_type from definition: {workflow_def.workflow_type}")
                db_instance.workflow_type = workflow_def.workflow_type
                print(f"[WorkflowStartOperator] DB instance workflow_type now: {db_instance.workflow_type}")
                logger.debug("Set workflow type",
                           workflow_type=workflow_def.workflow_type)
            else:
                print(f"[WorkflowStartOperator] Workflow definition has no workflow_type attribute")

            # For ADMIN workflows, set to pending_assignment initially
            print(f"[WorkflowStartOperator] Checking if ADMIN workflow: self.workflow_type={self.workflow_type}, WorkflowType.ADMIN={WorkflowType.ADMIN}")
            if self.workflow_type == WorkflowType.ADMIN:
                print(f"[WorkflowStartOperator] Setting admin workflow status")
                db_instance.status = "pending_assignment"
                db_instance.assignment_status = AssignmentStatus.PENDING_REVIEW
                logger.info("Set child workflow to pending_assignment status",
                           instance_id=db_instance.instance_id)
            else:
                print(f"[WorkflowStartOperator] Not an ADMIN workflow, keeping default status")

            # Store parent reference in context
            if "parent_instance_id" not in db_instance.context:
                db_instance.context["parent_instance_id"] = context.get("parent_instance_id")
                logger.debug("Added parent reference to context",
                           parent_instance_id=context.get("parent_instance_id"))

            # Set priority
            db_instance.priority = self.priority
            logger.debug("Set priority",
                        priority=self.priority)

            logger.info("Saving database instance",
                       instance_id=db_instance.instance_id)
            await db_instance.save()

            logger.info("Child workflow created successfully",
                       instance_id=db_instance.instance_id,
                       status=db_instance.status,
                       workflow_type=self.workflow_type.value)

            return db_instance

        except Exception as e:
            print(f"[WorkflowStartOperator] EXCEPTION in _create_child_workflow: {e}")
            print(f"[WorkflowStartOperator] Exception type: {type(e).__name__}")
            import traceback
            traceback.print_exc()
            logger.error("Exception in _create_child_workflow",
                        error=str(e),
                        error_type=type(e).__name__,
                        workflow_id=self.workflow_id,
                        exc_info=True)
            raise

    async def _assign_workflow(
        self,
        instance: WorkflowInstance,
        context: Dict[str, Any]
    ) -> None:
        """
        Assign the workflow to a team or user, with optional auto-assignment to specific user from Keycloak group.

        Args:
            instance: Workflow instance to assign
            context: Parent workflow context (for assigned_by info)
        """
        print(f"[WorkflowStartOperator] _assign_workflow started")
        print(f"[WorkflowStartOperator] assign_to: {self.assign_to}")
        print(f"[WorkflowStartOperator] auto_assign: {self.auto_assign}")
        print(f"[WorkflowStartOperator] assignee_role: {self.assignee_role}")
        print(f"[WorkflowStartOperator] instance.instance_id: {instance.instance_id}")

        assigned_by = context.get("user_id", "system")
        print(f"[WorkflowStartOperator] assigned_by: {assigned_by}")

        try:
            assigned_user_id = None

            if "team" in self.assign_to:
                team_id = self.assign_to["team"]
                print(f"[WorkflowStartOperator] Assigning to team: {team_id}")

                # If auto_assign is enabled, try to assign to specific user within the group
                if self.auto_assign:
                    print(f"[WorkflowStartOperator] Auto-assign enabled, looking for specific user")
                    try:
                        # Import the service here to avoid circular imports
                        from ...services.keycloak_group_assignment import keycloak_group_assignment_service

                        assigned_user_id = await keycloak_group_assignment_service.assign_to_user_from_group(
                            group_id=team_id,
                            required_role=self.assignee_role,
                            workflow_id=self.workflow_id,
                            assignment_strategy=self.assignment_strategy
                        )

                        if assigned_user_id:
                            print(f"[WorkflowStartOperator] Auto-assigned to specific user: {assigned_user_id}")
                            logger.info("Auto-assigned to specific user from group",
                                       user_id=assigned_user_id,
                                       group_id=team_id,
                                       role=self.assignee_role,
                                       instance_id=instance.instance_id)
                        else:
                            print(f"[WorkflowStartOperator] Auto-assignment failed, falling back to team assignment")
                            logger.warning("Auto-assignment to specific user failed, using team assignment",
                                         group_id=team_id,
                                         role=self.assignee_role)
                    except Exception as auto_assign_error:
                        print(f"[WorkflowStartOperator] Auto-assignment error: {auto_assign_error}")
                        logger.error("Error in auto-assignment, falling back to team assignment",
                                   error=str(auto_assign_error),
                                   group_id=team_id)

                # Assign to team (always done) and optionally to specific user
                instance.assign_to_team(
                    team_id=team_id,
                    assigned_by=assigned_by,
                    assignment_type=AssignmentType.AUTOMATIC,
                    notes=f"Auto-assigned by workflow {context.get('workflow_id', 'unknown')} task {self.task_id}"
                )

                # If we have a specific user, assign to them as well
                if assigned_user_id:
                    instance.assign_to_user(
                        user_id=assigned_user_id,
                        assigned_by=assigned_by,
                        assignment_type=AssignmentType.AUTOMATIC,
                        notes=f"Auto-assigned to user {assigned_user_id} from group {team_id} with role {self.assignee_role}"
                    )

                print(f"[WorkflowStartOperator] Team assignment completed")
                logger.info("Assigned to team",
                           team_id=team_id,
                           assigned_user_id=assigned_user_id,
                           instance_id=instance.instance_id)

            elif "admin" in self.assign_to:
                print(f"[WorkflowStartOperator] Assigning to user: {self.assign_to['admin']}")
                instance.assign_to_user(
                    user_id=self.assign_to["admin"],
                    assigned_by=assigned_by,
                    assignment_type=AssignmentType.AUTOMATIC,
                    notes=f"Auto-assigned by workflow {context.get('workflow_id', 'unknown')} task {self.task_id}"
                )
                assigned_user_id = self.assign_to["admin"]
                print(f"[WorkflowStartOperator] User assignment completed")
                logger.info("Assigned to user",
                           user_id=self.assign_to["admin"],
                           instance_id=instance.instance_id)

            print(f"[WorkflowStartOperator] Setting parent-child relationship")
            # Set parent-child relationship
            instance.parent_instance_id = context.get("instance_id")
            instance.parent_workflow_id = context.get("workflow_id")
            print(f"[WorkflowStartOperator] parent_instance_id set to: {instance.parent_instance_id}")
            print(f"[WorkflowStartOperator] parent_workflow_id set to: {instance.parent_workflow_id}")

            print(f"[WorkflowStartOperator] Updating status and assignment_status")

            # Determine initial status based on auto_start setting and assignment
            if self.auto_start and assigned_user_id:
                # Auto-start enabled and we have a specific user - start immediately
                instance.status = "running"
                instance.assignment_status = AssignmentStatus.UNDER_REVIEW
                print(f"[WorkflowStartOperator] Auto-start enabled with specific user - status set to running")
                logger.info("Auto-started workflow with specific user assignment",
                           instance_id=instance.instance_id,
                           assigned_user_id=assigned_user_id)
            else:
                # Standard flow - wait for manual start
                instance.status = "waiting_for_start"
                instance.assignment_status = AssignmentStatus.PENDING_REVIEW
                print(f"[WorkflowStartOperator] Standard flow - status set to waiting_for_start")

            print(f"[WorkflowStartOperator] Status set to: {instance.status}")
            print(f"[WorkflowStartOperator] Assignment status set to: {instance.assignment_status}")

            print(f"[WorkflowStartOperator] About to save instance")
            await instance.save()
            print(f"[WorkflowStartOperator] Instance saved successfully")

            # If auto-start is enabled and we have a specific user, trigger workflow execution
            if self.auto_start and assigned_user_id:
                try:
                    print(f"[WorkflowStartOperator] Triggering auto-start workflow execution")
                    from ...services.workflow_service import workflow_service

                    # Start the workflow execution
                    await workflow_service.execute_instance(instance.instance_id)
                    logger.info("Auto-started workflow execution triggered",
                               instance_id=instance.instance_id,
                               assigned_user_id=assigned_user_id)
                    print(f"[WorkflowStartOperator] Auto-start execution triggered successfully")

                except Exception as auto_start_error:
                    logger.error("Failed to auto-start workflow execution",
                               error=str(auto_start_error),
                               instance_id=instance.instance_id)
                    print(f"[WorkflowStartOperator] Auto-start execution failed: {auto_start_error}")
                    # Don't raise the error - assignment was successful, just auto-start failed

            logger.info("Parent-child relationship established",
                       child_instance_id=instance.instance_id,
                       parent_instance_id=instance.parent_instance_id,
                       parent_workflow_id=instance.parent_workflow_id,
                       auto_started=self.auto_start and assigned_user_id is not None)
            print(f"[WorkflowStartOperator] _assign_workflow completed successfully")

        except Exception as e:
            print(f"[WorkflowStartOperator] EXCEPTION in _assign_workflow: {e}")
            print(f"[WorkflowStartOperator] Exception type: {type(e).__name__}")
            import traceback
            traceback.print_exc()
            raise

    async def _check_child_status(self, context: Dict[str, Any]) -> TaskResult:
        """
        Check the status of the child workflow.

        Args:
            context: Parent workflow context

        Returns:
            TaskResult indicating current status
        """
        logger.debug("_check_child_status called",
                    child_instance_id=self.child_instance_id)

        if not self.child_instance_id:
            logger.error("No child workflow instance ID to check")
            return TaskResult(
                status=TaskStatus.FAILED,
                data={"error": "No child workflow instance to check"}
            )

        # Get updated instance from database
        logger.debug("Looking up child instance in database",
                    child_instance_id=self.child_instance_id)
        child = await WorkflowInstance.find_one(
            WorkflowInstance.instance_id == self.child_instance_id
        )

        if not child:
            logger.error("Child workflow instance not found in database",
                        child_instance_id=self.child_instance_id)
            return TaskResult(
                status=TaskStatus.FAILED,
                data={
                    "error": f"Child workflow {self.child_instance_id} not found",
                    "child_workflow_id": self.child_instance_id
                }
            )

        # Check for timeout
        if self.start_time:
            elapsed_minutes = (datetime.utcnow() - self.start_time).total_seconds() / 60
            if elapsed_minutes > self.timeout_minutes:
                logger.warning("Child workflow timed out",
                              child_instance_id=self.child_instance_id,
                              elapsed_minutes=elapsed_minutes,
                              timeout_minutes=self.timeout_minutes)
                return TaskResult(
                    status=TaskStatus.FAILED,
                    data={
                        "error": f"Timeout waiting for child workflow after {self.timeout_minutes} minutes",
                        "child_workflow_id": self.child_instance_id,
                        "child_status": child.status,
                        "elapsed_minutes": elapsed_minutes
                    }
                )

        # Check if completed
        print(f"[DEBUG] child.status = '{child.status}'")
        if child.status == "completed":
            # Check terminal status matches requirement
            terminal_status = child.terminal_status or "completed"
            print(f"[DEBUG] child.terminal_status = '{child.terminal_status}'")
            print(f"[DEBUG] terminal_status = '{terminal_status}'")
            print(f"[DEBUG] self.required_status = '{self.required_status}'")
            print(f"[DEBUG] Checking: terminal_status == self.required_status -> {terminal_status == self.required_status}")
            print(f"[DEBUG] Checking: self.required_status == 'any' -> {self.required_status == 'any'}")

            if terminal_status == self.required_status or self.required_status == "any":
                print(f"[DEBUG] SUCCESS: Child workflow completion validated")
                logger.info("Child workflow completed",
                           child_instance_id=self.child_instance_id,
                           terminal_status=terminal_status)
                try:
                    print(f"[DEBUG] Creating SUCCESS TaskResult...")
                    # Copy entire child context to parent, excluding system fields
                    child_data = {
                        "child_instance_id": self.child_instance_id,
                        "child_workflow_id": self.child_instance_id,
                        "child_status": terminal_status,
                        "completed_at": child.completed_at.isoformat() if child.completed_at else None,
                        "message": f"Child workflow completed with status {terminal_status}"
                    }

                    # Copy all child context data, excluding internal fields
                    if child.context:
                        for key, value in child.context.items():
                            if not key.startswith(('_', 'instance', 'workflow', 'task_instance')):
                                child_data[key] = value

                    task_result = TaskResult(
                        status=TaskStatus.CONTINUE,
                        data=child_data
                    )
                    print(f"[DEBUG] SUCCESS TaskResult created successfully")
                    return task_result
                except Exception as e:
                    print(f"[DEBUG] EXCEPTION creating SUCCESS TaskResult: {e}")
                    import traceback
                    traceback.print_exc()
                    raise
            else:
                print(f"[DEBUG] FAILURE: Terminal status mismatch")
                logger.warning("Child workflow completed with unexpected status",
                              child_instance_id=self.child_instance_id,
                              actual_status=terminal_status,
                              expected_status=self.required_status)
                return TaskResult(
                    status=TaskStatus.FAILED,
                    data={
                        "error": f"Child workflow completed with status {terminal_status}, expected {self.required_status}",
                        "child_workflow_id": self.child_instance_id,
                        "child_status": terminal_status
                    }
                )

        # Check if failed
        if child.status in ["failed", "cancelled"]:
            logger.error("Child workflow failed",
                        child_instance_id=self.child_instance_id,
                        status=child.status)
            return TaskResult(
                status=TaskStatus.FAILED,
                data={
                    "error": f"Child workflow {child.status}",
                    "child_workflow_id": self.child_instance_id,
                    "child_error": child.terminal_message or "Unknown error"
                }
            )

        # Still in progress - return waiting status
        self.last_check_time = datetime.utcnow()

        # Prepare status message based on assignment status
        status_message = "Child workflow in progress"
        if child.status == "pending_assignment":
            status_message = "Waiting for workflow assignment"
        elif child.status == "waiting_for_start":
            status_message = f"Assigned to {child.assigned_user_id or child.assigned_team_id}, waiting for start"
        elif child.assignment_status == AssignmentStatus.UNDER_REVIEW:
            status_message = f"Under review by {child.assigned_user_id or child.assigned_team_id}"

        logger.debug("Child workflow still in progress",
                    child_instance_id=self.child_instance_id,
                    child_status=child.status,
                    assignment_status=child.assignment_status.value if child.assignment_status else None)

        # Update state with last check time
        self._operator_state["last_check"] = datetime.utcnow().isoformat()
        context[self._state_key] = self._operator_state

        return TaskResult(
            status=TaskStatus.WAITING,
            data={
                "waiting_for": "child_workflow_completion",
                "child_instance_id": self.child_instance_id,  # Add instance_id
                "child_workflow_id": self.child_instance_id,  # Keep for backward compatibility
                "child_status": child.status,
                "assignment_status": child.assignment_status.value if child.assignment_status else None,
                "assigned_to": child.assigned_user_id or child.assigned_team_id,
                "message": status_message,
                "elapsed_minutes": (datetime.utcnow() - self.start_time).total_seconds() / 60 if self.start_time else 0,
                self._state_key: self._operator_state  # Persist state
            }
        )