import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


@pytest.fixture
def sample_workflow():
    """Create a sample workflow for testing"""
    workflow_data = {
        "workflow_id": "test_instance_workflow",
        "name": "Test Instance Workflow",
        "description": "A workflow for testing instances"
    }
    
    response = client.post("/api/v1/workflows/", json=workflow_data)
    assert response.status_code == 200
    return response.json()


def test_create_instance(sample_workflow):
    """Test creating a new workflow instance"""
    instance_data = {
        "workflow_id": sample_workflow["workflow_id"],
        "user_id": "test_user_123",
        "initial_context": {
            "name": "John Doe",
            "email": "john@example.com"
        }
    }
    
    response = client.post("/api/v1/instances/", json=instance_data)
    assert response.status_code == 200
    
    data = response.json()
    assert data["workflow_id"] == sample_workflow["workflow_id"]
    assert data["user_id"] == "test_user_123"
    assert data["status"] == "running"
    assert "instance_id" in data


def test_get_instance(sample_workflow):
    """Test getting an instance by ID"""
    # Create instance
    instance_data = {
        "workflow_id": sample_workflow["workflow_id"],
        "user_id": "test_user_456",
        "initial_context": {"test": "data"}
    }
    
    create_response = client.post("/api/v1/instances/", json=instance_data)
    assert create_response.status_code == 200
    
    instance_id = create_response.json()["instance_id"]
    
    # Get instance
    response = client.get(f"/api/v1/instances/{instance_id}")
    assert response.status_code == 200
    
    data = response.json()
    assert data["instance_id"] == instance_id
    assert data["user_id"] == "test_user_456"


def test_list_instances(sample_workflow):
    """Test listing instances"""
    # Create a couple of instances
    for i in range(2):
        instance_data = {
            "workflow_id": sample_workflow["workflow_id"],
            "user_id": f"test_user_{i}",
            "initial_context": {"index": i}
        }
        
        response = client.post("/api/v1/instances/", json=instance_data)
        assert response.status_code == 200
    
    # List instances
    response = client.get("/api/v1/instances/")
    assert response.status_code == 200
    
    data = response.json()
    assert "instances" in data
    assert "total" in data
    assert isinstance(data["instances"], list)
    assert data["total"] >= 2


def test_filter_instances_by_workflow(sample_workflow):
    """Test filtering instances by workflow ID"""
    # Create instance
    instance_data = {
        "workflow_id": sample_workflow["workflow_id"],
        "user_id": "filter_test_user",
        "initial_context": {}
    }
    
    create_response = client.post("/api/v1/instances/", json=instance_data)
    assert create_response.status_code == 200
    
    # Filter by workflow
    response = client.get(f"/api/v1/instances/?workflow_id={sample_workflow['workflow_id']}")
    assert response.status_code == 200
    
    data = response.json()
    for instance in data["instances"]:
        assert instance["workflow_id"] == sample_workflow["workflow_id"]


def test_update_instance(sample_workflow):
    """Test updating an instance"""
    # Create instance
    instance_data = {
        "workflow_id": sample_workflow["workflow_id"],
        "user_id": "update_test_user",
        "initial_context": {"original": "data"}
    }
    
    create_response = client.post("/api/v1/instances/", json=instance_data)
    assert create_response.status_code == 200
    
    instance_id = create_response.json()["instance_id"]
    
    # Update instance
    update_data = {
        "context_updates": {"new": "data", "updated": True}
    }
    
    response = client.put(f"/api/v1/instances/{instance_id}", json=update_data)
    assert response.status_code == 200
    
    data = response.json()
    assert data["context"]["new"] == "data"
    assert data["context"]["updated"] is True


def test_cancel_instance(sample_workflow):
    """Test cancelling an instance"""
    # Create instance
    instance_data = {
        "workflow_id": sample_workflow["workflow_id"],
        "user_id": "cancel_test_user",
        "initial_context": {}
    }
    
    create_response = client.post("/api/v1/instances/", json=instance_data)
    assert create_response.status_code == 200
    
    instance_id = create_response.json()["instance_id"]
    
    # Cancel instance
    response = client.post(f"/api/v1/instances/{instance_id}/cancel")
    assert response.status_code == 200
    
    # Verify cancellation
    get_response = client.get(f"/api/v1/instances/{instance_id}")
    assert get_response.status_code == 200
    assert get_response.json()["status"] == "cancelled"


def test_pause_and_resume_instance(sample_workflow):
    """Test pausing and resuming an instance"""
    # Create instance
    instance_data = {
        "workflow_id": sample_workflow["workflow_id"],
        "user_id": "pause_test_user",
        "initial_context": {}
    }
    
    create_response = client.post("/api/v1/instances/", json=instance_data)
    assert create_response.status_code == 200
    
    instance_id = create_response.json()["instance_id"]
    
    # Pause instance
    pause_response = client.post(f"/api/v1/instances/{instance_id}/pause")
    assert pause_response.status_code == 200
    
    # Verify pause
    get_response = client.get(f"/api/v1/instances/{instance_id}")
    assert get_response.status_code == 200
    assert get_response.json()["status"] == "paused"
    
    # Resume instance
    resume_response = client.post(f"/api/v1/instances/{instance_id}/resume")
    assert resume_response.status_code == 200
    
    # Verify resume
    get_response = client.get(f"/api/v1/instances/{instance_id}")
    assert get_response.status_code == 200
    assert get_response.json()["status"] == "running"


def test_approve_step(sample_workflow):
    """Test submitting approval for a step"""
    # Create instance
    instance_data = {
        "workflow_id": sample_workflow["workflow_id"],
        "user_id": "approval_test_user",
        "initial_context": {}
    }
    
    create_response = client.post("/api/v1/instances/", json=instance_data)
    assert create_response.status_code == 200
    
    instance_id = create_response.json()["instance_id"]
    
    # Submit approval
    approval_data = {
        "instance_id": instance_id,
        "step_id": "test_step",
        "decision": "approved",
        "comments": "Looks good",
        "approver_id": "manager_123"
    }
    
    response = client.post(f"/api/v1/instances/{instance_id}/approve", json=approval_data)
    assert response.status_code == 200
    
    data = response.json()
    assert data["instance_id"] == instance_id
    assert "approved" in data["message"]


def test_get_instance_history(sample_workflow):
    """Test getting instance execution history"""
    # Create instance
    instance_data = {
        "workflow_id": sample_workflow["workflow_id"],
        "user_id": "history_test_user",
        "initial_context": {}
    }
    
    create_response = client.post("/api/v1/instances/", json=instance_data)
    assert create_response.status_code == 200
    
    instance_id = create_response.json()["instance_id"]
    
    # Get history
    response = client.get(f"/api/v1/instances/{instance_id}/history")
    assert response.status_code == 200
    
    data = response.json()
    assert "instance_id" in data
    assert "workflow_id" in data
    assert "history" in data
    assert isinstance(data["history"], list)


def test_instance_not_found():
    """Test 404 error for non-existent instance"""
    response = client.get("/api/v1/instances/non_existent_instance")
    assert response.status_code == 404


def test_create_instance_with_non_existent_workflow():
    """Test creating instance with non-existent workflow"""
    instance_data = {
        "workflow_id": "non_existent_workflow",
        "user_id": "test_user",
        "initial_context": {}
    }
    
    response = client.post("/api/v1/instances/", json=instance_data)
    assert response.status_code == 404
    assert "not found" in response.json()["detail"]