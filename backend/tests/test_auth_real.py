"""
Real Authentication API Tests - No Mocking
Tests actual Keycloak authentication endpoints with real credentials
"""

import pytest
from .api_client import RealApiClient, AuthTokens
from .auth_helper import RealKeycloakAuth


@pytest.mark.auth
class TestRealAuthenticationAPI:
    """Test real authentication endpoints"""

    async def test_keycloak_authentication_user(self, auth_client: RealKeycloakAuth):
        """Test real user authentication against Keycloak"""
        tokens = await auth_client.authenticate_user()

        assert tokens.access_token
        assert tokens.user_info
        assert "username" in tokens.user_info or "preferred_username" in tokens.user_info

    async def test_keycloak_authentication_admin(self, auth_client: RealKeycloakAuth):
        """Test real admin authentication against Keycloak"""
        tokens = await auth_client.authenticate_admin()

        assert tokens.access_token
        assert tokens.user_info
        assert "username" in tokens.user_info or "preferred_username" in tokens.user_info

    async def test_auth_login_url_endpoint(self, api_client: RealApiClient):
        """Test /auth/login-url endpoint"""
        response = await api_client.get(
            "/auth/login-url",
            params={"redirect_uri": "https://example.com/callback"}
        )
        assert response.status_code == 200
        assert "login_url" in response.data

    async def test_auth_me_endpoint_with_user_token(self, api_client: RealApiClient, user_tokens: AuthTokens):
        """Test /auth/me endpoint with real user token"""
        response = await api_client.get("/auth/me", auth_tokens=user_tokens)
        assert response.status_code == 200
        assert "username" in response.data or "preferred_username" in response.data

    async def test_auth_me_endpoint_with_admin_token(self, api_client: RealApiClient, admin_tokens: AuthTokens):
        """Test /auth/me endpoint with real admin token"""
        response = await api_client.get("/auth/me", auth_tokens=admin_tokens)
        assert response.status_code == 200
        assert "username" in response.data or "preferred_username" in response.data

    async def test_auth_me_endpoint_without_token(self, api_client: RealApiClient):
        """Test /auth/me endpoint without authentication token"""
        response = await api_client.get("/auth/me")

        # Should return 401 or 403
        assert response.status_code in [401, 403]

    async def test_auth_token_refresh(self, auth_client: RealKeycloakAuth, user_tokens: AuthTokens):
        """Test real token refresh functionality"""
        if not user_tokens.refresh_token:
            pytest.skip("No refresh token available")

        new_tokens = await auth_client.refresh_token(user_tokens.refresh_token)
        assert new_tokens.access_token
        assert new_tokens.access_token != user_tokens.access_token  # Should be different
        assert new_tokens.user_info

    async def test_auth_logout(self, auth_client: RealKeycloakAuth, user_tokens: AuthTokens):
        """Test real logout functionality"""
        if not user_tokens.refresh_token:
            pytest.skip("No refresh token available for logout test")

        # Should not raise exception
        await auth_client.logout(user_tokens.refresh_token)


@pytest.mark.auth
@pytest.mark.integration
class TestRealAuthenticationIntegration:
    """Integration tests for real authentication flow"""

    async def test_full_authentication_flow(self, auth_client: RealKeycloakAuth, api_client: RealApiClient):
        """Test complete authentication flow from login to API access"""
        # 1. Authenticate user
        tokens = await auth_client.authenticate_user()
        assert tokens.access_token

        # 2. Use token to access protected endpoint
        response = await api_client.get("/auth/me", auth_tokens=tokens)
        assert response.status_code == 200

        # 3. Refresh token
        if tokens.refresh_token:
            new_tokens = await auth_client.refresh_token(tokens.refresh_token)
            assert new_tokens.access_token

            # 4. Use refreshed token
            response2 = await api_client.get("/auth/me", auth_tokens=new_tokens)
            assert response2.status_code == 200

        # 5. Logout
        if tokens.refresh_token:
            await auth_client.logout(tokens.refresh_token)

    async def test_permission_validation(self, api_client: RealApiClient, user_tokens: AuthTokens, admin_tokens: AuthTokens):
        """Test that different user types have appropriate permissions"""
        # Test admin-only endpoint with user token (should fail)
        admin_response = await api_client.get("/admin/stats", auth_tokens=user_tokens)
        assert admin_response.status_code in [401, 403], "User should not access admin endpoints"

        # Test admin-only endpoint with admin token (should succeed)
        admin_response2 = await api_client.get("/admin/stats", auth_tokens=admin_tokens)
        assert admin_response2.status_code == 200, "Admin should access admin endpoints"