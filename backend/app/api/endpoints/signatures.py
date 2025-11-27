"""
Signature API endpoints

Handles digital signature workflow operations including:
- Getting signable data for instances
- Receiving signatures from clients
- Verifying signatures
- Generating signed PDFs
"""
from fastapi import APIRouter, HTTPException, Depends, File, UploadFile, Query, Response, Request
from fastapi.responses import StreamingResponse
from typing import Dict, Any, Optional
from pydantic import BaseModel
import logging
import io

from app.services.auth_service import get_current_user_optional
from app.models.user import UserModel as User
from app.models.workflow import WorkflowInstance
from app.models.legal_entity import LegalEntity
from app.services.signature.context_signer import ContextSignerService
from app.services.signature.certificate_manager import CertificateManager
from app.services.signature.signature_verifier import SignatureVerifier
from app.services.visualizers.visualizer_factory import VisualizerFactory
from app.services.entity_service import EntityService

logger = logging.getLogger(__name__)

router = APIRouter()


# Request/Response Models
class SignableDataResponse(BaseModel):
    """Response model for signable data"""
    instance_id: str
    signature_field: str
    signable_data: Dict[str, Any]
    expires_at: str
    instructions: str = "Use your X.509 certificate to sign this data and submit the signature"


class SignatureSubmissionRequest(BaseModel):
    """Request model for signature submission"""
    signature: str  # Base64-encoded signature
    certificate: str  # PEM-encoded certificate
    algorithm: str = "RSA-SHA256"  # Signature algorithm used


class SignatureSubmissionResponse(BaseModel):
    """Response model for signature submission"""
    success: bool
    message: str
    signature_received: bool = False
    verification_result: Optional[Dict[str, Any]] = None


class SignatureStatusResponse(BaseModel):
    """Response model for signature status"""
    signature_field: str
    exists: bool
    status: str
    created_at: Optional[str] = None
    expires_at: Optional[str] = None
    signed_at: Optional[str] = None
    expired: Optional[bool] = None


class VerificationResponse(BaseModel):
    """Response model for signature verification"""
    valid: bool
    verification_timestamp: str
    signature_info: Dict[str, Any]
    verification_results: Dict[str, Any]
    overall_valid: bool


