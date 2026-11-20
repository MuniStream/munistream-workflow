"""
Automatic performance and response-time reports, and traffic-light alerts.

- `calcular` gathers the figures for a period from the same SLA service that
  feeds the traffic light and the public indicators.
- `render_pdf` / `render_xlsx` produce the files; `generar` stores them in S3,
  records a ReporteDesempeno and emails them to the configured recipients
  through the tenant's email channel.
- `evaluar_alertas` notifies each procedure once per escalation (to amber,
  then to red) to the assigned staff member, when their email is known, and a
  digest to the configured recipients.

Periods are computed in Mexico City time (UTC-6, no daylight saving since 2022).
"""
from __future__ import annotations

import io
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any, Dict, List, Optional, Tuple

from ..core.config import settings

logger = logging.getLogger(__name__)

from .sla import TZ_OFFSET

TZ_MX = timezone(TZ_OFFSET)
_RANK = {"verde": 0, "amarillo": 1, "rojo": 2}


# --- Periods -----------------------------------------------------------------

def _a_utc(local: datetime) -> datetime:
    return local.replace(tzinfo=TZ_MX).astimezone(timezone.utc).replace(tzinfo=None)


def periodo_semanal(ahora: Optional[datetime] = None) -> Tuple[datetime, datetime]:
    """Previous Monday-to-Monday week, local time, returned in naive UTC."""
    local = (ahora or datetime.utcnow()).replace(tzinfo=timezone.utc).astimezone(TZ_MX).replace(tzinfo=None)
    lunes = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return _a_utc(lunes - timedelta(days=7)), _a_utc(lunes)


def periodo_mensual(ahora: Optional[datetime] = None) -> Tuple[datetime, datetime]:
    """Previous calendar month, local time, returned in naive UTC."""
    local = (ahora or datetime.utcnow()).replace(tzinfo=timezone.utc).astimezone(TZ_MX).replace(tzinfo=None)
    inicio_mes = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    anterior = (inicio_mes - timedelta(days=1)).replace(day=1)
    return _a_utc(anterior), _a_utc(inicio_mes)


def _fecha(d: datetime) -> str:
    local = d.replace(tzinfo=timezone.utc).astimezone(TZ_MX)
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    return f"{local.day} de {meses[local.month - 1]} de {local.year}"


# --- Figures -----------------------------------------------------------------

def _p95(valores: List[float]) -> Optional[float]:
    if not valores:
        return None
    v = sorted(valores)
    return v[max(0, int(round(0.95 * (len(v) - 1))))]


