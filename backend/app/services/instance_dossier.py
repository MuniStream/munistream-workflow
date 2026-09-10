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
from .instance_attachments import collect_instance_attachments, collect_origin_attachments

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

# Claves que acaban en `_key` pero no guardan ninguna llave: son la ruta de un
# objeto en el almacenamiento. Redactarlas no protegia nada —la ruta sola no
# descarga nada, hace falta un permiso firmado— y en cambio rompia lo unico para
# lo que sirve: reconocer que el valor de un campo es un archivo y poder abrirlo.
_NO_SON_SECRETO = frozenset({"s3_key", "object_key", "file_key", "bucket_key", "storage_key"})

# Respaldo para instancias anteriores al registro de pasos (`_steps`), que no
# llevan procedencia guardada. Adivinar el paso a partir del nombre de la clave es
# fragil: cada operador bautiza las suyas a su gusto (`_data`, `_validated`,
# `_selections`, `_signer`, `_assertions_result`, `_discovery_cache`...) y la lista
# no se acaba nunca. Por eso dejo de ser el mecanismo; aqui solo evita que los
# expedientes viejos se vean peor que antes.
_LEGACY_TASK_SUFFIXES = ("_input", "_result", "_submitted_at", "_uploaded_files", "_citizen_data")

_MAX_DEPTH = 8


def _is_secret(key: str) -> bool:
    if key.lower() in _NO_SON_SECRETO:
        return False
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


def _task_of_legacy(key: str) -> Optional[str]:
    for suffix in _LEGACY_TASK_SUFFIXES:
        if key.endswith(suffix) and len(key) > len(suffix):
            return key[: -len(suffix)]
    return None


