"""
Real Keycloak Authentication Helper - No Mocking
Handles real authentication against actual Keycloak server
"""

import os
import httpx
import logging
from typing import Dict, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class AuthTokens:
    """Real authentication tokens from Keycloak"""
    access_token: str
    refresh_token: str
    expires_at: datetime
    user_info: Dict


class RealKeycloakAuth:
    """Real Keycloak authentication client - connects to actual Keycloak server"""

    def __init__(self):
        self.keycloak_url = os.getenv("TEST_KEYCLOAK_URL")
        self.realm = os.getenv("TEST_KEYCLOAK_REALM")
        self.client_id = os.getenv("TEST_KEYCLOAK_CLIENT_ID")

        if not all([self.keycloak_url, self.realm, self.client_id]):
            raise ValueError("Missing required Keycloak configuration")

        self.token_endpoint = f"{self.keycloak_url}/realms/{self.realm}/protocol/openid-connect/token"
        self.userinfo_endpoint = f"{self.keycloak_url}/realms/{self.realm}/protocol/openid-connect/userinfo"
        self.logout_endpoint = f"{self.keycloak_url}/realms/{self.realm}/protocol/openid-connect/logout"

        self.logger = logging.getLogger(__name__)

        # Cache tokens to avoid repeated authentication
        self._user_tokens: Optional[AuthTokens] = None
        self._admin_tokens: Optional[AuthTokens] = None

    async def authenticate_user(self) -> AuthTokens:
        """Authenticate real user account against actual Keycloak"""
        username = os.getenv("TEST_USER_USERNAME")
        password = os.getenv("TEST_USER_PASSWORD")

        if not username or not password:
            raise ValueError("Missing user credentials in environment")

        return await self._authenticate(username, password, "user")

    async def authenticate_admin(self) -> AuthTokens:
        """Authenticate real admin account against actual Keycloak"""
        username = os.getenv("TEST_ADMIN_USERNAME")
        password = os.getenv("TEST_ADMIN_PASSWORD")

        if not username or not password:
            raise ValueError("Missing admin credentials in environment")

        return await self._authenticate(username, password, "admin")

    async def _authenticate(self, username: str, password: str, user_type: str) -> AuthTokens:
        """Perform real authentication against Keycloak"""
        self.logger.info(f"Authenticating {user_type} '{username}' against real Keycloak: {self.keycloak_url}")

        # Check if we have valid cached tokens
        cached_tokens = self._user_tokens if user_type == "user" else self._admin_tokens
        if cached_tokens and cached_tokens.expires_at > datetime.now() + timedelta(minutes=5):
            self.logger.info(f"Using cached {user_type} tokens")
            return cached_tokens

        data = {
            "grant_type": "password",
            "username": username,
            "password": password,
            "client_id": self.client_id,
            "scope": "openid profile email"
        }

        async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
            try:
                # Make real HTTP request to Keycloak
                response = await client.post(self.token_endpoint, data=data)

                if response.status_code != 200:
                    self.logger.error(f"Keycloak authentication failed: {response.status_code} - {response.text}")
                    raise Exception(f"Authentication failed: {response.status_code}")

                token_data = response.json()

                # Get user info with access token
                user_info = await self._get_user_info(token_data["access_token"])

                # Calculate expiration time
                expires_in = token_data.get("expires_in", 3600)
                expires_at = datetime.now() + timedelta(seconds=expires_in)

                tokens = AuthTokens(
                    access_token=token_data["access_token"],
                    refresh_token=token_data.get("refresh_token", ""),
                    expires_at=expires_at,
                    user_info=user_info
                )

                # Cache tokens
                if user_type == "user":
                    self._user_tokens = tokens
                else:
                    self._admin_tokens = tokens

                self.logger.info(f"Successfully authenticated {user_type} '{username}' - expires at {expires_at}")
                return tokens

            except httpx.RequestError as e:
                self.logger.error(f"Network error during authentication: {e}")
                raise Exception(f"Network error: {e}")

    async def _get_user_info(self, access_token: str) -> Dict:
        """Get real user info from Keycloak"""
        headers = {"Authorization": f"Bearer {access_token}"}

        async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
            response = await client.get(self.userinfo_endpoint, headers=headers)

            if response.status_code == 200:
                return response.json()
            else:
                self.logger.warning(f"Failed to get user info: {response.status_code}")
                return {}

    async def refresh_token(self, refresh_token: str) -> AuthTokens:
        """Refresh real access token using refresh token"""
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.client_id
        }

        async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
            response = await client.post(self.token_endpoint, data=data)

            if response.status_code != 200:
                raise Exception(f"Token refresh failed: {response.status_code}")

            token_data = response.json()
            user_info = await self._get_user_info(token_data["access_token"])

            expires_in = token_data.get("expires_in", 3600)
            expires_at = datetime.now() + timedelta(seconds=expires_in)

            return AuthTokens(
                access_token=token_data["access_token"],
                refresh_token=token_data.get("refresh_token", refresh_token),
                expires_at=expires_at,
                user_info=user_info
            )

    async def logout(self, refresh_token: str):
        """Logout from real Keycloak session"""
        data = {
            "client_id": self.client_id,
            "refresh_token": refresh_token
        }

        async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
            await client.post(self.logout_endpoint, data=data)

        # Clear cached tokens
        self._user_tokens = None
        self._admin_tokens = None

    def get_auth_headers(self, tokens: AuthTokens) -> Dict[str, str]:
        """Get headers for authenticated API requests"""
        return {
            "Authorization": f"Bearer {tokens.access_token}",
            "Content-Type": "application/json"
        }