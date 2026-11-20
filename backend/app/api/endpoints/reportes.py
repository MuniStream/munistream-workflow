"""
Performance reports: history, on-demand generation, download and the
settings for the scheduled reports and traffic-light alerts.
"""
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, EmailStr, Field

from ...auth.provider import require_permission
from ...core.config import settings
from ...core.fechas import utc_aware
from ...models.reportes import ConfiguracionReportes, ReporteDesempeno
from ...services import reportes as svc

router = APIRouter()

_VER = require_permission("VIEW_INSTANCES")
_GESTIONAR = require_permission("MANAGE_WORKFLOWS")


def _salida(r: ReporteDesempeno) -> dict:
    return utc_aware({
        "reporte_id": r.reporte_id,
        "periodo": r.periodo,
        "desde": r.desde,
        "hasta": r.hasta,
        "generado_at": r.generado_at,
        "generado_por": r.generado_por,
        "resumen": r.resumen,
        "envios": [e.model_dump() for e in r.envios],
    })


@router.get("/reportes")
async def listar_reportes(limit: int = Query(50, ge=1, le=200), _: dict = Depends(_VER)):
    items = await ReporteDesempeno.find().sort("-generado_at").limit(limit).to_list()
    return {"reportes": [_salida(r) for r in items]}


class GenerarIn(BaseModel):
    periodo: str = Field("a_demanda", description="semanal | mensual | a_demanda")
    desde: Optional[datetime] = None
    hasta: Optional[datetime] = None
    enviar: bool = False


@router.post("/reportes", status_code=201)
async def generar_reporte(body: GenerarIn, user: dict = Depends(_GESTIONAR)):
    if body.periodo == "semanal":
        desde, hasta = svc.periodo_semanal()
    elif body.periodo == "mensual":
        desde, hasta = svc.periodo_mensual()
    else:
        hasta = body.hasta or datetime.utcnow()
        desde = body.desde or (hasta - timedelta(days=30))
    if desde >= hasta:
        raise HTTPException(status_code=400, detail="'desde' must be earlier than 'hasta'")
    quien = user.get("name") or user.get("preferred_username") or str(user.get("sub") or "")
    r = await svc.generar(body.periodo, desde, hasta, generado_por=quien, enviar=body.enviar)
    return _salida(r)


@router.get("/reportes/configuracion")
async def leer_configuracion(_: dict = Depends(_VER)):
    cfg = await svc.configuracion()
    return {"destinatarios": cfg.destinatarios, "semanal": cfg.semanal, "mensual": cfg.mensual, "alertas": cfg.alertas}


class ConfiguracionIn(BaseModel):
    destinatarios: List[EmailStr] = Field(default_factory=list, max_length=50)
    semanal: bool = True
    mensual: bool = True
    alertas: bool = True


@router.put("/reportes/configuracion")
async def guardar_configuracion(body: ConfiguracionIn, user: dict = Depends(_GESTIONAR)):
    cfg = await svc.configuracion()
    cfg.destinatarios = sorted({str(d).lower() for d in body.destinatarios})
    cfg.semanal, cfg.mensual, cfg.alertas = body.semanal, body.mensual, body.alertas
    cfg.updated_at = datetime.utcnow()
    cfg.updated_by = user.get("preferred_username") or str(user.get("sub") or "")
    await cfg.save()
    return {"destinatarios": cfg.destinatarios, "semanal": cfg.semanal, "mensual": cfg.mensual, "alertas": cfg.alertas}


@router.post("/reportes/alertas/evaluar")
async def evaluar_alertas(_: dict = Depends(_GESTIONAR)):
    """Run the daily traffic-light alert evaluation now."""
    res = await svc.evaluar_alertas()
    return {"nuevas": res["nuevas"], "envios": res["envios"]}


_TIPOS = {
    "pdf": ("pdf_key", "application/pdf"),
    "xlsx": ("xlsx_key", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
}


@router.get("/reportes/{reporte_id}/{formato}")
async def descargar(reporte_id: str, formato: str, _: dict = Depends(_VER)):
    if formato not in _TIPOS:
        raise HTTPException(status_code=404, detail="Unknown format")
    r = await ReporteDesempeno.find_one(ReporteDesempeno.reporte_id == reporte_id)
    if not r:
        raise HTTPException(status_code=404, detail="Report not found")
    from ...services.s3_storage import download_bytes

    campo, tipo = _TIPOS[formato]
    contenido = download_bytes(r.bucket, getattr(r, campo))
    nombre = f"reporte-desempeno-{svc._a_local_str(r.desde)}-{svc._a_local_str(r.hasta - timedelta(seconds=1))}.{formato}"
    return Response(content=contenido, media_type=tipo,
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})
