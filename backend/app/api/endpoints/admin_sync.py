"""
Admin Keycloak synchronization API endpoints.
Only accessible by users with ADMIN (root) role.
"""

from typing import Dict, Any
from fastapi import APIRouter, HTTPException, Depends, status
from datetime import datetime

from ...auth.provider import get_current_user
from ...services.keycloak_sync import keycloak_sync_service
import logging

logger = logging.getLogger(__name__)

router = APIRouter(include_in_schema=False)


async def require_admin_root(current_user: dict = Depends(get_current_user)) -> dict:
    """Require ADMIN (root) role with full system access"""
    user_roles = current_user.get("roles", [])

    if "admin" not in user_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin (root) access required. Only admins can manage Keycloak synchronization."
        )

    return current_user


@router.get("/sync/status")
async def get_sync_status(
    current_user: dict = Depends(require_admin_root)
) -> Dict[str, Any]:
    """
    Get current synchronization status between MuniStream and Keycloak.
    Shows counts and differences between systems.
    """
    try:
        status_info = await keycloak_sync_service.get_sync_status()

        return {
            "success": True,
            "timestamp": datetime.utcnow().isoformat(),
            "requested_by": current_user.get("email"),
            "sync_status": status_info
        }

    except Exception as e:
        logger.error(f"Error getting sync status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get synchronization status: {str(e)}"
        )


@router.post("/sync/users")
async def sync_all_users_to_keycloak(
    current_user: dict = Depends(require_admin_root)
) -> Dict[str, Any]:
    """
    Synchronize all users from MuniStream to Keycloak.
    This is a potentially long-running operation.
    """
    try:
        logger.info(f"Starting full user sync to Keycloak, requested by {current_user.get('email')}")

        sync_results = await keycloak_sync_service.sync_all_users()

        logger.info(f"User sync completed: {sync_results}")

        return {
            "success": True,
            "operation": "sync_users_to_keycloak",
            "timestamp": datetime.utcnow().isoformat(),
            "requested_by": current_user.get("email"),
            "results": sync_results,
            "message": f"Synced {sync_results['success']} users successfully, {sync_results['failed']} failed"
        }

    except Exception as e:
        logger.error(f"Error syncing users to Keycloak: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to sync users to Keycloak: {str(e)}"
        )


@router.post("/sync/teams")
async def sync_all_teams_to_keycloak(
    current_user: dict = Depends(require_admin_root)
) -> Dict[str, Any]:
    """
    Synchronize all teams from MuniStream to Keycloak groups.
    This operation creates/updates groups in Keycloak.
    """
    try:
        logger.info(f"Starting full team sync to Keycloak, requested by {current_user.get('email')}")

        sync_results = await keycloak_sync_service.sync_all_teams()

        logger.info(f"Team sync completed: {sync_results}")

        return {
            "success": True,
            "operation": "sync_teams_to_keycloak",
            "timestamp": datetime.utcnow().isoformat(),
            "requested_by": current_user.get("email"),
            "results": sync_results,
            "message": f"Synced {sync_results['success']} teams successfully, {sync_results['failed']} failed"
        }

    except Exception as e:
        logger.error(f"Error syncing teams to Keycloak: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to sync teams to Keycloak: {str(e)}"
        )


@router.post("/sync/from-keycloak")
async def import_from_keycloak(
    current_user: dict = Depends(require_admin_root)
) -> Dict[str, Any]:
    """
    Import users and groups from Keycloak to MuniStream.
    This operation creates/updates entities in MuniStream based on Keycloak data.
    """
    try:
        logger.info(f"Starting import from Keycloak, requested by {current_user.get('email')}")

        import_results = await keycloak_sync_service.import_from_keycloak()

        logger.info(f"Keycloak import completed: {import_results}")

        return {
            "success": True,
            "operation": "import_from_keycloak",
            "timestamp": datetime.utcnow().isoformat(),
            "requested_by": current_user.get("email"),
            "results": import_results,
            "message": (
                f"Imported {import_results['users']['imported']} users, "
                f"updated {import_results['users']['updated']} users, "
                f"imported {import_results['groups']['imported']} groups, "
                f"updated {import_results['groups']['updated']} groups"
            )
        }

    except Exception as e:
        logger.error(f"Error importing from Keycloak: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to import from Keycloak: {str(e)}"
        )


