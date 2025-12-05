"""
Public API endpoints for catalog access in workflows.

These endpoints allow workflow users to access catalog data with proper
permission filtering based on their Keycloak groups.
"""

from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status

from ...auth.provider import get_current_user
from ...models.catalog import CatalogStatus
from ...services.catalog_service import (
    CatalogService,
    CatalogNotFoundError,
    CatalogPermissionError
)
from ...schemas.catalog_schemas import (
    CatalogDataResponse,
    CatalogSearchRequest,
    CatalogSchemaResponse,
    CatalogListResponse,
    CatalogResponse,
    ErrorResponse
)
from ...core.logging_config import get_workflow_logger

logger = get_workflow_logger(__name__)

router = APIRouter(prefix="/catalogs", tags=["Catalogs"])


def _extract_user_groups(current_user: dict) -> List[str]:
    """Extract user groups from current user data"""
    groups = []

    # Get groups from different possible locations
    if "groups" in current_user:
        groups.extend(current_user["groups"])
    elif "roles" in current_user:
        groups.extend(current_user["roles"])

    # Add default public group
    if "public" not in groups:
        groups.append("public")

    return groups


@router.get("/", response_model=CatalogListResponse)
async def list_accessible_catalogs(
    status_filter: Optional[CatalogStatus] = Query(CatalogStatus.ACTIVE, description="Filter by status"),
    tags: Optional[List[str]] = Query(None, description="Filter by tags"),
    page: int = Query(0, ge=0, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    current_user: dict = Depends(get_current_user)
) -> CatalogListResponse:
    """
    List catalogs accessible to the current user.

    Returns only catalogs that the user has permission to view based on their
    Keycloak groups.
    """
    try:
        user_groups = _extract_user_groups(current_user)

        # Get catalogs accessible to user
        accessible_catalogs = await CatalogService.list_catalogs(
            user_groups=user_groups,
            status=status_filter,
            tags=tags,
            limit=page_size,
            skip=page * page_size
        )

        # Convert to response format (hide sensitive source config)
        catalog_responses = []
        for catalog in accessible_catalogs:
            # Sanitize source config for public API
            sanitized_config = {
                "source_type": catalog.source_type.value,
                "refresh_rate_minutes": catalog.source_config.get("refresh_rate_minutes", 60)
            }

            response = CatalogResponse(
                _id=str(catalog.id),
                catalog_id=catalog.catalog_id,
                name=catalog.name,
                description=catalog.description,
                source_type=catalog.source_type,
                source_config=sanitized_config,
                schema=[],  # Schema provided by separate endpoint
                permissions=[],  # Permissions not exposed in public API
                cache_config=catalog.cache_config.dict() if catalog.cache_config else {},
                created_by="",  # Don't expose creator in public API
                created_at=catalog.created_at,
                updated_at=catalog.updated_at,
                last_sync=catalog.last_sync,
                last_sync_result=catalog.last_sync_result,
                status=catalog.status,
                tags=catalog.tags
            )
            catalog_responses.append(response)

        return CatalogListResponse(
            catalogs=catalog_responses,
            total_count=len(catalog_responses),
            page=page,
            page_size=page_size
        )

    except Exception as e:
        logger.error(f"Error listing accessible catalogs: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list catalogs"
        )


@router.get("/{catalog_id}/schema", response_model=CatalogSchemaResponse)
async def get_catalog_schema(
    catalog_id: str,
    current_user: dict = Depends(get_current_user)
) -> CatalogSchemaResponse:
    """
    Get catalog schema visible to the current user.

    Returns only columns that the user has permission to view.
    """
    try:
        user_groups = _extract_user_groups(current_user)

        # Get catalog
        catalog = await CatalogService.get_catalog(catalog_id)

        # Check access
        if not catalog.is_accessible_by_user(user_groups):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied to this catalog"
            )

        # Get visible schema
        visible_schema = await CatalogService.get_catalog_schema(catalog_id, user_groups)
        visible_columns = [col["name"] for col in visible_schema]

        return CatalogSchemaResponse(
            catalog_id=catalog_id,
            catalog_name=catalog.name,
            schema=visible_schema,
            visible_columns=visible_columns,
            permissions_applied=len(catalog.permissions) > 0
        )

    except CatalogNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Catalog '{catalog_id}' not found"
        )
    except CatalogPermissionError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied to this catalog"
        )
    except Exception as e:
        logger.error(f"Error getting catalog schema: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get catalog schema"
        )


