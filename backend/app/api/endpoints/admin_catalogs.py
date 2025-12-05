"""
Admin API endpoints for catalog management.

These endpoints allow administrators to create, read, update, and delete catalogs,
as well as manage permissions and sync data.
"""

from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status, UploadFile, File
from beanie import PydanticObjectId
import uuid
import os
import aiofiles
from pathlib import Path

from ...auth.provider import require_admin, get_current_user
from ...models.catalog import Catalog, CatalogStatus, SourceType
from ...services.catalog_service import (
    CatalogService,
    CatalogNotFoundError,
    CatalogPermissionError
)
from ...services.catalog_permission_service import CatalogPermissionService
from ...services.catalog_connectors import CatalogSyncService
from ...schemas.catalog_schemas import (
    CreateCatalogRequest,
    UpdateCatalogRequest,
    CatalogResponse,
    CatalogListResponse,
    CatalogDataResponse,
    CatalogSearchRequest,
    TestConnectionRequest,
    TestConnectionResponse,
    PreviewDataRequest,
    PreviewDataResponse,
    BulkPermissionUpdateRequest,
    CatalogStatsResponse,
    SyncResultResponse,
    SuccessResponse,
    ErrorResponse
)
from ...core.logging_config import get_workflow_logger

logger = get_workflow_logger(__name__)

router = APIRouter(prefix="/catalogs", tags=["Admin - Catalogs"])


@router.post("/", response_model=CatalogResponse)
async def create_catalog(
    request: CreateCatalogRequest,
    current_user: dict = Depends(require_admin)
) -> CatalogResponse:
    """
    Create a new catalog.

    Requires admin role.
    """
    try:
        # DEBUG: Log incoming request data
        logger.info(f"Create catalog request data: {request.dict()}")
        logger.info(f"Request catalog_id: '{request.catalog_id}' (type: {type(request.catalog_id)})")
        logger.info(f"Request source_type: '{request.source_type}' (type: {type(request.source_type)})")
        # Convert request to dict format for service
        schema_dicts = [schema.dict() for schema in request.schema]
        permissions_dicts = [perm.dict() for perm in request.permissions]

        catalog = await CatalogService.create_catalog(
            catalog_id=request.catalog_id,
            name=request.name,
            description=request.description,
            source_type=request.source_type,
            source_config=request.source_config.dict(),
            schema=schema_dicts,
            created_by=current_user["sub"],
            permissions=permissions_dicts,
            tags=request.tags
        )

        logger.info(f"Admin {current_user['sub']} created catalog {request.catalog_id}")

        return CatalogResponse(
            _id=str(catalog.id),
            **catalog.dict()
        )

    except ValueError as e:
        logger.error(f"ValueError in create_catalog: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Exception in create_catalog: {type(e).__name__}: {str(e)}")
        logger.error(f"Exception details: {repr(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create catalog"
        )


@router.get("/", response_model=CatalogListResponse)
async def list_catalogs(
    status_filter: Optional[CatalogStatus] = Query(None, description="Filter by status"),
    source_type: Optional[SourceType] = Query(None, description="Filter by source type"),
    tags: Optional[List[str]] = Query(None, description="Filter by tags"),
    page: int = Query(0, ge=0, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    current_user: dict = Depends(require_admin)
) -> CatalogListResponse:
    """
    List all catalogs (admin view - no permission filtering).

    Requires admin role.
    """
    try:
        # Admin can see all catalogs regardless of permissions
        all_catalogs = await Catalog.find().skip(page * page_size).limit(page_size).to_list()
        total_count = await Catalog.count()

        # Apply filters
        filtered_catalogs = []
        for catalog in all_catalogs:
            # Status filter
            if status_filter and catalog.status != status_filter:
                continue
            # Source type filter
            if source_type and catalog.source_type != source_type:
                continue
            # Tags filter
            if tags and not any(tag in catalog.tags for tag in tags):
                continue

            filtered_catalogs.append(catalog)

        catalog_responses = []
        for catalog in filtered_catalogs:
            response = CatalogResponse(
                _id=str(catalog.id),
                **catalog.dict()
            )
            catalog_responses.append(response)

        return CatalogListResponse(
            catalogs=catalog_responses,
            total_count=len(catalog_responses),
            page=page,
            page_size=page_size
        )

    except Exception as e:
        logger.error(f"Error listing catalogs: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list catalogs"
        )


@router.get("/{catalog_id}", response_model=CatalogResponse)
async def get_catalog(
    catalog_id: str,
    current_user: dict = Depends(require_admin)
) -> CatalogResponse:
    """
    Get a specific catalog by ID.

    Requires admin role.
    """
    try:
        catalog = await CatalogService.get_catalog(catalog_id)

        return CatalogResponse(
            _id=str(catalog.id),
            **catalog.dict()
        )

    except CatalogNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Catalog '{catalog_id}' not found"
        )
    except Exception as e:
        logger.error(f"Error getting catalog: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get catalog"
        )


