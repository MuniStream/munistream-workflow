"""Arma el expediente de una instancia para la vista de administracion.

Reune en un solo sitio las tres cosas que el revisor necesita ver junto al
tramite y que hoy no viajan en ninguna respuesta: quien es el ciudadano, que
aporto en cada paso, y que archivos adjunto.

Cada pieza resuelve una ambiguedad heredada del modelo de datos, y esta
documentada donde ocurre.
"""

import json
import re
from typing import Any, Dict, List, Optional, Set

from ..models.customer import Customer
from .entity_serialization import DETAIL_MAX_BYTES
from .instance_attachments import collect_instance_attachments

# Claves cuyo valor no debe salir nunca del backend. Los tramites con firma
# digital guardan en el context la llave privada, su contrasena y el
# certificado (ver `private_key_field` / `password_field` en el formulario de
# firma del admin). El expediente es una vista de lectura: nada de eso hace
# falta para revisar, y no debe quedar en el historial del navegador ni en un
# volcado de red.
_SECRET_PATTERNS = re.compile(
    r"password|passwd|private_key|privatekey|secret|token|credential|_pfx|_p12|_key$",
    re.IGNORECASE,
)

# Sufijos con los que los pasos escriben en el context. Sirven para agrupar por
# tarea en vez de presentar un volcado plano.
_TASK_SUFFIXES = ("_input", "_result", "_submitted_at", "_uploaded_files", "_citizen_data")

_MAX_DEPTH = 8


def _is_secret(key: str) -> bool:
    return bool(_SECRET_PATTERNS.search(key))


