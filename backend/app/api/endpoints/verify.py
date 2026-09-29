"""
Verificación pública de documentos: lo que responde al escanear el QR.

Es un endpoint sin autenticación a propósito —quien verifica un documento es un
tercero: un inspector en el muelle, un comprador, otra dependencia— así que expone
solo lo que permite constatar que el documento es auténtico y está vigente. Nunca
el contenido de la entidad.

La regla de negocio vive en `services/entity_verification.py`, no aquí: es lo que
hay que poder probar sin Mongo.

Antes había además un `GET /verify/metadata/{entity_id}` que devolvía `entity.data`
íntegro **sin autenticación**: quien conociera un `entity_id` obtenía todos los
datos personales del ciudadano. No lo llamaba nadie —ni el portal, ni el admin, ni
ningún tenant— así que se eliminó. Los datos de una entidad se piden por
`/public/entities/{id}`, que sí comprueba quién es el dueño.
"""
from fastapi import APIRouter, Query
from typing import Optional
import logging

from ...services.entity_service import EntityService
from ...services.entity_verification import dictaminar

router = APIRouter()

logger = logging.getLogger(__name__)


@router.get("/{entity_id}")
async def verify_entity(
    entity_id: str,
    checksum: Optional[str] = Query(
        None, description="Checksum impreso en el QR, para constatar los datos del documento"
    ),
):
    """
    Constata la autenticidad y la vigencia de un documento.

    El `checksum` es opcional: los QR emitidos lo llevan, pero la liga también se
    puede teclear a mano, y eso no es una anomalía.

    Ejemplo: https://conapesca.dev.munistream.com/verify/pescador_rnpa_a1b2c3d4?checksum=abc123
    """
    try:
        # Sin restricción de dueño: verificar es justamente un acto de un tercero.
        entity = await EntityService.get_entity(entity_id, owner_user_id=None)

        if not entity:
            return {
                "valid": False,
                "entity_id": entity_id,
                "error": "No existe ningún documento con este folio.",
            }

        dictamen = dictaminar(entity, checksum=checksum)

        return {
            "valid": dictamen["valid"],
            "entity_id": entity.entity_id,
            "entity_type": entity.entity_type,
            "name": entity.name,
            "status": entity.status,
            "issued_by_authority": dictamen["issued_by_authority"],
            "verified": entity.verified,
            "verification_date": (
                entity.verification_date.isoformat() if entity.verification_date else None
            ),
            "verified_by": entity.verified_by,
            "created_at": entity.created_at.isoformat() if entity.created_at else None,
            "valid_until": dictamen["valid_until"],
            "authority": entity.data.get("authority") or "CONAPESCA",
            "document_type": entity.data.get("document_type") or entity.entity_type,
            "checksum_valid": dictamen["checksum_valid"],
            "checksum_provided": dictamen["checksum_provided"],
            "validation_errors": dictamen["motivos"] or None,
        }

    except Exception as e:
        logger.error(f"Error al verificar el documento {entity_id}: {e}", exc_info=True)
        return {
            "valid": False,
            "entity_id": entity_id,
            "error": "No fue posible completar la verificación en este momento.",
        }
