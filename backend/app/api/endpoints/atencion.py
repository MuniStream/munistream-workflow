"""
Citizen service: public indicators, feedback on resolved procedures and
support requests (citizen side under /public, staff side under /admin).
"""
import logging
import secrets
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ...auth.provider import require_permission
from ...core.fechas import utc_aware
from ...models.atencion import (
    CategoriaAtencion,
    EstadoAtencion,
    Feedback,
    MensajeAtencion,
    SolicitudAtencion,
)
from ...models.customer import Customer
from ...models.workflow import EventType, WorkflowInstance
from .public_auth import get_current_customer, require_instance_owner

logger = logging.getLogger(__name__)

public_router = APIRouter()
admin_router = APIRouter()

# Below this many cases a figure is not published (statistical disclosure).
MIN_PUBLICABLE = 5


# --- Public indicators -------------------------------------------------------

_cache: Dict[str, Any] = {}
_CACHE_TTL = 15 * 60


def _publicable(n: int) -> Optional[int]:
    return n if n >= MIN_PUBLICABLE else None


def _desglose_publicable(w: Dict[str, Any], resueltos: int) -> bool:
    if resueltos < MIN_PUBLICABLE:
        return False
    return all(n == 0 or n >= MIN_PUBLICABLE for n in (w["_aprobados"], w["_rechazados"]))


async def _calcular_indicadores(meses: int) -> Dict[str, Any]:
    from ...services import sla

    ahora = datetime.utcnow()
    desde = (ahora.replace(day=1, hour=0, minute=0, second=0, microsecond=0) - timedelta(days=31 * (meses - 1))).replace(day=1)
    # Only procedures with a legal term; a technical failure or a cancellation
    # is not a resolution, so those are left out of the figures.
    items = [
        i for i in await sla.semaforo(incluir_resueltos_desde=desde, ahora=ahora)
        if i.get("plazo") and (not i["plazo"]["resuelto"] or i["desenlace"] in ("completed", "rejected"))
    ]

    # Received in the window = entered review in the window.
    recibidos = [i for i in items if i["plazo"]["fecha_presentacion"] >= desde]
    resueltos = [i for i in items if i["plazo"]["resuelto"]]

    def mes(d: datetime) -> str:
        return d.strftime("%Y-%m")

    serie: Dict[str, Dict[str, int]] = defaultdict(lambda: {"recibidos": 0, "resueltos": 0})
    for i in recibidos:
        serie[mes(i["plazo"]["fecha_presentacion"])]["recibidos"] += 1
    resueltos_fecha = {
        w.instance_id: w.completed_at
        for w in await WorkflowInstance.find(
            {"instance_id": {"$in": [i["instance_id"] for i in resueltos]}}
        ).to_list()
    }
    for i in resueltos:
        fin = resueltos_fecha.get(i["instance_id"])
        if fin and fin >= desde:
            serie[mes(fin)]["resueltos"] += 1
    meses_lista = []
    cursor = desde
    while cursor <= ahora:
        clave = mes(cursor)
        meses_lista.append({"mes": clave, **serie.get(clave, {"recibidos": 0, "resueltos": 0})})
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)

    satisf = await Feedback.find({"created_at": {"$gte": desde}}).to_list()
    satisf_por_wf: Dict[str, List[int]] = defaultdict(list)
    for f in satisf:
        satisf_por_wf[f.workflow_id].append(f.calificacion)

    por_wf: Dict[str, Dict[str, Any]] = {}
    for i in items:
        w = por_wf.setdefault(i["workflow_id"], {
            "workflow_id": i["workflow_id"],
            "nombre": i["workflow_name"],
            "plazo_dias": i["plazo"]["plazo_dias"],
            "plazo_habiles": i["plazo"]["plazo_habiles"],
            "_recibidos": 0, "_en_revision": 0, "_resueltos": 0,
            "_aprobados": 0, "_rechazados": 0, "_en_plazo": 0, "_dias": [],
        })
        if i["plazo"]["fecha_presentacion"] >= desde:
            w["_recibidos"] += 1
        if i["plazo"]["resuelto"]:
            w["_resueltos"] += 1
            w["_dias"].append(i["plazo"]["dias_transcurridos"])
            if i["plazo"]["en_plazo"]:
                w["_en_plazo"] += 1
            if i["desenlace"] == "completed":
                w["_aprobados"] += 1
            elif i["desenlace"] == "rejected":
                w["_rechazados"] += 1
        else:
            w["_en_revision"] += 1

    tramites = []
    for w in sorted(por_wf.values(), key=lambda x: -x["_recibidos"]):
        res = w["_resueltos"]
        cal = satisf_por_wf.get(w["workflow_id"], [])
        tramites.append({
            "workflow_id": w["workflow_id"],
            "nombre": w["nombre"],
            "plazo_dias": w["plazo_dias"],
            "plazo_habiles": w["plazo_habiles"],
            "recibidos": _publicable(w["_recibidos"]),
            "en_revision": _publicable(w["_en_revision"]),
            "resueltos": _publicable(res),
            # Approved + rejected = resolved: if either is a small non-zero
            # count, publishing the other would reveal it by subtraction.
            "aprobados": w["_aprobados"] if _desglose_publicable(w, res) else None,
            "rechazados": w["_rechazados"] if _desglose_publicable(w, res) else None,
            "porcentaje_en_plazo": round(100 * w["_en_plazo"] / res, 1) if res >= MIN_PUBLICABLE else None,
            "dias_promedio_resolucion": round(mean(w["_dias"]), 1) if res >= MIN_PUBLICABLE else None,
            "satisfaccion": round(mean(cal), 2) if len(cal) >= MIN_PUBLICABLE else None,
            "suprimido": res < MIN_PUBLICABLE,
        })

    total_res = len(resueltos)
    en_plazo = sum(1 for i in resueltos if i["plazo"]["en_plazo"])
    califs = [f.calificacion for f in satisf]
    return {
        "periodo": {"desde": desde, "hasta": ahora, "meses": meses},
        "minimo_publicable": MIN_PUBLICABLE,
        "resumen": {
            "recibidos": len(recibidos),
            "resueltos": total_res,
            "en_revision": sum(1 for i in items if not i["plazo"]["resuelto"]),
            "porcentaje_en_plazo": round(100 * en_plazo / total_res, 1) if total_res >= MIN_PUBLICABLE else None,
            "dias_promedio_resolucion": (
                round(mean(i["plazo"]["dias_transcurridos"] for i in resueltos), 1)
                if total_res >= MIN_PUBLICABLE else None
            ),
            "satisfaccion": round(mean(califs), 2) if len(califs) >= MIN_PUBLICABLE else None,
            "opiniones": len(califs),
            "tipos_de_tramite": len(tramites),
        },
        "por_mes": meses_lista,
        "por_tramite": tramites,
        "generado": ahora,
    }