async def calcular(desde: datetime, hasta: datetime) -> Dict[str, Any]:
    from ..models.atencion import Feedback, SolicitudAtencion
    from ..models.workflow import StepExecution, WorkflowDefinition, WorkflowInstance
    from . import sla

    items = await sla.semaforo(incluir_resueltos_desde=desde, ahora=hasta)
    fechas_fin = {
        w.instance_id: w.completed_at
        for w in await WorkflowInstance.find(
            {"instance_id": {"$in": [i["instance_id"] for i in items if i["plazo"] and i["plazo"]["resuelto"]]}}
        ).to_list()
    }

    def presentado_en_periodo(i):
        return i["plazo"] and desde <= i["plazo"]["fecha_presentacion"] < hasta

    def resuelto_en_periodo(i):
        fin = fechas_fin.get(i["instance_id"])
        return (
            i["plazo"] and i["plazo"]["resuelto"] and fin and desde <= fin < hasta
            and i["desenlace"] in ("completed", "rejected")
        )

    abiertos = [i for i in items if i["plazo"] and not i["plazo"]["resuelto"]]

    por_wf: Dict[str, Dict[str, Any]] = {}
    for i in items:
        if not i["plazo"]:
            continue
        w = por_wf.setdefault(i["workflow_id"], {
            "workflow_id": i["workflow_id"], "nombre": i["workflow_name"],
            "plazo_dias": i["plazo"]["plazo_dias"],
            "recibidos": 0, "resueltos": 0, "aprobados": 0, "rechazados": 0,
            "en_plazo": 0, "dias": [], "abiertos": 0, "vencidos_abiertos": 0,
        })
        if presentado_en_periodo(i):
            w["recibidos"] += 1
        if resuelto_en_periodo(i):
            w["resueltos"] += 1
            w["dias"].append(i["plazo"]["dias_transcurridos"])
            w["en_plazo"] += 1 if i["plazo"]["en_plazo"] else 0
            w["aprobados" if i["desenlace"] == "completed" else "rechazados"] += 1
        if not i["plazo"]["resuelto"]:
            w["abiertos"] += 1
            if i["plazo"]["color"] == "rojo":
                w["vencidos_abiertos"] += 1

    tramites = []
    for w in sorted(por_wf.values(), key=lambda x: (-x["recibidos"], x["nombre"])):
        d = w.pop("dias")
        tramites.append({
            **w,
            "porcentaje_en_plazo": round(100 * w["en_plazo"] / w["resueltos"], 1) if w["resueltos"] else None,
            "dias_promedio": round(mean(d), 1) if d else None,
            "dias_max": max(d) if d else None,
        })

    # Response time per step: steps finished in the period, staff review
    # workflows and citizen procedures alike.
    nombres_wf = {d.workflow_id: d.name for d in await WorkflowDefinition.find().to_list()}
    duraciones: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    async for row in StepExecution.get_motor_collection().aggregate([
        {"$match": {"completed_at": {"$gte": desde, "$lt": hasta}, "duration_seconds": {"$gt": 0}}},
        {"$project": {"workflow_id": 1, "step_id": 1, "duration_seconds": 1}},
    ]):
        duraciones[(row["workflow_id"], row["step_id"])].append(row["duration_seconds"] / 3600)
    pasos = sorted(
        (
            {
                "workflow": nombres_wf.get(wid, wid),
                "paso": sid.replace("_", " "),
                "ejecuciones": len(v),
                "horas_promedio": round(mean(v), 1),
                "horas_p95": round(_p95(v), 1),
            }
            for (wid, sid), v in duraciones.items()
        ),
        key=lambda x: -x["horas_promedio"],
    )[:15]

    califs = [f.calificacion for f in await Feedback.find(
        {"created_at": {"$gte": desde, "$lt": hasta}}
    ).to_list()]
    nuevas = await SolicitudAtencion.find({"created_at": {"$gte": desde, "$lt": hasta}}).to_list()
    respondidas = [s for s in nuevas if s.primera_respuesta_at]
    horas_resp = [(s.primera_respuesta_at - s.created_at).total_seconds() / 3600 for s in respondidas]

    en_riesgo = sorted(
        (i for i in abiertos if i["color"] in ("amarillo", "rojo")),
        key=lambda i: (-_RANK[i["color"]], -(i["plazo"]["avance"] or 0)),
    )

    total_resueltos = sum(t["resueltos"] for t in tramites)
    total_en_plazo = sum(t["en_plazo"] for t in tramites)
    todos_dias = [
        i["plazo"]["dias_transcurridos"] for i in items if resuelto_en_periodo(i)
    ]
    semaforo = {c: sum(1 for i in abiertos if i["color"] == c) for c in ("verde", "amarillo", "rojo")}

    return {
        "desde": desde,
        "hasta": hasta,
        "institucion": settings.TENANT_NAME or settings.PROJECT_NAME,
        "resumen": {
            "recibidos": sum(t["recibidos"] for t in tramites),
            "resueltos": total_resueltos,
            "aprobados": sum(t["aprobados"] for t in tramites),
            "rechazados": sum(t["rechazados"] for t in tramites),
            "porcentaje_en_plazo": round(100 * total_en_plazo / total_resueltos, 1) if total_resueltos else None,
            "dias_promedio": round(mean(todos_dias), 1) if todos_dias else None,
            "en_revision": len(abiertos),
            "semaforo": semaforo,
            "satisfaccion": round(mean(califs), 2) if califs else None,
            "opiniones": len(califs),
            "solicitudes_atencion": len(nuevas),
            "solicitudes_respondidas": len(respondidas),
            "horas_primera_respuesta": round(mean(horas_resp), 1) if horas_resp else None,
        },
        "tramites": tramites,
        "pasos": pasos,
        "en_riesgo": [
            {
                "folio": i.get("folio") or i["instance_id"][:8],
                "tramite": i["workflow_name"],
                "color": i["color"],
                "dias": i["plazo"]["dias_transcurridos"],
                "plazo": i["plazo"]["plazo_dias"],
                "compromiso": i["plazo"]["fecha_compromiso"],
                "paso": (i.get("paso_actual") or "en espera de atención").replace("_", " "),
            }
            for i in en_riesgo[:50]
        ],
    }


