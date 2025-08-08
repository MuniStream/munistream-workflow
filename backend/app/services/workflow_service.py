from typing import List, Optional, Dict, Any
from datetime import datetime
from beanie import PydanticObjectId
from beanie.operators import In

from ..models.workflow import (
    WorkflowDefinition,
    WorkflowStep,
    WorkflowInstance,
    StepExecution,
    ApprovalRequest,
    WorkflowAuditLog
)
from ..workflows.dag import DAG, DAGInstance, DAGBag, InstanceStatus
from ..workflows.executor import DAGExecutor
from ..workflows.operators.base import BaseOperator


class WorkflowService:
    """Service layer for new DAG-based workflow operations"""
    
    def __init__(self):
        self.dag_bag = DAGBag()
        self.executor = DAGExecutor()
    
    async def register_dag(self, dag: DAG, created_by: str = None) -> WorkflowDefinition:
        """Register a DAG and create corresponding database records"""
        # Add DAG to bag
        self.dag_bag.add_dag(dag)
        
        # Create workflow definition record
        workflow_def = WorkflowDefinition(
            workflow_id=dag.dag_id,
            name=dag.description or dag.dag_id,
            description=dag.description,
            version=dag.version,
            status="active" if dag.status.value == "active" else "draft",
            start_step_id=dag.get_root_tasks()[0].task_id if dag.get_root_tasks() else None,
            category="automated",
            tags=dag.tags,
            metadata=dag.metadata,
            created_by=created_by
        )
        
        await workflow_def.insert()
        
        # Create step records for API compatibility
        for task_id, task in dag.tasks.items():
            step = WorkflowStep(
                step_id=task_id,
                workflow_id=dag.dag_id,
                name=task_id.replace("_", " ").title(),
                step_type=task.__class__.__name__.replace("Operator", "").lower(),
                description=f"{task.__class__.__name__} operation",
                required_inputs=[],
                optional_inputs=[],
                next_steps=[t.task_id for t in task.downstream_tasks],
                configuration=task.kwargs,
                requires_citizen_input=hasattr(task, 'form_config'),
                input_form=getattr(task, 'form_config', {}),
                created_by=created_by
            )
            await step.insert()
        
        return workflow_def
    
    async def get_dag(self, dag_id: str) -> Optional[DAG]:
        """Get DAG by ID"""
        return self.dag_bag.get_dag(dag_id)
    
    async def get_workflow_definition(self, workflow_id: str) -> Optional[WorkflowDefinition]:
        """Get workflow definition by ID"""
        return await WorkflowDefinition.find_one(WorkflowDefinition.workflow_id == workflow_id)
    
    @staticmethod
    async def list_workflow_definitions(
        status: Optional[str] = None,
        category: Optional[str] = None,
        skip: int = 0,
        limit: int = 20
    ) -> List[WorkflowDefinition]:
        """List workflow definitions with filters"""
        query = {}
        
        if status:
            query["status"] = status
        if category:
            query["category"] = category
        
        return await WorkflowDefinition.find(query).skip(skip).limit(limit).to_list()
    
    @staticmethod
    async def update_workflow_definition(
        workflow_id: str,
        updates: Dict[str, Any],
        updated_by: str = None
    ) -> Optional[WorkflowDefinition]:
        """Update workflow definition"""
        workflow_def = await WorkflowService.get_workflow_definition(workflow_id)
        if not workflow_def:
            return None
        
        for key, value in updates.items():
            if hasattr(workflow_def, key):
                setattr(workflow_def, key, value)
        
        workflow_def.updated_at = datetime.utcnow()
        workflow_def.updated_by = updated_by
        
        await workflow_def.save()
        return workflow_def
    
    async def get_workflow_steps(self, workflow_id: str) -> List[WorkflowStep]:
        """Get all steps for a workflow"""
        return await WorkflowStep.find(WorkflowStep.workflow_id == workflow_id).to_list()


    async def create_instance(
        self, 
        workflow_id: str,
        user_id: str, 
        initial_data: Dict[str, Any] = None
    ) -> DAGInstance:
        """Create a new DAG instance"""
        # Get DAG from bag
        dag = self.dag_bag.get_dag(workflow_id)
        if not dag:
            raise ValueError(f"Workflow {workflow_id} not found")
        
        # Create DAG instance
        dag_instance = dag.create_instance(user_id, initial_data)
        self.dag_bag.instances[dag_instance.instance_id] = dag_instance
        
        # Create database record for API compatibility
        status_mapping = {
            InstanceStatus.PENDING: "pending",
            InstanceStatus.RUNNING: "running",
            InstanceStatus.PAUSED: "awaiting_input",
            InstanceStatus.COMPLETED: "completed",
            InstanceStatus.FAILED: "failed",
            InstanceStatus.CANCELLED: "cancelled"
        }
        
        workflow_instance = WorkflowInstance(
            instance_id=dag_instance.instance_id,
            workflow_id=dag_instance.dag.dag_id,
            workflow_version=dag_instance.dag.version,
            user_id=user_id,
            user_data=initial_data or {},
            status=status_mapping.get(dag_instance.status, "pending"),
            current_step=dag_instance.current_task,
            context=dag_instance.context,
            completed_steps=list(dag_instance.completed_tasks),
            failed_steps=list(dag_instance.failed_tasks),
            started_at=dag_instance.created_at
        )
        
        await workflow_instance.insert()
        
        return dag_instance
    
    async def execute_instance(self, instance_id: str) -> bool:
        """Execute a DAG instance"""
        dag_instance = self.dag_bag.get_instance(instance_id)
        if not dag_instance:
            raise ValueError(f"Instance {instance_id} not found")
        
        # Submit to executor
        self.executor.submit_instance(dag_instance)
        return True
    
    async def get_instance(self, instance_id: str) -> Optional[DAGInstance]:
        """Get DAG instance by ID"""
        return self.dag_bag.get_instance(instance_id)
    
    async def get_user_instances(self, user_id: str) -> List[DAGInstance]:
        """Get all instances for a user"""
        return self.dag_bag.get_user_instances(user_id)
    
    async def update_instance_from_dag(self, dag_instance: DAGInstance):
        """Update database record from DAG instance state"""
        workflow_instance = await WorkflowInstance.find_one(
            WorkflowInstance.instance_id == dag_instance.instance_id
        )
        
        if workflow_instance:
            # Update status
            status_mapping = {
                InstanceStatus.PENDING: "pending",
                InstanceStatus.RUNNING: "running",
                InstanceStatus.PAUSED: "awaiting_input",
                InstanceStatus.COMPLETED: "completed", 
                InstanceStatus.FAILED: "failed",
                InstanceStatus.CANCELLED: "cancelled"
            }
            
            workflow_instance.status = status_mapping.get(dag_instance.status, "running")
            workflow_instance.current_step = dag_instance.current_task
            workflow_instance.context = dag_instance.context
            workflow_instance.completed_steps = list(dag_instance.completed_tasks)
            workflow_instance.failed_steps = list(dag_instance.failed_tasks)
            workflow_instance.completed_at = dag_instance.completed_at
            
            if dag_instance.completed_at and dag_instance.started_at:
                duration = (dag_instance.completed_at - dag_instance.started_at).total_seconds()
                workflow_instance.duration_seconds = duration
            
            await workflow_instance.save()
    
    async def start_executor(self):
        """Start the DAG executor"""
        await self.executor.start()
    
    async def stop_executor(self):
        """Stop the DAG executor"""
        await self.executor.stop()


