"""
Catalog data connectors for different data sources.

This module provides connectors for various data sources including SQL databases,
CSV files, JSON APIs, Excel files, and geographic data formats.
"""

import asyncio
import asyncpg
import pandas as pd
import httpx
import json
import csv
import io
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, Any, List, Optional, Union, Tuple
from urllib.parse import urlparse
import openpyxl

from ..models.catalog import SourceConfig, ColumnSchema, ColumnType, SyncResult, CatalogStatus
from ..core.logging_config import get_workflow_logger

logger = get_workflow_logger(__name__)


class BaseConnector(ABC):
    """Base class for all catalog data connectors"""

    def __init__(self, config: SourceConfig):
        self.config = config

    @abstractmethod
    async def connect(self) -> bool:
        """Test connection to data source"""
        pass

    @abstractmethod
    async def fetch_data(self) -> List[Dict[str, Any]]:
        """Fetch data from source"""
        pass

    @abstractmethod
    async def infer_schema(self) -> List[ColumnSchema]:
        """Infer schema from data source"""
        pass

    @abstractmethod
    async def validate_config(self) -> List[str]:
        """Validate connector configuration"""
        pass

    def _infer_column_type(self, sample_values: List[Any]) -> ColumnType:
        """Infer column type from sample values"""
        # Remove null/None values for inference
        non_null_values = [v for v in sample_values if v is not None and str(v).strip() != ""]

        if not non_null_values:
            return ColumnType.STRING

        # Check if all values are integers
        if all(isinstance(v, int) or (isinstance(v, str) and v.isdigit()) for v in non_null_values):
            return ColumnType.INTEGER

        # Check if all values are floats
        if all(self._is_float(v) for v in non_null_values):
            return ColumnType.FLOAT

        # Check if all values are booleans
        if all(isinstance(v, bool) or str(v).lower() in ['true', 'false', '0', '1'] for v in non_null_values):
            return ColumnType.BOOLEAN

        # Check if all values look like dates
        if all(self._is_date(v) for v in non_null_values):
            return ColumnType.DATETIME

        # Default to string
        return ColumnType.STRING

    def _is_float(self, value: Any) -> bool:
        """Check if value can be converted to float"""
        try:
            float(value)
            return True
        except (ValueError, TypeError):
            return False

    def _is_date(self, value: Any) -> bool:
        """Check if value looks like a date"""
        if isinstance(value, datetime):
            return True

        if isinstance(value, str):
            # Common date formats
            date_formats = [
                "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S",
                "%d/%m/%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S"
            ]
            for fmt in date_formats:
                try:
                    datetime.strptime(value, fmt)
                    return True
                except ValueError:
                    continue

        return False


class SQLConnector(BaseConnector):
    """Connector for SQL databases (PostgreSQL, MySQL, etc.)"""

    def __init__(self, config: SourceConfig):
        super().__init__(config)
        self.connection = None

    async def validate_config(self) -> List[str]:
        """Validate SQL configuration"""
        errors = []

        if not self.config.connection_string:
            errors.append("connection_string is required for SQL connector")

        if not self.config.query:
            errors.append("query is required for SQL connector")

        return errors

    async def connect(self) -> bool:
        """Test connection to database"""
        try:
            if self.connection:
                await self.connection.close()

            self.connection = await asyncpg.connect(self.config.connection_string)
            return True
        except Exception as e:
            logger.error(f"SQL connection failed: {str(e)}")
            return False

    async def fetch_data(self) -> List[Dict[str, Any]]:
        """Fetch data using SQL query"""
        if not self.connection:
            if not await self.connect():
                raise ConnectionError("Failed to connect to database")

        try:
            rows = await self.connection.fetch(self.config.query)
            data = []
            for row in rows:
                # Convert asyncpg.Record to dict
                row_dict = dict(row)
                data.append(row_dict)

            logger.info(f"Fetched {len(data)} rows from SQL database")
            return data

        except Exception as e:
            logger.error(f"SQL query failed: {str(e)}")
            raise
        finally:
            if self.connection:
                await self.connection.close()
                self.connection = None

    async def infer_schema(self) -> List[ColumnSchema]:
        """Infer schema from SQL query results"""
        # Get sample data
        sample_data = await self.fetch_data()

        if not sample_data:
            return []

        schemas = []
        first_row = sample_data[0]

        for column_name in first_row.keys():
            # Get sample values for type inference
            sample_values = [row.get(column_name) for row in sample_data[:100]]
            column_type = self._infer_column_type(sample_values)

            # Check if column has null values
            nullable = any(v is None for v in sample_values)

            schema = ColumnSchema(
                name=column_name,
                type=column_type,
                nullable=nullable,
                description=f"Column from SQL query: {column_name}"
            )
            schemas.append(schema)

        return schemas