# --- Rendering ---------------------------------------------------------------

def _n(v, sufijo="") -> str:
    return "s/d" if v is None else f"{v}{sufijo}"


def render_pdf(datos: Dict[str, Any]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    guinda = colors.HexColor("#611232")
    estilos = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=estilos["Title"], textColor=guinda, fontSize=16, spaceAfter=4)
    h2 = ParagraphStyle("h2", parent=estilos["Heading2"], textColor=guinda, fontSize=12, spaceBefore=12)
    normal = ParagraphStyle("n", parent=estilos["Normal"], fontSize=9, leading=12)
    celda = ParagraphStyle("c", parent=normal, fontSize=8, leading=10)

    cabecera = ParagraphStyle("hc", parent=celda, textColor=colors.white, fontName="Helvetica-Bold")

    def tabla(filas, anchos):
        data = [[Paragraph(str(c), cabecera if n == 0 else celda) for c in f] for n, f in enumerate(filas)]
        t = Table(data, colWidths=anchos, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), guinda),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#C9C2BC")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F6F4")]),
        ]))
        return t

    r = datos["resumen"]
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=1.6 * cm, rightMargin=1.6 * cm,
                            topMargin=1.5 * cm, bottomMargin=1.5 * cm,
                            title="Reporte de desempeño", author=datos["institucion"])
    flujo = [
        Paragraph("Reporte de desempeño y tiempos de respuesta", h1),
        Paragraph(f"{datos['institucion']}. Periodo del {_fecha(datos['desde'])} al "
                  f"{_fecha(datos['hasta'] - timedelta(seconds=1))}.", normal),
        Paragraph(f"Generado el {_fecha(datetime.utcnow())}. Plazos en días hábiles, contados desde que "
                  "el trámite ingresa a revisión.", normal),
        Paragraph("Resumen", h2),
        tabla([
            ["Indicador", "Valor"],
            ["Solicitudes recibidas (ingresaron a revisión)", r["recibidos"]],
            ["Solicitudes resueltas (aprobadas / rechazadas)", f"{r['resueltos']} ({r['aprobados']} / {r['rechazados']})"],
            ["Resueltas dentro del plazo legal", _n(r["porcentaje_en_plazo"], " %")],
            ["Días hábiles promedio de resolución", _n(r["dias_promedio"])],
            ["En revisión al cierre del periodo", r["en_revision"]],
            ["Semáforo al cierre (en tiempo / por vencer / en riesgo)",
             f"{r['semaforo']['verde']} / {r['semaforo']['amarillo']} / {r['semaforo']['rojo']}"],
            ["Satisfacción promedio (opiniones)", f"{_n(r['satisfaccion'])} ({r['opiniones']})"],
            ["Solicitudes de atención (respondidas)", f"{r['solicitudes_atencion']} ({r['solicitudes_respondidas']})"],
            ["Horas promedio a la primera respuesta", _n(r["horas_primera_respuesta"])],
        ], [11 * cm, 6.8 * cm]),
        Paragraph("Desempeño por trámite", h2),
    ]
    if datos["tramites"]:
        flujo.append(tabla(
            [["Trámite", "Plazo", "Recib.", "Resuel.", "En plazo", "Días prom.", "Días máx.", "Abiertos", "Vencidos"]]
            + [[t["nombre"], t["plazo_dias"], t["recibidos"], t["resueltos"], _n(t["porcentaje_en_plazo"], " %"),
                _n(t["dias_promedio"]), _n(t["dias_max"]), t["abiertos"], t["vencidos_abiertos"]]
               for t in datos["tramites"]],
            [5.6 * cm, 1.2 * cm, 1.3 * cm, 1.4 * cm, 1.6 * cm, 1.6 * cm, 1.6 * cm, 1.5 * cm, 1.5 * cm],
        ))
    else:
        flujo.append(Paragraph("Sin trámites con plazo legal en el periodo.", normal))

    flujo.append(Paragraph("Tiempos de respuesta por paso (los 15 más lentos)", h2))
    if datos["pasos"]:
        flujo.append(tabla(
            [["Trámite", "Paso", "Ejecuciones", "Horas prom.", "Horas p95"]]
            + [[p["workflow"], p["paso"], p["ejecuciones"], p["horas_promedio"], p["horas_p95"]] for p in datos["pasos"]],
            [6 * cm, 5 * cm, 2.2 * cm, 2.3 * cm, 2.3 * cm],
        ))
    else:
        flujo.append(Paragraph("Sin pasos concluidos en el periodo.", normal))

    flujo.append(Paragraph("Trámites por vencer y en riesgo al cierre", h2))
    if datos["en_riesgo"]:
        flujo.append(tabla(
            [["Folio", "Trámite", "Estado", "Días", "Compromiso", "Paso actual"]]
            + [[e["folio"], e["tramite"], "En riesgo" if e["color"] == "rojo" else "Por vencer",
                f"{e['dias']} de {e['plazo']}", _fecha(e["compromiso"]), e["paso"]] for e in datos["en_riesgo"]],
            [3.4 * cm, 5 * cm, 1.8 * cm, 1.6 * cm, 3 * cm, 3 * cm],
        ))
    else:
        flujo.append(Paragraph("Ningún trámite por vencer ni en riesgo.", normal))
    flujo.append(Spacer(1, 12))
    doc.build(flujo)
    return buf.getvalue()


