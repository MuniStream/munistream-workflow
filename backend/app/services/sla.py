"""
Hybrid traffic light (semáforo híbrido) for citizen procedures.

Two axes, the worst one wins:

1. Legal resolution term. Each procedure declares its term in
   `workflow_definitions.metadata.estimatedTime` ("21 días hábiles"), the same
   text the citizen sees. The clock starts when the procedure enters review
   (creation of its first ADMIN child instance) and stops when it is resolved.
   While the reviewer is waiting on corrections from the citizen
   (assignment MODIFICATION_REQUESTED) the clock is frozen.
2. Dwell time in the current step, compared with the historical p95 of that
   same step in that same workflow.

Business days skip weekends only; official holidays are not modelled (see
`entity_validity.sumar_dias_habiles`).
"""
from __future__ import annotations

import os
import re
import unicodedata
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .entity_validity import sumar_dias_habiles
from .estado_instancia import estado_efectivo

COLORES = ("verde", "amarillo", "rojo")
_RANK = {None: -1, "verde": 0, "amarillo": 1, "rojo": 2}

# Business days are counted on the local calendar (Mexico City, UTC-6 all year
# since 2022): a request submitted on Friday night local time must not count
# from Saturday because it is already Saturday in UTC.
TZ_OFFSET = timedelta(hours=float(os.getenv("SLA_TZ_OFFSET_HOURS", "-6")))

# Fraction of the legal term after which a procedure turns amber.
UMBRAL_AMARILLO = float(os.getenv("SLA_UMBRAL_AMARILLO", "0.7"))

_PLAZO_RE = re.compile(r"^\s*(\d+)\s+dias?\s+(habiles|naturales)\s*$")


def _sin_acentos(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    ).lower()


def parse_plazo(valor: Any) -> Optional[Tuple[int, bool]]:
    """(days, business_days) from the declared term, or None if it is not a
    resolution term ("Inmediato", "Presentar dentro de 72 horas…")."""
    if isinstance(valor, dict):
        dias = valor.get("dias")
        if isinstance(dias, int) and dias > 0:
            return dias, bool(valor.get("habiles", True))
        return None
    if not isinstance(valor, str) or not valor.strip():
        return None
    m = _PLAZO_RE.match(_sin_acentos(valor))
    if not m:
        return None
    return int(m.group(1)), m.group(2) == "habiles"


def plazo_de_metadata(metadata: Optional[Dict[str, Any]]) -> Optional[Tuple[int, bool]]:
    md = metadata or {}
    return parse_plazo(md.get("plazo_resolucion")) or parse_plazo(md.get("estimatedTime"))


def contar_dias_habiles(desde: datetime, hasta: datetime, habiles: bool = True) -> int:
    """Whole days elapsed after `desde` up to `hasta` (weekdays only if `habiles`)."""
    if hasta <= desde:
        return 0
    dias = 0
    dia = desde.date()
    fin = hasta.date()
    while dia < fin:
        dia += timedelta(days=1)
        if not habiles or dia.weekday() < 5:
            dias += 1
    return dias


def color_plazo(avance: float, umbral_amarillo: float = None) -> str:
    umbral = UMBRAL_AMARILLO if umbral_amarillo is None else umbral_amarillo
    if avance > 1:
        return "rojo"
    if avance >= umbral:
        return "amarillo"
    return "verde"


def color_permanencia(horas: Optional[float], p95_horas: Optional[float]) -> Optional[str]:
    if horas is None or not p95_horas:
        return None
    if horas > 2 * p95_horas:
        return "rojo"
    if horas > p95_horas:
        return "amarillo"
    return "verde"


def peor_color(*colores: Optional[str]) -> Optional[str]:
    return max(colores, key=lambda c: _RANK.get(c, -1)) if colores else None


def evaluar_plazo(
    presentado: datetime,
    plazo: Tuple[int, bool],
    ahora: Optional[datetime] = None,
    pausado_desde: Optional[datetime] = None,
    resuelto_en: Optional[datetime] = None,
    umbral_amarillo: Optional[float] = None,
) -> Dict[str, Any]:
    dias_plazo, habiles = plazo
    ahora = ahora or datetime.utcnow()
    corte = resuelto_en or pausado_desde or ahora
    transcurridos = contar_dias_habiles(presentado + TZ_OFFSET, corte + TZ_OFFSET, habiles)
    local = presentado + TZ_OFFSET
    compromiso = (
        sumar_dias_habiles(local, dias_plazo) if habiles else local + timedelta(days=dias_plazo)
    ) - TZ_OFFSET
    avance = transcurridos / dias_plazo
    return {
        "fecha_presentacion": presentado,
        "fecha_compromiso": compromiso,
        "plazo_dias": dias_plazo,
        "plazo_habiles": habiles,
        "dias_transcurridos": transcurridos,
        "avance": round(avance, 3),
        "color": color_plazo(avance, umbral_amarillo),
        "pausado": resuelto_en is None and pausado_desde is not None,
        "resuelto": resuelto_en is not None,
        "en_plazo": (transcurridos <= dias_plazo) if resuelto_en else None,
    }