class CSVConnector(BaseConnector):
    """Connector for CSV files (upload or download from URL)"""

    async def validate_config(self) -> List[str]:
        """Validate CSV configuration"""
        errors = []

        # Check that at least one source is provided
        sources = [self.config.url, self.config.file_path, self.config.uploaded_file_id]
        if not any(sources):
            errors.append("Either url, file_path, or uploaded_file_id is required for CSV connector")

        # Check that only one source is provided
        source_count = sum(1 for source in sources if source)
        if source_count > 1:
            errors.append("Only one of url, file_path, or uploaded_file_id should be provided")

        return errors

    async def connect(self) -> bool:
        """Test connection to CSV source"""
        try:
            if self.config.url:
                async with httpx.AsyncClient() as client:
                    response = await client.head(
                        self.config.url,
                        timeout=self.config.timeout_seconds
                    )
                    return response.status_code == 200
            elif self.config.uploaded_file_id:
                # Check if uploaded file exists
                from pathlib import Path
                upload_dir = Path("/app/uploads/catalog-files")
                file_pattern = f"{self.config.uploaded_file_id}.*"
                matching_files = list(upload_dir.glob(file_pattern))
                return len(matching_files) > 0
            else:
                # For file paths, check if accessible (in a real implementation)
                return True
        except Exception as e:
            logger.error(f"CSV connection test failed: {str(e)}")
            return False

    async def fetch_data(self) -> List[Dict[str, Any]]:
        """Fetch data from CSV"""
        try:
            if self.config.url:
                # Download CSV from URL
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        self.config.url,
                        timeout=self.config.timeout_seconds
                    )
                    response.raise_for_status()
                    csv_content = response.text
            elif self.config.uploaded_file_id:
                # Read from uploaded file
                from pathlib import Path
                upload_dir = Path("/app/uploads/catalog-files")
                file_pattern = f"{self.config.uploaded_file_id}.*"
                matching_files = list(upload_dir.glob(file_pattern))

                if not matching_files:
                    raise FileNotFoundError(f"Uploaded file with ID {self.config.uploaded_file_id} not found")

                file_path = matching_files[0]
                with open(file_path, 'r', encoding='utf-8') as f:
                    csv_content = f.read()
            else:
                # Read from file path (in production, handle file storage properly)
                with open(self.config.file_path, 'r', encoding='utf-8') as f:
                    csv_content = f.read()

            # Parse CSV
            csv_reader = csv.DictReader(
                io.StringIO(csv_content),
                delimiter=self.config.delimiter or ','
            )

            data = []
            for row in csv_reader:
                # Convert empty strings to None
                clean_row = {}
                for key, value in row.items():
                    clean_row[key] = value if value.strip() else None
                data.append(clean_row)

            logger.info(f"Fetched {len(data)} rows from CSV")
            return data

        except Exception as e:
            logger.error(f"CSV fetch failed: {str(e)}")
            raise

    async def infer_schema(self) -> List[ColumnSchema]:
        """Infer schema from CSV data"""
        sample_data = await self.fetch_data()

        if not sample_data:
            return []

        schemas = []
        first_row = sample_data[0]

        for column_name in first_row.keys():
            # Get sample values for type inference
            sample_values = [row.get(column_name) for row in sample_data[:100]]
            column_type = self._infer_column_type(sample_values)

            # Check if column has null values
            nullable = any(v is None or str(v).strip() == "" for v in sample_values)

            schema = ColumnSchema(
                name=column_name,
                type=column_type,
                nullable=nullable,
                description=f"Column from CSV: {column_name}"
            )
            schemas.append(schema)

        return schemas