@router.put("/{catalog_id}", response_model=CatalogResponse)
async def update_catalog(
    catalog_id: str,
    request: UpdateCatalogRequest,
    current_user: dict = Depends(require_admin)
) -> CatalogResponse:
    """
    Update a catalog.

    Requires admin role.
    """
    try:
        # Convert request to dict, filtering out None values
        updates = {}
        if request.name is not None:
            updates["name"] = request.name
        if request.description is not None:
            updates["description"] = request.description
        if request.source_config is not None:
            updates["source_config"] = request.source_config.dict()
        if request.schema is not None:
            updates["schema"] = [schema.dict() for schema in request.schema]
        if request.permissions is not None:
            updates["permissions"] = [perm.dict() for perm in request.permissions]
        if request.cache_config is not None:
            updates["cache_config"] = request.cache_config.dict()
        if request.status is not None:
            updates["status"] = request.status
        if request.tags is not None:
            updates["tags"] = request.tags

        catalog = await CatalogService.update_catalog(
            catalog_id=catalog_id,
            updates=updates,
            updated_by=current_user["sub"]
        )

        logger.info(f"Admin {current_user['sub']} updated catalog {catalog_id}")

        return CatalogResponse(
            _id=str(catalog.id),
            **catalog.dict()
        )

    except CatalogNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Catalog '{catalog_id}' not found"
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Error updating catalog: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update catalog"
        )


@router.delete("/{catalog_id}", response_model=SuccessResponse)
async def delete_catalog(
    catalog_id: str,
    current_user: dict = Depends(require_admin)
) -> SuccessResponse:
    """
    Delete a catalog and all its data.

    Requires admin role.
    """
    try:
        await CatalogService.delete_catalog(
            catalog_id=catalog_id,
            deleted_by=current_user["sub"]
        )

        logger.info(f"Admin {current_user['sub']} deleted catalog {catalog_id}")

        return SuccessResponse(
            message=f"Catalog '{catalog_id}' deleted successfully"
        )

    except CatalogNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Catalog '{catalog_id}' not found"
        )
    except Exception as e:
        logger.error(f"Error deleting catalog: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete catalog"
        )


@router.post("/{catalog_id}/sync", response_model=SyncResultResponse)
async def sync_catalog_data(
    catalog_id: str,
    current_user: dict = Depends(require_admin)
) -> SyncResultResponse:
    """
    Sync data for a catalog from its source.

    Requires admin role.
    """
    try:
        sync_result = await CatalogSyncService.sync_catalog_data(catalog_id)

        logger.info(f"Admin {current_user['sub']} synced catalog {catalog_id}")

        return SyncResultResponse(
            **sync_result.dict()
        )

    except CatalogNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Catalog '{catalog_id}' not found"
        )
    except Exception as e:
        logger.error(f"Error syncing catalog: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to sync catalog"
        )


