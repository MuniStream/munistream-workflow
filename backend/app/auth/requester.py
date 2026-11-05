"""
Who is calling, for resources that both portals reach with the same Keycloak
token: signed documents, entity files and signatures.

A citizen reaches only what they own. A staff member reaches an instance they
can view (access policy) and, with the VIEW capability, any entity of the
tenant: the same rule as the staff entity explorer (`GET /entities/`).

These routes used to take an optional legacy JWT: with a Keycloak token (or no
token at all) the requester was `None` and the ownership check was skipped,
so knowing an entity id -- printed on every QR code -- was enough to download
a citizen's document.
"""
from dataclasses import dataclass
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from .access_policy import VIEW, can, can_view, is_staff
from .provider import get_current_user, security_scheme


@dataclass
class Requester:
    """The authenticated caller. `customer` is set for citizens only."""
    user: Dict[str, Any]
    customer: Optional[Any] = None

    @property
    def is_staff(self) -> bool:
        return self.customer is None

    @property
    def owner_id(self) -> Optional[str]:
        """Id that instances (`user_id`) and entities (`owner_user_id`) store for a citizen."""
        return str(self.customer.id) if self.customer is not None else None


async def get_requester(
    credentials: HTTPAuthorizationCredentials = Depends(security_scheme),
) -> Requester:
    user = await get_current_user(credentials)
    if is_staff(user):
        return Requester(user=user)
    from ..api.endpoints.public_auth import get_current_customer
    customer = await get_current_customer(f"Bearer {credentials.credentials}")
    return Requester(user=user, customer=customer)


def can_access_instance(requester: Requester, instance) -> bool:
    if not requester.is_staff:
        owner = getattr(instance, "user_id", None)
        return bool(owner) and owner == requester.owner_id
    return can_view(requester.user, instance)


def can_access_entity(requester: Requester, entity) -> bool:
    owner = getattr(entity, "owner_user_id", None)
    if not requester.is_staff:
        return bool(owner) and owner == requester.owner_id
    return can(requester.user, VIEW)


async def require_entity_access(requester: Requester, entity) -> None:
    """404 rather than 403, so the response does not confirm that the id exists."""
    if entity is None or not can_access_entity(requester, entity):
        raise HTTPException(status_code=404, detail="Entity not found")


def require_instance_access(requester: Requester, instance) -> None:
    if instance is None:
        raise HTTPException(status_code=404, detail="Workflow instance not found")
    if not can_access_instance(requester, instance):
        raise HTTPException(status_code=403, detail="Access denied to this workflow instance")
