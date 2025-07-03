import pytest
import asyncio
from datetime import datetime
from beanie import init_beanie
from motor.motor_asyncio import AsyncIOMotorClient

from app.models.workflow import (
    WorkflowDefinition,
    WorkflowStep,
    WorkflowInstance,
    StepExecution,
    ApprovalRequest
)
from app.services.workflow_service import WorkflowService, InstanceService


# Test database configuration
TEST_DATABASE_NAME = "civicstream_test"
TEST_MONGODB_URL = "mongodb://localhost:27017"


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def test_db():
    """Set up test database"""
    client = AsyncIOMotorClient(TEST_MONGODB_URL)
    database = client[TEST_DATABASE_NAME]
    
    # Initialize Beanie
    await init_beanie(
        database=database,
        document_models=[
            WorkflowDefinition,
            WorkflowStep,
            WorkflowInstance,
            StepExecution,
            ApprovalRequest
        ]
    )
    
    yield database
    
    # Cleanup
    await client.drop_database(TEST_DATABASE_NAME)
    client.close()


@pytest.fixture
async def clean_db(test_db):
    """Clean database before each test"""
    # Delete all documents from all collections
    await WorkflowDefinition.delete_all()
    await WorkflowStep.delete_all()
    await WorkflowInstance.delete_all()
    await StepExecution.delete_all()
    await ApprovalRequest.delete_all()
    
    yield test_db


class TestWorkflowModels:
    """Test workflow database models"""
    
    async def test_create_workflow_definition(self, clean_db):
        """Test creating a workflow definition"""
        workflow_def = WorkflowDefinition(
            workflow_id="test_workflow",
            name="Test Workflow",
            description="A test workflow",
            version="1.0.0",
            created_by="test_user"
        )
        
        await workflow_def.insert()
        
        # Verify creation
        retrieved = await WorkflowDefinition.find_one(
            WorkflowDefinition.workflow_id == "test_workflow"
        )
        
        assert retrieved is not None
        assert retrieved.name == "Test Workflow"
        assert retrieved.version == "1.0.0"
        assert retrieved.status == "draft"  # default value
    
    async def test_create_workflow_step(self, clean_db):
        """Test creating a workflow step"""
        step = WorkflowStep(
            step_id="step_1",
            workflow_id="test_workflow",
            name="Test Step",
            step_type="action",
            required_inputs=["input1", "input2"],
            next_steps=["step_2"]
        )
        
        await step.insert()
        
        # Verify creation
        retrieved = await WorkflowStep.find_one(
            WorkflowStep.step_id == "step_1"
        )
        
        assert retrieved is not None
        assert retrieved.name == "Test Step"
        assert retrieved.step_type == "action"
        assert "input1" in retrieved.required_inputs
    
    async def test_create_workflow_instance(self, clean_db):
        """Test creating a workflow instance"""
        instance = WorkflowInstance(
            instance_id="inst_123",
            workflow_id="test_workflow",
            user_id="user_456",
            context={"key": "value"},
            priority=3
        )
        
        await instance.insert()
        
        # Verify creation
        retrieved = await WorkflowInstance.find_one(
            WorkflowInstance.instance_id == "inst_123"
        )
        
        assert retrieved is not None
        assert retrieved.workflow_id == "test_workflow"
        assert retrieved.user_id == "user_456"
        assert retrieved.context["key"] == "value"
        assert retrieved.priority == 3
        assert retrieved.status == "running"  # default value