# --- Data access --------------------------------------------------------------

_TERMINAL = ("completed", "failed", "cancelled", "abandoned")


# Below this many observations a percentile says nothing about the step.
MIN_MUESTRAS_P95 = 5


def _p95(valores: List[float]) -> Optional[float]:
    if len(valores) < MIN_MUESTRAS_P95:
        return None
    ordenados = sorted(valores)
    idx = max(0, int(round(0.95 * (len(ordenados) - 1))))
    return ordenados[idx]


async def _p95_por_paso(workflow_ids: Iterable[str]) -> Dict[Tuple[str, str], float]:
    """Historical p95 duration (hours) per (workflow_id, step_id)."""
    from ..models.workflow import StepExecution

    cursor = StepExecution.get_motor_collection().aggregate([
        {"$match": {"workflow_id": {"$in": list(set(workflow_ids))}, "duration_seconds": {"$ne": None}}},
        {"$group": {"_id": {"w": "$workflow_id", "s": "$step_id"}, "d": {"$push": "$duration_seconds"}}},
    ])
    out: Dict[Tuple[str, str], float] = {}
    async for row in cursor:
        p = _p95(row["d"])
        if p is not None:
            out[(row["_id"]["w"], row["_id"]["s"])] = p / 3600
    return out


ESPERA = "__espera__"


async def _p95_espera(workflow_ids: Iterable[str]) -> Dict[Tuple[str, str], float]:
    """Historical p95 (hours) of queue wait: from instance creation until its
    first step starts. Keyed as (workflow_id, ESPERA)."""
    from ..models.workflow import StepExecution, WorkflowInstance

    wids = list(set(workflow_ids))
    primeros: Dict[str, datetime] = {}
    cursor = StepExecution.get_motor_collection().aggregate([
        {"$match": {"workflow_id": {"$in": wids}, "started_at": {"$ne": None}}},
        {"$group": {"_id": "$instance_id", "inicio": {"$min": "$started_at"}}},
    ])
    async for row in cursor:
        primeros[row["_id"]] = row["inicio"]
    if not primeros:
        return {}
    esperas: Dict[str, List[float]] = {}
    instancias = await WorkflowInstance.find(
        {"instance_id": {"$in": list(primeros)}}
    ).to_list()
    for inst in instancias:
        delta = (primeros[inst.instance_id] - inst.created_at).total_seconds()
        if delta >= 0:
            esperas.setdefault(inst.workflow_id, []).append(delta)
    out: Dict[Tuple[str, str], float] = {}
    for wid, valores in esperas.items():
        p = _p95(valores)
        if p:
            out[(wid, ESPERA)] = p / 3600
    return out


async def _inicio_paso_actual(instance_id: str, step_id: Optional[str]) -> Optional[datetime]:
    from ..models.workflow import StepExecution

    if not step_id:
        return None
    ultima = await StepExecution.find(
        {"instance_id": instance_id, "step_id": step_id}
    ).sort("-started_at").limit(1).to_list()
    if ultima and ultima[0].started_at:
        return ultima[0].started_at
    return None


