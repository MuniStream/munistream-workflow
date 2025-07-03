import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_create_workflow():
    """Test creating a new workflow"""
    workflow_data = {
        "workflow_id": "test_workflow",
        "name": "Test Workflow",
        "description": "A test workflow",
        "version": "1.0.0",
        "metadata": {"category": "test"}
    }
    
    response = client.post("/api/v1/workflows/", json=workflow_data)
    assert response.status_code == 200
    
    data = response.json()
    assert data["workflow_id"] == "test_workflow"
    assert data["name"] == "Test Workflow"
    assert data["status"] == "draft"


def test_get_workflow():
    """Test getting a workflow by ID"""
    # First create a workflow
    workflow_data = {
        "workflow_id": "test_get_workflow",
        "name": "Get Test Workflow",
        "description": "A test workflow for get endpoint"
    }
    
    create_response = client.post("/api/v1/workflows/", json=workflow_data)
    assert create_response.status_code == 200
    
    # Then get it
    response = client.get("/api/v1/workflows/test_get_workflow")
    assert response.status_code == 200
    
    data = response.json()
    assert data["workflow_id"] == "test_get_workflow"
    assert data["name"] == "Get Test Workflow"


def test_list_workflows():
    """Test listing workflows"""
    response = client.get("/api/v1/workflows/")
    assert response.status_code == 200
    
    data = response.json()
    assert "workflows" in data
    assert "total" in data
    assert isinstance(data["workflows"], list)


def test_update_workflow():
    """Test updating a workflow"""
    # First create a workflow
    workflow_data = {
        "workflow_id": "test_update_workflow",
        "name": "Original Name",
        "description": "Original description"
    }
    
    create_response = client.post("/api/v1/workflows/", json=workflow_data)
    assert create_response.status_code == 200
    
    # Then update it
    update_data = {
        "name": "Updated Name",
        "description": "Updated description"
    }
    
    response = client.put("/api/v1/workflows/test_update_workflow", json=update_data)
    assert response.status_code == 200
    
    data = response.json()
    assert data["name"] == "Updated Name"
    assert data["description"] == "Updated description"


def test_create_example_workflow():
    """Test creating the example citizen registration workflow"""
    response = client.post("/api/v1/workflows/examples/citizen-registration")
    assert response.status_code == 200
    
    data = response.json()
    assert data["workflow_id"] == "citizen_registration_v1"
    assert data["name"] == "Citizen Registration"
    assert len(data["steps"]) > 0


def test_get_workflow_diagram():
    """Test getting workflow diagram"""
    # First create the example workflow
    create_response = client.post("/api/v1/workflows/examples/citizen-registration")
    assert create_response.status_code == 200
    
    # Then get its diagram
    response = client.get("/api/v1/workflows/citizen_registration_v1/diagram")
    assert response.status_code == 200
    
    data = response.json()
    assert data["diagram_type"] == "mermaid"
    assert "graph TD" in data["content"]


def test_validate_workflow():
    """Test workflow validation"""
    # Create example workflow
    create_response = client.post("/api/v1/workflows/examples/citizen-registration")
    assert create_response.status_code == 200
    
    # Validate it
    response = client.post("/api/v1/workflows/citizen_registration_v1/validate")
    assert response.status_code == 200
    
    data = response.json()
    assert data["valid"] is True


def test_workflow_not_found():
    """Test 404 error for non-existent workflow"""
    response = client.get("/api/v1/workflows/non_existent_workflow")
    assert response.status_code == 404


def test_duplicate_workflow_id():
    """Test creating workflow with duplicate ID"""
    workflow_data = {
        "workflow_id": "duplicate_test",
        "name": "First Workflow"
    }
    
    # Create first workflow
    response1 = client.post("/api/v1/workflows/", json=workflow_data)
    assert response1.status_code == 200
    
    # Try to create duplicate
    response2 = client.post("/api/v1/workflows/", json=workflow_data)
    assert response2.status_code == 400
    assert "already exists" in response2.json()["detail"]