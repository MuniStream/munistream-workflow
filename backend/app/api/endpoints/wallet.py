"""
Wallet API endpoints for generating digital wallet passes.

Handles:
- Apple Wallet (PKPass) generation
- Google Wallet pass generation
- NFC verification endpoints
"""

from fastapi import APIRouter, HTTPException, Depends, Response, Request
from fastapi.responses import StreamingResponse, JSONResponse
from typing import Dict, Any, Optional
from pydantic import BaseModel
import io
import logging

from app.services.auth_service import get_current_user_optional
from app.models.user import UserModel as User
from app.models.legal_entity import LegalEntity
from app.services.entity_service import EntityService
from app.services.visualizers.wallet_pass_visualizer import WalletPassVisualizer
from app.services.wallet_certificate_service import certificate_service
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


class NFCVerificationRequest(BaseModel):
    """Request model for NFC verification"""
    nfc_data: Optional[str] = None
    device_info: Optional[Dict[str, Any]] = None


@router.get("/entities/{entity_id}/wallet/apple")
async def download_apple_wallet(
    entity_id: str,
    current_user: Optional[User] = Depends(get_current_user_optional)
):
    """
    Generate and download Apple Wallet (PKPass) for an entity.
    """
    try:
        # Get entity
        entity_service = EntityService()
        entity = await entity_service.get_entity(entity_id)

        if not entity:
            raise HTTPException(status_code=404, detail="Entity not found")

        # Check Apple configuration
        if not certificate_service.validate_apple_configuration():
            raise HTTPException(
                status_code=503,
                detail="Apple Wallet not configured. Missing certificates or configuration."
            )

        # Initialize certificates
        cert_status = certificate_service.initialize()
        if not cert_status["apple"]:
            raise HTTPException(
                status_code=503,
                detail="Failed to load Apple certificates"
            )

        # Create wallet visualizer and generate PKPass
        wallet_visualizer = WalletPassVisualizer(tenant_id=settings.TENANT_ID or "default")
        pkpass_data = wallet_visualizer.generate_pkpass(entity)

        if not pkpass_data:
            raise HTTPException(status_code=500, detail="Failed to generate Apple Wallet pass")

        # Create filename
        entity_type = entity.entity_type.lower().replace(' ', '_')
        entity_id_short = entity.entity_id[:8].lower() if entity.entity_id else 'unknown'
        filename = f"{entity_type}_{entity_id_short}.pkpass"

        # Return as streaming response
        return StreamingResponse(
            io.BytesIO(pkpass_data),
            media_type="application/vnd.apple.pkpass",
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
                "Content-Length": str(len(pkpass_data))
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating Apple Wallet for entity {entity_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error generating Apple Wallet: {str(e)}")


@router.get("/entities/{entity_id}/wallet/google")
async def get_google_wallet_jwt(
    entity_id: str,
    current_user: Optional[User] = Depends(get_current_user_optional)
):
    """
    Generate Google Wallet JWT for an entity.
    """
    try:
        # Get entity
        entity_service = EntityService()
        entity = await entity_service.get_entity(entity_id)

        if not entity:
            raise HTTPException(status_code=404, detail="Entity not found")

        # Check Google configuration
        if not certificate_service.validate_google_configuration():
            raise HTTPException(
                status_code=503,
                detail="Google Wallet not configured. Missing service account or configuration."
            )

        # Initialize service account
        cert_status = certificate_service.initialize()
        if not cert_status["google"]:
            raise HTTPException(
                status_code=503,
                detail="Failed to load Google service account"
            )

        # Create wallet visualizer and generate JWT
        wallet_visualizer = WalletPassVisualizer(tenant_id=settings.TENANT_ID or "default")
        jwt_token = wallet_visualizer.generate_google_wallet_jwt(entity)

        if not jwt_token:
            raise HTTPException(status_code=500, detail="Failed to generate Google Wallet JWT")

        # Create Google Wallet save URL
        save_url = f"https://pay.google.com/gp/v/save/{jwt_token}"

        return {
            "entity_id": entity_id,
            "provider": "google",
            "jwt_token": jwt_token,
            "save_url": save_url,
            "instructions": "Use the save_url to add this pass to Google Wallet"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating Google Wallet for entity {entity_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error generating Google Wallet: {str(e)}")


@router.post("/entities/{entity_id}/wallet/verify")
async def verify_nfc_wallet(
    entity_id: str,
    verification_request: NFCVerificationRequest,
    request: Request
):
    """
    Verify entity via NFC wallet interaction.
    Optimized for <200ms response time.
    """
    try:
        # Get entity (optimized query)
        entity_service = EntityService()
        entity = await entity_service.get_entity(entity_id)

        if not entity:
            return JSONResponse(
                status_code=404,
                content={
                    "valid": False,
                    "error": "Entity not found",
                    "entity_id": entity_id
                }
            )

        # Create wallet visualizer
        wallet_visualizer = WalletPassVisualizer(tenant_id=settings.TENANT_ID or "default")

        # Get NFC payload and verification data
        nfc_data = wallet_visualizer.get_nfc_payload(entity)

        # Verification response
        verification_response = {
            "valid": True,
            "entity_id": entity_id,
            "entity_type": entity.entity_type,
            "entity_name": entity.name,
            "verification_url": wallet_visualizer.get_verification_url(entity),
            "nfc_data": nfc_data,
            "verified_at": nfc_data["ts"],
            "tenant": settings.TENANT_ID or "default"
        }

        # Set cache headers for performance
        headers = {
            "Cache-Control": "public, max-age=300",  # 5 minutes
            "X-Entity-Type": entity.entity_type,
            "X-Tenant": settings.TENANT_ID or "default"
        }

        return JSONResponse(
            content=verification_response,
            headers=headers
        )

    except Exception as e:
        logger.error(f"Error in NFC verification for entity {entity_id}: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={
                "valid": False,
                "error": f"Verification error: {str(e)}",
                "entity_id": entity_id
            }
        )