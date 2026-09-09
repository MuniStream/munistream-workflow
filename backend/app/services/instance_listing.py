"""Datos de identificación para los listados de tramites.

Un listado que solo muestra `workflow_id` y `user_id` no sirve para trabajar: el
revisor necesita saber de que tramite se trata y de quien es. Y en las
validaciones administrativas hace falta ademas el tramite de origen, porque
todas comparten el mismo nombre de flujo y sin el son indistinguibles.

Todo se resuelve en bloque para la pagina completa, no por fila: los listados
anteriores hacian dos consultas por instancia.
"""

from typing import Any, Dict, List, Sequence

from ..models.customer import Customer
from ..models.team import TeamModel
from ..models.user import UserModel
from ..models.workflow import StepExecution, WorkflowDefinition


async def _nombres_de_workflow(instancias: Sequence[Any]) -> Dict[str, str]:
    ids = {i.workflow_id for i in instancias if i.workflow_id}
    ids |= {
        (i.context or {}).get("parent_workflow_id")
        for i in instancias
        if (i.context or {}).get("parent_workflow_id")
    }
    ids.discard(None)
    if not ids:
        return {}
    definiciones = await WorkflowDefinition.find({"workflow_id": {"$in": list(ids)}}).to_list()
    return {d.workflow_id: d.name for d in definiciones if getattr(d, "name", None)}


async def _pasos_completados(instancias: Sequence[Any]) -> Dict[str, int]:
    """Avance real, de la coleccion de ejecuciones.

    `completed_steps` del documento no es fiable --hay instancias avanzadas con
    la lista vacia-- y `task_states` no se persiste.
    """
    ids = [i.instance_id for i in instancias]
    if not ids:
        return {}
    cursor = StepExecution.get_motor_collection().aggregate([
        {"$match": {"instance_id": {"$in": ids}, "status": "completed"}},
        {"$group": {"_id": {"i": "$instance_id", "s": "$step_id"}}},
        {"$group": {"_id": "$_id.i", "n": {"$sum": 1}}},
    ])
    return {fila["_id"]: fila["n"] async for fila in cursor}


async def _ciudadanos(instancias: Sequence[Any]) -> Dict[str, Customer]:
    """Resuelve los Customer de la pagina en una sola consulta.

    `user_id` es el id del Customer para los tramites del portal; para las
    instancias hijas, el del padre viaja en el context.
    """
    candidatos = set()
    for i in instancias:
        ctx = i.context or {}
        for valor in (i.user_id, ctx.get("parent_user_id"), ctx.get("customer_id")):
            if valor:
                candidatos.add(str(valor))
    if not candidatos:
        return {}

    from bson import ObjectId

    object_ids = []
    for c in candidatos:
        try:
            object_ids.append(ObjectId(c))
        except Exception:
            continue
    if not object_ids:
        return {}

    encontrados = await Customer.find({"_id": {"$in": object_ids}}).to_list()
    return {str(c.id): c for c in encontrados}


async def _asignados(instancias: Sequence[Any]) -> Dict[str, str]:
    """Nombre de quien tiene asignado cada tramite.

    Un listado que muestra el identificador del revisor no dice nada: hay que
    poder ver de un vistazo si algo esta en manos de alguien y de quien.
    """
    from bson import ObjectId

    ids_usuario, ids_equipo = set(), set()
    for i in instancias:
        if getattr(i, "assigned_user_id", None):
            ids_usuario.add(str(i.assigned_user_id))
        if getattr(i, "assigned_team_id", None):
            ids_equipo.add(str(i.assigned_team_id))

    nombres: Dict[str, str] = {}

    def a_objectid(valores):
        salida = []
        for v in valores:
            try:
                salida.append(ObjectId(v))
            except Exception:
                continue
        return salida

    if ids_usuario:
        oids = a_objectid(ids_usuario)
        if oids:
            for u in await UserModel.find({"_id": {"$in": oids}}).to_list():
                nombres[str(u.id)] = u.full_name or u.email

    if ids_equipo:
        oids = a_objectid(ids_equipo)
        if oids:
            for tm in await TeamModel.find({"_id": {"$in": oids}}).to_list():
                nombres[str(tm.id)] = getattr(tm, "name", None) or str(tm.id)
        # Los equipos tambien se referencian por su identificador legible.
        for t in await TeamModel.find({"team_id": {"$in": list(ids_equipo)}}).to_list():
            nombres[t.team_id] = getattr(t, "name", None) or t.team_id

    return nombres

async def enrich_instances(instancias: Sequence[Any]) -> List[Dict[str, Any]]:
    """Devuelve, por instancia, lo que un listado necesita para ser legible."""
    nombres = await _nombres_de_workflow(instancias)
    completados = await _pasos_completados(instancias)
    clientes = await _ciudadanos(instancias)
    asignados = await _asignados(instancias)

    salida = []
    for i in instancias:
        ctx = i.context or {}
        parent_wf = ctx.get("parent_workflow_id")
        cliente = (
            clientes.get(str(i.user_id))
            or clientes.get(str(ctx.get("customer_id") or ""))
            or clientes.get(str(ctx.get("parent_user_id") or ""))
        )

        total = len(getattr(i, "completed_steps", None) or [])
        hechos = completados.get(i.instance_id, total)

        salida.append({
            "instance_id": i.instance_id,
            "workflow_name": nombres.get(i.workflow_id) or i.workflow_id,
            # El tramite de origen: lo que de verdad identifica una validacion.
            "parent_workflow_name": nombres.get(parent_wf) if parent_wf else None,
            "parent_instance_id": ctx.get("parent_instance_id"),
            "citizen_name": getattr(cliente, "full_name", None) or ctx.get("customer_name"),
            "citizen_email": (
                getattr(cliente, "email", None)
                or ctx.get("customer_email")
                or ctx.get("parent_customer_email")
            ),
            "completed_steps_count": hechos,
            "assigned_to_name": (
                asignados.get(str(getattr(i, "assigned_user_id", "") or ""))
                or asignados.get(str(getattr(i, "assigned_team_id", "") or ""))
                # Los equipos viven en Keycloak y su identificador ya es
                # legible; mejor presentarlo que dejar el hueco vacio.
                or (
                    str(i.assigned_team_id).replace("_", " ").capitalize()
                    if getattr(i, "assigned_team_id", None) else None
                )
            ),
        })
    return salida