def render_xlsx(datos: Dict[str, Any]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    cab = Font(bold=True, color="FFFFFF")
    relleno = PatternFill("solid", fgColor="611232")

    def hoja(ws, filas):
        for f in filas:
            ws.append(f)
        for c in ws[1]:
            c.font, c.fill = cab, relleno
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = min(60, max(12, max(len(str(c.value or "")) for c in col) + 2))

    r = datos["resumen"]
    ws = wb.active
    ws.title = "Resumen"
    hoja(ws, [
        ["Indicador", "Valor"],
        ["Institución", datos["institucion"]],
        ["Desde", datos["desde"]],
        ["Hasta", datos["hasta"]],
        ["Solicitudes recibidas", r["recibidos"]],
        ["Solicitudes resueltas", r["resueltos"]],
        ["Aprobadas", r["aprobados"]],
        ["Rechazadas", r["rechazados"]],
        ["% resueltas en plazo", r["porcentaje_en_plazo"]],
        ["Días hábiles promedio", r["dias_promedio"]],
        ["En revisión al cierre", r["en_revision"]],
        ["Semáforo en tiempo", r["semaforo"]["verde"]],
        ["Semáforo por vencer", r["semaforo"]["amarillo"]],
        ["Semáforo en riesgo", r["semaforo"]["rojo"]],
        ["Satisfacción promedio", r["satisfaccion"]],
        ["Opiniones", r["opiniones"]],
        ["Solicitudes de atención", r["solicitudes_atencion"]],
        ["Respondidas", r["solicitudes_respondidas"]],
        ["Horas a primera respuesta", r["horas_primera_respuesta"]],
    ])
    hoja(wb.create_sheet("Por trámite"), [[
        "Trámite", "Plazo (días hábiles)", "Recibidos", "Resueltos", "Aprobados", "Rechazados",
        "% en plazo", "Días promedio", "Días máximo", "Abiertos", "Vencidos abiertos",
    ]] + [[t["nombre"], t["plazo_dias"], t["recibidos"], t["resueltos"], t["aprobados"], t["rechazados"],
           t["porcentaje_en_plazo"], t["dias_promedio"], t["dias_max"], t["abiertos"], t["vencidos_abiertos"]]
          for t in datos["tramites"]])
    hoja(wb.create_sheet("Pasos"), [["Trámite", "Paso", "Ejecuciones", "Horas promedio", "Horas p95"]]
         + [[p["workflow"], p["paso"], p["ejecuciones"], p["horas_promedio"], p["horas_p95"]] for p in datos["pasos"]])
    hoja(wb.create_sheet("En riesgo"), [["Folio", "Trámite", "Estado", "Días transcurridos", "Plazo", "Fecha compromiso", "Paso actual"]]
         + [[e["folio"], e["tramite"], "En riesgo" if e["color"] == "rojo" else "Por vencer", e["dias"], e["plazo"],
             e["compromiso"], e["paso"]] for e in datos["en_riesgo"]])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# --- Delivery ----------------------------------------------------------------

async def configuracion():
    from ..models.reportes import ConfiguracionReportes

    tenant = settings.TENANT_ID or "default"
    cfg = await ConfiguracionReportes.find_one(ConfiguracionReportes.tenant_id == tenant)
    return cfg or ConfiguracionReportes(tenant_id=tenant)


async def _enviar_correo(destinatarios: List[str], asunto: str, cuerpo: str, adjuntos=None) -> List[Dict[str, Any]]:
    """Send through the tenant's email channel; one result per recipient."""
    from ..notifier.encryption import decrypt_credentials
    from ..notifier.handlers import get_handler, with_infra_credentials
    from ..notifier.handlers.base import OutboundMessage
    from ..notifier.models import NotificationChannelConfig

    if not destinatarios:
        return []
    cfg = await NotificationChannelConfig.find_one(
        {"tenant_id": settings.TENANT_ID, "channel": "email", "enabled": True}
    )
    if not cfg:
        return [{"destinatario": d, "enviado": False, "error": "canal de correo no configurado"} for d in destinatarios]
    creds = decrypt_credentials(cfg.credentials_encrypted) if cfg.credentials_encrypted else {}
    creds = with_infra_credentials("email", creds)
    handler = get_handler("email")
    resultados = []
    for d in destinatarios:
        try:
            await handler.send(OutboundMessage(
                recipient=d, subject=asunto, body=cuerpo, from_address=cfg.from_address,
                channel_credentials=creds, tenant_id=settings.TENANT_ID, attachments=adjuntos or [],
            ))
            resultados.append({"destinatario": d, "enviado": True, "error": None})
        except Exception as exc:  # noqa: BLE001 - recorded per recipient
            logger.warning("report email to %s failed: %s", d, exc)
            resultados.append({"destinatario": d, "enviado": False, "error": str(exc)[:300]})
    return resultados


async def generar(periodo: str, desde: datetime, hasta: datetime, generado_por: str = "sistema",
                  enviar: bool = True):
    from ..models.reportes import EnvioReporte, ReporteDesempeno
    from ..notifier.handlers.base import Attachment
    from .s3_storage import default_bucket, upload_bytes

    datos = await calcular(desde, hasta)
    pdf = render_pdf(datos)
    xlsx = render_xlsx(datos)

    reporte_id = str(uuid.uuid4())
    bucket = default_bucket()
    base = f"reportes/{settings.TENANT_ID or 'default'}/{reporte_id}"
    nombre = f"reporte-desempeno-{_a_local_str(desde)}-{_a_local_str(hasta - timedelta(seconds=1))}"
    upload_bytes(bucket, f"{base}.pdf", pdf, "application/pdf")
    upload_bytes(bucket, f"{base}.xlsx", xlsx,
                 "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    reporte = ReporteDesempeno(
        reporte_id=reporte_id, periodo=periodo, desde=desde, hasta=hasta,
        generado_por=generado_por, bucket=bucket,
        pdf_key=f"{base}.pdf", xlsx_key=f"{base}.xlsx",
        resumen=datos["resumen"],
    )
    await reporte.insert()

    if enviar:
        cfg = await configuracion()
        r = datos["resumen"]
        cuerpo = (
            f"Reporte de desempeño de {datos['institucion']}, del {_fecha(desde)} al "
            f"{_fecha(hasta - timedelta(seconds=1))}.\n\n"
            f"- Solicitudes recibidas: {r['recibidos']}\n"
            f"- Solicitudes resueltas: {r['resueltos']}\n"
            f"- Resueltas dentro del plazo legal: {_n(r['porcentaje_en_plazo'], ' %')}\n"
            f"- Días hábiles promedio de resolución: {_n(r['dias_promedio'])}\n"
            f"- En revisión al cierre: {r['en_revision']} (por vencer: {r['semaforo']['amarillo']}, "
            f"en riesgo: {r['semaforo']['rojo']})\n\n"
            "Se adjuntan el reporte en PDF y los datos en Excel.\n"
        )
        envios = await _enviar_correo(
            cfg.destinatarios, f"Reporte de desempeño ({_fecha(desde)} al {_fecha(hasta - timedelta(seconds=1))})",
            cuerpo,
            [Attachment(f"{nombre}.pdf", pdf, "application/pdf"),
             Attachment(f"{nombre}.xlsx", xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")],
        )
        reporte.envios = [EnvioReporte(**e) for e in envios]
        await reporte.save()
    return reporte


def _a_local_str(d: datetime) -> str:
    return d.replace(tzinfo=timezone.utc).astimezone(TZ_MX).strftime("%Y%m%d")


# --- Alerts ------------------------------------------------------------------

def _admin_base_url() -> str:
    """Admin portal URL for links in staff emails. ADMIN_BASE_URL wins; else
    the platform's subdomain contract (admin.<citizen portal host>)."""
    if settings.ADMIN_BASE_URL:
        return settings.ADMIN_BASE_URL.rstrip("/")
    from urllib.parse import urlsplit, urlunsplit

    front = settings.FRONTEND_BASE_URL or ""
    partes = urlsplit(front)
    if not partes.netloc:
        return ""
    return urlunsplit(partes._replace(netloc=f"admin.{partes.netloc}", path="")).rstrip("/")


async def evaluar_alertas(ahora: Optional[datetime] = None) -> Dict[str, Any]:
    """Notify procedures that turned amber or red since the last alert."""
    from bson import ObjectId

    from ..models.reportes import AlertaSemaforo
    from ..models.user import UserModel
    from . import sla

    items = await sla.semaforo(ahora=ahora)
    previas = {a.instance_id: a for a in await AlertaSemaforo.find(
        {"instance_id": {"$in": [i["instance_id"] for i in items]}}
    ).to_list()}

    nuevas = []
    for i in items:
        color = i["color"]
        previa = previas.get(i["instance_id"])
        if color in ("amarillo", "rojo") and (previa is None or _RANK[color] > _RANK.get(previa.color, -1)):
            nuevas.append(i)
        # Record the current colour (also when it improves) so a later
        # escalation is notified again.
        if color in _RANK and (previa is None or previa.color != color):
            if previa is None:
                await AlertaSemaforo(instance_id=i["instance_id"], color=color).insert()
            else:
                previa.color, previa.notificado_at = color, datetime.utcnow()
                await previa.save()

    if not nuevas:
        return {"nuevas": 0, "envios": []}

    base_url = _admin_base_url()

    def linea(i):
        p = i["plazo"] or {}
        estado = "EN RIESGO" if i["color"] == "rojo" else "POR VENCER"
        plazo = f"{p.get('dias_transcurridos')} de {p.get('plazo_dias')} días hábiles" if p else "sin plazo legal"
        paso = (i.get("paso_actual") or "en espera de atención").replace("_", " ")
        enlace = f" {base_url}/instances/{i['review_instance_id']}" if base_url else ""
        return f"- [{estado}] {i['workflow_name']} ({i.get('folio') or i['instance_id'][:8]}): {plazo}; paso: {paso}.{enlace}"

    # Individual alerts to assigned staff with a known email.
    por_usuario: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for i in nuevas:
        if i.get("assigned_user_id"):
            por_usuario[str(i["assigned_user_id"])].append(i)
    oids = [ObjectId(u) for u in por_usuario if ObjectId.is_valid(u)]
    correos = {str(u.id): u.email for u in await UserModel.find({"_id": {"$in": oids}}).to_list()} if oids else {}

    envios = []
    for uid, lista in por_usuario.items():
        if uid in correos:
            envios += await _enviar_correo(
                [correos[uid]], "Trámites asignados por vencer o en riesgo",
                "Los siguientes trámites asignados a usted cambiaron de estado en el semáforo de atención:\n\n"
                + "\n".join(linea(i) for i in lista) + "\n",
            )

    cfg = await configuracion()
    if cfg.alertas:
        envios += await _enviar_correo(
            cfg.destinatarios, f"Semáforo de atención: {len(nuevas)} trámite(s) por vencer o en riesgo",
            'Trámites que cambiaron a "por vencer" o "en riesgo" desde el último aviso:\n\n'
            + "\n".join(linea(i) for i in nuevas) + "\n",
        )
    return {"nuevas": len(nuevas), "envios": envios}


# --- Scheduled entry points (arq cron) ----------------------------------------

async def cron_reporte_semanal(ctx) -> str:
    cfg = await configuracion()
    if not cfg.semanal:
        return "disabled"
    desde, hasta = periodo_semanal()
    r = await generar("semanal", desde, hasta)
    return r.reporte_id


async def cron_reporte_mensual(ctx) -> str:
    cfg = await configuracion()
    if not cfg.mensual:
        return "disabled"
    desde, hasta = periodo_mensual()
    r = await generar("mensual", desde, hasta)
    return r.reporte_id


async def cron_alertas_semaforo(ctx) -> str:
    res = await evaluar_alertas()
    return f"{res['nuevas']} alerts"