def _leer_registro(context: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Invierte `_steps`, el registro que el executor escribe al entregar cada salida.

    El registro va del paso a sus claves (`{paso: {_operator, _keys}}`); aqui se
    devuelve `clave -> {task, operator}`, que es como se consulta al recorrer el
    context.
    """
    registro = context.get("_steps")
    if not isinstance(registro, dict):
        return {}

    duenio: Dict[str, Dict[str, Any]] = {}
    disputadas = set()

    for task, paso in registro.items():
        if not isinstance(paso, dict):
            continue
        operador = paso.get("_operator")
        operador = operador if isinstance(operador, dict) else {}
        for clave in paso.get("_keys") or ():
            if not isinstance(clave, str):
                continue
            if clave in duenio and duenio[clave]["task"] != task:
                # Una clave que escriben varios pasos no es la salida de ninguno:
                # es una casilla compartida que se van sobrescribiendo, como el
                # `form_config` que cada paso en espera deja para la pantalla del
                # ciudadano. Atribuirla al ultimo que paso por ahi seria inventar.
                disputadas.add(clave)
                continue
            duenio[clave] = {"task": task, "operator": operador}

    for clave in disputadas:
        duenio.pop(clave, None)
    return duenio


def _pasos_del_registro(context: Dict[str, Any]) -> List[str]:
    """Ids de paso que el executor dejo anotados en esta instancia."""
    registro = context.get("_steps")
    if not isinstance(registro, dict):
        return []
    return [t for t in registro if isinstance(t, str) and t]


def _duenio_por_prefijo(key: str, pasos: List[str]) -> Optional[str]:
    for paso in pasos:
        if key.startswith(paso + "_"):
            return paso
    return None


def _curate_flat(context: Dict[str, Any]) -> Dict[str, Any]:
    """Sanea y agrupa por paso un context, sin mirar el del tramite padre.

    ``operators`` dice con que operador se produjo cada grupo, para que la interfaz
    pueda presentar un formulario como formulario y unos archivos como archivos en
    vez de volcar el JSON de todos igual.
    """
    by_task: Dict[str, Dict[str, Any]] = {}
    general: Dict[str, Any] = {}
    operators: Dict[str, Dict[str, Any]] = {}

    registrado = _leer_registro(context)
    # De mas largo a mas corto, para que gane el prefijo mas especifico cuando un
    # paso se llama como el principio de otro.
    pasos_conocidos = sorted(_pasos_del_registro(context), key=len, reverse=True)

    for key, value in context.items():
        if key in ("_parent_context", "_steps"):
            continue

        curated = curate_value(key, value)

        # Tres niveles, de mas a menos fiable. El respaldo se aplica clave a clave
        # y no instancia a instancia: una instancia viva puede tener pasos
        # anteriores al registro y pasos ya registrados, y descartarlo por tener
        # registro dejaria los primeros sin agrupar.
        procedencia = registrado.get(key)
        if procedencia:
            # 1. El executor dijo quien la escribio.
            task = procedencia["task"]
            nombre_operador = procedencia["operator"].get("operator")
            if nombre_operador:
                operators.setdefault(task, {
                    "operator": nombre_operador,
                    "name": procedencia["operator"].get("name"),
                    "group": procedencia["operator"].get("group"),
                })
        else:
            # 2. La clave es anterior al registro, pero empieza por el id de un
            #    paso que el registro si conoce. El id no se adivina: viene del
            #    propio registro, asi que reclamarla no es interpretar su nombre.
            #    Es lo que rescata lo que un paso dejo escrito en ejecuciones
            #    previas, como el cache de entidades elegidas.
            # 3. Y si tampoco, el sufijo, que es puro respaldo historico.
            task = _duenio_por_prefijo(key, pasos_conocidos) or _task_of_legacy(key)

        if task:
            by_task.setdefault(task, {})[key] = curated
        else:
            general[key] = curated

    return {"by_task": by_task, "general": general, "operators": operators}


def curate_context(
    context: Optional[Dict[str, Any]],
    origin_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Devuelve el context saneado y agrupado por paso.

    ``by_task`` permite mostrar "que aporto el ciudadano en cada paso"; ``general``
    recoge lo que no pertenece a ninguna tarea concreta (identidad sembrada al
    arrancar, decisiones de validacion, etc.).

    ``origin`` es el context del tramite del que nace esta instancia, curado y
    agrupado igual. Viaja embebido bajo ``_parent_context``, y presentarlo tal
    cual dentro del expediente actual lo convertia en un volcado ilegible al
    fondo de la pagina. Pero es justo lo que un revisor necesita ver: en una
    validacion administrativa, lo que se valida es lo que el ciudadano aporto en
    el tramite padre, no lo que hizo el flujo de validacion.
    """
    context = context or {}
    if not isinstance(context, dict):
        return {"by_task": {}, "general": {}, "operators": {}, "origin": None}

    resultado = _curate_flat(context)

    # El padre vivo cuando se ha podido cargar; si no, la copia que el hijo lleva
    # dentro, que es lo unico que hay pero se quedo en el momento del lanzamiento.
    padre = origin_context if isinstance(origin_context, dict) else context.get("_parent_context")
    resultado["origin"] = _curate_flat(padre) if isinstance(padre, dict) else None

    return resultado


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


async def load_origin_instance(instance):
    """La instancia de la que nace esta, cargada de la base.

    El hijo lleva dentro una copia del context del padre (`_parent_context`), y
    durante un tiempo el expediente de origen se armo con ella. Pero esa copia es
    una foto del momento en que se lanzo la validacion, y el trabajo del padre
    sigue despues: en un tramite normal la entidad se emite *tras* aprobar, asi
    que justo el documento que el revisor tiene que ver nunca estaba en la copia.
    Tampoco lleva el registro de pasos, de modo que el expediente de origen se
    quedaba sin procedencia y se leia como un volcado plano.

    No amplia lo que el revisor puede ver: ya tenia delante una copia entera de
    ese context. Lo que cambia es que ahora la ve al dia.
    """
    from ..models.workflow import WorkflowInstance

    context = getattr(instance, "context", None) or {}
    if not isinstance(context, dict):
        return None
    parent_id = context.get("parent_instance_id")
    if not parent_id or parent_id == getattr(instance, "instance_id", None):
        return None
    try:
        return await WorkflowInstance.find_one(WorkflowInstance.instance_id == parent_id)
    except Exception:
        # Un padre inalcanzable no debe tumbar el expediente: se sigue con la copia.
        return None


async def resolve_origin(instance) -> Optional[Dict[str, Any]]:
    """Tramite del que nace esta instancia, cuando es una hija.

    Las validaciones administrativas corren como instancia aparte, con su
    propio workflow ("Validacion Administrativa"). Visto desde ahi, el nombre
    del trabajo no dice *que* se esta validando: el tramite del ciudadano es el
    padre. Sin esto, dos validaciones de tramites distintos son
    indistinguibles en pantalla.
    """
    context = getattr(instance, "context", None) or {}
    parent_instance_id = context.get("parent_instance_id")
    parent_workflow_id = context.get("parent_workflow_id")
    if not parent_instance_id and not parent_workflow_id:
        return None

    # El propio tramite padre guarda en su context la referencia que uso para
    # lanzar la validacion, asi que estas claves tambien aparecen en el, con su
    # propio id. Ahi no hay origen que mostrar: es el tramite en si.
    if parent_instance_id and parent_instance_id == getattr(instance, "instance_id", None):
        return None
    if not parent_instance_id and parent_workflow_id == getattr(instance, "workflow_id", None):
        return None

    parent_name = parent_workflow_id
    if parent_workflow_id:
        try:
            from .workflow_service import workflow_service

            parent_dag = await workflow_service.get_dag(parent_workflow_id)
            parent_name = getattr(parent_dag, "name", None) or parent_workflow_id
        except Exception:
            # Un workflow padre que ya no esta cargado no debe tumbar el
            # expediente: se muestra su identificador.
            pass

    return {
        "parent_instance_id": parent_instance_id,
        "parent_workflow_id": parent_workflow_id,
        "parent_workflow_name": parent_name,
        "parent_task_id": context.get("parent_task_id"),
    }


async def build_admin_detail(instance, dag) -> Dict[str, Any]:
    """Expediente completo de una instancia, sin la cartera (que va aparte)."""
    attachments = collect_instance_attachments(instance)
    # El padre se carga una sola vez y se reparte: de el salen el expediente de
    # origen, sus adjuntos y el permiso para descargarlos.
    origin_instance = await load_origin_instance(instance)
    # Los del tramite padre van aparte y no mezclados: son de otro expediente, y
    # el revisor tiene que poder distinguir lo que se aporto aqui de lo que se
    # esta validando.
    origin_attachments = collect_origin_attachments(instance, origin_instance)
    citizen = await resolve_citizen(instance)
    origin = await resolve_origin(instance)

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
        "origin": origin,
        "context": curate_context(
            getattr(instance, "context", None),
            getattr(origin_instance, "context", None),
        ),
        "attachments": attachments,
        "origin_attachments": origin_attachments,
        "counts": {
            "attachments": len(attachments),
            "origin_attachments": len(origin_attachments),
        },
    }