@router.post("/{catalog_id}/permissions", response_model=CatalogResponse)
async def update_catalog_permissions(
    catalog_id: str,
    request: BulkPermissionUpdateRequest,
    current_user: dict = Depends(require_admin)
) -> CatalogResponse:
    """
    Update all permissions for a catalog.

    Requires admin role.
    """
    try:
        permissions_dicts = [perm.dict() for perm in request.permissions]

        catalog = await CatalogPermissionService.bulk_update_permissions(
            catalog_id=catalog_id,
            permission_rules=permissions_dicts
        )

        logger.info(f"Admin {current_user['sub']} updated permissions for catalog {catalog_id}")

        return CatalogResponse(
            _id=str(catalog.id),
            **catalog.dict()
        )

    except CatalogNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Catalog '{catalog_id}' not found"
        )
    except Exception as e:
        logger.error(f"Error updating catalog permissions: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update permissions"
        )


@router.post("/test-connection", response_model=TestConnectionResponse)
async def test_connection(
    request: TestConnectionRequest,
    current_user: dict = Depends(require_admin)
) -> TestConnectionResponse:
    """
    Test connection to a data source without creating a catalog.

    Requires admin role.
    """
    try:
        success = await CatalogSyncService.test_connection(
            source_type=request.source_type.value,
            config=request.source_config.dict()
        )

        if success:
            return TestConnectionResponse(
                success=True,
                message="Connection successful"
            )
        else:
            return TestConnectionResponse(
                success=False,
                message="Connection failed"
            )

    except Exception as e:
        logger.error(f"Error testing connection: {str(e)}")
        return TestConnectionResponse(
            success=False,
            message=f"Connection test failed: {str(e)}"
        )


@router.post("/preview-data", response_model=PreviewDataResponse)
async def preview_data(
    request: PreviewDataRequest,
    current_user: dict = Depends(require_admin)
) -> PreviewDataResponse:
    """
    Preview data from a source without storing it.

    Requires admin role.
    """
    try:
        data = await CatalogSyncService.preview_data(
            source_type=request.source_type.value,
            config=request.source_config.dict(),
            limit=request.limit
        )

        # Try to infer schema from preview data
        inferred_schema = []
        if data:
            from ....services.catalog_connectors import ConnectorFactory
            connector = ConnectorFactory.create_connector(
                request.source_type.value,
                request.source_config
            )
            # Mock the fetch_data method to return our preview
            original_fetch = connector.fetch_data
            connector.fetch_data = lambda: data
            try:
                schema_objects = await connector.infer_schema()
                inferred_schema = [
                    {
                        "name": col.name,
                        "type": col.type.value,
                        "nullable": col.nullable,
                        "description": col.description
                    }
                    for col in schema_objects
                ]
            except:
                pass  # Schema inference is optional
            finally:
                connector.fetch_data = original_fetch

        return PreviewDataResponse(
            success=True,
            data=data,
            inferred_schema=inferred_schema,
            row_count=len(data),
            message=f"Successfully previewed {len(data)} rows"
        )

    except Exception as e:
        logger.error(f"Error previewing data: {str(e)}")
        return PreviewDataResponse(
            success=False,
            message=f"Data preview failed: {str(e)}"
        )


@router.get("/{catalog_id}/data", response_model=CatalogDataResponse)
async def get_catalog_data_admin(
    catalog_id: str,
    search: CatalogSearchRequest = Depends(),
    current_user: dict = Depends(require_admin)
) -> CatalogDataResponse:
    """
    Get catalog data with admin permissions (no filtering).

    Requires admin role.
    """
    try:
        catalog = await CatalogService.get_catalog(catalog_id)

        # Admin sees all data without permission filtering
        # Use a special admin group that bypasses restrictions
        admin_groups = ["admin", "system"]

        data, total_count = await CatalogService.get_catalog_data(
            catalog_id=catalog_id,
            user_groups=admin_groups,
            filters=search.filters,
            search=search.search,
            limit=search.page_size,
            offset=search.page * search.page_size,
            sort_by=search.sort_by,
            sort_desc=search.sort_desc
        )

        # Get all columns for admin view
        all_columns = [col.name for col in catalog.schema]

        return CatalogDataResponse(
            data=data,
            total_count=total_count,
            page=search.page,
            page_size=search.page_size,
            catalog_id=catalog_id,
            visible_columns=all_columns
        )

    except CatalogNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Catalog '{catalog_id}' not found"
        )
    except Exception as e:
        logger.error(f"Error getting catalog data: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get catalog data"
        )