@router.get("/{catalog_id}/data", response_model=CatalogDataResponse)
async def get_catalog_data(
    catalog_id: str,
    search: Optional[str] = Query(None, description="Search across visible columns"),
    filters: Optional[str] = Query(None, description="JSON string of filters"),
    sort_by: Optional[str] = Query(None, description="Column to sort by"),
    sort_desc: bool = Query(False, description="Sort in descending order"),
    page: int = Query(0, ge=0, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    current_user: dict = Depends(get_current_user)
) -> CatalogDataResponse:
    """
    Get catalog data with user permission filtering applied.

    Returns only rows and columns that the user has permission to view.
    """
    try:
        user_groups = _extract_user_groups(current_user)

        # Parse filters if provided
        filter_dict = {}
        if filters:
            import json
            try:
                filter_dict = json.loads(filters)
            except json.JSONDecodeError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid filters JSON format"
                )

        # Get data with permission filtering
        data, total_count = await CatalogService.get_catalog_data(
            catalog_id=catalog_id,
            user_groups=user_groups,
            filters=filter_dict,
            search=search,
            limit=page_size,
            offset=page * page_size,
            sort_by=sort_by,
            sort_desc=sort_desc
        )

        # Get visible columns for response
        catalog = await CatalogService.get_catalog(catalog_id)
        visible_columns = catalog.get_visible_columns_for_user(user_groups)

        # Log access for audit
        logger.info(
            f"User {current_user.get('sub', 'unknown')} accessed catalog {catalog_id}: "
            f"{len(data)} rows, {len(visible_columns)} columns"
        )

        return CatalogDataResponse(
            data=data,
            total_count=total_count,
            page=page,
            page_size=page_size,
            catalog_id=catalog_id,
            visible_columns=visible_columns
        )

    except CatalogNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Catalog '{catalog_id}' not found"
        )
    except CatalogPermissionError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied to this catalog"
        )
    except Exception as e:
        logger.error(f"Error getting catalog data: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get catalog data"
        )


@router.post("/{catalog_id}/search", response_model=CatalogDataResponse)
async def search_catalog_data(
    catalog_id: str,
    search_request: CatalogSearchRequest,
    current_user: dict = Depends(get_current_user)
) -> CatalogDataResponse:
    """
    Search catalog data with advanced filtering options.

    Allows complex search and filtering with user permission filtering applied.
    """
    try:
        user_groups = _extract_user_groups(current_user)

        # Get data with permission filtering
        data, total_count = await CatalogService.get_catalog_data(
            catalog_id=catalog_id,
            user_groups=user_groups,
            filters=search_request.filters,
            search=search_request.search,
            limit=search_request.page_size,
            offset=search_request.page * search_request.page_size,
            sort_by=search_request.sort_by,
            sort_desc=search_request.sort_desc
        )

        # Get visible columns for response
        catalog = await CatalogService.get_catalog(catalog_id)
        visible_columns = catalog.get_visible_columns_for_user(user_groups)

        # Log search for audit
        logger.info(
            f"User {current_user.get('sub', 'unknown')} searched catalog {catalog_id}: "
            f"query='{search_request.search}', filters={search_request.filters}, "
            f"results={len(data)}"
        )

        return CatalogDataResponse(
            data=data,
            total_count=total_count,
            page=search_request.page,
            page_size=search_request.page_size,
            catalog_id=catalog_id,
            visible_columns=visible_columns
        )

    except CatalogNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Catalog '{catalog_id}' not found"
        )
    except CatalogPermissionError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied to this catalog"
        )
    except Exception as e:
        logger.error(f"Error searching catalog data: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to search catalog data"
        )


@router.get("/{catalog_id}/info", response_model=Dict[str, Any])
async def get_catalog_info(
    catalog_id: str,
    current_user: dict = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Get basic catalog information for workflow use.

    Returns metadata about the catalog without exposing sensitive configuration.
    """
    try:
        user_groups = _extract_user_groups(current_user)

        # Get catalog
        catalog = await CatalogService.get_catalog(catalog_id)

        # Check access
        if not catalog.is_accessible_by_user(user_groups):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied to this catalog"
            )

        # Get user's effective permissions
        effective_permissions = catalog.get_visible_columns_for_user(user_groups)
        max_rows = catalog.get_max_rows_for_user(user_groups)

        return {
            "catalog_id": catalog.catalog_id,
            "name": catalog.name,
            "description": catalog.description,
            "status": catalog.status.value,
            "last_sync": catalog.last_sync.isoformat() if catalog.last_sync else None,
            "visible_columns": effective_permissions,
            "max_rows": max_rows,
            "total_columns": len(catalog.schema),
            "permissions_applied": len(catalog.permissions) > 0,
            "tags": catalog.tags,
            "cache_enabled": catalog.cache_config.enabled if catalog.cache_config else False
        }

    except CatalogNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Catalog '{catalog_id}' not found"
        )
    except Exception as e:
        logger.error(f"Error getting catalog info: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get catalog info"
        )