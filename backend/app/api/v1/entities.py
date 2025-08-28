"""
Entity validation and auto-completion endpoints

This module provides real-time validation and auto-completion
for entity fields, allowing frontends to provide immediate
feedback and assistance to users.
"""

from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any, List, Optional
from pydantic import BaseModel
import importlib
import logging

from app.services.auth_service import get_current_user_optional
from app.models.user import UserModel as User

logger = logging.getLogger(__name__)

router = APIRouter()


class EntityValidationRequest(BaseModel):
    """Request model for entity validation"""
    entity_type: str
    data: Dict[str, Any]
    validate_only: bool = False  # If True, only validate without auto-completion


class EntityAutoCompleteRequest(BaseModel):
    """Request model for entity auto-completion"""
    entity_type: str
    data: Dict[str, Any]
    trigger_field: str  # Which field triggered the auto-complete (e.g., "postal_code")


class EntityValidationResponse(BaseModel):
    """Response model for entity validation"""
    valid: bool
    validation_status: str
    errors: List[str] = []
    warnings: List[str] = []
    auto_filled_fields: List[str] = []
    auto_filled_data: Dict[str, Any] = {}
    suggestions: Dict[str, Any] = {}


async def get_entity_service():
    """
    Get entity validation service from loaded plugins.
    This searches through loaded plugins for entity validation capabilities.
    """
    try:
        # Import the plugin manager
        from app.api.endpoints.plugins import plugin_manager
        
        # Search through loaded plugins for entity services
        for plugin in plugin_manager.plugins:
            if hasattr(plugin, 'local_path') and plugin.local_path:
                # Add plugin path to sys.path temporarily
                import sys
                if plugin.local_path not in sys.path:
                    sys.path.insert(0, plugin.local_path)
                
                try:
                    # Try to import entities module from the plugin
                    entities_module = importlib.import_module("entities")
                    
                    if hasattr(entities_module, "validation_service"):
                        return entities_module.validation_service
                        
                except ImportError:
                    # This plugin doesn't have entities, continue
                    continue
                finally:
                    # Clean up sys.path
                    if plugin.local_path in sys.path:
                        sys.path.remove(plugin.local_path)
        
        # No entity service found in any plugin
        raise HTTPException(
            status_code=503,
            detail="No entity validation service available from loaded plugins"
        )
        
    except Exception as e:
        logger.error(f"Error accessing entity service: {str(e)}")
        raise HTTPException(
            status_code=503,
            detail="Entity validation service not available"
        )


@router.post("/validate", response_model=EntityValidationResponse)
async def validate_entity(
    request: EntityValidationRequest,
    current_user: Optional[User] = Depends(get_current_user_optional)
):
    """
    Validate entity data and optionally perform auto-completion.
    
    This endpoint:
    1. Creates an entity instance with provided data
    2. Validates the entity according to its rules
    3. Optionally performs auto-completion of missing fields
    4. Returns validation results and any auto-filled data
    """
    try:
        # Get the entity validation service
        validation_service = await get_entity_service()
        
        # Create entity instance
        entity = await validation_service.create_entity(
            request.entity_type, 
            request.data
        )
        
        # Perform validation
        is_valid = await entity.validate()
        
        # Prepare response
        response = EntityValidationResponse(
            valid=is_valid,
            validation_status=entity.validation_status,
            errors=entity.validation_errors,
            warnings=[]
        )
        
        # Perform auto-completion if requested and validation passed
        if not request.validate_only and is_valid:
            auto_filled_data = await entity.auto_complete()
            
            # Identify which fields were auto-filled
            for field, value in auto_filled_data.items():
                if field not in request.data or request.data.get(field) != value:
                    response.auto_filled_fields.append(field)
            
            response.auto_filled_data = auto_filled_data
        
        # Add any suggestions (like neighborhood options for postal codes)
        if hasattr(entity, "data") and isinstance(entity.data, dict):
            for key, value in entity.data.items():
                if key.endswith("_options"):
                    response.suggestions[key] = value
        
        return response
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Entity validation error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Entity validation failed: {str(e)}"
        )


@router.post("/auto-complete", response_model=EntityValidationResponse)
async def auto_complete_entity(
    request: EntityAutoCompleteRequest,
    current_user: Optional[User] = Depends(get_current_user_optional)
):
    """
    Auto-complete entity fields based on a trigger field change.
    
    This is useful for real-time form assistance, like:
    - Filling city/state when postal code is entered
    - Extracting data from CURP
    - Suggesting values based on partial input
    """
    try:
        # Get the entity validation service
        validation_service = await get_entity_service()
        
        # Create entity instance
        entity = await validation_service.create_entity(
            request.entity_type,
            request.data
        )
        
        # Perform auto-completion
        auto_filled_data = await entity.auto_complete()
        
        # Prepare response
        response = EntityValidationResponse(
            valid=True,  # We're not validating here
            validation_status="auto_completed",
            errors=[],
            warnings=[],
            auto_filled_fields=entity.auto_filled_fields if hasattr(entity, "auto_filled_fields") else [],
            auto_filled_data=auto_filled_data
        )
        
        # Add any suggestions
        if isinstance(auto_filled_data, dict):
            for key, value in auto_filled_data.items():
                if key.endswith("_options"):
                    response.suggestions[key] = value
        
        return response
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Entity auto-complete error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Entity auto-complete failed: {str(e)}"
        )


@router.get("/types")
async def get_entity_types(
    current_user: Optional[User] = Depends(get_current_user_optional)
):
    """
    Get list of available entity types and their validation rules.
    
    This helps frontends to:
    - Know which entity types are available
    - Get validation rules for client-side validation
    - Understand required fields
    """
    try:
        validation_service = await get_entity_service()
        
        # Get registered entity types
        entity_types = []
        
        # This would need to be implemented in the validation service
        # For now, return a basic structure
        return {
            "entity_types": [
                "address",
                "person_name", 
                "mexican_id",
                "property"
            ]
        }
        
    except Exception as e:
        logger.error(f"Error getting entity types: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get entity types: {str(e)}"
        )


@router.get("/rules/{entity_type}")
async def get_entity_rules(
    entity_type: str,
    current_user: Optional[User] = Depends(get_current_user_optional)
):
    """
    Get validation rules for a specific entity type.
    
    Returns:
    - Required fields
    - Field validation patterns
    - Field descriptions
    - Any other validation rules
    """
    try:
        validation_service = await get_entity_service()
        
        # Create a dummy entity to get its rules
        entity = await validation_service.create_entity(entity_type, {})
        
        return {
            "entity_type": entity_type,
            "required_fields": entity.get_required_fields() if hasattr(entity, "get_required_fields") else [],
            "validation_rules": entity.get_validation_rules() if hasattr(entity, "get_validation_rules") else {}
        }
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting entity rules: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get entity rules: {str(e)}"
        )