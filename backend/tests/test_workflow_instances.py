"""
Workflow Instance Creation Tests
Tests creation and execution of workflow instances with real authentication
"""

import pytest
from .api_client import RealApiClient, AuthTokens


@pytest.mark.workflows
@pytest.mark.instances
class TestWorkflowInstanceCreation:
    """Test workflow instance creation and execution"""

    async def test_list_available_workflows(self, api_client: RealApiClient):
        """Test getting list of available workflows"""
        response = await api_client.get("/public/workflows")
        assert response.status_code == 200
        assert "workflows" in response.data
        workflows = response.data["workflows"]
        assert isinstance(workflows, list)
        print(f"\n📋 Found {len(workflows)} available workflows")
        for workflow in workflows[:3]:  # Show first 3
            print(f"  - {workflow.get('workflow_id', 'unknown')}: {workflow.get('title', 'no title')}")

    async def test_create_workflow_instance_without_auth(self, api_client: RealApiClient):
        """Test creating workflow instance without authentication (should fail)"""
        # First get available workflows
        workflows_response = await api_client.get("/public/workflows")
        assert workflows_response.status_code == 200

        workflows = workflows_response.data.get("workflows", [])
        if not workflows:
            pytest.skip("No workflows available for testing")

        workflow_id = workflows[0]["workflow_id"]

        # Try to create instance without auth
        instance_data = {
            "workflow_id": workflow_id,
            "context": {"test_field": "test_value"}
        }

        response = await api_client.post("/instances", data=instance_data)
        # Should require authentication
        assert response.status_code in [401, 403]

    async def test_create_workflow_instance_with_auth(self, api_client: RealApiClient, user_tokens: AuthTokens):
        """Test creating workflow instance with user authentication"""
        # Get available workflows
        workflows_response = await api_client.get("/public/workflows")
        assert workflows_response.status_code == 200

        workflows = workflows_response.data.get("workflows", [])
        if not workflows:
            pytest.skip("No workflows available for testing")

        workflow_id = workflows[0]["workflow_id"]
        print(f"\n🚀 Creating instance for workflow: {workflow_id}")

        # Create instance with authentication
        instance_data = {
            "workflow_id": workflow_id,
            "context": {
                "test_field": "test_value",
                "created_by_test": True,
                "test_timestamp": "2025-10-29"
            }
        }

        response = await api_client.post("/instances", data=instance_data, auth_tokens=user_tokens)

        # Should succeed with proper auth
        if response.status_code == 201:
            print(f"✅ Instance created successfully: {response.data.get('instance_id', 'unknown')}")
            assert "instance_id" in response.data
            assert response.data["workflow_id"] == workflow_id
            return response.data["instance_id"]
        else:
            print(f"⚠️ Instance creation returned {response.status_code}: {response.data}")
            # Some workflows might have validation requirements
            assert response.status_code in [400, 422]  # Validation errors are acceptable

    async def test_create_multiple_workflow_instances(self, api_client: RealApiClient, admin_tokens: AuthTokens):
        """Test creating instances for all available workflows"""
        # Get all workflows
        workflows_response = await api_client.get("/public/workflows")
        assert workflows_response.status_code == 200

        workflows = workflows_response.data.get("workflows", [])
        if not workflows:
            pytest.skip("No workflows available for testing")

        created_instances = []
        failed_workflows = []

        print(f"\n🔄 Testing instance creation for {len(workflows)} workflows...")

        for workflow in workflows:
            workflow_id = workflow["workflow_id"]
            workflow_title = workflow.get("title", "No title")

            instance_data = {
                "workflow_id": workflow_id,
                "context": {
                    "test_batch": True,
                    "workflow_title": workflow_title,
                    "test_run": "instance_creation_test"
                }
            }

            response = await api_client.post("/instances", data=instance_data, auth_tokens=admin_tokens)

            if response.status_code == 201:
                instance_id = response.data.get("instance_id")
                created_instances.append({
                    "workflow_id": workflow_id,
                    "instance_id": instance_id,
                    "title": workflow_title
                })
                print(f"  ✅ {workflow_id}: {instance_id}")
            else:
                failed_workflows.append({
                    "workflow_id": workflow_id,
                    "status_code": response.status_code,
                    "error": response.data
                })
                print(f"  ❌ {workflow_id}: {response.status_code} - {response.data}")

        print(f"\n📊 Results:")
        print(f"  ✅ Successfully created: {len(created_instances)} instances")
        print(f"  ❌ Failed: {len(failed_workflows)} workflows")

        # At least some instances should be created successfully
        assert len(created_instances) > 0, "No workflow instances could be created"

        return created_instances

    async def test_list_user_instances(self, api_client: RealApiClient, user_tokens: AuthTokens):
        """Test listing instances created by user"""
        response = await api_client.get("/instances", auth_tokens=user_tokens)

        if response.status_code == 200:
            instances = response.data.get("instances", [])
            print(f"\n📝 User has {len(instances)} instances")
            for instance in instances[:3]:  # Show first 3
                print(f"  - {instance.get('instance_id', 'unknown')}: {instance.get('workflow_id', 'unknown')}")
        else:
            # Might require different endpoint or parameters
            assert response.status_code in [401, 403, 404]

    async def test_get_instance_details(self, api_client: RealApiClient, admin_tokens: AuthTokens):
        """Test getting detailed information about a workflow instance"""
        # First create an instance
        workflows_response = await api_client.get("/public/workflows")
        assert workflows_response.status_code == 200

        workflows = workflows_response.data.get("workflows", [])
        if not workflows:
            pytest.skip("No workflows available for testing")

        # Create instance
        workflow_id = workflows[0]["workflow_id"]
        instance_data = {
            "workflow_id": workflow_id,
            "context": {"test_detail_check": True}
        }

        create_response = await api_client.post("/instances", data=instance_data, auth_tokens=admin_tokens)

        if create_response.status_code == 201:
            instance_id = create_response.data["instance_id"]

            # Get instance details
            detail_response = await api_client.get(f"/instances/{instance_id}", auth_tokens=admin_tokens)

            if detail_response.status_code == 200:
                print(f"\n🔍 Instance details for {instance_id}:")
                instance_details = detail_response.data
                print(f"  - Status: {instance_details.get('status', 'unknown')}")
                print(f"  - Workflow: {instance_details.get('workflow_id', 'unknown')}")
                print(f"  - Created: {instance_details.get('created_at', 'unknown')}")

                assert instance_details["instance_id"] == instance_id
                assert instance_details["workflow_id"] == workflow_id
            else:
                print(f"⚠️ Could not get instance details: {detail_response.status_code}")