@router.get("/instances/{instance_id}/signable-data/{signature_field}")
async def get_signable_data(
    instance_id: str,
    signature_field: str,
    current_user: Optional[User] = Depends(get_current_user_optional)
) -> SignableDataResponse:
    """
    Get signable data for a workflow instance.

    This endpoint is called by the client-side signing component to get
    the data that needs to be signed.
    """
    try:
        # Verify instance exists
        instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
        if not instance:
            raise HTTPException(status_code=404, detail="Workflow instance not found")

        # Verify user has access to this instance
        if current_user and instance.customer_id != current_user.customer_id:
            raise HTTPException(status_code=403, detail="Access denied to this workflow instance")

        # Get signable data
        signable_data = await ContextSignerService.get_signable_data(
            instance_id=instance_id,
            signature_field=signature_field
        )

        if not signable_data:
            raise HTTPException(
                status_code=404,
                detail="No signable data found or data has expired"
            )

        # Get signature status to include expiration info
        status = await ContextSignerService.get_signature_status(
            instance_id=instance_id,
            signature_field=signature_field
        )

        return SignableDataResponse(
            instance_id=instance_id,
            signature_field=signature_field,
            signable_data=signable_data,
            expires_at=status.get("expires_at", ""),
            instructions="Sign this data with your X.509 certificate and private key"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get signable data: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve signable data")


@router.post("/instances/{instance_id}/signatures/{signature_field}")
async def submit_signature(
    instance_id: str,
    signature_field: str,
    signature_data: SignatureSubmissionRequest,
    current_user: Optional[User] = Depends(get_current_user_optional)
) -> SignatureSubmissionResponse:
    """
    Submit a digital signature for a workflow instance.

    This endpoint receives the signature from the client-side signing component.
    """
    try:
        # Verify instance exists
        instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
        if not instance:
            raise HTTPException(status_code=404, detail="Workflow instance not found")

        # Verify user has access
        if current_user and instance.customer_id != current_user.customer_id:
            raise HTTPException(status_code=403, detail="Access denied to this workflow instance")

        # Validate certificate
        cert_validation = await CertificateManager.validate_certificate_for_signing(
            certificate_pem=signature_data.certificate
        )

        if not cert_validation["valid"]:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid certificate: {'; '.join(cert_validation['errors'])}"
            )

        # Prepare signature data for storage
        signature_storage_data = {
            "signature": signature_data.signature,
            "certificate": signature_data.certificate,
            "algorithm": signature_data.algorithm,
            "timestamp": await ContextSignerService._get_current_timestamp(),
            "certificate_info": cert_validation["info"]
        }

        # Store signature
        success = await ContextSignerService.store_signature(
            instance_id=instance_id,
            signature_field=signature_field,
            signature_data=signature_storage_data
        )

        if not success:
            raise HTTPException(status_code=500, detail="Failed to store signature")

        # Verify the signature
        verification_result = None
        try:
            # Get the original signable data
            signable_data = await ContextSignerService.get_signable_data(
                instance_id=instance_id,
                signature_field=signature_field
            )

            if signable_data:
                is_valid = await ContextSignerService.validate_signature(
                    signable_data=signable_data,
                    signature_data=signature_storage_data
                )

                verification_result = {
                    "valid": is_valid,
                    "verified_at": await ContextSignerService._get_current_timestamp()
                }
            else:
                verification_result = {
                    "valid": False,
                    "error": "Could not retrieve original signable data"
                }

        except Exception as e:
            logger.warning(f"Signature verification failed during submission: {e}")
            verification_result = {
                "valid": False,
                "error": str(e)
            }

        return SignatureSubmissionResponse(
            success=True,
            message="Signature submitted successfully",
            signature_received=True,
            verification_result=verification_result
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to submit signature: {e}")
        raise HTTPException(status_code=500, detail="Failed to submit signature")


@router.get("/instances/{instance_id}/signature-status/{signature_field}")
async def get_signature_status(
    instance_id: str,
    signature_field: str,
    current_user: Optional[User] = Depends(get_current_user_optional)
) -> SignatureStatusResponse:
    """
    Get the status of a signature for a workflow instance.
    """
    try:
        # Verify instance exists
        instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
        if not instance:
            raise HTTPException(status_code=404, detail="Workflow instance not found")

        # Verify user has access
        if current_user and instance.customer_id != current_user.customer_id:
            raise HTTPException(status_code=403, detail="Access denied to this workflow instance")

        # Get signature status
        status = await ContextSignerService.get_signature_status(
            instance_id=instance_id,
            signature_field=signature_field
        )

        if not status:
            raise HTTPException(status_code=404, detail="Signature status not found")

        return SignatureStatusResponse(**status)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get signature status: {e}")
        raise HTTPException(status_code=500, detail="Failed to get signature status")


@router.post("/entities/{entity_id}/verify-signature")
async def verify_entity_signature(
    entity_id: str,
    current_user: Optional[User] = Depends(get_current_user_optional)
) -> VerificationResponse:
    """
    Verify the digital signature of an entity.
    """
    try:
        # Get entity
        entity = await EntityService.get_entity(entity_id, current_user.customer_id if current_user else None)
        if not entity:
            raise HTTPException(status_code=404, detail="Entity not found")

        # Check if entity has signature
        signature_data = entity.data.get("signature") if entity.data else None
        if not signature_data:
            raise HTTPException(status_code=404, detail="Entity has no signature data")

        # Create verifier and verify signature
        verifier = SignatureVerifier()

        # Reconstruct the original signed data based on signed_fields
        signed_fields = signature_data.get("signed_fields", [])
        if not signed_fields:
            raise HTTPException(status_code=400, detail="No signed fields information in signature")

        # Reconstruct signable data
        import json
        signable_data = {}

        for field in signed_fields:
            if field == "entity_id":
                signable_data["entity_id"] = entity.entity_id
            elif field == "entity_type":
                signable_data["entity_type"] = entity.entity_type
            elif field == "timestamp":
                signable_data["timestamp"] = signature_data.get("timestamp")
            elif field in entity.data and field != "signature":
                signable_data[field] = entity.data[field]

        # Add metadata
        signable_data.update({
            "signature_purpose": "entity_signing",
            "signed_fields": signed_fields
        })

        data_json = json.dumps(signable_data, sort_keys=True)
        data_bytes = data_json.encode('utf-8')

        # Create verification report
        verification_report = await verifier.create_verification_report(
            data=data_bytes,
            signature_data=signature_data
        )

        return VerificationResponse(**verification_report)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to verify entity signature: {e}")
        raise HTTPException(status_code=500, detail="Signature verification failed")


@router.get("/entities/{entity_id}/pdf")
async def get_entity_pdf(
    entity_id: str,
    include_signatures: bool = True,
    current_user: Optional[User] = Depends(get_current_user_optional)
):
    """
    Generate and download PDF for an entity using its configured visualizer.
    """
    try:
        # Get entity
        entity = await EntityService.get_entity(entity_id, current_user.customer_id if current_user else None)
        if not entity:
            raise HTTPException(status_code=404, detail="Entity not found")

        # Determine visualizer from entity configuration only (security)
        visualizer = "pdf_report"  # Safe default
        if entity.entity_display_config and "visualizer" in entity.entity_display_config:
            visualizer = entity.entity_display_config["visualizer"]

        # Override for signed documents
        if include_signatures and entity.data and "signature" in entity.data:
            visualizer = "signed_pdf"

        # Get base URL for frontend (citizen portal) - used for QR codes and verification links
        from app.core.config import settings
        base_url = settings.FRONTEND_BASE_URL

        # Get visualizer
        pdf_visualizer = VisualizerFactory.get_visualizer(
            visualizer_type=visualizer,
            config={"include_signatures": include_signatures, "base_url": base_url}
        )

        if not pdf_visualizer:
            raise HTTPException(status_code=400, detail=f"Unknown visualizer type: {visualizer}")

        # Generate PDF
        pdf_data = await pdf_visualizer.generate_pdf(entity)

        if not pdf_data:
            raise HTTPException(status_code=500, detail="Failed to generate PDF")

        # Get download info
        download_info = await pdf_visualizer.get_download_info(entity)

        # Return PDF as streaming response
        return StreamingResponse(
            io.BytesIO(pdf_data),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename=\"{download_info['filename']}\""
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to generate entity PDF: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate PDF")


@router.get("/entities/{entity_id}/preview")
async def get_entity_preview(
    entity_id: str,
    visualizer: str = "pdf_report",
    current_user: Optional[User] = Depends(get_current_user_optional)
) -> Dict[str, Any]:
    """
    Get base64-encoded preview of entity PDF for display in browser.
    """
    try:
        # Get entity
        entity = await EntityService.get_entity(entity_id, current_user.customer_id if current_user else None)
        if not entity:
            raise HTTPException(status_code=404, detail="Entity not found")

        # Determine visualizer type
        if entity.data and "signature" in entity.data:
            visualizer = "signed_pdf"

        # Get base URL for frontend (citizen portal) - used for QR codes and verification links
        from app.core.config import settings
        base_url = settings.FRONTEND_BASE_URL

        # Get visualizer
        pdf_visualizer = VisualizerFactory.get_visualizer(
            visualizer_type=visualizer,
            config={"base_url": base_url}
        )

        if not pdf_visualizer:
            raise HTTPException(status_code=400, detail=f"Unknown visualizer type: {visualizer}")

        # Generate preview
        preview_base64 = await pdf_visualizer.generate_preview(entity)

        if not preview_base64:
            raise HTTPException(status_code=500, detail="Failed to generate preview")

        # Get preview info
        preview_info = await pdf_visualizer.get_preview_info(entity)

        return {
            "entity_id": entity_id,
            "preview_base64": preview_base64,
            "content_type": "application/pdf",
            "visualizer": visualizer,
            "preview_info": preview_info
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to generate entity preview: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate preview")


@router.get("/entities/{entity_id}/html")
async def get_entity_html(
    request: Request,
    entity_id: str,
    visualizer: str = Query("pdf_report", description="Visualizer type to use"),
    current_user: Optional[User] = Depends(get_current_user_optional)
) -> Response:
    """
    Get HTML representation of entity for display in iframe.
    """
    try:
        # Get entity
        entity = await EntityService.get_entity(entity_id, current_user.customer_id if current_user else None)
        if not entity:
            raise HTTPException(status_code=404, detail="Entity not found")

        # Determine visualizer type based on entity configuration
        if entity.entity_display_config and "visualizer" in entity.entity_display_config:
            visualizer = entity.entity_display_config["visualizer"]
        elif entity.data and "signature" in entity.data:
            visualizer = "signed_pdf"

        # Get base URL for frontend (citizen portal) - used for QR codes and verification links
        from app.core.config import settings
        base_url = settings.FRONTEND_BASE_URL

        # Get visualizer
        entity_visualizer = VisualizerFactory.get_visualizer(
            visualizer_type=visualizer,
            config={"base_url": base_url}
        )

        if not entity_visualizer:
            raise HTTPException(status_code=400, detail=f"Unknown visualizer type: {visualizer}")

        # All visualizers support HTML by default, no need to check

        # Generate HTML
        html_content = await entity_visualizer.generate_html(entity)

        if not html_content:
            raise HTTPException(status_code=500, detail="Failed to generate HTML")

        # Return HTML with proper headers for iframe embedding
        # CORS is handled by NGINX, so we don't add duplicate headers
        return Response(
            content=html_content,
            media_type="text/html",
            headers={"Cache-Control": "no-cache"}
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to generate entity HTML: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate HTML")


@router.post("/validate-certificate")
async def validate_certificate(
    certificate_file: UploadFile = File(...),
    current_user: Optional[User] = Depends(get_current_user_optional)
) -> Dict[str, Any]:
    """
    Validate an uploaded X.509 certificate for signing.
    """
    try:
        # Read certificate file
        certificate_content = await certificate_file.read()

        # Parse certificate
        certificate_pem = CertificateManager.parse_certificate_from_file_content(
            certificate_content
        )

        if not certificate_pem:
            raise HTTPException(status_code=400, detail="Invalid certificate file format")

        # Validate certificate
        validation_result = await CertificateManager.validate_certificate_for_signing(
            certificate_pem
        )

        return {
            "filename": certificate_file.filename,
            "validation": validation_result,
            "certificate_info": validation_result.get("info", {}),
            "valid": validation_result["valid"]
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Certificate validation failed: {e}")
        raise HTTPException(status_code=500, detail="Certificate validation failed")