"""
Unit tests for the admin portal access policy (`app.auth.access_policy`), the
step capability used by `submit-data`, and the translation of Keycloak groups
into team ids. No database or Keycloak is needed.
"""
import asyncio
import os
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.auth import access_policy as policy  # noqa: E402
from app.auth.access_policy import (  # noqa: E402
    ADMINISTER, APPROVE, ASSIGN, REVIEW, START, VIEW,
)


def user(*roles, sub="u1", teams=()):
    return {"sub": sub, "roles": list(roles), "teams": list(teams)}


def instance(team=None, assignee=None):
    return SimpleNamespace(assigned_team_id=team, assigned_user_id=assignee)


# --- capability matrix -------------------------------------------------------

EXPECTED = {
    "viewer": {VIEW},
    "reviewer": {VIEW, START, REVIEW},
    "approver": {VIEW, START, REVIEW, APPROVE},
    "manager": {VIEW, START, REVIEW, APPROVE, ASSIGN},
    "admin": {VIEW, START, REVIEW, APPROVE, ASSIGN, ADMINISTER},
}


@pytest.mark.parametrize("role", sorted(EXPECTED))
def test_each_role_has_exactly_its_capabilities(role):
    assert policy.capabilities(user(role)) == EXPECTED[role]


def test_several_roles_get_the_union():
    assert policy.capabilities(user("viewer", "approver")) == EXPECTED["approver"]


def test_citizen_has_no_capabilities_and_is_not_staff():
    citizen = user("default-roles-conapesca-citizen", "offline_access")
    assert policy.capabilities(citizen) == frozenset()
    assert policy.is_staff(citizen) is False


# --- instance visibility -----------------------------------------------------

def test_team_member_sees_team_instance():
    assert policy.can_view(user("viewer", teams=["a"]), instance(team="a"))


def test_other_team_does_not_see_it():
    assert not policy.can_view(user("manager", teams=["b"]), instance(team="a"))


def test_direct_assignee_sees_it_without_team():
    assert policy.can_view(user("reviewer", sub="x"), instance(team="a", assignee="x"))


def test_admin_sees_everything():
    assert policy.can_view(user("admin"), instance(team="z"))


def test_team_membership_without_system_role_grants_nothing():
    assert not policy.can_view(user(teams=["a"]), instance(team="a"))


# --- acting on an instance ---------------------------------------------------

@pytest.mark.parametrize("role,capability,allowed", [
    ("viewer", START, False),
    ("viewer", REVIEW, False),
    ("reviewer", REVIEW, True),
    ("reviewer", APPROVE, False),
    ("approver", APPROVE, True),
    ("approver", ASSIGN, False),
    ("manager", ASSIGN, True),
    ("manager", ADMINISTER, False),
])
def test_can_act_on_own_team_instance(role, capability, allowed):
    assert policy.can_act(user(role, teams=["a"]), instance(team="a"), capability) is allowed


def test_capability_is_not_enough_outside_the_team():
    assert not policy.can_act(user("manager", teams=["b"]), instance(team="a"), ASSIGN)


def test_require_capability_raises_403():
    with pytest.raises(HTTPException) as role_error:
        policy.require_capability(user("viewer", teams=["a"]), REVIEW, instance(team="a"))
    assert role_error.value.status_code == 403
    with pytest.raises(HTTPException) as team_error:
        policy.require_capability(user("reviewer", teams=["b"]), REVIEW, instance(team="a"))
    assert team_error.value.status_code == 403


# --- listing filter ----------------------------------------------------------

def test_admin_filter_is_unrestricted():
    assert policy.instance_filter(user("admin")) == {}


def test_staff_filter_covers_direct_and_team_assignments():
    assert policy.instance_filter(user("reviewer", sub="x", teams=["a", "b"])) == {
        "$or": [{"assigned_user_id": "x"}, {"assigned_team_id": {"$in": ["a", "b"]}}]
    }


def test_citizen_filter_is_none_and_require_raises():
    assert policy.instance_filter(user("offline_access")) is None
    with pytest.raises(HTTPException):
        policy.require_instance_filter(user("offline_access"))


def test_staff_without_sub_or_teams_matches_nothing():
    assert policy.instance_filter({"roles": ["viewer"]}) == {"_id": {"$exists": False}}


def test_merge_filters_keeps_both_conditions():
    staff = {"$or": [{"assigned_user_id": "x"}]}
    query = {"$or": [{"instance_id": "1"}], "status": "running"}
    assert policy.merge_filters(staff, query) == {"$and": [query, staff]}
    assert policy.merge_filters({}, query) == query
    assert policy.merge_filters(staff, {}) == staff


# --- submit-data step capability ---------------------------------------------

def _dag_instance(task):
    return SimpleNamespace(dag=SimpleNamespace(tasks={"t": task}))


def test_signing_step_requires_approve():
    from app.api.endpoints.instances import capability_for_task

    class SignerOperator:  # same name as the real operator class
        pass

    class TenantSigner(SignerOperator):
        pass

    assert capability_for_task(_dag_instance(SignerOperator()), "t") == APPROVE
    assert capability_for_task(_dag_instance(TenantSigner()), "t") == APPROVE


def test_other_steps_require_review():
    from app.api.endpoints.instances import capability_for_task

    class UserInputOperator:
        pass

    assert capability_for_task(_dag_instance(UserInputOperator()), "t") == REVIEW
    assert capability_for_task(_dag_instance(UserInputOperator()), "missing") == REVIEW


# --- Keycloak groups -> team ids ---------------------------------------------

def test_groups_are_translated_by_team_name_or_id(monkeypatch):
    from app.services import user_teams

    async def fake_map():
        return {"imipas": "imipas", "validadores ambientales": "validadores_ambientales_conapesca",
                "validadores_ambientales_conapesca": "validadores_ambientales_conapesca"}

    monkeypatch.setattr(user_teams, "_group_to_team_map", fake_map)
    result = asyncio.run(user_teams.teams_from_groups(["/IMIPAS", "Validadores Ambientales", "unknown"]))
    assert result == ["imipas", "validadores_ambientales_conapesca"]


def test_no_groups_means_no_teams():
    from app.services import user_teams

    assert asyncio.run(user_teams.teams_from_groups(None)) == []
