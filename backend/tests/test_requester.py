"""
Unit tests for `app.auth.requester`: who may reach a signed document, an entity
file or a signature through the routes both portals share. No database or
Keycloak is needed.
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

from app.auth.requester import (  # noqa: E402
    Requester, can_access_entity, can_access_instance, require_entity_access,
    require_instance_access,
)


def citizen(customer_id="c1"):
    return Requester(user={"sub": "kc-" + customer_id, "roles": ["default-roles-x"]},
                     customer=SimpleNamespace(id=customer_id))


def staff(*roles, sub="s1", teams=()):
    return Requester(user={"sub": sub, "roles": list(roles), "teams": list(teams)})


def instance(owner="c1", team=None, assignee=None):
    return SimpleNamespace(user_id=owner, assigned_team_id=team, assigned_user_id=assignee)


def entity(owner="c1"):
    return SimpleNamespace(owner_user_id=owner)


# --- instances ---------------------------------------------------------------

def test_citizen_reaches_only_own_instance():
    assert can_access_instance(citizen("c1"), instance(owner="c1"))
    assert not can_access_instance(citizen("c2"), instance(owner="c1"))


def test_instance_without_owner_is_nobodys():
    assert not can_access_instance(citizen("c1"), instance(owner=None))


def test_staff_follows_the_access_policy():
    assert can_access_instance(staff("reviewer", teams=["a"]), instance(team="a"))
    assert not can_access_instance(staff("manager", teams=["b"]), instance(team="a"))
    assert can_access_instance(staff("admin"), instance(team="z"))


def test_require_instance_access_errors():
    with pytest.raises(HTTPException) as missing:
        require_instance_access(citizen(), None)
    assert missing.value.status_code == 404
    with pytest.raises(HTTPException) as foreign:
        require_instance_access(citizen("c2"), instance(owner="c1"))
    assert foreign.value.status_code == 403


# --- entities ----------------------------------------------------------------

def test_citizen_reaches_only_own_entity():
    assert can_access_entity(citizen("c1"), entity(owner="c1"))
    assert not can_access_entity(citizen("c2"), entity(owner="c1"))
    assert not can_access_entity(citizen("c1"), entity(owner=None))


def test_staff_with_a_system_role_reaches_entities():
    assert can_access_entity(staff("viewer"), entity())
    assert can_access_entity(staff("admin"), entity())


def test_foreign_entity_is_reported_as_missing():
    for case in (None, entity(owner="c1")):
        with pytest.raises(HTTPException) as error:
            asyncio.run(require_entity_access(citizen("c2"), case))
        assert error.value.status_code == 404


def test_requester_kind():
    assert citizen().is_staff is False and citizen("c9").owner_id == "c9"
    assert staff("viewer").is_staff is True and staff("viewer").owner_id is None
