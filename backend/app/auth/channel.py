"""
The client (Keycloak `azp`) a citizen request came through: the portal, or an
agent such as an assistant over WhatsApp acting with the person's token. It is
recorded next to what the citizen submitted so a case shows how each step was
filled in.

Set by `get_current_customer` for the request being served; async
dependencies run in the request's context, so the endpoint reads it back.
"""
from contextvars import ContextVar
from typing import Optional

_current_client: ContextVar[Optional[str]] = ContextVar("citizen_request_client", default=None)


def set_request_client(client_id: Optional[str]) -> None:
    _current_client.set(client_id or None)


def request_client() -> Optional[str]:
    return _current_client.get()
