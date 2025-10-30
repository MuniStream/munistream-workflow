"""
Real Public API Tests - No Mocking
Tests actual public endpoints with real authentication and data
"""

import pytest
from .api_client import RealApiClient, AuthTokens


@pytest.mark.public
class TestRealPublicAPI:
    """Test real public API endpoints"""

    async def test_public_workflows_endpoint(self, api_client: RealApiClient):
        """Test /public/workflows endpoint for process workflows"""
        response = await api_client.get("/public/workflows")
        assert response.status_code == 200
        assert "workflows" in response.data
        assert isinstance(response.data["workflows"], list)

    async def test_public_workflows_documents_endpoint(self, api_client: RealApiClient):
        """Test /public/workflows/documents endpoint for document processing workflows"""
        response = await api_client.get("/public/workflows/documents")
        assert response.status_code == 200
        assert "workflows" in response.data
        assert isinstance(response.data["workflows"], list)

    async def test_public_workflows_featured_endpoint(self, api_client: RealApiClient):
        """Test /public/workflows/featured endpoint"""
        response = await api_client.get("/public/workflows/featured")
        assert response.status_code == 200

    async def test_public_workflows_categories_endpoint(self, api_client: RealApiClient):
        """Test /public/workflows/categories endpoint"""
        response = await api_client.get("/public/workflows/categories")
        assert response.status_code == 200

    async def test_public_entities_endpoint(self, api_client: RealApiClient):
        """Test /public/entities endpoint"""
        response = await api_client.get("/public/entities")
        assert response.status_code == 200

    async def test_public_entity_types_endpoint(self, api_client: RealApiClient):
        """Test /public/entity-types endpoint"""
        response = await api_client.get("/public/entity-types")
        assert response.status_code == 200

    async def test_public_workflows_search_endpoint(self, api_client: RealApiClient):
        """Test /public/workflows/search endpoint with query parameters"""
        search_params = {"q": "test", "category": "general"}
        response = await api_client.get("/public/workflows/search", params=search_params)
        assert response.status_code == 200

    async def test_public_workflow_detail_endpoint(self, api_client: RealApiClient):
        """Test /public/workflows/{workflow_id} endpoint"""
        workflows_response = await api_client.get("/public/workflows")
        assert workflows_response.status_code == 200

        workflows = workflows_response.data.get("workflows", [])
        if not workflows:
            pytest.skip("No workflows available for testing workflow detail endpoint")

        workflow_id = workflows[0]["workflow_id"]
        response = await api_client.get(f"/public/workflows/{workflow_id}")
        assert response.status_code == 200
        assert "workflow_id" in response.data


@pytest.mark.public
class TestRealPublicAuthenticatedAPI:
    """Test public API endpoints that require authentication"""

    async def test_public_auth_me_endpoint(self, api_client: RealApiClient, user_tokens: AuthTokens):
        """Test /public/auth/me endpoint with real user token"""
        response = await api_client.get("/public/auth/me", auth_tokens=user_tokens)
        assert response.status_code == 200

    async def test_public_workflow_start_endpoint(self, api_client: RealApiClient, user_tokens: AuthTokens):
        """Test /public/workflows/{workflow_id}/start endpoint"""
        workflows_response = await api_client.get("/public/workflows")
        assert workflows_response.status_code == 200

        workflows = workflows_response.data.get("workflows", [])
        if not workflows:
            pytest.skip("No workflows available for testing workflow start endpoint")

        workflow_id = workflows[0]["workflow_id"]
        start_data = {"context": {"test_field": "test_value"}}

        response = await api_client.post(f"/public/workflows/{workflow_id}/start", data=start_data, auth_tokens=user_tokens)
        assert response.status_code in [201, 400]  # 201 created or 400 validation error

    async def test_public_my_instances_endpoint(self, api_client: RealApiClient, user_tokens: AuthTokens):
        """Test /public/workflows/my-instances endpoint"""
        response = await api_client.get("/public/workflows/my-instances", auth_tokens=user_tokens)
        assert response.status_code == 200


@pytest.mark.public
@pytest.mark.parametrize("endpoint", [
    "/public/workflows",
    "/public/workflows/documents",
    "/public/workflows/featured",
    "/public/workflows/categories",
    "/public/entities",
    "/public/entity-types"
])
class TestRealPublicEndpoints:
    """Parametrized tests for all public endpoints"""

    async def test_endpoint_accessibility(self, api_client: RealApiClient, endpoint: str):
        """Test that endpoint is accessible and returns 200"""
        response = await api_client.get(endpoint)
        assert response.status_code == 200

    async def test_endpoint_with_trailing_slash(self, api_client: RealApiClient, endpoint: str):
        """Test endpoint with trailing slash"""
        response = await api_client.get(endpoint + "/")
        assert response.status_code == 200