async def semaforo(
    workflow_id: Optional[str] = None,
    incluir_resueltos_desde: Optional[datetime] = None,
    ahora: Optional[datetime] = None,
    instance_ids: Optional[List[str]] = None,
    incluir_en_captura: bool = False,
) -> List[Dict[str, Any]]:
    """Evaluate every citizen procedure that has entered review.

    Open procedures always; resolved ones only if `incluir_resueltos_desde`
    is given (used by indicators and reports). `instance_ids` restricts the
    evaluation to those procedures, resolved or not (citizen view);
    `incluir_en_captura` also returns procedures still being filled in by
    the citizen, with no legal term running."""
    from ..models.workflow import (
        AssignmentStatus,
        WorkflowDefinition,
        WorkflowInstance,
        WorkflowType,
    )

    ahora = ahora or datetime.utcnow()

    defs = await WorkflowDefinition.find().to_list()
    nombres = {d.workflow_id: d.name for d in defs}
    plazos = {d.workflow_id: plazo_de_metadata(d.metadata) for d in defs}

    filtro: Dict[str, Any] = {
        "workflow_type": {"$ne": WorkflowType.ADMIN},
        "parent_instance_id": None,
    }
    if workflow_id:
        filtro["workflow_id"] = workflow_id
    if instance_ids is not None:
        filtro["instance_id"] = {"$in": list(instance_ids)}
    elif incluir_resueltos_desde:
        filtro["$or"] = [
            {"status": {"$nin": list(_TERMINAL)}},
            {"completed_at": {"$gte": incluir_resueltos_desde}},
        ]
    else:
        filtro["status"] = {"$nin": list(_TERMINAL)}

    tramites = await WorkflowInstance.find(filtro).to_list()
    if not tramites:
        return []

    ids = [t.instance_id for t in tramites]
    hijos = await WorkflowInstance.find(
        {"parent_instance_id": {"$in": ids}, "workflow_type": WorkflowType.ADMIN}
    ).sort("+created_at").to_list()
    revisiones: Dict[str, List[Any]] = {}
    for h in hijos:
        revisiones.setdefault(h.parent_instance_id, []).append(h)

    wids = [t.workflow_id for t in tramites] + [h.workflow_id for h in hijos]
    p95 = await _p95_por_paso(wids)
    p95.update(await _p95_espera(h.workflow_id for h in hijos))

    resultado: List[Dict[str, Any]] = []
    for t in tramites:
        propias = revisiones.get(t.instance_id, [])
        if not propias:
            # Still being captured by the citizen: the legal term has not started.
            if incluir_en_captura:
                resultado.append({
                    "instance_id": t.instance_id,
                    "workflow_id": t.workflow_id,
                    "workflow_name": nombres.get(t.workflow_id, t.workflow_id),
                    "folio": (t.context or {}).get("folio_seguimiento"),
                    "user_id": t.user_id,
                    "status": t.status,
                    "desenlace": estado_efectivo(t),
                    "en_captura": True,
                    "plazo": None,
                    "color": None,
                })
            continue

        abierta = next((h for h in propias if h.status not in _TERMINAL), None)
        resuelto_en = t.completed_at if t.status in _TERMINAL else None

        pausado_desde = None
        if abierta is not None and abierta.assignment_status == AssignmentStatus.MODIFICATION_REQUESTED:
            pausado_desde = abierta.reviewed_at or abierta.updated_at

        plazo = plazos.get(t.workflow_id)
        eval_plazo = (
            evaluar_plazo(propias[0].created_at, plazo, ahora, pausado_desde, resuelto_en)
            if plazo
            else None
        )

        # Where the procedure is now: the open review if any, else the procedure itself.
        foco = abierta or t
        horas = p95_h = None
        color_paso = None
        if resuelto_en is None:
            inicio = await _inicio_paso_actual(foco.instance_id, foco.current_step)
            if inicio is None and not foco.current_step:
                # Review created but nobody has taken it yet: waiting in the queue.
                inicio = foco.created_at
            if inicio:
                horas = round((ahora - inicio).total_seconds() / 3600, 1)
            clave_paso = foco.current_step or ESPERA
            p95_h = p95.get((foco.workflow_id, clave_paso))
            color_paso = color_permanencia(horas, p95_h)

        color = peor_color(eval_plazo["color"] if eval_plazo else None, color_paso)
        if eval_plazo and eval_plazo["pausado"]:
            # Waiting on the citizen: not the institution's delay.
            color = eval_plazo["color"]

        resultado.append({
            "instance_id": t.instance_id,
            "workflow_id": t.workflow_id,
            "workflow_name": nombres.get(t.workflow_id, t.workflow_id),
            "folio": (t.context or {}).get("folio_seguimiento"),
            "user_id": t.user_id,
            "status": t.status,
            "desenlace": estado_efectivo(t),
            "en_captura": False,
            "review_instance_id": (abierta or propias[-1]).instance_id,
            "assigned_user_id": foco.assigned_user_id,
            "assigned_team_id": foco.assigned_team_id,
            "paso_actual": foco.current_step if resuelto_en is None else None,
            "en_espera": resuelto_en is None and not foco.current_step,
            "permanencia_horas": horas,
            "p95_paso_horas": round(p95_h, 1) if p95_h else None,
            "color_paso": color_paso,
            "plazo": eval_plazo,
            "color": color,
        })
    return resultado


def resumen(items: List[Dict[str, Any]]) -> Dict[str, int]:
    conteo = {c: 0 for c in COLORES}
    conteo["sin_plazo"] = 0
    conteo["pausados"] = 0
    for it in items:
        if it["plazo"] is None:
            conteo["sin_plazo"] += 1
        if it["plazo"] and it["plazo"]["pausado"]:
            conteo["pausados"] += 1
        if it["color"] in conteo:
            conteo[it["color"]] += 1
    return conteo


_ESTADO_CIUDADANO = {"verde": "en_tiempo", "amarillo": "por_vencer", "rojo": "vencido"}


def vista_ciudadano(item: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """What the applicant may see about the term of their own procedure: the
    legal axis only (internal step metrics stay with the staff)."""
    if not item:
        return None
    if item.get("en_captura"):
        return {"en_captura": True}
    p = item.get("plazo")
    if not p:
        return {"en_captura": False, "sin_plazo": True}
    return {
        "en_captura": False,
        "sin_plazo": False,
        "fecha_presentacion": p["fecha_presentacion"],
        "fecha_compromiso": p["fecha_compromiso"],
        "plazo_dias": p["plazo_dias"],
        "plazo_habiles": p["plazo_habiles"],
        "dias_transcurridos": p["dias_transcurridos"],
        "pausado": p["pausado"],
        "resuelto": p["resuelto"],
        "en_plazo": p["en_plazo"],
        "estado": None if p["resuelto"] else _ESTADO_CIUDADANO[p["color"]],
    }


async def plazos_de_ciudadano(instance_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    from ..core.fechas import utc_aware

    items = await semaforo(instance_ids=instance_ids, incluir_en_captura=True)
    return {i["instance_id"]: utc_aware(vista_ciudadano(i)) for i in items}
