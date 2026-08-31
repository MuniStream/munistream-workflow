"""
Catalog service for managing data catalogs in workflows.

This service provides CRUD operations, permission checking, and data filtering
for catalogs that can be used in workflows.
"""

import asyncio
import logging
import unicodedata
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from beanie import PydanticObjectId
from beanie.operators import In, And, Or
from pymongo.errors import DuplicateKeyError

from ..models.catalog import (
    Catalog,
    CatalogData,
    CatalogRow,
    CatalogStatus,
    SourceType,
    ColumnSchema,
    PermissionRule,
    SyncResult
)
from ..core.logging_config import get_workflow_logger

logger = get_workflow_logger(__name__)


class CatalogNotFoundError(Exception):
    """Raised when a catalog is not found"""
    pass


class CatalogPermissionError(Exception):
    """Raised when user lacks permission to access catalog"""
    pass


class CatalogService:
    """Service for managing catalogs and their data"""

    @staticmethod
    async def create_catalog(
        catalog_id: str,
        name: str,
        description: str,
        source_type: SourceType,
        source_config: Dict[str, Any],
        schema: List[Dict[str, Any]],
        created_by: str,
        permissions: Optional[List[Dict[str, Any]]] = None,
        tags: Optional[List[str]] = None
    ) -> Catalog:
        """Create a new catalog"""
        try:
            # Convert schema dicts to ColumnSchema objects
            column_schemas = [ColumnSchema(**col) for col in schema]

            # Convert permission dicts to PermissionRule objects
            permission_rules = []
            if permissions:
                permission_rules = [PermissionRule(**perm) for perm in permissions]

            catalog = Catalog(
                catalog_id=catalog_id,
                name=name,
                description=description,
                source_type=source_type,
                source_config=source_config,
                schema=column_schemas,
                created_by=created_by,
                permissions=permission_rules,
                tags=tags or [],
                status=CatalogStatus.DRAFT
            )

            await catalog.insert()
            logger.info(f"Created catalog: {catalog_id}")
            return catalog

        except DuplicateKeyError:
            raise ValueError(f"Catalog with ID '{catalog_id}' already exists")

    @staticmethod
    async def get_catalog(catalog_id: str) -> Catalog:
        """Get catalog by ID"""
        catalog = await Catalog.find_one(Catalog.catalog_id == catalog_id)
        if not catalog:
            raise CatalogNotFoundError(f"Catalog '{catalog_id}' not found")
        return catalog

    @staticmethod
    async def get_catalog_by_object_id(object_id: PydanticObjectId) -> Catalog:
        """Get catalog by MongoDB ObjectId"""
        catalog = await Catalog.get(object_id)
        if not catalog:
            raise CatalogNotFoundError(f"Catalog with ID '{object_id}' not found")
        return catalog

    @staticmethod
    async def list_catalogs(
        user_groups: List[str],
        status: Optional[CatalogStatus] = None,
        source_type: Optional[SourceType] = None,
        tags: Optional[List[str]] = None,
        limit: int = 100,
        skip: int = 0
    ) -> List[Catalog]:
        """List catalogs accessible to user"""
        query_filters = []

        # Filter by status
        if status:
            query_filters.append(Catalog.status == status)

        # Filter by source type
        if source_type:
            query_filters.append(Catalog.source_type == source_type)

        # Filter by tags
        if tags:
            query_filters.append(In(Catalog.tags, tags))

        # Build query
        if query_filters:
            query = And(*query_filters)
        else:
            query = {}

        # Get all catalogs matching filters
        all_catalogs = await Catalog.find(query).skip(skip).limit(limit).to_list()

        # Filter by user permissions
        accessible_catalogs = []
        for catalog in all_catalogs:
            if catalog.is_accessible_by_user(user_groups):
                accessible_catalogs.append(catalog)

        return accessible_catalogs

    @staticmethod
    async def update_catalog(
        catalog_id: str,
        updates: Dict[str, Any],
        updated_by: str
    ) -> Catalog:
        """Update catalog configuration"""
        catalog = await CatalogService.get_catalog(catalog_id)

        # Handle special fields
        if "schema" in updates and isinstance(updates["schema"], list):
            updates["schema"] = [ColumnSchema(**col) for col in updates["schema"]]

        if "permissions" in updates and isinstance(updates["permissions"], list):
            updates["permissions"] = [PermissionRule(**perm) for perm in updates["permissions"]]

        # Update fields
        for key, value in updates.items():
            if hasattr(catalog, key):
                setattr(catalog, key, value)

        catalog.updated_at = datetime.utcnow()

        await catalog.save()
        logger.info(f"Updated catalog: {catalog_id} by {updated_by}")
        return catalog

    @staticmethod
    async def delete_catalog(catalog_id: str, deleted_by: str) -> bool:
        """Delete catalog and its data"""
        catalog = await CatalogService.get_catalog(catalog_id)

        # Delete associated data
        await CatalogData.find(CatalogData.catalog_id == catalog_id).delete()

        # Delete catalog
        await catalog.delete()

        logger.info(f"Deleted catalog: {catalog_id} by {deleted_by}")
        return True

    @staticmethod
    async def get_catalog_data(
        catalog_id: str,
        user_groups: List[str],
        filters: Optional[Dict[str, Any]] = None,
        search: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        sort_by: Optional[str] = None,
        sort_desc: bool = False
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Get filtered catalog data for user"""
        catalog = await CatalogService.get_catalog(catalog_id)

        # Check user access
        if not catalog.is_accessible_by_user(user_groups):
            raise CatalogPermissionError(f"User lacks access to catalog '{catalog_id}'")

        visible_columns = catalog.get_visible_columns_for_user(user_groups)
        row_filters = catalog.get_row_filters_for_user(user_groups)
        max_rows = catalog.get_max_rows_for_user(user_groups)

        # --- Almacenamiento por-fila (catálogos grandes): consulta en Mongo ---
        if await CatalogService._has_row_storage(catalog_id):
            query = CatalogService._mongo_query(
                catalog_id, visible_columns, row_filters, filters, search
            )
            total_count = await CatalogRow.find(query).count()
            if max_rows is not None:
                total_count = min(total_count, max_rows)

            find = CatalogRow.find(query)
            if sort_by and sort_by in visible_columns:
                find = find.sort((f"data.{sort_by}", -1 if sort_desc else 1))
            effective_offset = offset
            effective_limit = limit
            if max_rows is not None:
                effective_limit = max(0, min(limit, max_rows - offset))
            rows = await find.skip(effective_offset).limit(effective_limit).to_list()
            data = [
                {col: r.data.get(col) for col in visible_columns if col in r.data}
                for r in rows
            ]
            return data, total_count

        # --- Legado: documento único CatalogData (catálogos pequeños) ---
        catalog_data = await CatalogData.find_one(CatalogData.catalog_id == catalog_id)
        if not catalog_data:
            return [], 0

        data = catalog_data.data
        if not data:
            return [], 0

        # Apply user permissions
        visible_columns = catalog.get_visible_columns_for_user(user_groups)
        row_filters = catalog.get_row_filters_for_user(user_groups)
        max_rows = catalog.get_max_rows_for_user(user_groups)

        # Filter columns
        filtered_data = []
        for row in data:
            filtered_row = {col: row.get(col) for col in visible_columns if col in row}
            filtered_data.append(filtered_row)

        # Apply row filters (basic implementation)
        if row_filters:
            filtered_data = CatalogService._apply_row_filters(filtered_data, row_filters)

        # Apply search
        if search:
            filtered_data = CatalogService._apply_search(filtered_data, search, visible_columns)

        # Apply additional filters
        if filters:
            filtered_data = CatalogService._apply_filters(filtered_data, filters)

        total_count = len(filtered_data)

        # Apply sorting
        if sort_by and sort_by in visible_columns:
            reverse = sort_desc
            filtered_data.sort(
                key=lambda x: x.get(sort_by, ""),
                reverse=reverse
            )

        # Apply pagination and max rows limit
        end_index = offset + limit
        if max_rows:
            end_index = min(end_index, max_rows)

        paginated_data = filtered_data[offset:end_index]

        return paginated_data, total_count

    @staticmethod
    async def get_distinct_values(
        catalog_id: str,
        user_groups: List[str],
        column: str,
        filters: Optional[Dict[str, Any]] = None,
        search: Optional[str] = None,
        limit: int = 5000,
    ) -> List[Any]:
        """Valores distintos (ordenados) de una columna, con permisos/filtros aplicados.

        Sirve para poblar los niveles de una selección jerárquica en cascada
        (p. ej. estados; municipios de un estado; localidades de un municipio)
        sin traer todas las filas al cliente.
        """
        catalog = await CatalogService.get_catalog(catalog_id)
        if not catalog.is_accessible_by_user(user_groups):
            raise CatalogPermissionError(f"User lacks access to catalog '{catalog_id}'")

        visible_columns = catalog.get_visible_columns_for_user(user_groups)
        if column not in visible_columns:
            return []

        row_filters = catalog.get_row_filters_for_user(user_groups)

        # --- Almacenamiento por-fila: distinct en Mongo ---
        if await CatalogService._has_row_storage(catalog_id):
            query = CatalogService._mongo_query(
                catalog_id, visible_columns, row_filters, filters, search
            )
            coll = CatalogRow.get_motor_collection()
            values = await coll.distinct(f"data.{column}", query)
            values = [v for v in values if v is not None and v != ""]
            values.sort(key=lambda v: str(v))
            return values[:limit]

        # --- Legado: documento único ---
        catalog_data = await CatalogData.find_one(CatalogData.catalog_id == catalog_id)
        if not catalog_data or not catalog_data.data:
            return []

        rows = [
            {col: row.get(col) for col in visible_columns if col in row}
            for row in catalog_data.data
        ]

        row_filters = catalog.get_row_filters_for_user(user_groups)
        if row_filters:
            rows = CatalogService._apply_row_filters(rows, row_filters)
        if search:
            rows = CatalogService._apply_search(rows, search, visible_columns)
        if filters:
            rows = CatalogService._apply_filters(rows, filters)

        seen = set()
        distinct: List[Any] = []
        for row in rows:
            val = row.get(column)
            if val is None or val == "":
                continue
            key = str(val)
            if key in seen:
                continue
            seen.add(key)
            distinct.append(val)
            if len(distinct) >= limit:
                break

        distinct.sort(key=lambda v: str(v))
        return distinct

    @staticmethod
    def _apply_row_filters(data: List[Dict[str, Any]], filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Apply row-level filters to data"""
        filtered_data = []

        for row in data:
            matches = True
            for field, condition in filters.items():
                if field not in row:
                    matches = False
                    break

                value = row[field]
                if isinstance(condition, dict):
                    # Handle MongoDB-style operators
                    for op, op_value in condition.items():
                        if op == "$eq" and value != op_value:
                            matches = False
                            break
                        elif op == "$ne" and value == op_value:
                            matches = False
                            break
                        elif op == "$in" and value not in op_value:
                            matches = False
                            break
                        elif op == "$nin" and value in op_value:
                            matches = False
                            break
                        # Add more operators as needed
                else:
                    # Simple equality check
                    if value != condition:
                        matches = False
                        break

            if matches:
                filtered_data.append(row)

        return filtered_data

    @staticmethod
    def _apply_search(data: List[Dict[str, Any]], search: str, columns: List[str]) -> List[Dict[str, Any]]:
        """Apply text search across visible columns.

        Búsqueda por substring, case-insensitive **y** insensible a acentos
        (p.ej. "camaron" encuentra "Camarón"), necesario para español.
        """
        def _norm(text: str) -> str:
            return ''.join(
                c for c in unicodedata.normalize('NFKD', text.lower())
                if not unicodedata.combining(c)
            )

        search_term = _norm(search)
        filtered_data = []

        for row in data:
            matches = False
            for col in columns:
                if col in row:
                    if search_term in _norm(str(row[col])):
                        matches = True
                        break

            if matches:
                filtered_data.append(row)

        return filtered_data

    @staticmethod
    def _apply_filters(data: List[Dict[str, Any]], filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Apply additional filters to data"""
        filtered_data = []

        for row in data:
            matches = True
            for field, value in filters.items():
                if field not in row or row[field] != value:
                    matches = False
                    break

            if matches:
                filtered_data.append(row)

        return filtered_data

    @staticmethod
    async def update_catalog_status(catalog_id: str, status: CatalogStatus) -> Catalog:
        """Update catalog status"""
        catalog = await CatalogService.get_catalog(catalog_id)
        catalog.status = status
        catalog.updated_at = datetime.utcnow()
        await catalog.save()
        return catalog

    @staticmethod
    async def get_catalog_schema(catalog_id: str, user_groups: List[str]) -> List[Dict[str, Any]]:
        """Get catalog schema visible to user"""
        catalog = await CatalogService.get_catalog(catalog_id)

        # Check user access
        if not catalog.is_accessible_by_user(user_groups):
            raise CatalogPermissionError(f"User lacks access to catalog '{catalog_id}'")

        visible_columns = catalog.get_visible_columns_for_user(user_groups)

        # Return only visible column schemas
        visible_schema = []
        for col_schema in catalog.schema:
            if col_schema.name in visible_columns:
                visible_schema.append({
                    "name": col_schema.name,
                    "type": col_schema.type,
                    "nullable": col_schema.nullable,
                    "description": col_schema.description,
                    "indexed": col_schema.indexed
                })

        return visible_schema

    @staticmethod
    async def store_catalog_data(catalog_id: str, data: List[Dict[str, Any]]) -> CatalogData:
        """Store or update catalog data (almacenamiento por-fila).

        Escribe cada fila como un documento ``CatalogRow`` independiente, por lo
        que no hay tope de 16MB y las consultas (filtros, distinct, paginación)
        se resuelven en Mongo. Reemplaza cualquier dato previo del catálogo,
        incluida la representación legada en ``CatalogData`` (documento único).
        """
        # Limpiar datos previos (por-fila y legado de documento único).
        await CatalogRow.find(CatalogRow.catalog_id == catalog_id).delete()
        await CatalogData.find(CatalogData.catalog_id == catalog_id).delete()

        row_count = len(data)

        # Insertar por lotes para no saturar memoria con catálogos grandes.
        BATCH = 5000
        for i in range(0, row_count, BATCH):
            chunk = data[i:i + BATCH]
            await CatalogRow.insert_many(
                [CatalogRow(catalog_id=catalog_id, data=row) for row in chunk]
            )

        # Documento de metadatos (sin las filas embebidas) para row_count/estadísticas.
        catalog_data = CatalogData(
            catalog_id=catalog_id,
            data=[],
            metadata={
                "last_updated": datetime.utcnow().isoformat(),
                "source": "per_row",
                "storage": "catalog_rows",
            },
            synced_at=datetime.utcnow(),
            version=1,
            row_count=row_count,
            size_bytes=0,
        )
        await catalog_data.insert()
        logger.info(f"Stored data for catalog {catalog_id}: {row_count} rows (per-row)")
        return catalog_data

    @staticmethod
    async def _has_row_storage(catalog_id: str) -> bool:
        """True si el catálogo usa almacenamiento por-fila (CatalogRow)."""
        return await CatalogRow.find(CatalogRow.catalog_id == catalog_id).limit(1).count() > 0

    @staticmethod
    def _mongo_query(catalog_id: str, visible_columns: List[str],
                     row_filters: Optional[Dict[str, Any]],
                     filters: Optional[Dict[str, Any]],
                     search: Optional[str]) -> Dict[str, Any]:
        """Construye el query Mongo para CatalogRow (campos bajo ``data.``)."""
        query: Dict[str, Any] = {"catalog_id": catalog_id}
        for f in (row_filters or {}), (filters or {}):
            for k, v in f.items():
                query[f"data.{k}"] = v
        if search:
            # Substring, insensible a mayúsculas, sobre las columnas visibles.
            import re
            rx = {"$regex": re.escape(search), "$options": "i"}
            query["$or"] = [{f"data.{col}": rx} for col in visible_columns]
        return query