class JSONConnector(BaseConnector):
    """Connector for JSON APIs and files"""

    async def validate_config(self) -> List[str]:
        """Validate JSON configuration"""
        errors = []

        if not self.config.url and not self.config.file_path and not self.config.endpoint:
            errors.append("url, file_path, or endpoint is required for JSON connector")

        return errors

    async def connect(self) -> bool:
        """Test connection to JSON source"""
        try:
            if self.config.url or self.config.endpoint:
                url = self.config.url or self.config.endpoint
                async with httpx.AsyncClient() as client:
                    response = await client.head(
                        url,
                        headers=self.config.headers or {},
                        timeout=self.config.timeout_seconds
                    )
                    return response.status_code in [200, 405]  # 405 for HEAD not allowed
            else:
                return True  # File path check
        except Exception as e:
            logger.error(f"JSON connection test failed: {str(e)}")
            return False

    async def fetch_data(self) -> List[Dict[str, Any]]:
        """Fetch data from JSON source"""
        try:
            if self.config.url or self.config.endpoint:
                # Fetch from URL/API
                url = self.config.url or self.config.endpoint
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        url,
                        headers=self.config.headers or {},
                        timeout=self.config.timeout_seconds
                    )
                    response.raise_for_status()
                    json_data = response.json()
            else:
                # Read from file
                with open(self.config.file_path, 'r', encoding='utf-8') as f:
                    json_data = json.load(f)

            # Convert to list of dictionaries if needed
            if isinstance(json_data, dict):
                # If it's a single object, wrap in list
                if "data" in json_data and isinstance(json_data["data"], list):
                    data = json_data["data"]
                elif "results" in json_data and isinstance(json_data["results"], list):
                    data = json_data["results"]
                else:
                    data = [json_data]
            elif isinstance(json_data, list):
                data = json_data
            else:
                raise ValueError("JSON data must be object or array")

            logger.info(f"Fetched {len(data)} rows from JSON")
            return data

        except Exception as e:
            logger.error(f"JSON fetch failed: {str(e)}")
            raise

    async def infer_schema(self) -> List[ColumnSchema]:
        """Infer schema from JSON data"""
        sample_data = await self.fetch_data()

        if not sample_data:
            return []

        # Flatten nested objects and collect all keys
        all_keys = set()
        flattened_data = []

        for row in sample_data[:100]:  # Sample first 100 rows
            flat_row = self._flatten_dict(row)
            flattened_data.append(flat_row)
            all_keys.update(flat_row.keys())

        schemas = []
        for key in sorted(all_keys):
            # Get sample values for type inference
            sample_values = [row.get(key) for row in flattened_data]
            column_type = self._infer_column_type(sample_values)

            # Check if column has null values
            nullable = any(v is None for v in sample_values)

            schema = ColumnSchema(
                name=key,
                type=column_type,
                nullable=nullable,
                description=f"Column from JSON: {key}"
            )
            schemas.append(schema)

        return schemas

    def _flatten_dict(self, d: Dict[str, Any], parent_key: str = '', sep: str = '.') -> Dict[str, Any]:
        """Flatten nested dictionary"""
        items = []
        for k, v in d.items():
            new_key = f"{parent_key}{sep}{k}" if parent_key else k
            if isinstance(v, dict):
                items.extend(self._flatten_dict(v, new_key, sep=sep).items())
            elif isinstance(v, list) and v and isinstance(v[0], dict):
                # For arrays of objects, take first object structure
                items.extend(self._flatten_dict(v[0], new_key, sep=sep).items())
            else:
                items.append((new_key, v))
        return dict(items)