class TestWorkflowService:
    """Test workflow service operations"""
    
    async def test_create_workflow_definition_service(self, clean_db):
        """Test creating workflow definition via service"""
        workflow_def = await WorkflowService.create_workflow_definition(
            workflow_id="service_test_workflow",
            name="Service Test Workflow",
            description="Created via service",
            created_by="test_user",
            metadata={"test": True}
        )
        
        assert workflow_def.workflow_id == "service_test_workflow"
        assert workflow_def.name == "Service Test Workflow"
        assert workflow_def.metadata["test"] is True
        
        # Verify it was saved
        retrieved = await WorkflowService.get_workflow_definition("service_test_workflow")
        assert retrieved is not None
        assert retrieved.name == "Service Test Workflow"
    
    async def test_list_workflow_definitions(self, clean_db):
        """Test listing workflow definitions"""
        # Create multiple workflows
        for i in range(3):
            await WorkflowService.create_workflow_definition(
                workflow_id=f"workflow_{i}",
                name=f"Workflow {i}",
                created_by="test_user"
            )
        
        # List all workflows
        workflows = await WorkflowService.list_workflow_definitions()
        assert len(workflows) == 3
        
        # Test pagination
        workflows_page = await WorkflowService.list_workflow_definitions(skip=1, limit=1)
        assert len(workflows_page) == 1
    
    async def test_update_workflow_definition(self, clean_db):
        """Test updating workflow definition"""
        # Create workflow
        workflow_def = await WorkflowService.create_workflow_definition(
            workflow_id="update_test",
            name="Original Name",
            created_by="test_user"
        )
        
        # Update workflow
        updated = await WorkflowService.update_workflow_definition(
            workflow_id="update_test",
            updates={"name": "Updated Name", "status": "active"},
            updated_by="updater_user"
        )
        
        assert updated is not None
        assert updated.name == "Updated Name"
        assert updated.status == "active"
        assert updated.updated_by == "updater_user"


class TestInstanceService:
    """Test instance service operations"""
    
    async def test_create_instance_service(self, clean_db):
        """Test creating instance via service"""
        # First create a workflow
        await WorkflowService.create_workflow_definition(
            workflow_id="test_workflow",
            name="Test Workflow",
            created_by="test_user"
        )
        
        # Create instance
        instance = await InstanceService.create_instance(
            workflow_id="test_workflow",
            user_id="test_user",
            initial_context={"initial": "data"},
            priority=8
        )
        
        assert instance.workflow_id == "test_workflow"
        assert instance.user_id == "test_user"
        assert instance.context["initial"] == "data"
        assert instance.priority == 8
        
        # Verify workflow statistics were updated
        workflow_def = await WorkflowService.get_workflow_definition("test_workflow")
        assert workflow_def.total_instances == 1
    
    async def test_list_instances_with_filters(self, clean_db):
        """Test listing instances with filters"""
        # Create workflow
        await WorkflowService.create_workflow_definition(
            workflow_id="filter_test_workflow",
            name="Filter Test",
            created_by="test_user"
        )
        
        # Create multiple instances
        for i in range(3):
            await InstanceService.create_instance(
                workflow_id="filter_test_workflow",
                user_id=f"user_{i}",
                initial_context={"index": i}
            )
        
        # Test filtering by workflow_id
        instances = await InstanceService.list_instances(workflow_id="filter_test_workflow")
        assert len(instances) == 3
        
        # Test filtering by user_id
        user_instances = await InstanceService.list_instances(user_id="user_1")
        assert len(user_instances) == 1
        assert user_instances[0].user_id == "user_1"
    
    async def test_complete_instance(self, clean_db):
        """Test completing an instance"""
        # Create workflow and instance
        await WorkflowService.create_workflow_definition(
            workflow_id="complete_test_workflow",
            name="Complete Test",
            created_by="test_user"
        )
        
        instance = await InstanceService.create_instance(
            workflow_id="complete_test_workflow",
            user_id="test_user"
        )
        
        # Complete instance
        completed = await InstanceService.complete_instance(
            instance_id=instance.instance_id,
            terminal_status="SUCCESS",
            terminal_message="All done!"
        )
        
        assert completed is not None
        assert completed.status == "completed"
        assert completed.terminal_status == "SUCCESS"
        assert completed.terminal_message == "All done!"
        assert completed.completed_at is not None
        
        # Verify workflow statistics were updated
        workflow_def = await WorkflowService.get_workflow_definition("complete_test_workflow")
        assert workflow_def.successful_instances == 1


if __name__ == "__main__":
    pytest.main([__file__])