@public_router.get("/indicadores")
async def indicadores_publicos(meses: int = Query(12, ge=1, le=24)):
    """Institutional indicators, anonymous and aggregated. Counts below
    MIN_PUBLICABLE are withheld (null) so no individual case can be singled
    out. Cached for 15 minutes."""
    clave = f"indicadores:{meses}"
    hit = _cache.get(clave)
    if hit and time.monotonic() - hit[0] < _CACHE_TTL:
        return hit[1]
    datos = utc_aware(await _calcular_indicadores(meses))
    _cache[clave] = (time.monotonic(), datos)
    return datos


# --- Feedback ----------------------------------------------------------------

class FeedbackIn(BaseModel):
    calificacion: int = Field(..., ge=1, le=5)
    comentario: Optional[str] = Field(None, max_length=1000)


def _feedback_out(f: Feedback) -> Dict[str, Any]:
    return utc_aware({
        "instance_id": f.instance_id,
        "calificacion": f.calificacion,
        "comentario": f.comentario,
        "created_at": f.created_at,
    })


async def _instancia_propia(instance_id: str, customer: Customer) -> WorkflowInstance:
    inst = await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")
    require_instance_owner(inst, customer)
    return inst


@public_router.get("/instances/{instance_id}/feedback")
async def obtener_feedback(instance_id: str, customer: Customer = Depends(get_current_customer)):
    await _instancia_propia(instance_id, customer)
    f = await Feedback.find_one(Feedback.instance_id == instance_id)
    return {"feedback": _feedback_out(f) if f else None}


@public_router.post("/instances/{instance_id}/feedback", status_code=201)
async def dejar_feedback(
    instance_id: str, body: FeedbackIn, customer: Customer = Depends(get_current_customer)
):
    inst = await _instancia_propia(instance_id, customer)
    # Resolved = approved or rejected (both end as "completed"); a technical
    # failure or a cancellation is not something the applicant can rate.
    if inst.status != "completed":
        raise HTTPException(status_code=409, detail="The procedure has not been resolved")
    if await Feedback.find_one(Feedback.instance_id == instance_id):
        raise HTTPException(status_code=409, detail="Feedback already submitted")
    f = Feedback(
        instance_id=instance_id,
        workflow_id=inst.workflow_id,
        customer_id=str(customer.id),
        calificacion=body.calificacion,
        comentario=(body.comentario or "").strip() or None,
    )
    await f.insert()
    _cache.clear()
    return {"feedback": _feedback_out(f)}


