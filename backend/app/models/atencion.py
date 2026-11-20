"""
Citizen service models: feedback on a finished procedure and support
requests (help desk threads between a citizen and the institution's staff).
"""
from datetime import datetime
from enum import Enum
from typing import List, Optional

from beanie import Document, Indexed
from pydantic import BaseModel, Field


class Feedback(Document):
    """Rating left by the applicant once their procedure is resolved."""

    instance_id: Indexed(str, unique=True)
    workflow_id: Indexed(str)
    customer_id: Indexed(str)
    calificacion: int = Field(..., ge=1, le=5)
    comentario: Optional[str] = Field(None, max_length=1000)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "citizen_feedback"


class CategoriaAtencion(str, Enum):
    DUDA_TRAMITE = "duda_tramite"
    PROBLEMA_TECNICO = "problema_tecnico"
    QUEJA = "queja"
    SUGERENCIA = "sugerencia"
    OTRO = "otro"


class EstadoAtencion(str, Enum):
    # Waiting on the institution
    ABIERTA = "abierta"
    # Answered, waiting on the citizen (or nothing else to do)
    RESPONDIDA = "respondida"
    CERRADA = "cerrada"


class MensajeAtencion(BaseModel):
    autor: str = Field(..., description="ciudadano | personal")
    autor_id: str
    autor_nombre: Optional[str] = None
    texto: str = Field(..., min_length=1, max_length=4000)
    fecha: datetime = Field(default_factory=datetime.utcnow)


class SolicitudAtencion(Document):
    """A citizen's support request and its conversation with the staff."""

    solicitud_id: Indexed(str, unique=True)
    folio: Indexed(str, unique=True)
    customer_id: Indexed(str)
    asunto: str = Field(..., min_length=3, max_length=200)
    categoria: CategoriaAtencion
    instance_id: Optional[str] = None
    workflow_id: Optional[str] = None
    estado: EstadoAtencion = EstadoAtencion.ABIERTA
    asignado_a: Optional[str] = None
    mensajes: List[MensajeAtencion] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    # First staff reply, for response-time metrics.
    primera_respuesta_at: Optional[datetime] = None
    cerrada_at: Optional[datetime] = None

    class Settings:
        name = "citizen_support_requests"
        indexes = [[("estado", 1), ("updated_at", -1)]]
