"""
Catalog Permission Service for managing access control to catalogs.

This service handles permission checking, group-based access control,
and integration with Keycloak groups.
"""

from typing import Dict, Any, List, Set, Optional
from ..models.catalog import Catalog, PermissionRule
from ..core.logging_config import get_workflow_logger

logger = get_workflow_logger(__name__)


class CatalogPermissionService:
    """Service for managing catalog permissions and access control"""

    @staticmethod
    async def add_permission_rule(
        catalog_id: str,
        group_id: str,
        can_view: bool = True,
        visible_columns: Optional[List[str]] = None,
        row_filters: Optional[Dict[str, Any]] = None,
        max_rows: Optional[int] = None
    ) -> Catalog:
        """Add or update permission rule for a group"""
        from .catalog_service import CatalogService

        catalog = await CatalogService.get_catalog(catalog_id)

        # Remove existing rule for this group if it exists
        catalog.permissions = [
            rule for rule in catalog.permissions
            if rule.group_id != group_id
        ]

        # Add new rule
        new_rule = PermissionRule(
            group_id=group_id,
            can_view=can_view,
            visible_columns=visible_columns or [],
            row_filters=row_filters or {},
            max_rows=max_rows
        )
        catalog.permissions.append(new_rule)

        await catalog.save()
        logger.info(f"Added permission rule for group {group_id} on catalog {catalog_id}")
        return catalog

    @staticmethod
    async def remove_permission_rule(catalog_id: str, group_id: str) -> Catalog:
        """Remove permission rule for a group"""
        from .catalog_service import CatalogService

        catalog = await CatalogService.get_catalog(catalog_id)

        # Remove rule for this group
        original_count = len(catalog.permissions)
        catalog.permissions = [
            rule for rule in catalog.permissions
            if rule.group_id != group_id
        ]

        if len(catalog.permissions) < original_count:
            await catalog.save()
            logger.info(f"Removed permission rule for group {group_id} on catalog {catalog_id}")
        else:
            logger.warning(f"No permission rule found for group {group_id} on catalog {catalog_id}")

        return catalog

    @staticmethod
    async def bulk_update_permissions(
        catalog_id: str,
        permission_rules: List[Dict[str, Any]]
    ) -> Catalog:
        """Update all permission rules for a catalog"""
        from .catalog_service import CatalogService

        catalog = await CatalogService.get_catalog(catalog_id)

        # Convert dict rules to PermissionRule objects
        new_rules = []
        for rule_dict in permission_rules:
            rule = PermissionRule(**rule_dict)
            new_rules.append(rule)

        catalog.permissions = new_rules
        await catalog.save()
        logger.info(f"Updated {len(new_rules)} permission rules for catalog {catalog_id}")
        return catalog

    @staticmethod
    def get_effective_permissions(
        catalog: Catalog,
        user_groups: List[str]
    ) -> Dict[str, Any]:
        """Get effective permissions for user based on their groups"""
        if not catalog.permissions:
            # No permissions defined = accessible to all with all columns
            all_columns = [col.name for col in catalog.schema]
            return {
                "can_view": True,
                "visible_columns": all_columns,
                "row_filters": {},
                "max_rows": None
            }

        # Check if user has any access
        has_access = False
        visible_columns = set()
        combined_row_filters = {}
        max_rows = None

        for rule in catalog.permissions:
            if rule.group_id in user_groups and rule.can_view:
                has_access = True

                # Collect visible columns
                if not rule.visible_columns:  # Empty list means all columns
                    visible_columns = {col.name for col in catalog.schema}
                else:
                    visible_columns.update(rule.visible_columns)

                # Combine row filters
                if rule.row_filters:
                    for field, condition in rule.row_filters.items():
                        if field in combined_row_filters:
                            # Combine with AND logic
                            if isinstance(combined_row_filters[field], dict) and "$and" in combined_row_filters[field]:
                                combined_row_filters[field]["$and"].append(condition)
                            else:
                                combined_row_filters[field] = {
                                    "$and": [combined_row_filters[field], condition]
                                }
                        else:
                            combined_row_filters[field] = condition

                # Apply most restrictive max_rows
                if rule.max_rows is not None:
                    if max_rows is None or rule.max_rows < max_rows:
                        max_rows = rule.max_rows

        return {
            "can_view": has_access,
            "visible_columns": list(visible_columns),
            "row_filters": combined_row_filters,
            "max_rows": max_rows
        }

    @staticmethod
    async def get_user_accessible_catalogs(
        user_groups: List[str],
        all_catalogs: Optional[List[Catalog]] = None
    ) -> List[Catalog]:
        """Get all catalogs accessible to user"""
        if all_catalogs is None:
            all_catalogs = await Catalog.find().to_list()

        accessible_catalogs = []
        for catalog in all_catalogs:
            if catalog.is_accessible_by_user(user_groups):
                accessible_catalogs.append(catalog)

        return accessible_catalogs

    @staticmethod
    async def validate_group_exists(group_id: str) -> bool:
        """
        Validate if a Keycloak group exists.

        Note: This is a placeholder for future Keycloak integration.
        In a full implementation, this would check against Keycloak API.
        """
        # TODO: Implement actual Keycloak group validation
        # For now, assume common groups exist
        common_groups = [
            "admin", "manager", "reviewer", "approver", "viewer",
            "JUDDC", "catastro", "conapesca", "departamento_obras",
            "public", "citizens"
        ]
        return group_id in common_groups

    @staticmethod
    async def get_group_hierarchy(group_id: str) -> List[str]:
        """
        Get group hierarchy for inheritance.

        Note: This is a placeholder for future Keycloak integration.
        In a full implementation, this would get parent groups from Keycloak.
        """
        # TODO: Implement actual Keycloak group hierarchy
        # For now, simple hierarchy mapping
        hierarchy_map = {
            "admin": ["admin", "manager", "reviewer", "viewer", "public"],
            "manager": ["manager", "reviewer", "viewer", "public"],
            "reviewer": ["reviewer", "viewer", "public"],
            "viewer": ["viewer", "public"],
            "JUDDC": ["JUDDC", "catastro", "reviewer", "viewer", "public"],
        }
        return hierarchy_map.get(group_id, [group_id, "public"])

    @staticmethod
    async def check_column_permission(
        catalog: Catalog,
        user_groups: List[str],
        column_name: str
    ) -> bool:
        """Check if user has permission to view specific column"""
        visible_columns = catalog.get_visible_columns_for_user(user_groups)
        return column_name in visible_columns

    @staticmethod
    async def get_permission_summary(catalog: Catalog) -> Dict[str, Any]:
        """Get summary of all permissions for a catalog"""
        summary = {
            "catalog_id": catalog.catalog_id,
            "total_rules": len(catalog.permissions),
            "groups_with_access": [],
            "public_access": len(catalog.permissions) == 0,
            "column_restrictions": {},
            "row_filters_count": 0
        }

        for rule in catalog.permissions:
            if rule.can_view:
                summary["groups_with_access"].append(rule.group_id)

                # Track column restrictions
                if rule.visible_columns:
                    summary["column_restrictions"][rule.group_id] = len(rule.visible_columns)
                else:
                    summary["column_restrictions"][rule.group_id] = len(catalog.schema)

                # Count row filters
                if rule.row_filters:
                    summary["row_filters_count"] += len(rule.row_filters)

        return summary

    @staticmethod
    async def audit_permission_access(
        catalog_id: str,
        user_id: str,
        user_groups: List[str],
        action: str,
        accessed_columns: Optional[List[str]] = None,
        row_count: Optional[int] = None
    ) -> None:
        """
        Audit catalog access for compliance.

        Note: This is a placeholder for future audit logging.
        In a full implementation, this would store detailed access logs.
        """
        audit_data = {
            "catalog_id": catalog_id,
            "user_id": user_id,
            "user_groups": user_groups,
            "action": action,
            "accessed_columns": accessed_columns,
            "row_count": row_count,
            "timestamp": logger.info.__self__.logger.handlers[0].formatter.formatTime(
                logger.info.__self__.logger.handlers[0], None
            ) if logger.info.__self__.logger.handlers else None
        }

        # TODO: Store in audit collection
        logger.info(f"Catalog access audit: {audit_data}")

    @staticmethod
    def validate_permission_rule(rule: Dict[str, Any], catalog_schema: List[str]) -> List[str]:
        """Validate permission rule and return any errors"""
        errors = []

        # Validate required fields
        if "group_id" not in rule:
            errors.append("group_id is required")

        # Validate visible_columns
        if "visible_columns" in rule and rule["visible_columns"]:
            invalid_columns = set(rule["visible_columns"]) - set(catalog_schema)
            if invalid_columns:
                errors.append(f"Invalid columns: {', '.join(invalid_columns)}")

        # Validate row_filters
        if "row_filters" in rule and rule["row_filters"]:
            for field in rule["row_filters"].keys():
                if field not in catalog_schema:
                    errors.append(f"Row filter field '{field}' not in schema")

        # Validate max_rows
        if "max_rows" in rule and rule["max_rows"] is not None:
            if not isinstance(rule["max_rows"], int) or rule["max_rows"] < 0:
                errors.append("max_rows must be a non-negative integer")

        return errors