@admin_router.get("/satisfaccion")
async def satisfaccion(
    workflow_id: Optional[str] = Query(None),
    _: dict = Depends(require_permission("VIEW_INSTANCES")),
):
    """Ratings left by applicants, overall or for one procedure."""
    filtro = {"workflow_id": workflow_id} if workflow_id else {}
    todas = await Feedback.find(filtro).sort("-created_at").to_list()
    distribucion = {str(k): 0 for k in range(1, 6)}
    for f in todas:
        distribucion[str(f.calificacion)] += 1
    return {
        "total": len(todas),
        "promedio": round(mean(f.calificacion for f in todas), 2) if todas else None,
        "distribucion": distribucion,
        "comentarios": [
            {**_feedback_out(f), "workflow_id": f.workflow_id}
            for f in todas if f.comentario
        ][:20],
    }


# --- Support requests --------------------------------------------------------

class SolicitudIn(BaseModel):
    asunto: str = Field(..., min_length=3, max_length=200)
    categoria: CategoriaAtencion
    mensaje: str = Field(..., min_length=1, max_length=4000)
    instance_id: Optional[str] = None


class MensajeIn(BaseModel):
    texto: str = Field(..., min_length=1, max_length=4000)


class CambioSolicitud(BaseModel):
    estado: Optional[EstadoAtencion] = None
    asignado_a: Optional[str] = None


def _folio_atencion() -> str:
    return f"AT-{datetime.utcnow():%Y%m%d}-{secrets.token_hex(3).upper()}"