@router.get("/stats/overview", response_model=CatalogStatsResponse)
async def get_catalog_stats(
    current_user: dict = Depends(require_admin)
) -> CatalogStatsResponse:
    """
    Get catalog system statistics.

    Requires admin role.
    """
    try:
        # Count catalogs by status
        all_catalogs = await Catalog.find().to_list()

        total_catalogs = len(all_catalogs)
        active_catalogs = sum(1 for c in all_catalogs if c.status == CatalogStatus.ACTIVE)
        inactive_catalogs = sum(1 for c in all_catalogs if c.status == CatalogStatus.INACTIVE)
        error_catalogs = sum(1 for c in all_catalogs if c.status == CatalogStatus.ERROR)

        # Calculate total rows (approximate)
        total_rows = 0
        recent_syncs = []
        for catalog in all_catalogs:
            if catalog.last_sync_result and catalog.last_sync_result.success:
                total_rows += catalog.last_sync_result.rows_synced
                recent_syncs.append({
                    "catalog_id": catalog.catalog_id,
                    "last_sync": catalog.last_sync.isoformat() if catalog.last_sync else None,
                    "rows": catalog.last_sync_result.rows_synced,
                    "status": catalog.status.value
                })

        # Sort recent syncs by date
        recent_syncs.sort(key=lambda x: x["last_sync"] or "", reverse=True)

        return CatalogStatsResponse(
            total_catalogs=total_catalogs,
            active_catalogs=active_catalogs,
            inactive_catalogs=inactive_catalogs,
            error_catalogs=error_catalogs,
            total_rows=total_rows,
            last_sync_summary={
                "recent_syncs": recent_syncs[:10],  # Last 10 syncs
                "successful_syncs": len([s for s in recent_syncs if s["status"] == "active"]),
                "failed_syncs": len([s for s in recent_syncs if s["status"] == "error"])
            }
        )

    except Exception as e:
        logger.error(f"Error getting catalog stats: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get catalog statistics"
        )


@router.post("/upload-file")
async def upload_catalog_file(
    file: UploadFile = File(...),
    current_user: dict = Depends(require_admin)
):
    """
    Upload a file for catalog data source.

    Supports CSV, Excel (XLS/XLSX) files.
    Returns file ID that can be used in catalog source configuration.
    """
    try:
        # Validate file type
        allowed_extensions = {'.csv', '.xls', '.xlsx'}
        file_extension = Path(file.filename).suffix.lower() if file.filename else ''

        if file_extension not in allowed_extensions:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported file type. Allowed: {', '.join(allowed_extensions)}"
            )

        # Validate file size (10MB limit)
        max_size_bytes = 10 * 1024 * 1024  # 10MB
        file_content = await file.read()
        if len(file_content) > max_size_bytes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File too large. Maximum size: {max_size_bytes / (1024*1024):.1f}MB"
            )

        # Generate unique file ID
        file_id = str(uuid.uuid4())

        # Create upload directory if it doesn't exist
        upload_dir = Path("/app/uploads/catalog-files")
        upload_dir.mkdir(parents=True, exist_ok=True)

        # Save file with unique name
        file_path = upload_dir / f"{file_id}{file_extension}"

        async with aiofiles.open(file_path, 'wb') as f:
            await f.write(file_content)

        logger.info(f"Admin {current_user['sub']} uploaded file {file.filename} (ID: {file_id})")

        return {
            "file_id": file_id,
            "filename": file.filename,
            "size_bytes": len(file_content),
            "file_type": file_extension,
            "message": f"File '{file.filename}' uploaded successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading file: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload file"
        )