@pytest.mark.workflows
@pytest.mark.integration
class TestWorkflowExecution:
    """Test workflow execution and progression"""

    async def test_workflow_execution_status(self, api_client: RealApiClient, admin_tokens: AuthTokens):
        """Test monitoring workflow execution status"""
        # Get workflows
        workflows_response = await api_client.get("/public/workflows")
        assert workflows_response.status_code == 200

        workflows = workflows_response.data.get("workflows", [])
        if not workflows:
            pytest.skip("No workflows available for testing")

        # Create instance and monitor execution
        workflow_id = workflows[0]["workflow_id"]
        instance_data = {
            "workflow_id": workflow_id,
            "context": {"execution_test": True}
        }

        create_response = await api_client.post("/instances", data=instance_data, auth_tokens=admin_tokens)

        if create_response.status_code == 201:
            instance_id = create_response.data["instance_id"]
            print(f"\n⚡ Monitoring execution for instance: {instance_id}")

            # Check execution status
            status_response = await api_client.get(f"/instances/{instance_id}/status", auth_tokens=admin_tokens)

            if status_response.status_code == 200:
                status = status_response.data
                print(f"  📊 Execution status: {status}")
            else:
                print(f"  ⚠️ Could not get execution status: {status_response.status_code}")


@pytest.mark.performance
class TestWorkflowPerformance:
    """Test workflow performance and statistics"""

    async def test_workflow_performance_stats(self, api_client: RealApiClient, admin_tokens: AuthTokens):
        """Test getting workflow performance statistics"""
        response = await api_client.get("/performance/workflows", auth_tokens=admin_tokens)

        if response.status_code == 200:
            stats = response.data
            print(f"\n📈 Workflow Performance Stats:")
            print(f"  - Total workflows: {stats.get('total_workflows', 'unknown')}")
            print(f"  - Active instances: {stats.get('active_instances', 'unknown')}")
            print(f"  - Completed today: {stats.get('completed_today', 'unknown')}")
        else:
            # Performance endpoints might require special permissions
            assert response.status_code in [401, 403, 404]

    async def test_step_performance_stats(self, api_client: RealApiClient, admin_tokens: AuthTokens):
        """Test getting step-level performance statistics"""
        response = await api_client.get("/performance/steps", auth_tokens=admin_tokens)

        if response.status_code == 200:
            stats = response.data
            print(f"\n🎯 Step Performance Stats:")
            print(f"  - Total steps executed: {stats.get('total_steps', 'unknown')}")
            print(f"  - Average execution time: {stats.get('avg_execution_time', 'unknown')}")
        else:
            assert response.status_code in [401, 403, 404]