# Global workflow service instance
workflow_service = WorkflowService()


class InstanceService:
    """Legacy service class for API compatibility"""
    
    @staticmethod
    async def get_instance(instance_id: str) -> Optional[WorkflowInstance]:
        """Get workflow instance by ID"""
        return await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
    
    @staticmethod
    async def list_instances(
        workflow_id: Optional[str] = None,
        user_id: Optional[str] = None,
        status: Optional[str] = None,
        skip: int = 0,
        limit: int = 20
    ) -> List[WorkflowInstance]:
        """List workflow instances with filters"""
        query = {}
        
        if workflow_id:
            query["workflow_id"] = workflow_id
        if user_id:
            query["user_id"] = user_id
        if status:
            query["status"] = status
        
        return await WorkflowInstance.find(query).sort(-WorkflowInstance.started_at).skip(skip).limit(limit).to_list()
    
    @staticmethod
    async def update_instance(
        instance_id: str,
        updates: Dict[str, Any]
    ) -> Optional[WorkflowInstance]:
        """Update workflow instance"""
        instance = await InstanceService.get_instance(instance_id)
        if not instance:
            return None
        
        for key, value in updates.items():
            if hasattr(instance, key):
                if key == "context" and isinstance(value, dict):
                    # Merge context updates
                    instance.context.update(value)
                else:
                    setattr(instance, key, value)
        
        instance.updated_at = datetime.utcnow()
        await instance.save()
        
        return instance
    
    @staticmethod
    async def complete_instance(
        instance_id: str,
        terminal_status: str,
        terminal_message: str = None
    ) -> Optional[WorkflowInstance]:
        """Mark instance as completed"""
        instance = await InstanceService.get_instance(instance_id)
        if not instance:
            return None
        
        instance.status = "completed"
        instance.terminal_status = terminal_status
        instance.terminal_message = terminal_message
        instance.completed_at = datetime.utcnow()
        
        if instance.started_at:
            duration = (instance.completed_at - instance.started_at).total_seconds()
            instance.duration_seconds = duration
        
        await instance.save()
        
        # Update workflow statistics
        workflow_def = await WorkflowService.get_workflow_definition(instance.workflow_id)
        if workflow_def:
            if terminal_status == "SUCCESS":
                workflow_def.successful_instances += 1
            else:
                workflow_def.failed_instances += 1
            await workflow_def.save()
        
        return instance