def _solicitud_out(s: SolicitudAtencion, nombres: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    return utc_aware({
        "solicitud_id": s.solicitud_id,
        "folio": s.folio,
        "asunto": s.asunto,
        "categoria": s.categoria,
        "estado": s.estado,
        "instance_id": s.instance_id,
        "workflow_id": s.workflow_id,
        "asignado_a": s.asignado_a,
        "mensajes": [m.model_dump() for m in s.mensajes],
        "created_at": s.created_at,
        "updated_at": s.updated_at,
        "primera_respuesta_at": s.primera_respuesta_at,
        "solicitante": (nombres or {}).get(s.customer_id),
    })


async def _solicitud_propia(solicitud_id: str, customer: Customer) -> SolicitudAtencion:
    s = await SolicitudAtencion.find_one(SolicitudAtencion.solicitud_id == solicitud_id)
    # Same answer for "not yours" and "does not exist": no enumeration.
    if not s or s.customer_id != str(customer.id):
        raise HTTPException(status_code=404, detail="Support request not found")
    return s


@public_router.post("/atencion", status_code=201)
async def crear_solicitud(body: SolicitudIn, customer: Customer = Depends(get_current_customer)):
    workflow_id = None
    if body.instance_id:
        inst = await _instancia_propia(body.instance_id, customer)
        workflow_id = inst.workflow_id
    s = SolicitudAtencion(
        solicitud_id=str(uuid.uuid4()),
        folio=_folio_atencion(),
        customer_id=str(customer.id),
        asunto=body.asunto.strip(),
        categoria=body.categoria,
        instance_id=body.instance_id,
        workflow_id=workflow_id,
        mensajes=[MensajeAtencion(
            autor="ciudadano", autor_id=str(customer.id),
            autor_nombre=customer.full_name, texto=body.mensaje.strip(),
        )],
    )
    await s.insert()
    return _solicitud_out(s)


@public_router.get("/atencion")
async def mis_solicitudes(customer: Customer = Depends(get_current_customer)):
    items = await SolicitudAtencion.find(
        SolicitudAtencion.customer_id == str(customer.id)
    ).sort("-updated_at").to_list()
    return {"solicitudes": [_solicitud_out(s) for s in items]}


@public_router.get("/atencion/{solicitud_id}")
async def mi_solicitud(solicitud_id: str, customer: Customer = Depends(get_current_customer)):
    return _solicitud_out(await _solicitud_propia(solicitud_id, customer))


@public_router.post("/atencion/{solicitud_id}/mensajes")
async def responder_como_ciudadano(
    solicitud_id: str, body: MensajeIn, customer: Customer = Depends(get_current_customer)
):
    s = await _solicitud_propia(solicitud_id, customer)
    if s.estado == EstadoAtencion.CERRADA:
        raise HTTPException(status_code=409, detail="The support request is closed")
    s.mensajes.append(MensajeAtencion(
        autor="ciudadano", autor_id=str(customer.id),
        autor_nombre=customer.full_name, texto=body.texto.strip(),
    ))
    # The ball is back in the institution's court.
    s.estado = EstadoAtencion.ABIERTA
    s.updated_at = datetime.utcnow()
    await s.save()
    return _solicitud_out(s)


@public_router.post("/atencion/{solicitud_id}/cerrar")
async def cerrar_como_ciudadano(solicitud_id: str, customer: Customer = Depends(get_current_customer)):
    s = await _solicitud_propia(solicitud_id, customer)
    s.estado = EstadoAtencion.CERRADA
    s.cerrada_at = s.updated_at = datetime.utcnow()
    await s.save()
    return _solicitud_out(s)


async def _nombres_de(customer_ids) -> Dict[str, str]:
    from bson import ObjectId

    oids = [ObjectId(i) for i in set(customer_ids) if ObjectId.is_valid(i)]
    if not oids:
        return {}
    return {str(c.id): c.full_name for c in await Customer.find({"_id": {"$in": oids}}).to_list()}


@admin_router.get("/atencion")
async def bandeja_atencion(
    estado: Optional[EstadoAtencion] = Query(None),
    categoria: Optional[CategoriaAtencion] = Query(None),
    _: dict = Depends(require_permission("VIEW_INSTANCES")),
):
    filtro: Dict[str, Any] = {}
    if estado:
        filtro["estado"] = estado
    if categoria:
        filtro["categoria"] = categoria
    items = await SolicitudAtencion.find(filtro).sort("-updated_at").to_list()

    conteo = {e.value: 0 for e in EstadoAtencion}
    async for row in SolicitudAtencion.get_motor_collection().aggregate(
        [{"$group": {"_id": "$estado", "n": {"$sum": 1}}}]
    ):
        conteo[row["_id"]] = row["n"]

    respondidas = await SolicitudAtencion.find({"primera_respuesta_at": {"$ne": None}}).to_list()
    horas = [(s.primera_respuesta_at - s.created_at).total_seconds() / 3600 for s in respondidas]

    nombres = await _nombres_de(s.customer_id for s in items)
    return {
        "conteo": conteo,
        "horas_promedio_primera_respuesta": round(mean(horas), 1) if horas else None,
        "solicitudes": [_solicitud_out(s, nombres) for s in items],
    }


@admin_router.get("/atencion/{solicitud_id}")
async def detalle_atencion(solicitud_id: str, _: dict = Depends(require_permission("VIEW_INSTANCES"))):
    s = await SolicitudAtencion.find_one(SolicitudAtencion.solicitud_id == solicitud_id)
    if not s:
        raise HTTPException(status_code=404, detail="Support request not found")
    return _solicitud_out(s, await _nombres_de([s.customer_id]))


@admin_router.post("/atencion/{solicitud_id}/responder")
async def responder_como_personal(
    solicitud_id: str,
    body: MensajeIn,
    user: dict = Depends(require_permission("REVIEW_DOCUMENTS")),
):
    s = await SolicitudAtencion.find_one(SolicitudAtencion.solicitud_id == solicitud_id)
    if not s:
        raise HTTPException(status_code=404, detail="Support request not found")
    if s.estado == EstadoAtencion.CERRADA:
        raise HTTPException(status_code=409, detail="The support request is closed")
    ahora = datetime.utcnow()
    autor_id = str(user.get("sub") or user.get("id") or "")
    s.mensajes.append(MensajeAtencion(
        autor="personal", autor_id=autor_id,
        autor_nombre=user.get("name") or user.get("preferred_username"),
        texto=body.texto.strip(), fecha=ahora,
    ))
    s.estado = EstadoAtencion.RESPONDIDA
    s.primera_respuesta_at = s.primera_respuesta_at or ahora
    s.asignado_a = s.asignado_a or autor_id
    s.updated_at = ahora
    await s.save()

    try:
        from ...services.workflow_service import workflow_service

        await workflow_service.executor.event_manager.publish_event(
            event_type=EventType.SUPPORT_REPLIED,
            workflow_id="atencion_ciudadana",
            instance_id=s.solicitud_id,
            user_id=s.customer_id,
            event_data={"folio": s.folio, "asunto": s.asunto},
        )
    except Exception:
        logger.exception("Failed to publish SUPPORT_REPLIED for %s", s.solicitud_id)

    return _solicitud_out(s, await _nombres_de([s.customer_id]))


# POST and not PATCH: the platform's nginx CORS policy allows GET/POST/PUT/DELETE.
@admin_router.post("/atencion/{solicitud_id}/estado")
async def actualizar_atencion(
    solicitud_id: str,
    body: CambioSolicitud,
    _: dict = Depends(require_permission("REVIEW_DOCUMENTS")),
):
    s = await SolicitudAtencion.find_one(SolicitudAtencion.solicitud_id == solicitud_id)
    if not s:
        raise HTTPException(status_code=404, detail="Support request not found")
    if body.estado is not None:
        s.estado = body.estado
        if body.estado == EstadoAtencion.CERRADA:
            s.cerrada_at = datetime.utcnow()
    if body.asignado_a is not None:
        s.asignado_a = body.asignado_a or None
    s.updated_at = datetime.utcnow()
    await s.save()
    return _solicitud_out(s, await _nombres_de([s.customer_id]))
