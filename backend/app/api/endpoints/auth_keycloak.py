"""
Keycloak Authentication API endpoints.
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
import httpx
import os

from ...auth.provider import keycloak, get_current_user

router = APIRouter()


class TokenExchangeRequest(BaseModel):
    """Request for exchanging authorization code for tokens"""
    code: str
    redirect_uri: str
    code_verifier: Optional[str] = None


class TokenRefreshRequest(BaseModel):
    """Request for refreshing access token"""
    refresh_token: str


class TokenResponse(BaseModel):
    """Token response"""
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    refresh_token: Optional[str] = None
    id_token: Optional[str] = None


@router.get("/login-url")
async def get_login_url(redirect_uri: str, state: Optional[str] = None):
    """
    Get Keycloak login URL for frontend to redirect to
    """
    params = {
        "client_id": keycloak.client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": "openid profile email"
    }

    if state:
        params["state"] = state

    query_string = "&".join(f"{k}={v}" for k, v in params.items())
    login_url = f"{keycloak.realm_url}/protocol/openid-connect/auth?{query_string}"

    return {"login_url": login_url}


@router.post("/token", response_model=TokenResponse)
async def exchange_code_for_token(request: TokenExchangeRequest):
    """
    Exchange authorization code for access token
    Used after Keycloak redirects back to frontend
    """
    data = {
        "grant_type": "authorization_code",
        "code": request.code,
        "redirect_uri": request.redirect_uri,
        "client_id": keycloak.client_id
    }

    if keycloak.client_secret:
        data["client_secret"] = keycloak.client_secret

    if request.code_verifier:
        data["code_verifier"] = request.code_verifier

    async with httpx.AsyncClient(verify=False) as client:
        response = await client.post(
            keycloak.token_endpoint,
            data=data
        )

        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code,
                detail="Failed to exchange code for token"
            )

        return response.json()


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(request: TokenRefreshRequest):
    """
    Refresh access token using refresh token
    """
    data = {
        "grant_type": "refresh_token",
        "refresh_token": request.refresh_token,
        "client_id": keycloak.client_id
    }

    if keycloak.client_secret:
        data["client_secret"] = keycloak.client_secret

    async with httpx.AsyncClient(verify=False) as client:
        response = await client.post(
            keycloak.token_endpoint,
            data=data
        )

        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code,
                detail="Failed to refresh token"
            )

        return response.json()


@router.post("/logout")
async def logout(refresh_token: str, current_user: dict = Depends(get_current_user)):
    """
    Logout user and revoke tokens
    """
    data = {
        "client_id": keycloak.client_id,
        "refresh_token": refresh_token
    }

    if keycloak.client_secret:
        data["client_secret"] = keycloak.client_secret

    async with httpx.AsyncClient(verify=False) as client:
        response = await client.post(
            f"{keycloak.realm_url}/protocol/openid-connect/logout",
            data=data
        )

        return {"message": "Logged out successfully"}


@router.get("/me")
async def get_current_user_info(current_user: dict = Depends(get_current_user)):
    """
    Get current authenticated user information
    """
    return {
        "username": current_user.get("username"),
        "email": current_user.get("email"),
        "name": current_user.get("name"),
        "roles": current_user.get("roles"),
        "email_verified": current_user.get("email_verified")
    }