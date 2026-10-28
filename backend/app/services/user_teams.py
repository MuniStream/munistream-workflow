"""
Teams the requesting staff member belongs to.

Keycloak is the source of truth: a staff member belongs to a team when they are
in that team's group, and the admin portal token carries their groups in the
`groups` claim (a "Group Membership" mapper on the client). Adding someone to the
group is all it takes; nothing has to be replicated by hand.

Groups are named after `TeamModel.name` (that is how `sync_team_to_keycloak_group`
creates them), while instances are assigned by `team_id`, so group names are
translated through the teams collection. A group named exactly like the
`team_id` is accepted as well.

Membership stored in Mongo (`TeamModel.members`), which the team administration
endpoints edit, is honoured too.
"""
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..auth.access_policy import SYSTEM_ROLES

_CACHE_SECONDS = 60
_cache: Tuple[float, Dict[str, str]] = (0.0, {})


async def _group_to_team_map() -> Dict[str, str]:
    """{lower-cased team name or team_id: team_id} for active teams, cached."""
    global _cache
    now = time.monotonic()
    if now - _cache[0] < _CACHE_SECONDS and _cache[1]:
        return _cache[1]
    from ..models.team import TeamModel

    mapping: Dict[str, str] = {}
    for team in await TeamModel.find({"is_active": True}).to_list():
        mapping[team.team_id.lower()] = team.team_id
        if team.name:
            mapping[team.name.strip().lower()] = team.team_id
    _cache = (now, mapping)
    return mapping


def invalidate_cache() -> None:
    global _cache
    _cache = (0.0, {})


def _group_names(groups: Optional[Iterable[str]]) -> List[str]:
    # With full.path enabled the claim carries "/Group/Subgroup"; keep the last segment.
    return [g.rstrip("/").split("/")[-1].strip().lower() for g in (groups or []) if g]


async def teams_from_groups(groups: Optional[Iterable[str]]) -> List[str]:
    names = _group_names(groups)
    if not names:
        return []
    mapping = await _group_to_team_map()
    return [mapping[name] for name in names if name in mapping]


async def teams_from_membership(current_user: Dict[str, Any]) -> List[str]:
    """
    Teams whose Mongo member list includes the user.

    Matching goes through the email because that is how the rest of the system
    links the Keycloak account to the internal user: members are stored with the
    internal Mongo id, not with the token `sub`. The `sub` is accepted as well.
    """
    from ..models.team import TeamModel
    from ..models.user import UserModel

    identities = set()
    sub = current_user.get("sub")
    if sub:
        identities.add(sub)

    email = current_user.get("email")
    if email:
        internal = await UserModel.find_one(UserModel.email == email)
        if internal:
            identities.add(str(internal.id))
            if getattr(internal, "user_id", None):
                identities.add(str(internal.user_id))

    if not identities:
        return []

    teams = await TeamModel.find(
        {"is_active": True, "members.user_id": {"$in": list(identities)}}
    ).to_list()
    return [
        team.team_id
        for team in teams
        if any(m.user_id in identities and m.is_active for m in (team.members or []))
    ]


async def get_user_teams(current_user: Dict[str, Any]) -> List[str]:
    """Union of the teams from Keycloak groups and from Mongo membership."""
    groups = (current_user.get("token_claims") or {}).get("groups")
    result: List[str] = []
    for team in await teams_from_groups(groups) + await teams_from_membership(current_user):
        if team not in result:
            result.append(team)
    return result


async def _team_group_id(team_id: str) -> Optional[str]:
    """Id of the Keycloak group of a team (named after the team name or team_id)."""
    from urllib.parse import quote

    from ..models.team import TeamModel
    from .keycloak_sync import keycloak_sync_service

    team = await TeamModel.find_one(TeamModel.team_id == team_id)
    names = [n for n in {team_id, getattr(team, "name", None)} if n]
    for name in names:
        status, groups = await keycloak_sync_service._make_admin_request(
            "GET", f"/groups?search={quote(name)}&exact=true&briefRepresentation=true"
        )
        if status != 200 or not isinstance(groups, list):
            continue
        for group in groups:
            if (group.get("name") or "").strip().lower() == name.strip().lower():
                return group.get("id")
    return None


async def team_members(team_id: str) -> List[Dict[str, Any]]:
    """
    Members of a team's Keycloak group, as assignable users.

    `id` is the Keycloak user id, i.e. the `sub` of their token, which is what
    `assigned_user_id` must hold for the assignee to see the instance.
    """
    from .keycloak_sync import keycloak_sync_service

    group_id = await _team_group_id(team_id)
    if not group_id:
        return []
    status, members = await keycloak_sync_service._make_admin_request(
        "GET", f"/groups/{group_id}/members?max=1000&briefRepresentation=true"
    )
    if status != 200 or not isinstance(members, list):
        return []

    result: List[Dict[str, Any]] = []
    for member in members:
        if not member.get("enabled", True):
            continue
        status, roles = await keycloak_sync_service._make_admin_request(
            "GET", f"/users/{member['id']}/role-mappings/realm"
        )
        role_names = [r.get("name") for r in roles] if status == 200 and isinstance(roles, list) else []
        full_name = " ".join(p for p in [member.get("firstName"), member.get("lastName")] if p)
        result.append({
            "id": member["id"],
            "username": member.get("username"),
            "email": member.get("email"),
            "name": full_name or member.get("email") or member.get("username"),
            "roles": [r for r in role_names if r in SYSTEM_ROLES],
        })
    return result
