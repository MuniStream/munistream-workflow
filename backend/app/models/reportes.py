"""
Automatic performance reports and traffic-light alerts.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from beanie import Document, Indexed
from pydantic import BaseModel, Field


class EnvioReporte(BaseModel):
    destinatario: str
    enviado: bool
    error: Optional[str] = None
    fecha: datetime = Field(default_factory=datetime.utcnow)


class ReporteDesempeno(Document):
    """A generated performance report (PDF + XLSX stored in S3)."""

    reporte_id: Indexed(str, unique=True)
    periodo: str = Field(..., description="semanal | mensual | a_demanda")
    desde: datetime
    hasta: datetime
    generado_at: datetime = Field(default_factory=datetime.utcnow)
    generado_por: str = Field("sistema", description="'sistema' (scheduled) or the staff user")
    bucket: str
    pdf_key: str
    xlsx_key: str
    resumen: Dict[str, Any] = Field(default_factory=dict)
    envios: List[EnvioReporte] = Field(default_factory=list)

    class Settings:
        name = "performance_reports"
        indexes = [[("generado_at", -1)]]


class ConfiguracionReportes(Document):
    """Per-tenant settings for scheduled reports and alerts (one document)."""

    tenant_id: Indexed(str, unique=True)
    destinatarios: List[str] = Field(default_factory=list)
    semanal: bool = True
    mensual: bool = True
    alertas: bool = True
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    updated_by: Optional[str] = None

    class Settings:
        name = "report_settings"


class AlertaSemaforo(Document):
    """Last traffic-light colour notified for a procedure, so an alert goes
    out once per escalation and not every day."""

    instance_id: Indexed(str, unique=True)
    color: str
    notificado_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "traffic_light_alerts"