@router.get("/keycloak/stats")
async def get_keycloak_stats(
    current_user: dict = Depends(require_admin_root)
) -> Dict[str, Any]:
    """
    Get Keycloak statistics in real-time.
    Returns user counts by role, group information, and role statistics.
    """
    try:
        logger.info(f"Getting Keycloak stats, requested by {current_user.get('email')}")

        # Get all users from Keycloak
        keycloak_users = await keycloak_sync_service.get_all_keycloak_users()

        # Count users by role and status
        user_stats = {
            "total": len(keycloak_users),
            "active": 0,
            "inactive": 0,
            "byRole": {}
        }

        for kc_user in keycloak_users:
            # Count active/inactive users
            if kc_user.get('enabled', False):
                user_stats["active"] += 1
            else:
                user_stats["inactive"] += 1

            # Count by role
            realm_roles = kc_user.get('realmRoles', [])
            user_role = 'citizen'  # default
            if 'admin' in realm_roles:
                user_role = 'admin'
            elif 'manager' in realm_roles:
                user_role = 'manager'
            elif 'reviewer' in realm_roles:
                user_role = 'reviewer'
            elif 'approver' in realm_roles:
                user_role = 'approver'
            elif 'viewer' in realm_roles:
                user_role = 'viewer'

            user_stats["byRole"][user_role] = user_stats["byRole"].get(user_role, 0) + 1

        # Get groups from Keycloak
        keycloak_groups = await keycloak_sync_service.get_all_keycloak_groups()

        group_stats = {
            "total": len(keycloak_groups),
            "list": []
        }

        for group in keycloak_groups:
            group_info = {
                "name": group.get('name', ''),
                "memberCount": len(group.get('members', [])),
                "description": group.get('attributes', {}).get('description', [''])[0] if group.get('attributes', {}).get('description') else None
            }
            group_stats["list"].append(group_info)

        # Get realm roles
        realm_roles = await keycloak_sync_service.get_all_keycloak_roles()

        role_stats = {
            "total": len(realm_roles),
            "list": []
        }

        for role in realm_roles:
            # Count users with this role
            users_with_role = sum(1 for user in keycloak_users if role['name'] in user.get('realmRoles', []))
            role_info = {
                "name": role.get('name', ''),
                "userCount": users_with_role,
                "description": role.get('description', '')
            }
            role_stats["list"].append(role_info)

        return {
            "users": user_stats,
            "groups": group_stats,
            "roles": role_stats,
            "timestamp": datetime.utcnow().isoformat(),
            "requested_by": current_user.get("email")
        }

    except Exception as e:
        logger.error(f"Error getting Keycloak stats: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get Keycloak statistics: {str(e)}"
        )


@router.post("/sync/full")
async def perform_full_sync(
    current_user: dict = Depends(require_admin_root)
) -> Dict[str, Any]:
    """
    Perform a complete bidirectional synchronization.
    First syncs MuniStream data to Keycloak, then imports any missing data back.
    """
    try:
        logger.info(f"Starting full bidirectional sync, requested by {current_user.get('email')}")

        results = {
            "users_to_keycloak": {"success": 0, "failed": 0, "total": 0},
            "teams_to_keycloak": {"success": 0, "failed": 0, "total": 0},
            "import_from_keycloak": {
                "users": {"imported": 0, "updated": 0, "failed": 0},
                "groups": {"imported": 0, "updated": 0, "failed": 0}
            }
        }

        # Step 1: Sync users to Keycloak
        logger.info("Step 1: Syncing users to Keycloak")
        user_sync_results = await keycloak_sync_service.sync_all_users()
        results["users_to_keycloak"] = user_sync_results

        # Step 2: Sync teams to Keycloak
        logger.info("Step 2: Syncing teams to Keycloak")
        team_sync_results = await keycloak_sync_service.sync_all_teams()
        results["teams_to_keycloak"] = team_sync_results

        # Step 3: Import from Keycloak (to catch any external changes)
        logger.info("Step 3: Importing from Keycloak")
        import_results = await keycloak_sync_service.import_from_keycloak()
        results["import_from_keycloak"] = import_results

        logger.info(f"Full sync completed: {results}")

        # Calculate summary
        total_operations = (
            user_sync_results["success"] + user_sync_results["failed"] +
            team_sync_results["success"] + team_sync_results["failed"] +
            import_results["users"]["imported"] + import_results["users"]["updated"] + import_results["users"]["failed"] +
            import_results["groups"]["imported"] + import_results["groups"]["updated"] + import_results["groups"]["failed"]
        )

        total_successful = (
            user_sync_results["success"] + team_sync_results["success"] +
            import_results["users"]["imported"] + import_results["users"]["updated"] +
            import_results["groups"]["imported"] + import_results["groups"]["updated"]
        )

        return {
            "success": True,
            "operation": "full_bidirectional_sync",
            "timestamp": datetime.utcnow().isoformat(),
            "requested_by": current_user.get("email"),
            "results": results,
            "summary": {
                "total_operations": total_operations,
                "successful_operations": total_successful,
                "success_rate": (total_successful / total_operations * 100) if total_operations > 0 else 0
            },
            "message": f"Full sync completed: {total_successful}/{total_operations} operations successful"
        }

    except Exception as e:
        logger.error(f"Error performing full sync: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to perform full synchronization: {str(e)}"
        )