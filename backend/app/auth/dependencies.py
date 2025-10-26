"""
Authentication dependencies for API endpoints.
"""

from typing import Optional
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from ..core.config import settings

# Security scheme
security = HTTPBearer(auto_error=False)


async def get_current_tenant(request: Request) -> Optional[str]:
    """
    Extract tenant ID from request.

    Tries multiple sources:
    1. X-Tenant-Id header
    2. Subdomain from host
    3. Environment variable
    """
    # Try header first
    tenant_id = request.headers.get("X-Tenant-Id")
    if tenant_id:
        return tenant_id

    # Try subdomain
    host = request.headers.get("host", "")
    if "." in host:
        subdomain = host.split(".")[0]
        # Map common subdomains to tenant IDs
        if subdomain in ["conapesca", "catastro", "demo"]:
            return subdomain

    # Fall back to environment variable
    return getattr(settings, "TENANT_ID", None)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> Optional[dict]:
    """
    Get current authenticated user from JWT token.
    This is a simplified version - implement full Keycloak validation as needed.
    """
    if not credentials:
        return None

    # TODO: Implement Keycloak token validation
    # For now, return a mock user
    return {
        "id": "user123",
        "username": "testuser",
        "roles": ["user"]
    }


async def require_admin(
    user: dict = Depends(get_current_user)
) -> dict:
    """
    Require admin role for endpoint access.
    """
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    if "admin" not in user.get("roles", []):
        raise HTTPException(status_code=403, detail="Admin access required")

    return user