def _approx_size(value: Any) -> int:
    try:
        return len(value) if isinstance(value, str) else len(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return DETAIL_MAX_BYTES + 1


def curate_value(key: str, value: Any, depth: int = 0) -> Any:
    """Redacta secretos y recorta valores enormes, conservando la forma."""
    if _is_secret(key):
        return {"__redacted": True}

    if depth > _MAX_DEPTH:
        return {"__truncated": True}

    if isinstance(value, dict):
        return {k: curate_value(k, v, depth + 1) for k, v in value.items()}

    if isinstance(value, list):
        return [curate_value(key, v, depth + 1) for v in value]

    if _approx_size(value) > DETAIL_MAX_BYTES:
        # Defensa en profundidad: el executor ya purga base64 al guardar, pero
        # el historico puede tener blobs y no queremos que un expediente viejo
        # devuelva megas de JSON.
        return {"__truncated": True, "size": _approx_size(value)}

    return value


def _task_of(key: str) -> Optional[str]:
    for suffix in _TASK_SUFFIXES:
        if key.endswith(suffix) and len(key) > len(suffix):
            return key[: -len(suffix)]
    return None


def curate_context(context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Devuelve el context saneado y agrupado por paso.

    ``by_task`` permite mostrar "que aporto el ciudadano en cada paso"; ``general``
    recoge lo que no pertenece a ninguna tarea concreta (identidad sembrada al
    arrancar, decisiones de validacion, etc.).
    """
    context = context or {}
    if not isinstance(context, dict):
        return {"by_task": {}, "general": {}}

    by_task: Dict[str, Dict[str, Any]] = {}
    general: Dict[str, Any] = {}

    for key, value in context.items():
        curated = curate_value(key, value)
        task = _task_of(key)
        if task:
            by_task.setdefault(task, {})[key] = curated
        else:
            general[key] = curated

    return {"by_task": by_task, "general": general}


async def _customer_by_any_id(candidate: Optional[str]) -> Optional[Customer]:
    """Resuelve un Customer tanto si el id es su _id de Mongo como si es el sub de Keycloak.

    ``WorkflowInstance.user_id`` guarda una cosa u otra segun quien creo la
    instancia: el portal ciudadano guarda ``str(Customer.id)``, mientras que las
    creadas desde la API de administracion guardan el ``sub`` de Keycloak.
    """
    if not candidate:
        return None

    try:
        found = await Customer.get(candidate)
        if found:
            return found
    except Exception:
        # No era un ObjectId valido; se intenta como id de Keycloak.
        pass

    try:
        return await Customer.find_one(Customer.keycloak_id == candidate)
    except Exception:
        return None


async def resolve_citizen(instance) -> Dict[str, Any]:
    """Identifica al ciudadano dueno del tramite.

    Se intenta en cascada porque ninguna fuente cubre todos los casos, y se
    devuelve ``source`` para que la interfaz pueda avisar cuando el dato es
    inferido en lugar de leido del registro del ciudadano.
    """
    context = getattr(instance, "context", None) or {}
    user_id = getattr(instance, "user_id", None)

    customer = await _customer_by_any_id(user_id)
    if customer:
        data = customer.to_public_dict()
        return {
            "user_id": user_id,
            "full_name": data.get("full_name"),
            "email": data.get("email"),
            "curp": data.get("curp"),
            "rfc": data.get("rfc"),
            "source": "customer",
        }

    # El portal siembra la identidad en el context al arrancar el tramite.
    if context.get("customer_name") or context.get("customer_email"):
        return {
            "user_id": user_id,
            "full_name": context.get("customer_name"),
            "email": context.get("customer_email"),
            "curp": None,
            "rfc": None,
            "source": "context",
        }

    # Las instancias hijas no heredan `customer_name`, solo la referencia al padre.
    parent_id = context.get("parent_user_id")
    parent = await _customer_by_any_id(parent_id)
    if parent:
        data = parent.to_public_dict()
        return {
            "user_id": user_id,
            "full_name": data.get("full_name"),
            "email": data.get("email"),
            "curp": data.get("curp"),
            "rfc": data.get("rfc"),
            "source": "parent",
        }

    if context.get("parent_customer_email"):
        return {
            "user_id": user_id,
            "full_name": None,
            "email": context.get("parent_customer_email"),
            "curp": None,
            "rfc": None,
            "source": "parent_context",
        }

    return {
        "user_id": user_id,
        "full_name": None,
        "email": None,
        "curp": None,
        "rfc": None,
        "source": "unknown",
    }


def _string_values(value: Any, depth: int, out: Set[str]) -> None:
    if depth > _MAX_DEPTH:
        return
    if isinstance(value, str):
        out.add(value)
    elif isinstance(value, dict):
        for v in value.values():
            _string_values(v, depth + 1, out)
    elif isinstance(value, list):
        for v in value:
            _string_values(v, depth + 1, out)


def entity_ids_in_use(instance, wallet_entity_ids: Set[str]) -> List[str]:
    """Entidades de la cartera que este tramite referencia.

    En vez de adivinar en que claves del context puede aparecer un entity_id
    --hay varias convenciones segun el operador que lo escriba-- se intersecta
    el conjunto de cadenas del context con los ids que ya conocemos de la
    cartera. No hace consultas extra y no depende de nombres de campo.
    """
    if not wallet_entity_ids:
        return []
    found: Set[str] = set()
    _string_values(getattr(instance, "context", None) or {}, 0, found)
    return sorted(found & wallet_entity_ids)


async def build_admin_detail(instance, dag) -> Dict[str, Any]:
    """Expediente completo de una instancia, sin la cartera (que va aparte)."""
    attachments = collect_instance_attachments(instance)
    citizen = await resolve_citizen(instance)

    status = getattr(instance, "status", None)
    return {
        "instance": {
            "instance_id": instance.instance_id,
            "workflow_id": instance.workflow_id,
            "workflow_name": getattr(dag, "name", None) or instance.workflow_id,
            "status": status.value if hasattr(status, "value") else status,
            "current_step": getattr(instance, "current_step", None),
            "created_at": getattr(instance, "created_at", None),
            "updated_at": getattr(instance, "updated_at", None),
            "completed_at": getattr(instance, "completed_at", None),
            "assignment": {
                "assigned_user_id": getattr(instance, "assigned_user_id", None),
                "assigned_team_id": getattr(instance, "assigned_team_id", None),
                "assignment_status": (
                    instance.assignment_status.value
                    if getattr(instance, "assignment_status", None)
                    else None
                ),
                "assigned_at": getattr(instance, "assigned_at", None),
                "assigned_by": getattr(instance, "assigned_by", None),
                "assignment_notes": getattr(instance, "assignment_notes", None),
            },
        },
        "citizen": citizen,
        "context": curate_context(getattr(instance, "context", None)),
        "attachments": attachments,
        "counts": {"attachments": len(attachments)},
    }
