"""
Catalog models for MuniStream workflow system.

Catalogs allow administrators to define data sources (SQL, CSV, JSON, etc.)
that can be used in workflows with granular permission control.
"""

from datetime import datetime
from enum import Enum
from typing import Dict, Any, List, Optional
from beanie import Document
from pydantic import BaseModel, Field


class SourceType(str, Enum):
    """Supported catalog data source types"""
    SQL = "sql"
    CSV_UPLOAD = "csv_upload"
    CSV_URL = "csv_url"
    JSON = "json"
    TOPOJSON = "topojson"
    GEOJSON = "geojson"
    XLS = "xls"
    XLSX = "xlsx"
    API = "api"


class CatalogStatus(str, Enum):
    """Catalog status"""
    ACTIVE = "active"
    INACTIVE = "inactive"
    SYNCING = "syncing"
    ERROR = "error"
    DRAFT = "draft"


class ColumnType(str, Enum):
    """Column data types"""
    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    JSON = "json"
    GEOMETRY = "geometry"


class ColumnSchema(BaseModel):
    """Schema definition for a catalog column"""
    name: str
    type: ColumnType
    nullable: bool = True
    description: Optional[str] = None
    indexed: bool = False


class SourceConfig(BaseModel):
    """Configuration for different data sources"""
    # SQL configuration
    connection_string: Optional[str] = None
    query: Optional[str] = None

    # CSV/Excel configuration
    url: Optional[str] = None
    file_path: Optional[str] = None
    # File upload configuration
    uploaded_file_id: Optional[str] = None  # Reference to uploaded file in storage
    uploaded_filename: Optional[str] = None  # Original filename
    file_size_bytes: Optional[int] = None  # File size for validation
    # File format options
    delimiter: Optional[str] = ","
    has_header: bool = True
    sheet_name: Optional[str] = None  # For Excel files

    # API configuration
    endpoint: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    auth_config: Optional[Dict[str, Any]] = None

    # General configuration
    refresh_rate_minutes: int = 60
    timeout_seconds: int = 30


class PermissionRule(BaseModel):
    """Permission rule for a specific group"""
    group_id: str  # Keycloak group name
    can_view: bool = True
    visible_columns: List[str] = []  # Empty list means all columns
    row_filters: Dict[str, Any] = {}  # MongoDB-style query filters
    max_rows: Optional[int] = None


class CacheConfig(BaseModel):
    """Cache configuration"""
    enabled: bool = True
    ttl_seconds: int = 3600  # 1 hour default
    max_size_mb: int = 100


class SyncResult(BaseModel):
    """Result of a data sync operation"""
    success: bool
    rows_synced: int = 0
    error_message: Optional[str] = None
    synced_at: datetime = Field(default_factory=datetime.utcnow)
    duration_seconds: float = 0


class Catalog(Document):
    """
    Catalog model for storing data source definitions and configurations.

    A catalog defines a data source that can be used in workflows,
    with fine-grained permission control at catalog, column, and row level.
    """

    catalog_id: str = Field(..., unique=True, index=True)
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None

    # Source configuration
    source_type: SourceType
    source_config: SourceConfig

    # Schema definition
    schema: List[ColumnSchema] = []

    # Permissions
    permissions: List[PermissionRule] = []

    # Cache configuration
    cache_config: CacheConfig = Field(default_factory=CacheConfig)

    # Metadata
    created_by: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_sync: Optional[datetime] = None
    last_sync_result: Optional[SyncResult] = None

    # Status
    status: CatalogStatus = CatalogStatus.DRAFT

    # Tags for organization
    tags: List[str] = []

    class Settings:
        name = "catalogs"
        indexes = [
            "catalog_id",
            "status",
            "created_by",
            "tags",
            ("source_type", "status"),
        ]

    def __repr__(self) -> str:
        return f"<Catalog {self.catalog_id}: {self.name}>"

    def is_accessible_by_user(self, user_groups: List[str]) -> bool:
        """Check if user has access to this catalog based on their groups"""
        if not self.permissions:
            return True  # No restrictions means accessible to all

        for rule in self.permissions:
            if rule.group_id in user_groups and rule.can_view:
                return True
        return False

    def get_visible_columns_for_user(self, user_groups: List[str]) -> List[str]:
        """Get list of columns visible to user based on their groups"""
        all_columns = [col.name for col in self.schema]

        if not self.permissions:
            return all_columns

        visible_columns = set()
        for rule in self.permissions:
            if rule.group_id in user_groups and rule.can_view:
                if not rule.visible_columns:  # Empty list means all columns
                    return all_columns
                visible_columns.update(rule.visible_columns)

        return list(visible_columns)

    def get_row_filters_for_user(self, user_groups: List[str]) -> Dict[str, Any]:
        """Get combined row filters for user based on their groups"""
        combined_filters = {}

        for rule in self.permissions:
            if rule.group_id in user_groups and rule.can_view and rule.row_filters:
                # Combine filters with AND logic
                for key, value in rule.row_filters.items():
                    if key in combined_filters:
                        # If same field has multiple conditions, create $and
                        if isinstance(combined_filters[key], dict) and "$and" in combined_filters[key]:
                            combined_filters[key]["$and"].append({key: value})
                        else:
                            combined_filters[key] = {"$and": [combined_filters[key], value]}
                    else:
                        combined_filters[key] = value

        return combined_filters

    def get_max_rows_for_user(self, user_groups: List[str]) -> Optional[int]:
        """Get maximum rows allowed for user"""
        max_rows = None

        for rule in self.permissions:
            if rule.group_id in user_groups and rule.can_view:
                if rule.max_rows is not None:
                    if max_rows is None or rule.max_rows < max_rows:
                        max_rows = rule.max_rows

        return max_rows


class CatalogData(Document):
    """
    Storage for actual catalog data.

    Separate from Catalog model to allow for efficient data updates
    and potential sharding/partitioning in the future.
    """

    catalog_id: str = Field(..., index=True)
    data: List[Dict[str, Any]] = []
    metadata: Dict[str, Any] = {}

    # Sync information
    synced_at: datetime = Field(default_factory=datetime.utcnow)
    version: int = 1

    # Statistics
    row_count: int = 0
    size_bytes: int = 0

    class Settings:
        name = "catalog_data"
        indexes = [
            "catalog_id",
            "synced_at",
            "version",
        ]

    def __repr__(self) -> str:
        return f"<CatalogData {self.catalog_id}: {self.row_count} rows>"