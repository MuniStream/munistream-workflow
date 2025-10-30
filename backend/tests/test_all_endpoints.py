"""
Comprehensive Real API Tests - All Endpoints
Tests all major API endpoints to detect 307 redirects and validate functionality
"""

import pytest
from .api_client import RealApiClient, AuthTokens


@pytest.mark.parametrize("endpoint", [
    # Public endpoints
    "/public/workflows",
    "/public/workflows/documents",
    "/public/workflows/featured",
    "/public/workflows/categories",
    "/public/entities",
    "/public/entity-types",

    # Auth endpoints
    "/auth/login-url?redirect_uri=https://example.com",

    # Performance endpoints
    "/performance/health",
    "/performance/stats",
    "/performance/workflows",
    "/performance/steps",

    # Workflow endpoints
    "/workflows",

    # Category endpoints
    "/categories",

    # Theme endpoints
    "/themes/current",
    "/themes/list",

    # Plugin endpoints
    "/plugins",
])
class TestAllPublicEndpoints:
    """Test all public endpoints that don't require authentication"""

    async def test_endpoint_returns_200(self, api_client: RealApiClient, endpoint: str):
        """Test that endpoint returns 200 status"""
        response = await api_client.get(endpoint)
        assert response.status_code == 200


@pytest.mark.parametrize("endpoint", [
    # Auth-required endpoints
    "/auth/me",
    "/instances",
    "/documents",
    "/teams",
    "/admin/stats",
    "/admin/pending-approvals",
    "/entities/validate",
    "/entities/auto-complete",
    "/entities/types",
    "/entities/rules/rfc",
])
class TestAllAuthenticatedEndpoints:
    """Test all endpoints that require authentication"""

    async def test_endpoint_with_user_auth(self, api_client: RealApiClient, user_tokens: AuthTokens, endpoint: str):
        """Test endpoint with user authentication"""
        response = await api_client.get(endpoint, auth_tokens=user_tokens)
        # Should be 200 or 403 (if user doesn't have permission)
        assert response.status_code in [200, 403]

    async def test_endpoint_with_admin_auth(self, api_client: RealApiClient, admin_tokens: AuthTokens, endpoint: str):
        """Test endpoint with admin authentication"""
        response = await api_client.get(endpoint, auth_tokens=admin_tokens)
        assert response.status_code == 200


@pytest.mark.parametrize("endpoint", [
    "/workflows",
    "/instances",
    "/documents",
    "/admin/stats",
    "/performance/stats",
    "/entities/types",
    "/categories",
    "/teams",
    "/themes/current",
    "/plugins",
])
class TestEndpointTrailingSlash:
    """Test endpoints with trailing slash to catch 307 redirects"""

    async def test_endpoint_with_trailing_slash(self, api_client: RealApiClient, admin_tokens: AuthTokens, endpoint: str):
        """Test endpoint with trailing slash - will fail if 307 redirect occurs"""
        endpoint_with_slash = endpoint + "/"
        response = await api_client.get(endpoint_with_slash, auth_tokens=admin_tokens)
        # Should return 200, not 307
        assert response.status_code == 200


class TestCoreWorkflowFunctionality:
    """Test core workflow functionality end-to-end"""

    async def test_list_workflows(self, api_client: RealApiClient):
        """Test listing workflows"""
        response = await api_client.get("/public/workflows")
        assert response.status_code == 200
        assert "workflows" in response.data

    async def test_list_document_workflows(self, api_client: RealApiClient):
        """Test listing document processing workflows"""
        response = await api_client.get("/public/workflows/documents")
        assert response.status_code == 200
        assert "workflows" in response.data

    async def test_performance_stats(self, api_client: RealApiClient, admin_tokens: AuthTokens):
        """Test performance stats endpoint"""
        response = await api_client.get("/performance/stats", auth_tokens=admin_tokens)
        assert response.status_code == 200

    async def test_health_check(self, api_client: RealApiClient):
        """Test API health check"""
        response = await api_client.get("/performance/health")
        assert response.status_code == 200

    async def test_entity_types(self, api_client: RealApiClient):
        """Test entity types endpoint"""
        response = await api_client.get("/public/entity-types")
        assert response.status_code == 200


class TestRedirectDetection:
    """Specific tests to detect and report 307 redirects"""

    async def test_common_redirect_patterns(self, api_client: RealApiClient, admin_tokens: AuthTokens):
        """Test common URL patterns that cause redirects"""
        # These patterns commonly cause 307 redirects
        test_patterns = [
            "/workflows/",      # Trailing slash
            "/workflows//",     # Double slash
            "/instances/",      # Trailing slash
            "/documents/",      # Trailing slash
            "/admin/stats/",    # Trailing slash
        ]

        for pattern in test_patterns:
            response = await api_client.get(pattern, auth_tokens=admin_tokens)
            # Log any redirects detected
            if response.redirects:
                print(f"REDIRECT DETECTED: {pattern} -> {response.redirects}")
            # Should not get 307
            assert response.status_code != 307, f"307 redirect on {pattern}"

    async def test_all_endpoints_redirect_detection(self, api_client: RealApiClient, admin_tokens: AuthTokens):
        """Comprehensive redirect detection test"""
        # The redirect detection is built into the api_client
        # This test will run and the final report will show any detected redirects
        endpoints = [
            "/workflows", "/instances", "/documents", "/admin/stats",
            "/performance/stats", "/entities/types", "/categories",
            "/teams", "/themes/current", "/plugins"
        ]

        for endpoint in endpoints:
            response = await api_client.get(endpoint, auth_tokens=admin_tokens)
            # Just verify we get a reasonable response
            assert response.status_code in [200, 401, 403, 404]