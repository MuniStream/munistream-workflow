"""
The client (Keycloak `azp`) a citizen request came through: the portal, or an
agent such as an assistant over WhatsApp acting with the person's token. It is
recorded next to what the citizen submitted so a case shows how each step was
filled in.

An agent reaches the backend through the MCP server, which exchanges the
person's token: the exchanged token's `azp` is the MCP server
(`munistream-mcp-<tenant>`), and the MCP names the agent in
`X-MuniStream-Agent`. The header is believed only on tokens of an MCP server;
anyone else's header is ignored.

Set by `get_current_customer` for the request being served; async
dependencies run in the request's context, so the endpoint reads it back.
"""
from contextvars import ContextVar
from typing import Optional

_current_client: ContextVar[Optional[str]] = ContextVar("citizen_request_client", default=None)


MCP_CLIENT_PREFIX = "munistream-mcp-"


def channel_of(azp: Optional[str], agent_header: Optional[str]) -> Optional[str]:
    """The client to record: the agent behind an MCP server, or the token's own client."""
    # Called directly (not as a dependency), the header argument is FastAPI's default marker, not a string.
    if azp and azp.startswith(MCP_CLIENT_PREFIX) and isinstance(agent_header, str) and 0 < len(agent_header) <= 100:
        return agent_header.strip()
    return azp or None


def set_request_client(client_id: Optional[str]) -> None:
    _current_client.set(client_id or None)


def request_client() -> Optional[str]:
    return _current_client.get()