class ExcelConnector(BaseConnector):
    """Connector for Excel files (XLS/XLSX)"""

    async def validate_config(self) -> List[str]:
        """Validate Excel configuration"""
        errors = []

        # Check that at least one source is provided
        sources = [self.config.url, self.config.file_path, self.config.uploaded_file_id]
        if not any(sources):
            errors.append("Either url, file_path, or uploaded_file_id is required for Excel connector")

        # Check that only one source is provided
        source_count = sum(1 for source in sources if source)
        if source_count > 1:
            errors.append("Only one of url, file_path, or uploaded_file_id should be provided")

        return errors

    async def connect(self) -> bool:
        """Test connection to Excel source"""
        try:
            if self.config.url:
                async with httpx.AsyncClient() as client:
                    response = await client.head(
                        self.config.url,
                        timeout=self.config.timeout_seconds
                    )
                    return response.status_code == 200
            elif self.config.uploaded_file_id:
                # Check if uploaded file exists
                from pathlib import Path
                upload_dir = Path("/app/uploads/catalog-files")
                file_pattern = f"{self.config.uploaded_file_id}.*"
                matching_files = list(upload_dir.glob(file_pattern))
                return len(matching_files) > 0
            else:
                return True  # File path check
        except Exception as e:
            logger.error(f"Excel connection test failed: {str(e)}")
            return False

    async def fetch_data(self) -> List[Dict[str, Any]]:
        """Fetch data from Excel file"""
        try:
            if self.config.url:
                # Download Excel from URL
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        self.config.url,
                        timeout=self.config.timeout_seconds
                    )
                    response.raise_for_status()
                    excel_content = response.content

                # Read Excel from bytes
                df = pd.read_excel(
                    io.BytesIO(excel_content),
                    sheet_name=self.config.sheet_name or 0
                )
            elif self.config.uploaded_file_id:
                # Read from uploaded file
                from pathlib import Path
                upload_dir = Path("/app/uploads/catalog-files")
                file_pattern = f"{self.config.uploaded_file_id}.*"
                matching_files = list(upload_dir.glob(file_pattern))

                if not matching_files:
                    raise FileNotFoundError(f"Uploaded file with ID {self.config.uploaded_file_id} not found")

                file_path = matching_files[0]
                df = pd.read_excel(
                    file_path,
                    sheet_name=self.config.sheet_name or 0
                )
            else:
                # Read from file path
                df = pd.read_excel(
                    self.config.file_path,
                    sheet_name=self.config.sheet_name or 0
                )

            # Convert DataFrame to list of dictionaries
            # Replace NaN with None
            df = df.where(pd.notnull(df), None)
            data = df.to_dict('records')

            logger.info(f"Fetched {len(data)} rows from Excel")
            return data

        except Exception as e:
            logger.error(f"Excel fetch failed: {str(e)}")
            raise

    async def infer_schema(self) -> List[ColumnSchema]:
        """Infer schema from Excel data"""
        sample_data = await self.fetch_data()

        if not sample_data:
            return []

        schemas = []
        first_row = sample_data[0]

        for column_name in first_row.keys():
            # Get sample values for type inference
            sample_values = [row.get(column_name) for row in sample_data[:100]]
            column_type = self._infer_column_type(sample_values)

            # Check if column has null values
            nullable = any(v is None for v in sample_values)

            schema = ColumnSchema(
                name=column_name,
                type=column_type,
                nullable=nullable,
                description=f"Column from Excel: {column_name}"
            )
            schemas.append(schema)

        return schemas


