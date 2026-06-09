"""S3 storage helpers compartidos por endpoints y operadores.

Objetivo: que los archivos subidos por el ciudadano (multipart en submit-data)
vivan en S3 desde el principio en lugar de viajar en el context Mongo como
base64. Eso evita el cap de 16 MB BSON y el `_strip_oversized_base64_blobs`
del executor que borra blobs > 100 KB antes de que el S3UploadOperator pueda
procesarlos.
"""
from __future__ import annotations

import os
import re
import unicodedata
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

import boto3

_S3_CLIENT: Optional["boto3.client"] = None


def get_s3_client():
    """Devuelve un cliente boto3 S3 cacheado, configurado igual que el
    `S3UploadOperator._init_s3_client` original (region + endpoint URL opcional
    para MinIO local)."""
    global _S3_CLIENT
    if _S3_CLIENT is None:
        _S3_CLIENT = boto3.client(
            "s3",
            region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
            endpoint_url=os.getenv("S3_ENDPOINT_URL"),
        )
    return _S3_CLIENT


def default_bucket() -> str:
    """Bucket donde van todos los uploads. `submit-data` usa el prefix `tmp/`
    dentro del mismo bucket; el operador hace copy al destino final dentro
    del bucket también para evitar cross-bucket ACLs.

    Override per-tenant via env (`UPLOADS_BUCKET`) — los compose files de cada
    tenant ya configuran `S3_BUCKET_NAME` (ej. `conapesca-uploads`)."""
    return (
        os.getenv("UPLOADS_BUCKET")
        or os.getenv("S3_BUCKET_NAME")
        or "munistream-uploads"
    )


def sanitize_filename(filename: str) -> str:
    """ASCII-safe filename para S3 key y metadata headers (mismas reglas que
    `_sanitize_filename` en s3_upload.py)."""
    if not filename:
        return "file"
    nfkd = unicodedata.normalize("NFKD", filename)
    ascii_only = nfkd.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", ascii_only).strip("_")
    return cleaned or "file"


def _put_params(
    bucket: str,
    key: str,
    content_type: str,
    metadata: Dict[str, str],
) -> Dict[str, Any]:
    """Parámetros base de put/copy; solo agrega ServerSideEncryption cuando
    estamos hablando con S3 real (no MinIO)."""
    params: Dict[str, Any] = {
        "Bucket": bucket,
        "Key": key,
        "ContentType": content_type,
        "Metadata": {k: v for k, v in metadata.items() if v},
        "StorageClass": "STANDARD",
        "ACL": "private",
    }
    endpoint_url = os.getenv("S3_ENDPOINT_URL")
    if not endpoint_url or "amazonaws.com" in endpoint_url:
        params["ServerSideEncryption"] = "AES256"
    return params


def upload_bytes(
    bucket: str,
    key: str,
    content: bytes,
    content_type: str,
    metadata: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Sube bytes a S3. Devuelve un dict con `etag`, `version_id` y el tamaño."""
    params = _put_params(bucket, key, content_type, metadata or {})
    params["Body"] = content
    resp = get_s3_client().put_object(**params)
    return {
        "etag": resp.get("ETag", "").strip('"'),
        "version_id": resp.get("VersionId"),
        "size": len(content),
    }


def copy_object(
    src_bucket: str,
    src_key: str,
    dst_bucket: str,
    dst_key: str,
    content_type: Optional[str] = None,
    metadata: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Copia un objeto entre keys (o entre buckets). Usado por el
    S3UploadOperator para mover `tmp/<...>` al destino final del operador."""
    extra: Dict[str, Any] = {
        "CopySource": {"Bucket": src_bucket, "Key": src_key},
        "Bucket": dst_bucket,
        "Key": dst_key,
        "StorageClass": "STANDARD",
        "ACL": "private",
    }
    if content_type:
        extra["ContentType"] = content_type
    if metadata is not None:
        extra["Metadata"] = {k: v for k, v in metadata.items() if v}
        extra["MetadataDirective"] = "REPLACE"
    endpoint_url = os.getenv("S3_ENDPOINT_URL")
    if not endpoint_url or "amazonaws.com" in endpoint_url:
        extra["ServerSideEncryption"] = "AES256"
    resp = get_s3_client().copy_object(**extra)
    return {
        "etag": (resp.get("CopyObjectResult") or {}).get("ETag", "").strip('"'),
        "version_id": resp.get("VersionId"),
    }


def delete_object(bucket: str, key: str) -> None:
    """Borra un objeto. Errores silenciosos — el caller decide qué hacer si
    no se pudo limpiar (típicamente solo loggear)."""
    get_s3_client().delete_object(Bucket=bucket, Key=key)


def upload_pending_file(
    instance_id: str,
    task_id: str,
    field_name: str,
    filename: Optional[str],
    content_type: Optional[str],
    file_content: bytes,
) -> Dict[str, Any]:
    """Sube un archivo recién subido por el ciudadano al bucket bajo el
    prefix `tmp/<instance>/<task>/<field>/<uuid>_<filename>` y devuelve la
    referencia que se guarda en context.

    El S3UploadOperator detecta este shape (`{filename, content_type, size,
    s3_key, s3_bucket}` sin `base64`) y hace copy al destino final + delete
    del tmp.
    """
    safe = sanitize_filename(filename or "file")
    bucket = default_bucket()
    key = f"tmp/{instance_id}/{task_id}/{field_name}/{uuid.uuid4().hex}_{safe}"
    ct = content_type or "application/octet-stream"
    upload_bytes(
        bucket=bucket,
        key=key,
        content=file_content,
        content_type=ct,
        metadata={
            "instance-id": instance_id,
            "task-id": task_id,
            "field": field_name,
            "uploaded-at": datetime.utcnow().isoformat(),
            "pending": "true",
        },
    )
    return {
        "filename": filename or safe,
        "content_type": ct,
        "size": len(file_content),
        "s3_key": key,
        "s3_bucket": bucket,
    }
