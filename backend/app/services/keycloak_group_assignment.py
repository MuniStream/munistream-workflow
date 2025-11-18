"""
Keycloak Group Assignment Service

Handles automatic assignment of workflow instances to users within Keycloak groups
based on roles and assignment strategies like round-robin.
"""

import logging
from typing import Dict, List, Optional, Any
from datetime import datetime
import hashlib
import json

from .keycloak_sync import keycloak_sync_service
from ..core.config import settings

logger = logging.getLogger(__name__)


class KeycloakGroupAssignmentService:
    """Service for assigning workflows to users from Keycloak groups"""

    def __init__(self):
        self.round_robin_cache = {}  # In-memory cache for round-robin state

    async def get_group_members_with_role(self, group_id: str, required_role: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get all members of a Keycloak group, optionally filtered by role.

        Args:
            group_id: Keycloak group ID or name
            required_role: Required role (e.g., 'reviewer', 'approver'). If None, returns all members.

        Returns:
            List of users with their details
        """
        try:
            logger.info(f"Getting group members for group: {group_id}, required_role: {required_role}")

            # Get all groups to find the target group
            groups = await keycloak_sync_service.get_all_keycloak_groups()
            target_group = None

            # Find group by ID or name
            for group in groups:
                if group.get('id') == group_id or group.get('name') == group_id:
                    target_group = group
                    break

            if not target_group:
                logger.warning(f"Group not found: {group_id}")
                return []

            group_members = target_group.get('members', [])
            if not group_members:
                logger.info(f"No members found in group: {group_id}")
                return []

            # Filter by role if specified
            if required_role:
                filtered_members = []
                for member in group_members:
                    member_roles = member.get('realmRoles', [])
                    if required_role in member_roles:
                        filtered_members.append(member)

                logger.info(f"Found {len(filtered_members)} members with role '{required_role}' in group '{group_id}'")
                return filtered_members
            else:
                logger.info(f"Found {len(group_members)} total members in group '{group_id}'")
                return group_members

        except Exception as e:
            logger.error(f"Error getting group members: {e}")
            return []

    async def get_next_assignee_round_robin(
        self,
        group_id: str,
        required_role: Optional[str] = None,
        workflow_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Get the next user to assign using round-robin strategy.

        Args:
            group_id: Keycloak group ID or name
            required_role: Required role for assignee
            workflow_id: Workflow ID for tracking round-robin state

        Returns:
            Selected user details or None if no suitable user found
        """
        try:
            # Get eligible members
            members = await self.get_group_members_with_role(group_id, required_role)

            if not members:
                logger.warning(f"No eligible members found for group: {group_id}, role: {required_role}")
                return None

            # Create cache key for round-robin state
            cache_key = self._get_round_robin_cache_key(group_id, required_role, workflow_id)

            # Get current round-robin index
            current_index = self.round_robin_cache.get(cache_key, 0)

            # Ensure index is within bounds (in case group membership changed)
            if current_index >= len(members):
                current_index = 0

            # Select user
            selected_user = members[current_index]

            # Update round-robin index for next assignment
            next_index = (current_index + 1) % len(members)
            self.round_robin_cache[cache_key] = next_index

            logger.info(f"Round-robin selected user: {selected_user.get('username', 'unknown')} "
                       f"(index {current_index} of {len(members)}) for group: {group_id}")

            return selected_user

        except Exception as e:
            logger.error(f"Error in round-robin assignment: {e}")
            return None

    async def assign_to_user_from_group(
        self,
        group_id: str,
        required_role: Optional[str] = None,
        workflow_id: Optional[str] = None,
        assignment_strategy: str = "round_robin"
    ) -> Optional[str]:
        """
        Assign workflow to a user from a Keycloak group using specified strategy.

        Args:
            group_id: Keycloak group ID or name
            required_role: Required role for assignee
            workflow_id: Workflow ID for context
            assignment_strategy: Assignment strategy (currently only round_robin supported)

        Returns:
            Selected user ID (email or username) or None
        """
        try:
            logger.info(f"Assigning from group: {group_id}, role: {required_role}, strategy: {assignment_strategy}")

            if assignment_strategy == "round_robin":
                selected_user = await self.get_next_assignee_round_robin(
                    group_id, required_role, workflow_id
                )
            else:
                logger.warning(f"Unsupported assignment strategy: {assignment_strategy}, falling back to round_robin")
                selected_user = await self.get_next_assignee_round_robin(
                    group_id, required_role, workflow_id
                )

            if selected_user:
                # Use Keycloak user ID (id) instead of email for consistency with JWT tokens
                user_id = selected_user.get('id') or selected_user.get('email') or selected_user.get('username')
                user_email = selected_user.get('email', 'unknown')
                logger.info(f"Successfully assigned to user ID: {user_id} (email: {user_email})")
                return user_id
            else:
                logger.warning(f"No suitable user found for assignment")
                return None

        except Exception as e:
            logger.error(f"Error in group assignment: {e}")
            return None

    def _get_round_robin_cache_key(
        self,
        group_id: str,
        required_role: Optional[str],
        workflow_id: Optional[str]
    ) -> str:
        """
        Generate cache key for round-robin state tracking.

        Args:
            group_id: Keycloak group ID
            required_role: Required role
            workflow_id: Workflow ID

        Returns:
            Cache key string
        """
        key_parts = [
            f"group:{group_id}",
            f"role:{required_role or 'any'}",
            f"workflow:{workflow_id or 'any'}"
        ]
        key_string = "|".join(key_parts)

        # Create hash for consistent key length
        key_hash = hashlib.md5(key_string.encode()).hexdigest()[:16]

        return f"rr:{key_hash}"

    async def get_assignment_stats(self) -> Dict[str, Any]:
        """
        Get statistics about group-based assignments.

        Returns:
            Dictionary with assignment statistics
        """
        try:
            stats = {
                "round_robin_cache_entries": len(self.round_robin_cache),
                "active_group_assignments": list(self.round_robin_cache.keys()),
                "cache_state": dict(self.round_robin_cache)
            }

            return stats

        except Exception as e:
            logger.error(f"Error getting assignment stats: {e}")
            return {}

    def reset_round_robin_state(self, group_id: Optional[str] = None, workflow_id: Optional[str] = None):
        """
        Reset round-robin state for debugging or maintenance.

        Args:
            group_id: Specific group to reset (if None, resets all)
            workflow_id: Specific workflow to reset (if None, resets all)
        """
        try:
            if group_id is None and workflow_id is None:
                # Reset all
                self.round_robin_cache.clear()
                logger.info("Reset all round-robin state")
            else:
                # Reset specific entries
                keys_to_remove = []
                for key in self.round_robin_cache.keys():
                    # Simple string matching - could be improved with proper parsing
                    if group_id and f"group:{group_id}" in key:
                        keys_to_remove.append(key)
                    elif workflow_id and f"workflow:{workflow_id}" in key:
                        keys_to_remove.append(key)

                for key in keys_to_remove:
                    del self.round_robin_cache[key]

                logger.info(f"Reset round-robin state for {len(keys_to_remove)} entries")

        except Exception as e:
            logger.error(f"Error resetting round-robin state: {e}")


# Global service instance
keycloak_group_assignment_service = KeycloakGroupAssignmentService()