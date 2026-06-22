"""
Generic post-authentication callback registry.

Plugins (or any module loaded at startup) may register async callbacks that run
on every authenticated citizen request, right after the Customer record has been
resolved/synced. The backend stays agnostic: it does not know what the callbacks
do, and registering none is a no-op.

A callback has the signature:

    async def callback(customer, payload, tenant_id) -> None

where `customer` is the resolved Customer document, `payload` is the verified
token claims dict, and `tenant_id` is the backend's configured tenant (may be
None). Exceptions raised by a callback are logged and swallowed so a failing
extension never breaks authentication.
"""
from typing import Any, Awaitable, Callable, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

PostAuthCallback = Callable[[Any, Dict[str, Any], Optional[str]], Awaitable[None]]

_post_auth_callbacks: List[PostAuthCallback] = []


def register_post_auth_callback(callback: PostAuthCallback) -> None:
    """Register a callback to run after a citizen is authenticated.

    Idempotent: registering the same callable twice is ignored so a plugin
    reload does not stack duplicates.
    """
    if callback not in _post_auth_callbacks:
        _post_auth_callbacks.append(callback)
        logger.info(
            "Registered post-auth callback: %s",
            getattr(callback, "__qualname__", repr(callback)),
        )


def clear_post_auth_callbacks() -> None:
    """Remove all registered callbacks (used by tests)."""
    _post_auth_callbacks.clear()


async def run_post_auth_callbacks(
    customer: Any,
    payload: Dict[str, Any],
    tenant_id: Optional[str] = None,
) -> None:
    """Run all registered post-auth callbacks, isolating failures."""
    for callback in _post_auth_callbacks:
        try:
            await callback(customer, payload, tenant_id)
        except Exception:
            logger.exception(
                "post-auth callback failed: %s",
                getattr(callback, "__qualname__", repr(callback)),
            )
