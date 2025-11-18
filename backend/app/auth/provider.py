"""
Keycloak authentication provider for MuniStream
"""
import os
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
import httpx
from jose import jwt, JWTError
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import logging
from ..core.config import settings

logger = logging.getLogger(__name__)

security_scheme = HTTPBearer()


class KeycloakProvider:
    """Keycloak OAuth 2.0/OIDC authentication provider"""

    def __init__(self):
        """Initialize Keycloak provider from environment variables"""
        self.server_url = os.getenv("KEYCLOAK_URL", "http://localhost:8180").rstrip('/')
        self.realm = os.getenv("KEYCLOAK_REALM", "munistream")
        self.client_id = os.getenv("KEYCLOAK_CLIENT_ID", "munistream-backend")
        self.client_secret = os.getenv("KEYCLOAK_CLIENT_SECRET")

        # Build endpoints
        self.realm_url = f"{self.server_url}/realms/{self.realm}"
        self.token_endpoint = f"{self.realm_url}/protocol/openid-connect/token"
        self.userinfo_endpoint = f"{self.realm_url}/protocol/openid-connect/userinfo"
        self.introspect_endpoint = f"{self.realm_url}/protocol/openid-connect/token/introspect"
        self.jwks_uri = f"{self.realm_url}/protocol/openid-connect/certs"

        # Cache for JWKS
        self._jwks_cache = None
        self._jwks_cache_time = None
        self._jwks_cache_duration = timedelta(hours=1)

        logger.info(f"Keycloak provider initialized for realm: {self.realm}")

    async def get_jwks(self) -> Dict[str, Any]:
        """Get JSON Web Key Set from Keycloak"""
        now = datetime.utcnow()
        if (self._jwks_cache is None or
            self._jwks_cache_time is None or
            now - self._jwks_cache_time > self._jwks_cache_duration):

            try:
                async with httpx.AsyncClient(verify=False, timeout=10.0) as client:
                    logger.info(f"Fetching JWKS from: {self.jwks_uri}")
                    response = await client.get(self.jwks_uri)
                    response.raise_for_status()
                    self._jwks_cache = response.json()
                    self._jwks_cache_time = now
                    logger.info("JWKS fetched successfully")
            except Exception as e:
                logger.error(f"Failed to fetch JWKS from {self.jwks_uri}: {e}")
                raise

        return self._jwks_cache

    async def verify_token(self, token: str) -> Dict[str, Any]:
        """Verify and decode a JWT token"""
        try:
            # Debug: Log token info
            logger.info(f"Token length: {len(token)}")
            logger.info(f"Token first 50 chars: {token[:50]}...")
            logger.info(f"Token last 10 chars: ...{token[-10:]}")

            # Get JWKS for verification
            jwks = await self.get_jwks()

            # Decode and verify token
            unverified_header = jwt.get_unverified_header(token)

            rsa_key = {}
            for key in jwks["keys"]:
                if key["kid"] == unverified_header["kid"]:
                    rsa_key = {
                        "kty": key["kty"],
                        "kid": key["kid"],
                        "use": key["use"],
                        "n": key["n"],
                        "e": key["e"]
                    }
                    break

            if not rsa_key:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Unable to find appropriate key"
                )

            # Decode without audience verification first to check the actual audience
            unverified_payload = jwt.decode(
                token,
                key="",  # Empty key for unverified decode
                options={"verify_signature": False, "verify_aud": False}
            )

            # Service account tokens have "account" as audience
            # Regular user tokens have the client_id
            audience = unverified_payload.get("aud", [])
            if isinstance(audience, str):
                audience = [audience]

            # Verify with proper audience
            # Accept tokens from munistream-backend, munistream-admin, and munistream-citizen clients
            valid_audiences = ["account", self.client_id, "munistream-admin", "munistream-citizen"]
            if any(aud in audience for aud in valid_audiences):
                # Try to decode with configured valid issuers
                valid_issuers = settings.KEYCLOAK_VALID_ISSUERS or [self.realm_url]

                payload = None
                last_error = None

                for issuer in valid_issuers:
                    try:
                        payload = jwt.decode(
                            token,
                            rsa_key,
                            algorithms=["RS256"],
                            audience=audience[0] if audience else self.client_id,
                            issuer=issuer
                        )
                        logger.debug(f"Token validated with issuer: {issuer}")
                        break
                    except JWTError as e:
                        last_error = e
                        continue

                if not payload:
                    token_issuer = unverified_payload.get("iss", "unknown")
                    raise JWTError(f"Token issuer '{token_issuer}' not in valid issuers: {valid_issuers}. Error: {last_error}")
            else:
                raise JWTError(f"Invalid audience: {audience}. Expected one of: {valid_audiences}")

            return payload

        except JWTError as e:
            logger.error(f"JWT verification failed: {e}")
            logger.error(f"Full exception details: {str(e)}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    async def introspect_token(self, token: str) -> Dict[str, Any]:
        """Check if token is active via introspection"""
        async with httpx.AsyncClient(verify=False) as client:
            response = await client.post(
                self.introspect_endpoint,
                data={
                    "token": token,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret
                }
            )
            response.raise_for_status()
            return response.json()

    def extract_roles(self, token_claims: Dict[str, Any]) -> List[str]:
        """Extract roles from token claims"""
        roles = []

        # Extract realm roles
        if "realm_access" in token_claims:
            roles.extend(token_claims["realm_access"].get("roles", []))

        # Extract client roles
        if "resource_access" in token_claims:
            if self.client_id in token_claims["resource_access"]:
                client_roles = token_claims["resource_access"][self.client_id].get("roles", [])
                roles.extend(client_roles)

        return list(set(roles))

    async def get_user_info(self, access_token: str) -> Dict[str, Any]:
        """Get user information from access token"""
        token_claims = await self.verify_token(access_token)
        extracted_roles = self.extract_roles(token_claims)

        user_info = {
            "sub": token_claims.get("sub"),
            "email": token_claims.get("email"),
            "username": token_claims.get("preferred_username"),
            "name": token_claims.get("name"),
            "roles": extracted_roles,
            "email_verified": token_claims.get("email_verified", False),
            "token_claims": token_claims
        }

        return user_info


# Global provider instance
keycloak = KeycloakProvider()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security_scheme)
) -> dict:
    """FastAPI dependency to get current authenticated user"""
    logger.info(f"get_current_user called with credentials: {credentials}")

    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing",
            headers={"WWW-Authenticate": "Bearer"},
        )

    logger.info(f"Token from credentials: length={len(credentials.credentials)}")
    logger.info(f"Token scheme: {credentials.scheme}")
    logger.info(f"Token first 50: {credentials.credentials[:50]}...")

    try:
        user_info = await keycloak.get_user_info(credentials.credentials)
        return user_info
    except Exception as e:
        logger.error(f"Authentication failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


def require_roles(required_roles: List[str]):
    """Dependency to require specific roles"""
    async def role_checker(
        current_user: dict = Depends(get_current_user)
    ) -> dict:
        user_roles = current_user.get("roles", [])

        if not any(role in user_roles for role in required_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required roles: {', '.join(required_roles)}"
            )

        return current_user

    return role_checker


# Specific admin dependency
async def get_current_admin(
    current_user: dict = Depends(get_current_user)
) -> dict:
    """FastAPI dependency to get current authenticated admin user"""
    user_roles = current_user.get("roles", [])
    admin_roles = ["admin", "manager", "approver", "reviewer"]

    if not any(role in user_roles for role in admin_roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required. Insufficient permissions."
        )

    return current_user


# Common permission helpers
require_admin = require_roles(["admin"])
require_manager = require_roles(["manager"])
require_manager_or_admin = require_roles(["manager", "admin"])
require_approver = require_roles(["approver", "admin"])
require_reviewer = require_roles(["reviewer", "manager", "admin"])


def require_permission(permission: str):
    """Map legacy permissions to roles"""
    permission_map = {
        "MANAGE_WORKFLOWS": ["manager", "admin"],
        "APPROVE_STEPS": ["approver", "manager", "admin"],
        "REVIEW_DOCUMENTS": ["reviewer", "manager", "admin"],
        "VIEW_DOCUMENTS": ["reviewer", "manager", "admin", "viewer", "approver"],
        "VIEW_INSTANCES": ["reviewer", "manager", "admin", "viewer", "approver"],
        "VIEW_ONLY": ["viewer", "reviewer", "approver", "manager", "admin"]
    }

    roles = permission_map.get(permission, ["admin"])
    return require_roles(roles)