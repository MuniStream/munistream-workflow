"""
Pydantic schemas for catalog API endpoints.

These schemas define the request and response models for catalog-related operations.
"""

from datetime import datetime
from typing import Dict, Any, List, Optional, Union
from pydantic import BaseModel, Field, validator

from ..models.catalog import SourceType, CatalogStatus, ColumnType


class ColumnSchemaRequest(BaseModel):
    """Schema for column definition in requests"""
    name: str = Field(..., min_length=1, max_length=100)
    type: ColumnType
    nullable: bool = True
    description: Optional[str] = None
    indexed: bool = False


class SourceConfigRequest(BaseModel):
    """Schema for source configuration in requests"""
    # SQL configuration
    connection_string: Optional[str] = None
    query: Optional[str] = None

    # CSV/Excel configuration
    url: Optional[str] = None
    file_path: Optional[str] = None
    # File upload configuration
    uploaded_file_id: Optional[str] = None
    uploaded_filename: Optional[str] = None
    file_size_bytes: Optional[int] = None
    # File format options
    delimiter: Optional[str] = ","
    has_header: bool = True
    sheet_name: Optional[str] = None

    # API configuration
    endpoint: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    auth_config: Optional[Dict[str, Any]] = None

    # General configuration
    refresh_rate_minutes: int = Field(default=60, ge=1, le=10080)  # 1 min to 1 week
    timeout_seconds: int = Field(default=30, ge=5, le=300)  # 5s to 5 min

    @validator('connection_string')
    def validate_connection_string(cls, v):
        if v and len(v.strip()) == 0:
            raise ValueError("Connection string cannot be empty")
        return v

    @validator('query')
    def validate_query(cls, v):
        if v and len(v.strip()) == 0:
            raise ValueError("Query cannot be empty")
        return v


class PermissionRuleRequest(BaseModel):
    """Schema for permission rule in requests"""
    group_id: str = Field(..., min_length=1, max_length=100)
    can_view: bool = True
    visible_columns: List[str] = Field(default_factory=list)
    row_filters: Dict[str, Any] = Field(default_factory=dict)
    max_rows: Optional[int] = Field(None, ge=0)


class CacheConfigRequest(BaseModel):
    """Schema for cache configuration in requests"""
    enabled: bool = True
    ttl_seconds: int = Field(default=3600, ge=60, le=86400)  # 1 min to 1 day
    max_size_mb: int = Field(default=100, ge=1, le=1000)


class CreateCatalogRequest(BaseModel):
    """Schema for creating a new catalog"""
    catalog_id: str = Field(..., min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    source_type: SourceType
    source_config: SourceConfigRequest
    schema: List[ColumnSchemaRequest] = Field(default_factory=list)
    permissions: List[PermissionRuleRequest] = Field(default_factory=list)
    cache_config: Optional[CacheConfigRequest] = None
    tags: List[str] = Field(default_factory=list)

    @validator('tags')
    def validate_tags(cls, v):
        # Limit number of tags and their length
        if len(v) > 10:
            raise ValueError("Maximum 10 tags allowed")
        for tag in v:
            if len(tag) > 50:
                raise ValueError("Tag length cannot exceed 50 characters")
        return v


class UpdateCatalogRequest(BaseModel):
    """Schema for updating a catalog"""
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    source_config: Optional[SourceConfigRequest] = None
    schema: Optional[List[ColumnSchemaRequest]] = None
    permissions: Optional[List[PermissionRuleRequest]] = None
    cache_config: Optional[CacheConfigRequest] = None
    status: Optional[CatalogStatus] = None
    tags: Optional[List[str]] = None

    @validator('tags')
    def validate_tags(cls, v):
        if v is not None:
            if len(v) > 10:
                raise ValueError("Maximum 10 tags allowed")
            for tag in v:
                if len(tag) > 50:
                    raise ValueError("Tag length cannot exceed 50 characters")
        return v


class SyncResultResponse(BaseModel):
    """Schema for sync result responses"""
    success: bool
    rows_synced: int = 0
    error_message: Optional[str] = None
    synced_at: datetime
    duration_seconds: float = 0


class CatalogResponse(BaseModel):
    """Schema for catalog responses"""
    id: str = Field(alias="_id")
    catalog_id: str
    name: str
    description: Optional[str] = None
    source_type: SourceType
    source_config: Dict[str, Any]  # Simplified for response
    schema: List[Dict[str, Any]]
    permissions: List[Dict[str, Any]]
    cache_config: Dict[str, Any]
    created_by: str
    created_at: datetime
    updated_at: datetime
    last_sync: Optional[datetime] = None
    last_sync_result: Optional[SyncResultResponse] = None
    status: CatalogStatus
    tags: List[str]

    class Config:
        allow_population_by_field_name = True


class CatalogListResponse(BaseModel):
    """Schema for catalog list responses"""
    catalogs: List[CatalogResponse]
    total_count: int
    page: int
    page_size: int


class CatalogDataResponse(BaseModel):
    """Schema for catalog data responses"""
    data: List[Dict[str, Any]]
    total_count: int
    page: int
    page_size: int
    catalog_id: str
    visible_columns: List[str]


class CatalogSearchRequest(BaseModel):
    """Schema for catalog data search requests"""
    search: Optional[str] = None
    filters: Dict[str, Any] = Field(default_factory=dict)
    sort_by: Optional[str] = None
    sort_desc: bool = False
    page: int = Field(default=0, ge=0)
    page_size: int = Field(default=20, ge=1, le=100)


class TestConnectionRequest(BaseModel):
    """Schema for testing connection to a data source"""
    source_type: SourceType
    source_config: SourceConfigRequest


class TestConnectionResponse(BaseModel):
    """Schema for connection test results"""
    success: bool
    message: str
    details: Optional[Dict[str, Any]] = None


class PreviewDataRequest(BaseModel):
    """Schema for previewing data from a source"""
    source_type: SourceType
    source_config: SourceConfigRequest
    limit: int = Field(default=10, ge=1, le=100)


class PreviewDataResponse(BaseModel):
    """Schema for data preview results"""
    success: bool
    data: List[Dict[str, Any]] = Field(default_factory=list)
    inferred_schema: List[Dict[str, Any]] = Field(default_factory=list)
    message: Optional[str] = None
    row_count: int = 0


class BulkPermissionUpdateRequest(BaseModel):
    """Schema for bulk updating permissions"""
    permissions: List[PermissionRuleRequest]


class CatalogStatsResponse(BaseModel):
    """Schema for catalog statistics"""
    total_catalogs: int
    active_catalogs: int
    inactive_catalogs: int
    error_catalogs: int
    total_rows: int
    last_sync_summary: Dict[str, Any]


class CatalogSchemaResponse(BaseModel):
    """Schema for catalog schema responses (user-filtered)"""
    catalog_id: str
    catalog_name: str
    schema: List[Dict[str, Any]]
    visible_columns: List[str]
    permissions_applied: bool


class ValidationErrorResponse(BaseModel):
    """Schema for validation error responses"""
    error: str
    error_type: str = "validation_error"
    details: Optional[Dict[str, Any]] = None


class SuccessResponse(BaseModel):
    """Schema for simple success responses"""
    success: bool = True
    message: str
    data: Optional[Dict[str, Any]] = None


class ErrorResponse(BaseModel):
    """Schema for error responses"""
    success: bool = False
    error: str
    error_type: str
    details: Optional[Dict[str, Any]] = None