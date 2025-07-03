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
from ..workflows.workflow import Workflow
from ..workflows.base import BaseStep


class WorkflowService:
    """Service layer for workflow operations"""
    
    @staticmethod
    async def create_workflow_definition(
        workflow_id: str,
        name: str,
        description: str = None,
        version: str = "1.0.0",
        created_by: str = None,
        metadata: Dict[str, Any] = None
    ) -> WorkflowDefinition:
        """Create a new workflow definition"""
        workflow_def = WorkflowDefinition(
            workflow_id=workflow_id,
            name=name,
            description=description,
            version=version,
            created_by=created_by,
            metadata=metadata or {}
        )
        
        await workflow_def.insert()
        return workflow_def
    
    @staticmethod
    async def get_workflow_definition(workflow_id: str) -> Optional[WorkflowDefinition]:
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
    
    @staticmethod
    async def save_workflow_steps(workflow_id: str, workflow: Workflow) -> List[WorkflowStep]:
        """Save workflow steps to database"""
        # Delete existing steps
        await WorkflowStep.find(WorkflowStep.workflow_id == workflow_id).delete()
        
        # Create new steps
        steps = []
        for step_id, step in workflow.steps.items():
            step_type = "action"
            configuration = {}
            
            if hasattr(step, 'conditions'):
                step_type = "conditional"
                # Store condition info (simplified for now)
                configuration["conditions"] = list(step.conditions.keys()).__len__()
            elif hasattr(step, 'approvers'):
                step_type = "approval"
                configuration["approvers"] = step.approvers
                configuration["approval_type"] = step.approval_type
            elif hasattr(step, 'service_name'):
                step_type = "integration"
                configuration["service_name"] = step.service_name
                configuration["endpoint"] = step.endpoint
            elif hasattr(step, 'terminal_status'):
                step_type = "terminal"
                configuration["terminal_status"] = step.terminal_status
            
            workflow_step = WorkflowStep(
                step_id=step_id,
                workflow_id=workflow_id,
                name=step.name,
                step_type=step_type,
                description=step.description,
                required_inputs=step.required_inputs,
                optional_inputs=step.optional_inputs,
                next_steps=[s.step_id for s in step.next_steps],
                configuration=configuration
            )
            
            steps.append(workflow_step)
        
        # Bulk insert
        if steps:
            await WorkflowStep.insert_many(steps)
        
        return steps
    
    @staticmethod
    async def get_workflow_steps(workflow_id: str) -> List[WorkflowStep]:
        """Get all steps for a workflow"""
        return await WorkflowStep.find(WorkflowStep.workflow_id == workflow_id).to_list()


class InstanceService:
    """Service layer for workflow instance operations"""
    
    @staticmethod
    async def create_instance(
        workflow_id: str,
        user_id: str,
        initial_context: Dict[str, Any] = None,
        user_data: Dict[str, Any] = None,
        priority: int = 5
    ) -> WorkflowInstance:
        """Create a new workflow instance"""
        import uuid
        
        instance = WorkflowInstance(
            instance_id=str(uuid.uuid4()),
            workflow_id=workflow_id,
            user_id=user_id,
            context=initial_context or {},
            user_data=user_data or {},
            priority=priority
        )
        
        await instance.insert()
        
        # Update workflow statistics
        workflow_def = await WorkflowService.get_workflow_definition(workflow_id)
        if workflow_def:
            workflow_def.total_instances += 1
            await workflow_def.save()
        
        return instance
    
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