class ConnectorFactory:
    """Factory for creating appropriate connectors based on source type"""

    @staticmethod
    def create_connector(source_type: str, config: SourceConfig) -> BaseConnector:
        """Create connector instance based on source type"""
        if source_type == "sql":
            return SQLConnector(config)
        elif source_type in ["csv_upload", "csv_url"]:
            return CSVConnector(config)
        elif source_type in ["json", "topojson", "geojson"]:
            return JSONConnector(config)
        elif source_type in ["xls", "xlsx"]:
            return ExcelConnector(config)
        else:
            raise ValueError(f"Unsupported source type: {source_type}")


class CatalogSyncService:
    """Service for synchronizing catalog data from various sources"""

    @staticmethod
    async def sync_catalog_data(catalog_id: str) -> SyncResult:
        """Sync data for a specific catalog"""
        from .catalog_service import CatalogService

        start_time = datetime.utcnow()

        try:
            # Get catalog configuration
            catalog = await CatalogService.get_catalog(catalog_id)

            # Create appropriate connector
            connector = ConnectorFactory.create_connector(
                catalog.source_type.value,
                catalog.source_config
            )

            # Validate configuration
            config_errors = await connector.validate_config()
            if config_errors:
                error_msg = f"Configuration errors: {', '.join(config_errors)}"
                return SyncResult(
                    success=False,
                    error_message=error_msg,
                    duration_seconds=0
                )

            # Test connection
            if not await connector.connect():
                return SyncResult(
                    success=False,
                    error_message="Failed to connect to data source",
                    duration_seconds=0
                )

            # Fetch data
            data = await connector.fetch_data()

            # Infer schema from the fetched data
            inferred_schema = await connector.infer_schema()

            # Update catalog schema
            catalog.schema = inferred_schema

            # Store data
            await CatalogService.store_catalog_data(catalog_id, data)

            # Calculate duration
            end_time = datetime.utcnow()
            duration = (end_time - start_time).total_seconds()

            # Update catalog sync info
            sync_result = SyncResult(
                success=True,
                rows_synced=len(data),
                duration_seconds=duration
            )

            catalog.last_sync = end_time
            catalog.last_sync_result = sync_result
            catalog.status = CatalogStatus.ACTIVE
            await catalog.save()

            logger.info(f"Updated catalog schema with {len(inferred_schema)} columns")

            logger.info(f"Successfully synced catalog {catalog_id}: {len(data)} rows")
            return sync_result

        except Exception as e:
            end_time = datetime.utcnow()
            duration = (end_time - start_time).total_seconds()

            error_msg = str(e)
            logger.error(f"Catalog sync failed for {catalog_id}: {error_msg}")

            # Update catalog with error status
            try:
                catalog = await CatalogService.get_catalog(catalog_id)
                catalog.status = "error"
                catalog.last_sync_result = SyncResult(
                    success=False,
                    error_message=error_msg,
                    duration_seconds=duration
                )
                await catalog.save()
            except:
                pass  # Don't fail if we can't update catalog

            return SyncResult(
                success=False,
                error_message=error_msg,
                duration_seconds=duration
            )

    @staticmethod
    async def test_connection(source_type: str, config: Dict[str, Any]) -> bool:
        """Test connection to a data source"""
        try:
            source_config = SourceConfig(**config)
            connector = ConnectorFactory.create_connector(source_type, source_config)
            return await connector.connect()
        except Exception as e:
            logger.error(f"Connection test failed: {str(e)}")
            return False

    @staticmethod
    async def preview_data(source_type: str, config: Dict[str, Any], limit: int = 10) -> List[Dict[str, Any]]:
        """Preview data from a source without storing it"""
        try:
            source_config = SourceConfig(**config)
            connector = ConnectorFactory.create_connector(source_type, source_config)

            data = await connector.fetch_data()
            return data[:limit]
        except Exception as e:
            logger.error(f"Data preview failed: {str(e)}")
            raise