"""
Access policy for the admin portal: who can see which workflow instance and what
each staff member is allowed to do with it.

This module is the single source of truth. Endpoints must not compare role names
by hand: they ask here through `can`, `can_act` or `require_capability`, and
instance listings are filtered with `instance_filter`.

Capability matrix (each role includes the previous one; admin also sees
everything):

    viewer    view the instances of their teams (read only)
    reviewer  + start, fill in and review (issue the review decision, request changes)
    approver  + final approval and signature
    manager   + assign and unassign within their teams
    admin     + administer (workflow definitions, catalogs, every instance)

A user with several roles gets the union of their capabilities; a user in several
teams sees the instances of all of them. Instances assigned directly to a user
are always visible to that user.
"""
from typing import Any, Dict, Iterable, List, Optional

from fastapi import HTTPException

VIEW = "view"
START = "start"
REVIEW = "review"
APPROVE = "approve"
ASSIGN = "assign"
ADMINISTER = "administer"

_VIEWER = {VIEW}
_REVIEWER = _VIEWER | {START, REVIEW}
_APPROVER = _REVIEWER | {APPROVE}
_MANAGER = _APPROVER | {ASSIGN}
_ADMIN = _MANAGER | {ADMINISTER}

ROLE_CAPABILITIES: Dict[str, frozenset] = {
    "viewer": frozenset(_VIEWER),
    "reviewer": frozenset(_REVIEWER),
    "approver": frozenset(_APPROVER),
    "manager": frozenset(_MANAGER),
    "admin": frozenset(_ADMIN),
}

# System roles: the ones that grant access to the admin portal.
SYSTEM_ROLES = tuple(ROLE_CAPABILITIES)

# Roles that, for a member of the assigned team, grant access to the instance.
# Reviewers and viewers are included, not only managers: a team queue that only
# managers can open is not a queue, and the reviewer is who actually decides.
TEAM_ACCESS_ROLES = SYSTEM_ROLES


def _roles(user: Dict[str, Any]) -> List[str]:
    return list(user.get("roles") or [])


def capabilities(user: Dict[str, Any]) -> frozenset:
    """Union of the capabilities of every role the user holds."""
    result = set()
    for role in _roles(user):
        result |= ROLE_CAPABILITIES.get(role, frozenset())
    return frozenset(result)


def is_admin(user: Dict[str, Any]) -> bool:
    return "admin" in _roles(user)


def is_staff(user: Dict[str, Any]) -> bool:
    """Holds at least one system role (i.e. is not a citizen)."""
    return any(role in ROLE_CAPABILITIES for role in _roles(user))


def can(user: Dict[str, Any], capability: str) -> bool:
    return capability in capabilities(user)


def user_teams(user: Dict[str, Any]) -> List[str]:
    return list(user.get("teams") or user.get("team_ids") or [])


def can_view_instance(instance, roles: Iterable[str], user_id: Optional[str],
                      teams: Iterable[str]) -> bool:
    """
    The access decision for one instance, kept apart from where the data comes
    from so it can be tested without a database.

    - admin: everything.
    - direct assignment: the assignee.
    - team assignment: members of that team holding a system role.
    """
    roles = list(roles or [])
    if "admin" in roles:
        return True

    if user_id and getattr(instance, "assigned_user_id", None) == user_id:
        return True

    team = getattr(instance, "assigned_team_id", None)
    if team and team in set(teams or []):
        return any(role in TEAM_ACCESS_ROLES for role in roles)

    return False


def can_view(user: Dict[str, Any], instance) -> bool:
    return can_view_instance(instance, _roles(user), user.get("sub"), user_teams(user))


def can_act(user: Dict[str, Any], instance, capability: str) -> bool:
    """Holds the capability and also has access to that particular instance."""
    return can(user, capability) and can_view(user, instance)


def require_capability(user: Dict[str, Any], capability: str, instance=None) -> None:
    """Raise 403 unless the user can perform `capability` (on `instance`, if given)."""
    if not can(user, capability):
        raise HTTPException(
            status_code=403,
            detail=f"Your role does not allow this action ({capability}).",
        )
    if instance is not None and not can_view(user, instance):
        raise HTTPException(
            status_code=403,
            detail="You can only work on instances assigned to you or to your team.",
        )


def instance_filter(user: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Mongo condition for staff instance listings.

    - admin: `{}` (no restriction).
    - staff: instances assigned to the user or to one of their teams.
    - no system role: `None` (the endpoint must answer 403).
    """
    if is_admin(user):
        return {}
    if not is_staff(user):
        return None
    conditions: List[Dict[str, Any]] = []
    if user.get("sub"):
        conditions.append({"assigned_user_id": user["sub"]})
    teams = user_teams(user)
    if teams:
        conditions.append({"assigned_team_id": {"$in": teams}})
    if not conditions:
        # No criteria at all: match nothing instead of everything.
        return {"_id": {"$exists": False}}
    return {"$or": conditions}


def require_instance_filter(user: Dict[str, Any]) -> Dict[str, Any]:
    """`instance_filter`, answering 403 to anyone who is not staff."""
    condition = instance_filter(user)
    if condition is None:
        raise HTTPException(status_code=403, detail="Staff access only.")
    return condition


def merge_filters(policy: Dict[str, Any], query: Dict[str, Any]) -> Dict[str, Any]:
    """Combine the policy condition with a listing query without clobbering keys."""
    if not policy:
        return query
    if not query:
        return dict(policy)
    return {"$and": [query, policy]}
