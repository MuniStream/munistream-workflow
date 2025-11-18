"""
Keycloak synchronization service for users, teams, and roles
"""
import logging
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime
import asyncio
import httpx
from ..models.user import UserModel, UserRole
from ..models.team import TeamModel
from ..auth.provider import keycloak
from ..core.config import settings
import os

logger = logging.getLogger(__name__)


class KeycloakSyncService:
    """Service for synchronizing data between MuniStream and Keycloak"""

    def __init__(self):
        """Initialize Keycloak sync service"""
        self.keycloak_url = os.getenv("KEYCLOAK_URL", "http://localhost:8180").rstrip('/')
        self.realm = os.getenv("KEYCLOAK_REALM", "munistream")
        self.admin_username = os.getenv("KEYCLOAK_ADMIN_USER", "admin")
        self.admin_password = os.getenv("KEYCLOAK_ADMIN_PASSWORD", "admin")

        # Admin client credentials
        self.admin_client_id = os.getenv("KEYCLOAK_ADMIN_CLIENT_ID", "admin-cli")
        self.admin_client_secret = os.getenv("KEYCLOAK_ADMIN_CLIENT_SECRET")

        # Build admin endpoints
        self.admin_realm_url = f"{self.keycloak_url}/admin/realms/{self.realm}"
        self.admin_token_url = f"{self.keycloak_url}/realms/master/protocol/openid-connect/token"

        # Cache for admin token
        self._admin_token = None
        self._admin_token_expires = None

        logger.info(f"KeycloakSyncService initialized for realm: {self.realm}")

    async def _get_admin_token(self) -> str:
        """Get admin access token for Keycloak management"""
        now = datetime.utcnow()

        # Check if token is still valid
        if (self._admin_token and self._admin_token_expires and
            now.timestamp() < self._admin_token_expires):
            return self._admin_token

        # Get new admin token
        async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
            logger.info("Getting new Keycloak admin token")

            token_data = {
                "grant_type": "password",
                "client_id": self.admin_client_id,
                "username": self.admin_username,
                "password": self.admin_password
            }

            if self.admin_client_secret:
                token_data["client_secret"] = self.admin_client_secret

            response = await client.post(
                self.admin_token_url,
                data=token_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )

            if response.status_code != 200:
                logger.error(f"Failed to get admin token: {response.status_code} - {response.text}")
                raise Exception(f"Failed to get Keycloak admin token: {response.status_code}")

            token_info = response.json()
            self._admin_token = token_info["access_token"]

            # Set expiration (subtract 60 seconds for safety)
            expires_in = token_info.get("expires_in", 300)
            self._admin_token_expires = now.timestamp() + expires_in - 60

            logger.info("Admin token obtained successfully")
            return self._admin_token

    async def _make_admin_request(self, method: str, endpoint: str, data: Optional[Dict] = None) -> Tuple[int, Dict]:
        """Make authenticated request to Keycloak admin API"""
        token = await self._get_admin_token()
        url = f"{self.admin_realm_url}{endpoint}"

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
            if method.upper() == "GET":
                response = await client.get(url, headers=headers)
            elif method.upper() == "POST":
                response = await client.post(url, headers=headers, json=data or {})
            elif method.upper() == "PUT":
                response = await client.put(url, headers=headers, json=data or {})
            elif method.upper() == "DELETE":
                response = await client.delete(url, headers=headers)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            try:
                result_data = response.json() if response.content else {}
            except:
                result_data = {}

            return response.status_code, result_data

    async def sync_user_to_keycloak(self, user: UserModel) -> bool:
        """Sync a user from MuniStream to Keycloak"""
        try:
            logger.info(f"Syncing user {user.email} to Keycloak")

            # Check if user already exists
            status_code, existing_users = await self._make_admin_request(
                "GET", f"/users?email={user.email}"
            )

            if status_code != 200:
                logger.error(f"Failed to check existing user: {status_code}")
                return False

            keycloak_user_data = {
                "username": user.username,
                "email": user.email,
                "emailVerified": True,
                "enabled": user.status.value == "active",
                "firstName": user.full_name.split(" ")[0] if user.full_name else "",
                "lastName": " ".join(user.full_name.split(" ")[1:]) if user.full_name and " " in user.full_name else "",
                "attributes": {
                    "department": [user.department] if user.department else [],
                    "phone": [user.phone] if user.phone else [],
                    "munistream_user_id": [str(user.id)],
                    "role": [user.role.value]
                }
            }

            if existing_users and len(existing_users) > 0:
                # Update existing user
                user_id = existing_users[0]["id"]
                status_code, result = await self._make_admin_request(
                    "PUT", f"/users/{user_id}", keycloak_user_data
                )

                if status_code == 204:
                    logger.info(f"User {user.email} updated in Keycloak")
                    await self._sync_user_roles_to_keycloak(user_id, user.role)
                    return True
                else:
                    logger.error(f"Failed to update user in Keycloak: {status_code}")
                    return False
            else:
                # Create new user
                status_code, result = await self._make_admin_request(
                    "POST", "/users", keycloak_user_data
                )

                if status_code == 201:
                    logger.info(f"User {user.email} created in Keycloak")

                    # Get the created user ID and sync roles
                    status_code, new_users = await self._make_admin_request(
                        "GET", f"/users?email={user.email}"
                    )

                    if status_code == 200 and new_users:
                        user_id = new_users[0]["id"]
                        await self._sync_user_roles_to_keycloak(user_id, user.role)

                    return True
                else:
                    logger.error(f"Failed to create user in Keycloak: {status_code} - {result}")
                    return False

        except Exception as e:
            logger.error(f"Error syncing user to Keycloak: {e}")
            return False

    async def _sync_user_roles_to_keycloak(self, keycloak_user_id: str, role: UserRole) -> bool:
        """Sync user roles to Keycloak"""
        try:
            # Get available roles in Keycloak
            status_code, realm_roles = await self._make_admin_request("GET", "/roles")

            if status_code != 200:
                logger.error(f"Failed to get realm roles: {status_code}")
                return False

            # Map MuniStream roles to Keycloak roles
            role_mapping = {
                UserRole.ADMIN: "admin",
                UserRole.MANAGER: "manager",
                UserRole.REVIEWER: "reviewer",
                UserRole.APPROVER: "approver",
                UserRole.VIEWER: "viewer"
            }

            keycloak_role_name = role_mapping.get(role, "viewer")

            # Find the role in available roles
            target_role = None
            for realm_role in realm_roles:
                if realm_role["name"] == keycloak_role_name:
                    target_role = realm_role
                    break

            if not target_role:
                logger.warning(f"Role {keycloak_role_name} not found in Keycloak realm")
                return False

            # Assign role to user
            status_code, result = await self._make_admin_request(
                "POST",
                f"/users/{keycloak_user_id}/role-mappings/realm",
                [target_role]
            )

            if status_code == 204:
                logger.info(f"Role {keycloak_role_name} assigned to user in Keycloak")
                return True
            else:
                logger.error(f"Failed to assign role: {status_code}")
                return False

        except Exception as e:
            logger.error(f"Error syncing user roles: {e}")
            return False

    async def sync_team_to_keycloak_group(self, team: TeamModel) -> bool:
        """Sync a team from MuniStream to Keycloak group"""
        try:
            logger.info(f"Syncing team {team.name} to Keycloak group")

            # Check if group already exists
            status_code, existing_groups = await self._make_admin_request(
                "GET", f"/groups?search={team.name}"
            )

            if status_code != 200:
                logger.error(f"Failed to check existing group: {status_code}")
                return False

            group_data = {
                "name": team.name,
                "path": f"/{team.name}",
                "attributes": {
                    "department": [team.department] if team.department else [],
                    "munistream_team_id": [team.team_id],
                    "specializations": team.specializations,
                    "max_concurrent_tasks": [str(team.max_concurrent_tasks)]
                }
            }

            # Check if group exists by name
            existing_group = None
            for group in existing_groups:
                if group["name"] == team.name:
                    existing_group = group
                    break

            if existing_group:
                # Update existing group
                group_id = existing_group["id"]
                status_code, result = await self._make_admin_request(
                    "PUT", f"/groups/{group_id}", group_data
                )

                if status_code == 204:
                    logger.info(f"Group {team.name} updated in Keycloak")
                    return True
                else:
                    logger.error(f"Failed to update group: {status_code}")
                    return False
            else:
                # Create new group
                status_code, result = await self._make_admin_request(
                    "POST", "/groups", group_data
                )

                if status_code == 201:
                    logger.info(f"Group {team.name} created in Keycloak")
                    return True
                else:
                    logger.error(f"Failed to create group: {status_code} - {result}")
                    return False

        except Exception as e:
            logger.error(f"Error syncing team to Keycloak: {e}")
            return False

    async def sync_user_to_team_groups(self, user: UserModel) -> bool:
        """Sync user's team memberships to Keycloak groups"""
        try:
            logger.info(f"Syncing user {user.email} team memberships to Keycloak groups")

            # Get user from Keycloak
            status_code, keycloak_users = await self._make_admin_request(
                "GET", f"/users?email={user.email}"
            )

            if status_code != 200 or not keycloak_users:
                logger.error(f"User {user.email} not found in Keycloak")
                return False

            keycloak_user_id = keycloak_users[0]["id"]

            # Get user's teams
            user_teams = await TeamModel.find(
                {"members.user_id": str(user.id), "members.is_active": True}
            ).to_list()

            for team in user_teams:
                # Find corresponding group in Keycloak
                status_code, groups = await self._make_admin_request(
                    "GET", f"/groups?search={team.name}"
                )

                if status_code == 200 and groups:
                    for group in groups:
                        if group["name"] == team.name:
                            group_id = group["id"]

                            # Add user to group
                            status_code, result = await self._make_admin_request(
                                "PUT", f"/users/{keycloak_user_id}/groups/{group_id}"
                            )

                            if status_code == 204:
                                logger.info(f"User {user.email} added to group {team.name}")
                            else:
                                logger.warning(f"Failed to add user to group {team.name}: {status_code}")
                            break

            return True

        except Exception as e:
            logger.error(f"Error syncing user team memberships: {e}")
            return False

    async def sync_all_users(self) -> Dict[str, int]:
        """Sync all users from MuniStream to Keycloak"""
        logger.info("Starting full user sync to Keycloak")

        results = {"success": 0, "failed": 0, "total": 0}

        users = await UserModel.find_all().to_list()
        results["total"] = len(users)

        for user in users:
            try:
                success = await self.sync_user_to_keycloak(user)
                if success:
                    await self.sync_user_to_team_groups(user)
                    results["success"] += 1
                else:
                    results["failed"] += 1
            except Exception as e:
                logger.error(f"Error syncing user {user.email}: {e}")
                results["failed"] += 1

        logger.info(f"User sync completed: {results}")
        return results

    async def sync_all_teams(self) -> Dict[str, int]:
        """Sync all teams from MuniStream to Keycloak groups"""
        logger.info("Starting full team sync to Keycloak")

        results = {"success": 0, "failed": 0, "total": 0}

        teams = await TeamModel.find_all().to_list()
        results["total"] = len(teams)

        for team in teams:
            try:
                success = await self.sync_team_to_keycloak_group(team)
                if success:
                    results["success"] += 1
                else:
                    results["failed"] += 1
            except Exception as e:
                logger.error(f"Error syncing team {team.name}: {e}")
                results["failed"] += 1

        logger.info(f"Team sync completed: {results}")
        return results

    async def import_from_keycloak(self) -> Dict[str, Any]:
        """Import users and groups from Keycloak to MuniStream"""
        logger.info("Starting import from Keycloak")

        results = {
            "users": {"imported": 0, "updated": 0, "failed": 0},
            "groups": {"imported": 0, "updated": 0, "failed": 0}
        }

        try:
            # Import users
            status_code, keycloak_users = await self._make_admin_request("GET", "/users")

            if status_code == 200:
                for kc_user in keycloak_users:
                    try:
                        await self._import_user_from_keycloak(kc_user, results)
                    except Exception as e:
                        logger.error(f"Error importing user {kc_user.get('email', 'unknown')}: {e}")
                        results["users"]["failed"] += 1

            # Import groups
            status_code, keycloak_groups = await self._make_admin_request("GET", "/groups")

            if status_code == 200:
                for kc_group in keycloak_groups:
                    try:
                        await self._import_group_from_keycloak(kc_group, results)
                    except Exception as e:
                        logger.error(f"Error importing group {kc_group.get('name', 'unknown')}: {e}")
                        results["groups"]["failed"] += 1

        except Exception as e:
            logger.error(f"Error during Keycloak import: {e}")

        logger.info(f"Keycloak import completed: {results}")
        return results

    async def _import_user_from_keycloak(self, kc_user: Dict, results: Dict):
        """Import a single user from Keycloak"""
        if not kc_user.get("email"):
            return

        # Check if user already exists
        existing_user = await UserModel.find_one(UserModel.email == kc_user["email"])

        # Map Keycloak attributes to MuniStream user
        user_data = {
            "email": kc_user["email"],
            "username": kc_user.get("username", kc_user["email"]),
            "full_name": f"{kc_user.get('firstName', '')} {kc_user.get('lastName', '')}".strip(),
            "status": "active" if kc_user.get("enabled", True) else "inactive"
        }

        # Extract custom attributes
        attributes = kc_user.get("attributes", {})
        if "department" in attributes and attributes["department"]:
            user_data["department"] = attributes["department"][0]
        if "phone" in attributes and attributes["phone"]:
            user_data["phone"] = attributes["phone"][0]
        if "role" in attributes and attributes["role"]:
            try:
                user_data["role"] = UserRole(attributes["role"][0])
            except ValueError:
                user_data["role"] = UserRole.VIEWER

        if existing_user:
            # Update existing user
            for key, value in user_data.items():
                if hasattr(existing_user, key):
                    setattr(existing_user, key, value)
            existing_user.updated_at = datetime.utcnow()
            await existing_user.save()
            results["users"]["updated"] += 1
        else:
            # Create new user
            new_user = UserModel(**user_data)
            await new_user.save()
            results["users"]["imported"] += 1

    async def _import_group_from_keycloak(self, kc_group: Dict, results: Dict):
        """Import a single group from Keycloak as a team"""
        if not kc_group.get("name"):
            return

        # Check if team already exists
        existing_team = await TeamModel.find_one(TeamModel.name == kc_group["name"])

        # Map Keycloak group to MuniStream team
        team_data = {
            "name": kc_group["name"],
            "team_id": kc_group["name"].lower().replace(" ", "_"),
        }

        # Extract custom attributes
        attributes = kc_group.get("attributes", {})
        if "department" in attributes and attributes["department"]:
            team_data["department"] = attributes["department"][0]
        if "specializations" in attributes:
            team_data["specializations"] = attributes["specializations"]
        if "max_concurrent_tasks" in attributes and attributes["max_concurrent_tasks"]:
            try:
                team_data["max_concurrent_tasks"] = int(attributes["max_concurrent_tasks"][0])
            except (ValueError, IndexError):
                pass

        if existing_team:
            # Update existing team
            for key, value in team_data.items():
                if hasattr(existing_team, key):
                    setattr(existing_team, key, value)
            existing_team.updated_at = datetime.utcnow()
            await existing_team.save()
            results["groups"]["updated"] += 1
        else:
            # Create new team
            new_team = TeamModel(**team_data)
            await new_team.save()
            results["groups"]["imported"] += 1

    async def get_sync_status(self) -> Dict[str, Any]:
        """Get synchronization status and statistics"""
        try:
            # Get local counts
            local_users_count = await UserModel.count()
            local_teams_count = await TeamModel.count()

            # Get Keycloak counts
            status_code, kc_users = await self._make_admin_request("GET", "/users")
            kc_users_count = len(kc_users) if status_code == 200 else 0

            status_code, kc_groups = await self._make_admin_request("GET", "/groups")
            kc_groups_count = len(kc_groups) if status_code == 200 else 0

            return {
                "status": "connected",
                "last_sync": datetime.utcnow().isoformat(),
                "local_counts": {
                    "users": local_users_count,
                    "teams": local_teams_count
                },
                "keycloak_counts": {
                    "users": kc_users_count,
                    "groups": kc_groups_count
                },
                "sync_diff": {
                    "users": local_users_count - kc_users_count,
                    "teams": local_teams_count - kc_groups_count
                }
            }

        except Exception as e:
            logger.error(f"Error getting sync status: {e}")
            return {
                "status": "error",
                "error": str(e),
                "last_sync": None
            }

    async def get_all_keycloak_users(self) -> List[Dict[str, Any]]:
        """Get all users from Keycloak realm"""
        try:
            status_code, users_data = await self._make_admin_request("GET", "/users")

            if status_code == 200:
                return users_data if isinstance(users_data, list) else []
            else:
                logger.error(f"Failed to get users from Keycloak: HTTP {status_code}")
                return []

        except Exception as e:
            logger.error(f"Error getting users from Keycloak: {e}")
            return []

    async def get_all_keycloak_groups(self) -> List[Dict[str, Any]]:
        """Get all groups from Keycloak realm"""
        try:
            status_code, groups_data = await self._make_admin_request("GET", "/groups")

            if status_code == 200:
                groups = groups_data if isinstance(groups_data, list) else []

                # For each group, get member details with roles
                for group in groups:
                    group_id = group.get('id')
                    if group_id:
                        # Get group members with their roles
                        members_with_roles = await self.get_group_members_with_roles(group_id)
                        group['members'] = members_with_roles
                    else:
                        group['members'] = []

                return groups
            else:
                logger.error(f"Failed to get groups from Keycloak: HTTP {status_code}")
                return []

        except Exception as e:
            logger.error(f"Error getting groups from Keycloak: {e}")
            return []

    async def get_group_members_with_roles(self, group_id: str) -> List[Dict[str, Any]]:
        """Get group members with their realm roles"""
        try:
            # Get group members
            member_status, members_data = await self._make_admin_request("GET", f"/groups/{group_id}/members")

            if member_status != 200:
                logger.error(f"Failed to get group members: HTTP {member_status}")
                return []

            members = members_data if isinstance(members_data, list) else []
            members_with_roles = []

            # For each member, get their roles
            for member in members:
                user_id = member.get('id')
                if user_id:
                    # Get user's realm roles
                    roles_status, roles_data = await self._make_admin_request("GET", f"/users/{user_id}/role-mappings/realm")

                    if roles_status == 200:
                        user_roles = roles_data if isinstance(roles_data, list) else []
                        member['realmRoles'] = [role.get('name') for role in user_roles if role.get('name')]
                    else:
                        member['realmRoles'] = []

                    members_with_roles.append(member)

            return members_with_roles

        except Exception as e:
            logger.error(f"Error getting group members with roles for group {group_id}: {e}")
            return []

    async def get_all_keycloak_roles(self) -> List[Dict[str, Any]]:
        """Get all realm roles from Keycloak"""
        try:
            status_code, roles_data = await self._make_admin_request("GET", "/roles")

            if status_code == 200:
                return roles_data if isinstance(roles_data, list) else []
            else:
                logger.error(f"Failed to get roles from Keycloak: HTTP {status_code}")
                return []

        except Exception as e:
            logger.error(f"Error getting roles from Keycloak: {e}")
            return []


# Global service instance
keycloak_sync_service = KeycloakSyncService()