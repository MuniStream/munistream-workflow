"""
Generic post-authentication callback registry.

Plugins (or any module loaded at startup) may register async callbacks that run
once per Keycloak session of a citizen, right after the Customer record has been
resolved/synced. The backend stays agnostic: it does not know what the callbacks
do, and registering none is a no-op.

"Post-auth" used to mean "on every authenticated request": the callbacks were
awaited inside `get_current_customer`, so each portal request paid for them and
any callback that talked to an external service hit it on every click. Now the
backend claims the session (`claim_post_auth_session`) with an atomic update and
only the request that wins the claim runs the callbacks; concurrent requests of
the same login see the claim already taken and skip them. A new login brings a
new `sid`, so the callbacks run again.

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

# Clave de Customer.metadata con la sesión que ya ejecutó los callbacks.
POST_AUTH_SESSION_KEY = "post_auth_session"


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


def has_post_auth_callbacks() -> bool:
    return bool(_post_auth_callbacks)


def post_auth_session_key(payload: Dict[str, Any]) -> Optional[str]:
    """Identificador de la sesión de Keycloak a la que pertenece el token.

    `sid` es lo que Keycloak pone en el access token desde la versión 18;
    `session_state` es el nombre anterior. `auth_time` cambia en cada login
    interactivo y sirve de respaldo. Sin ninguno no hay forma de saber si el
    token es de una sesión nueva.
    """
    sid = payload.get("sid") or payload.get("session_state")
    if sid:
        return f"sid:{sid}"
    auth_time = payload.get("auth_time")
    if auth_time:
        return f"auth_time:{auth_time}"
    return None


async def claim_post_auth_session(customer: Any, payload: Dict[str, Any]) -> bool:
    """Reclama la sesión del token para ejecutar los callbacks; True si le toca.

    El reclamo es un update condicional sobre el documento del Customer: de
    varias requests concurrentes del mismo login solo una lo modifica, y esa es
    la que ejecuta los callbacks. La marca se copia también en memoria para que
    un `customer.save()` posterior (los callbacks lo hacen) no la borre.
    """
    key = post_auth_session_key(payload)
    if key is None:
        # Sin identificador de sesión se conserva el comportamiento anterior.
        logger.debug("token sin sid/session_state/auth_time: post-auth callbacks en cada request")
        return True

    if not isinstance(customer.metadata, dict):
        customer.metadata = {}
    if customer.metadata.get(POST_AUTH_SESSION_KEY) == key:
        return False

    result = await type(customer).get_motor_collection().update_one(
        {"_id": customer.id, f"metadata.{POST_AUTH_SESSION_KEY}": {"$ne": key}},
        {"$set": {f"metadata.{POST_AUTH_SESSION_KEY}": key}},
    )
    customer.metadata[POST_AUTH_SESSION_KEY] = key
    return result.modified_count == 1


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
