"""
Entity validation and auto-completion endpoints

This module provides real-time validation and auto-completion
for entity fields, allowing frontends to provide immediate
feedback and assistance to users.
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import StreamingResponse, JSONResponse
from typing import Dict, Any, List, Optional
from pydantic import BaseModel
import importlib
import logging
import boto3
import os
from urllib.parse import urlparse
import io

from app.services.auth_service import get_current_user_optional
from app.models.user import UserModel as User
from app.services.entity_service import EntityService
from app.services.pdf_generation import EntityReportGenerator

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


@router.get("/{entity_id}/files/fetch")
async def fetch_entity_file(
    entity_id: str,
    file_url: str,
    convert: Optional[str] = Query(None, description="Convert file to format (png, jpg, preview)"),
    page: Optional[str] = Query(None, description="PDF page number or 'all'"),
    max_width: Optional[int] = Query(None, description="Maximum width for converted images"),
    thumbnail: Optional[bool] = Query(False, description="Return thumbnail version"),
    current_user: Optional[User] = Depends(get_current_user_optional)
):
    """
    Fetch a specific file from an entity and optionally convert it for preview.

    This endpoint securely fetches files by:
    1. Looking up the entity by ID
    2. Verifying the requested file_url exists in the entity's data
    3. Downloading from S3/MinIO
    4. Optionally converting to image format for preview
    5. Serving with proper content type and caching headers

    Args:
        entity_id: The entity ID to fetch files from
        file_url: The exact file URL that must exist in the entity's data
        convert: Optional conversion format (png, jpg, preview)
        page: For PDFs, page number or 'all' for all pages
        max_width: Maximum width for converted images
        thumbnail: Return smaller thumbnail version

    Returns:
        StreamingResponse with the file content
    """
    try:
        # Import here to avoid circular imports
        from app.services.entity_service import EntityService

        # Fetch the entity from database using the same method as other endpoints
        entity = await EntityService.get_entity(entity_id, current_user.id if current_user else None)
        if not entity:
            raise HTTPException(
                status_code=404,
                detail=f"Entity {entity_id} not found"
            )

        # Extract all file URLs from entity data to verify the requested URL is valid
        entity_data = entity.data if hasattr(entity, 'data') else {}
        valid_urls = []

        # Collect all file URLs from the entity
        for field_name, field_value in entity_data.items():
            if 'file' in field_name.lower() or 'url' in field_name.lower():
                if isinstance(field_value, list):
                    valid_urls.extend(field_value)
                elif isinstance(field_value, str):
                    valid_urls.append(field_value)

        # Verify the requested file_url exists in the entity
        if file_url not in valid_urls:
            raise HTTPException(
                status_code=403,
                detail=f"File URL not found in entity {entity_id}"
            )

        # Parse the URL to extract bucket and key
        from urllib.parse import urlparse
        parsed_url = urlparse(file_url)

        # Extract bucket and key from path
        path_parts = parsed_url.path.strip('/').split('/', 1)
        if len(path_parts) != 2:
            raise HTTPException(
                status_code=400,
                detail="Invalid S3 URL format in entity data"
            )

        bucket_name = path_parts[0]
        s3_key = path_parts[1]

        # Initialize S3 client with environment configuration
        s3_client = boto3.client(
            's3',
            region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
            endpoint_url=os.getenv("S3_ENDPOINT_URL"),
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY")
        )

        # Download file from S3
        try:
            response = s3_client.get_object(Bucket=bucket_name, Key=s3_key)
            file_content = response['Body'].read()
            content_type = response.get('ContentType', 'application/octet-stream')
            content_length = response.get('ContentLength', len(file_content))

            # Extract filename from S3 key
            filename = os.path.basename(s3_key)

            # Handle file conversion if requested
            if convert:
                from app.services.file_conversion_service import FileConversionService

                conversion_service = FileConversionService()

                # Parse page parameter
                page_param = None
                if page:
                    if page == "all":
                        page_param = "all"
                    else:
                        try:
                            page_param = int(page)
                        except ValueError:
                            page_param = 1

                # Convert file
                conversion_result = await conversion_service.convert_file(
                    file_bytes=file_content,
                    filename=filename,
                    convert_format=convert,
                    page=page_param,
                    max_width=max_width,
                    thumbnail=thumbnail
                )

                # Return JSON response with conversion result
                return JSONResponse(
                    content=conversion_result,
                    headers={
                        'Cache-Control': 'public, max-age=3600',  # Cache for 1 hour
                        'Content-Type': 'application/json'
                    }
                )

            # No conversion requested - return file as-is
            headers = {
                'Content-Type': content_type,
                'Content-Length': str(content_length),
                'Content-Disposition': f'inline; filename="{filename}"',
                'Cache-Control': 'public, max-age=86400'  # Cache original files for 24 hours
            }

            # Return file as streaming response
            return StreamingResponse(
                io.BytesIO(file_content),
                media_type=content_type,
                headers=headers
            )

        except Exception as s3_error:
            logger.error(f"S3 download error for entity {entity_id}: {str(s3_error)}")
            raise HTTPException(
                status_code=404,
                detail=f"File not found or inaccessible"
            )

    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        logger.error(f"File fetch error for entity {entity_id}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch file: {str(e)}"
        )