class StepExecutionService:
    """Service layer for step execution tracking"""
    
    @staticmethod
    async def create_step_execution(
        instance_id: str,
        step_id: str,
        workflow_id: str,
        inputs: Dict[str, Any] = None
    ) -> StepExecution:
        """Create a new step execution record"""
        import uuid
        
        execution = StepExecution(
            execution_id=str(uuid.uuid4()),
            instance_id=instance_id,
            step_id=step_id,
            workflow_id=workflow_id,
            inputs=inputs or {},
            started_at=datetime.utcnow()
        )
        
        await execution.insert()
        return execution
    
    @staticmethod
    async def complete_step_execution(
        execution_id: str,
        status: str,
        outputs: Dict[str, Any] = None,
        error_message: str = None
    ) -> Optional[StepExecution]:
        """Complete a step execution"""
        execution = await StepExecution.find_one(StepExecution.execution_id == execution_id)
        if not execution:
            return None
        
        execution.status = status
        execution.outputs = outputs or {}
        execution.error_message = error_message
        execution.completed_at = datetime.utcnow()
        
        if execution.started_at:
            duration = (execution.completed_at - execution.started_at).total_seconds()
            execution.duration_seconds = duration
        
        await execution.save()
        return execution
    
    @staticmethod
    async def get_instance_executions(instance_id: str) -> List[StepExecution]:
        """Get all step executions for an instance"""
        return await StepExecution.find(
            StepExecution.instance_id == instance_id
        ).sort(StepExecution.started_at).to_list()


class AuditService:
    """Service layer for audit logging"""
    
    @staticmethod
    async def log_action(
        action: str,
        actor: str,
        target: str,
        workflow_id: str = None,
        instance_id: str = None,
        before_state: Dict[str, Any] = None,
        after_state: Dict[str, Any] = None,
        **context
    ):
        """Log an audit action"""
        import uuid
        
        log_entry = WorkflowAuditLog(
            log_id=str(uuid.uuid4()),
            workflow_id=workflow_id,
            instance_id=instance_id,
            action=action,
            actor=actor,
            target=target,
            before_state=before_state,
            after_state=after_state,
            ip_address=context.get("ip_address"),
            user_agent=context.get("user_agent"),
            session_id=context.get("session_id")
        )
        
        await log_entry.insert()
        return log_entry
    
    @staticmethod
    async def get_audit_logs(
        workflow_id: str = None,
        instance_id: str = None,
        actor: str = None,
        skip: int = 0,
        limit: int = 50
    ) -> List[WorkflowAuditLog]:
        """Get audit logs with filters"""
        query = {}
        
        if workflow_id:
            query["workflow_id"] = workflow_id
        if instance_id:
            query["instance_id"] = instance_id
        if actor:
            query["actor"] = actor
        
        return await WorkflowAuditLog.find(query).sort(-WorkflowAuditLog.timestamp).skip(skip).limit(limit).to_list()