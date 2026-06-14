"""
File Download Endpoints

Handles downloading files from S3/MinIO storage using s3_key paths.
"""

import io
import os
import mimetypes
import asyncio
from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from ...services import s3_storage
from ...core.logging_config import get_workflow_logger

logger = get_workflow_logger(__name__)
router = APIRouter()


@router.get("/download/{s3_key:path}")
async def download_file(s3_key: str):
    """
    Download a file from S3/MinIO using its s3_key path.

    El bucket sale de la env `S3_BUCKET_NAME` (vía `s3_storage.default_bucket`)
    y el cliente boto3 cae al default credential chain (env vars en local con
    MinIO, IAM role del instance profile en EC2). Si el objeto no existe se
    devuelve 404 explícito; cualquier otro error es 500 — antes este endpoint
    enmascaraba toda excepción como 404 y ocultaba bugs como un endpoint
    hardcoded a http://minio:9000 o credenciales mal configuradas.
    """
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