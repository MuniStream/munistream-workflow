"""
File Download Endpoints

Descarga de archivos de S3/MinIO a partir de su ``s3_key``.

`GET /download/{s3_key}` se sirve a veces desde un ancla del navegador, donde
no cabe una cabecera ``Authorization``. Durante mucho tiempo eso se resolvio
dejando la ruta completamente abierta, lo que convertia la s3_key --que es
derivable: ``tmp/<instance>/<task>/<campo>/...``-- en la unica credencial de
todo el bucket de subidas.

Ahora la autorizacion ocurre en `POST /grant`, que exige sesion, comprueba que
el archivo pertenezca a un recurso que el solicitante puede ver, y emite un
token firmado de vida corta. El flag `FILES_DOWNLOAD_REQUIRE_TOKEN` permite
desplegar este backend antes que los frontends: mientras este en False la ruta
sigue aceptando peticiones sin token, para no romper enlaces en vuelo.
"""

import io
import os
import mimetypes
import asyncio
from typing import Any, Dict, List, Optional, Set

from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ...services import s3_storage
from ...services.entity_service import EntityService
from ...core.config import settings
from ...core.file_tokens import issue_token, verify_token
from ...core.logging_config import get_workflow_logger
from ...models.customer import Customer
from .public_auth import get_current_customer

logger = get_workflow_logger(__name__)
router = APIRouter()

# Profundidad maxima al recorrer el `data` de una entidad buscando referencias.
_MAX_DEPTH = 8


class FileGrantScope(BaseModel):
    entity_id: Optional[str] = None


class FileGrantRequest(BaseModel):
    scope: FileGrantScope
    s3_keys: List[str] = Field(default_factory=list, max_length=200)


def _collect_entity_s3_keys(value: Any, depth: int, out: Set[str]) -> None:
    """Recoge las s3_key referenciadas en el `data` de una entidad.

    Las referencias aparecen en dos formas: dicts con clave ``s3_key`` (subidas
    y salidas de operador) y listas planas bajo ``s3_keys``. Se recogen ambas en
    vez de adivinar por nombre de campo, que es la heuristica fragil que usa
    `fetch_entity_file`.
    """
    if depth > _MAX_DEPTH:
        return

    if isinstance(value, dict):
        for k, v in value.items():
            if k == "s3_key" and isinstance(v, str) and v.strip():
                out.add(v)
            elif k == "s3_keys" and isinstance(v, list):
                out.update(x for x in v if isinstance(x, str) and x.strip())
            else:
                _collect_entity_s3_keys(v, depth + 1, out)
        return

    if isinstance(value, list):
        for v in value:
            _collect_entity_s3_keys(v, depth + 1, out)


@router.post("/grant")
async def grant_file_access(
    request: FileGrantRequest,
    current_customer: Customer = Depends(get_current_customer),
) -> Dict[str, Any]:
    """Emite tokens de descarga para archivos de un recurso del ciudadano.

    Solo se firman las s3_key que realmente estan referenciadas en el recurso
    del `scope`, y solo si el solicitante es su dueno. Pedir una llave ajena no
    devuelve error distinto: simplemente no se emite token para ella, para no
    confirmar que el objeto existe.
    """
    if not request.scope.entity_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="scope.entity_id es obligatorio",
        )

    entity = await EntityService.get_entity(
        request.scope.entity_id,
        owner_user_id=str(current_customer.id),
    )
    if not entity:
        # 404 tambien cuando la entidad existe pero es de otro: no filtrar
        # existencia de recursos ajenos.
        raise HTTPException(status_code=404, detail="Entity not found")

    allowed: Set[str] = set()
    _collect_entity_s3_keys(entity.data, 0, allowed)

    grants = []
    for key in request.s3_keys:
        if key not in allowed:
            logger.warning(
                f"Grant denegado: {key} no pertenece a la entidad {entity.entity_id}"
            )
            continue
        token, expires_at = issue_token(key, str(current_customer.id))
        grants.append({"s3_key": key, "token": token, "expires_at": expires_at})

    return {"grants": grants, "ttl_seconds": settings.FILES_DOWNLOAD_TOKEN_TTL_SECONDS}


@router.get("/download/{s3_key:path}")
async def download_file(
    s3_key: str,
    t: Optional[str] = Query(None, description="Token de descarga emitido por /files/grant"),
):
    """
    Descarga un archivo de S3/MinIO a partir de su ``s3_key``.

    El bucket sale de la env `S3_BUCKET_NAME` (via `s3_storage.default_bucket`)
    y el cliente boto3 cae al default credential chain (env vars en local con
    MinIO, IAM role del instance profile en EC2). Si el objeto no existe se
    devuelve 404 explicito; cualquier otro error es 502 -- antes este endpoint
    enmascaraba toda excepcion como 404 y ocultaba bugs como un endpoint
    hardcoded a http://minio:9000 o credenciales mal configuradas.
    """
    if settings.FILES_DOWNLOAD_REQUIRE_TOKEN and not verify_token(t or "", s3_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de descarga ausente, invalido o caducado",
        )

    bucket = s3_storage.default_bucket()
    client = s3_storage.get_s3_client()

    logger.info(f"Downloading s3://{bucket}/{s3_key}")

    try:
        response = await asyncio.to_thread(
            client.get_object, Bucket=bucket, Key=s3_key
        )
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "NoSuchBucket", "404"):
            logger.warning(f"S3 object missing for {bucket}/{s3_key}: {code}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"File not found: {s3_key}",
            )
        logger.error(f"S3 ClientError for {bucket}/{s3_key}: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"S3 error: {code or 'unknown'}",
        )

    file_bytes = await asyncio.to_thread(response["Body"].read)

    filename = os.path.basename(s3_key) or "downloaded_file"
    content_type, _ = mimetypes.guess_type(filename)
    content_type = content_type or response.get("ContentType") or "application/octet-stream"

    logger.info(f"Serving {filename} ({len(file_bytes)} bytes, {content_type})")

    return StreamingResponse(
        io.BytesIO(file_bytes),
        media_type=content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(file_bytes)),
        },
    )
