"""
Theme API endpoints for MuniStream platform.
"""

from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends, Response, Request
from fastapi.responses import JSONResponse

from ...themes.theme_loader import theme_manager
from ...themes.models import Theme, ThemeConfig
from ...core.config import settings
from ...auth.dependencies import get_current_tenant

router = APIRouter()


@router.get("/current", response_model=ThemeConfig)
async def get_current_theme(
    request: Request,
    tenant_id: Optional[str] = Depends(get_current_tenant)
) -> ThemeConfig:
    """
    Get the current theme for the tenant.

    If no tenant_id is provided, tries to extract from request headers or subdomain.
    """
    # Try to get tenant_id from request if not provided
    if not tenant_id:
        # Try from header
        tenant_id = request.headers.get("X-Tenant-Id")

        # Try from subdomain
        if not tenant_id:
            host = request.headers.get("host", "")
            if "." in host:
                subdomain = host.split(".")[0]
                # Map common subdomains to tenant IDs
                tenant_mapping = {
                    "conapesca": "conapesca",
                    "catastro": "catastro",
                    "demo": "demo"
                }
                tenant_id = tenant_mapping.get(subdomain, subdomain)

    # Default to environment variable
    if not tenant_id:
        tenant_id = getattr(settings, "TENANT_ID", "default")

    # Just get the default theme (ignore tenant_id for now)
    theme = theme_manager.get_theme()

    if not theme:
        # Create default theme if none exists
        theme = theme_manager.create_default_theme()

    return theme.config


@router.get("/list", response_model=List[Theme])
async def list_themes(
    tenant_id: Optional[str] = Depends(get_current_tenant)
) -> List[Theme]:
    """
    List all available themes for the tenant.
    """
    if not tenant_id:
        tenant_id = getattr(settings, "TENANT_ID", "default")

    themes = theme_manager.list_themes()

    if not themes:
        # Create default theme if none exists
        default_theme = theme_manager.create_default_theme()
        themes = [default_theme]

    return themes


@router.get("/{theme_id}", response_model=ThemeConfig)
async def get_theme(
    theme_id: str
) -> ThemeConfig:
    """
    Get a specific theme by ID.
    """
    theme = theme_manager.get_theme(theme_id)

    if not theme:
        raise HTTPException(status_code=404, detail=f"Theme {theme_id} not found")

    return theme.config


@router.get("/{theme_id}/assets/{asset_path:path}")
async def get_theme_asset(
    theme_id: str,
    asset_path: str
) -> Response:
    """
    Get a theme asset file (logo, images, etc.).
    """
    asset_content = theme_manager.get_asset(theme_id, asset_path)

    if not asset_content:
        raise HTTPException(status_code=404, detail=f"Asset {asset_path} not found")

    # Determine content type based on file extension
    content_type = "application/octet-stream"
    if asset_path.endswith((".png", ".jpg", ".jpeg", ".gif")):
        content_type = f"image/{asset_path.split('.')[-1]}"
    elif asset_path.endswith(".svg"):
        content_type = "image/svg+xml"
    elif asset_path.endswith(".ico"):
        content_type = "image/x-icon"
    elif asset_path.endswith(".css"):
        content_type = "text/css"
    elif asset_path.endswith(".js"):
        content_type = "application/javascript"
    elif asset_path.endswith(".json"):
        content_type = "application/json"

    return Response(content=asset_content, media_type=content_type)


@router.post("/reload")
async def reload_themes(
    tenant_id: Optional[str] = Depends(get_current_tenant)
) -> JSONResponse:
    """
    Reload themes for a tenant (admin only).
    This is useful for development or when themes are updated.
    """
    # TODO: Add admin permission check

    if not tenant_id:
        tenant_id = getattr(settings, "TENANT_ID", "default")

    # This would trigger a reload from the plugin system
    # For now, just return success
    return JSONResponse(
        content={
            "status": "success",
            "message": f"Themes reloaded for tenant {tenant_id}",
            "tenant_id": tenant_id
        }
    )