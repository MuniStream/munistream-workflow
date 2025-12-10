"""
Entity Verification API endpoints.

Public endpoints for verifying entity authenticity via QR codes.
No authentication required - these are public verification endpoints.
"""
from fastapi import APIRouter, Query
from typing import Optional
import logging
import hashlib

from ...services.entity_service import EntityService
from ...models.user import UserModel

router = APIRouter()


@router.get("/{entity_id}")
async def verify_entity(
    entity_id: str,
    checksum: Optional[str] = Query(None, description="Expected checksum for data integrity verification")
):
    """
    Verify entity authenticity without requiring authentication.
    This endpoint is used by QR codes for public verification.

    The checksum parameter should contain the SHA-256 hash of critical entity data
    for integrity verification. QR codes should include this for security.

    Example QR URL: https://conapesca.dev.munistream.com/verify/entity123?checksum=abc123
    """
    try:
        # Get entity without user restriction (public verification)
        entity = await EntityService.get_entity(entity_id, owner_user_id=None)

        if not entity:
            return {
                "valid": False,
                "error": "Entity not found",
                "entity_id": entity_id
            }

        # Perform validation checks
        validation_errors = []

        # Check entity status
        if entity.status not in ["active", "vigente", "valid"]:
            validation_errors.append(f"Entity status is {entity.status}")

        # Check if entity is verified
        if not entity.verified:
            validation_errors.append("Entity is not verified")

        # Check expiry date if present
        expiry_date = entity.data.get("expiry_date") or entity.data.get("fecha_vencimiento")
        if expiry_date:
            try:
                from datetime import datetime
                expiry = datetime.fromisoformat(expiry_date.replace('Z', '+00:00'))
                if expiry < datetime.utcnow():
                    validation_errors.append("Entity has expired")
            except:
                validation_errors.append("Invalid expiry date format")

        # Verify data integrity with client-provided checksum
        checksum_valid = True
        calculated_checksum = None

        if checksum:
            # Calculate checksum of critical data fields (same algorithm as QR generation)
            critical_data = {
                "entity_id": entity.entity_id,
                "entity_type": entity.entity_type,
                "name": entity.name,
                "status": entity.status,
                "created_at": entity.created_at.isoformat() if entity.created_at else None
            }
            # Sort keys for consistent hashing
            data_string = str(sorted(critical_data.items()))
            calculated_checksum = hashlib.sha256(data_string.encode()).hexdigest()[:16]
            checksum_valid = checksum.lower() == calculated_checksum.lower()

            if not checksum_valid:
                validation_errors.append("Data integrity check failed - document may have been modified")

        # Determine overall validity
        is_valid = len(validation_errors) == 0

        # Return verification information
        return {
            "valid": is_valid,
            "entity_id": entity.entity_id,
            "entity_type": entity.entity_type,
            "name": entity.name,
            "status": entity.status,
            "verified": entity.verified,
            "verification_date": entity.verification_date.isoformat() if entity.verification_date else None,
            "verified_by": entity.verified_by,
            "created_at": entity.created_at.isoformat() if entity.created_at else None,
            "authority": entity.data.get("authority", "Unknown"),
            "document_type": entity.data.get("document_type", "Unknown"),
            "checksum_valid": checksum_valid,
            "checksum_provided": checksum is not None,
            "calculated_checksum": calculated_checksum,
            "validation_errors": validation_errors if validation_errors else None
        }

    except Exception as e:
        logging.getLogger(__name__).error(f"Error verifying entity {entity_id}: {e}")
        return {
            "valid": False,
            "error": "Verification system error",
            "entity_id": entity_id
        }


@router.get("/metadata/{entity_id}")
async def get_entity_metadata(entity_id: str):
    """
    Get complete metadata information for any entity.
    This endpoint provides detailed information about the entity including all data fields.
    No authentication required - public metadata access.
    """
    try:
        # Get entity without user restriction (public metadata access)
        entity = await EntityService.get_entity(entity_id, owner_user_id=None)

        if not entity:
            return {
                "found": False,
                "error": "Entity not found",
                "entity_id": entity_id
            }

        # Get owner information
        owner_name = entity.owner_user_id
        try:
            if entity.owner_user_id:
                owner = await UserModel.find_one(UserModel.user_id == entity.owner_user_id)
                if owner and hasattr(owner, 'name') and owner.name:
                    owner_name = owner.name
                elif owner and hasattr(owner, 'full_name') and owner.full_name:
                    owner_name = owner.full_name
                elif owner and hasattr(owner, 'email') and owner.email:
                    owner_name = owner.email
        except Exception:
            # If user lookup fails, keep the user_id as fallback
            pass

        # Return complete entity metadata
        return {
            "found": True,
            "entity_id": entity.entity_id,
            "entity_type": entity.entity_type,
            "entity_subtype": getattr(entity, 'entity_subtype', None),
            "name": entity.name,
            "status": entity.status,
            "verified": entity.verified,
            "verification_date": entity.verification_date.isoformat() if entity.verification_date else None,
            "verified_by": entity.verified_by,
            "created_at": entity.created_at.isoformat() if entity.created_at else None,
            "updated_at": entity.updated_at.isoformat() if entity.updated_at else None,
            "owner_name": owner_name,
            "metadata": entity.data,  # All the entity-specific data
            "visualization_config": entity.visualization_config,
            "entity_display_config": entity.entity_display_config
        }

    except Exception as e:
        logging.getLogger(__name__).error(f"Error retrieving metadata for entity {entity_id}: {e}")
        return {
            "found": False,
            "error": "Metadata retrieval system error",
            "entity_id": entity_id
        }