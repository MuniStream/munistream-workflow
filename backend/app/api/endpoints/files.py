"""
File Download Endpoints

Handles downloading files from S3/MinIO storage using s3_key paths.
"""

import os
import io
import mimetypes
from typing import Any
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from ...services.file_conversion_service import FileConversionService
from ...core.logging_config import get_workflow_logger

logger = get_workflow_logger(__name__)
router = APIRouter()


@router.get("/download/{s3_key:path}")
async def download_file(s3_key: str):
    """
    Download a file from S3/MinIO using its s3_key path.

    Args:
        s3_key: The S3 key path for the file (e.g., "catastro/documents/...")

    Returns:
        StreamingResponse with the file content
    """
    try:
        # Initialize file conversion service (has S3 download functionality)
        file_service = FileConversionService()

        # Construct S3 URL from s3_key
        # Get S3 config from environment
        s3_endpoint = os.getenv("S3_ENDPOINT", "http://minio:9000")
        s3_bucket = os.getenv("S3_BUCKET", "munistream-uploads")

        # Build the S3 URL
        s3_url = f"{s3_endpoint}/{s3_bucket}/{s3_key}"

        logger.info(f"Downloading file from S3: {s3_url}")

        # Download file from S3
        file_bytes, filename = await file_service._download_from_s3(s3_url, None)

        # Determine content type
        content_type, _ = mimetypes.guess_type(filename or s3_key)
        if not content_type:
            content_type = "application/octet-stream"

        # Extract just the filename from the s3_key if no filename provided
        if not filename:
            filename = os.path.basename(s3_key)

        # Create streaming response
        file_like = io.BytesIO(file_bytes)

        logger.info(f"Serving file: {filename} ({len(file_bytes)} bytes, {content_type})")

        return StreamingResponse(
            io.BytesIO(file_bytes),
            media_type=content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": str(len(file_bytes))
            }
        )

    except Exception as e:
        logger.error(f"Error downloading file {s3_key}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File not found: {s3_key}"
        )