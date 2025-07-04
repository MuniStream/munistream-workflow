"""
Document management API endpoints.
Handles document upload, verification, folder management, and reuse across workflows.
"""

from typing import List, Optional
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Query, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.security import HTTPBearer

from ...models.document import DocumentModel, DocumentType, DocumentStatus, VerificationMethod
from ...schemas.document import (
    DocumentUploadRequest, DocumentUpdateRequest, DocumentVerificationRequest,
    DocumentSignatureRequest, DocumentSearchRequest, DocumentShareRequest,
    DocumentResponse, DocumentListResponse, DocumentFolderResponse,
    DocumentStatsResponse, DocumentVerificationResponse, DocumentReuseResponse,
    BulkDocumentOperationRequest, BulkDocumentOperationResponse
)
from ...services.document_service import document_service
from ...core.config import settings

router = APIRouter()
security = HTTPBearer()

# Dependency to get current user (simplified)
async def get_current_user(token: str = Depends(security)):
    # In real implementation, decode JWT token and get user
    return {"user_id": "user123", "role": "citizen"}

async def get_current_admin(token: str = Depends(security)):
    # In real implementation, verify admin role
    return {"user_id": "admin123", "role": "administrator"}


@router.post("/upload", response_model=DocumentResponse)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    document_type: DocumentType = Query(...),
    title: str = Query(...),
    description: Optional[str] = Query(None),
    access_level: str = Query("private"),
    tags: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user)
):
    """Upload a new document"""
    
    # Parse tags
    tag_list = [tag.strip() for tag in tags.split(",")] if tags else []
    
    # Create upload request
    upload_request = DocumentUploadRequest(
        document_type=document_type,
        title=title,
        description=description,
        access_level=access_level,
        tags=tag_list,
        category=category
    )
    
    try:
        # Upload document
        document = await document_service.upload_document(
            file, current_user["user_id"], upload_request
        )
        
        # Convert to response model
        response = DocumentResponse.from_orm(document)
        response.is_expired = document.is_expired()
        response.can_be_reused = document.can_be_reused()
        
        return response
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@router.get("/", response_model=DocumentListResponse)
async def list_documents(
    document_types: Optional[List[DocumentType]] = Query(None),
    statuses: Optional[List[DocumentStatus]] = Query(None),
    tags: Optional[List[str]] = Query(None),
    category: Optional[str] = Query(None),
    from_date: Optional[datetime] = Query(None),
    to_date: Optional[datetime] = Query(None),
    verified_only: bool = Query(False),
    can_reuse: bool = Query(False),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user)
):
    """List documents for the current user"""
    
    search_request = DocumentSearchRequest(
        document_types=document_types,
        statuses=statuses,
        tags=tags,
        category=category,
        from_date=from_date,
        to_date=to_date,
        verified_only=verified_only,
        can_reuse=can_reuse,
        limit=limit,
        offset=offset
    )
    
    documents, total_count = await document_service.search_documents(
        current_user["user_id"], search_request
    )
    
    # Convert to response models
    document_responses = []
    for doc in documents:
        response = DocumentResponse.from_orm(doc)
        response.is_expired = doc.is_expired()
        response.can_be_reused = doc.can_be_reused()
        document_responses.append(response)
    
    return DocumentListResponse(
        documents=document_responses,
        total_count=total_count,
        has_more=offset + limit < total_count,
        next_offset=offset + limit if offset + limit < total_count else None
    )


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get a specific document"""
    
    document = await document_service.get_document(document_id, current_user["user_id"])
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    response = DocumentResponse.from_orm(document)
    response.is_expired = document.is_expired()
    response.can_be_reused = document.can_be_reused()
    
    return response


@router.put("/{document_id}", response_model=DocumentResponse)
async def update_document(
    document_id: str,
    update_request: DocumentUpdateRequest,
    current_user: dict = Depends(get_current_user)
):
    """Update document metadata"""
    
    document = await document_service.get_document(document_id, current_user["user_id"])
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Update fields
    if update_request.title is not None:
        document.title = update_request.title
    if update_request.description is not None:
        document.description = update_request.description
    if update_request.access_level is not None:
        document.access_level = update_request.access_level
    if update_request.tags is not None:
        document.tags = update_request.tags
    if update_request.category is not None:
        document.category = update_request.category
    if update_request.expires_at is not None:
        document.expires_at = update_request.expires_at
    
    document.updated_at = datetime.utcnow()
    await document.save()
    
    response = DocumentResponse.from_orm(document)
    response.is_expired = document.is_expired()
    response.can_be_reused = document.can_be_reused()
    
    return response


@router.delete("/{document_id}")
async def delete_document(
    document_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Delete a document"""
    
    document = await document_service.get_document(document_id, current_user["user_id"])
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Delete file from storage
    await document_service.storage_service.delete_file(document.file_path)
    
    # Delete document record
    await document.delete()
    
    return {"message": "Document deleted successfully"}


@router.get("/{document_id}/download")
async def download_document(
    document_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Download document file"""
    
    document = await document_service.get_document(document_id, current_user["user_id"])
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    file_path = await document_service.storage_service.get_file_path(document.file_path)
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    return FileResponse(
        path=str(file_path),
        filename=document.metadata.original_filename or f"{document.title}.pdf",
        media_type=document.metadata.mime_type or "application/octet-stream"
    )


@router.post("/{document_id}/verify", response_model=DocumentVerificationResponse)
async def verify_document(
    document_id: str,
    verification_request: DocumentVerificationRequest,
    current_admin: dict = Depends(get_current_admin)
):
    """Verify a document (admin only)"""
    
    document = await document_service.verify_document(
        document_id,
        current_admin["user_id"],
        verification_request.verification_method,
        verification_request.approve,
        verification_request.verification_notes
    )
    
    return DocumentVerificationResponse(
        document_id=document.document_id,
        status=document.status,
        verification_method=document.verification_method,
        verified_by=document.verified_by,
        verified_at=document.verified_at,
        verification_notes=document.verification_notes,
        confidence_score=document.metadata.confidence_score
    )


@router.post("/{document_id}/sign", response_model=DocumentResponse)
async def sign_document(
    document_id: str,
    signature_request: DocumentSignatureRequest,
    current_admin: dict = Depends(get_current_admin)
):
    """Add digital signature to document (admin only)"""
    
    document = await document_service.get_document(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Add signature
    document.add_signature(
        signer_id=current_admin["user_id"],
        signer_name=current_admin.get("name", "Administrator"),
        signer_role=current_admin["role"],
        signature_method=signature_request.signature_method,
        signature_data=signature_request.signature_data
    )
    
    await document.save()
    
    response = DocumentResponse.from_orm(document)
    response.is_expired = document.is_expired()
    response.can_be_reused = document.can_be_reused()
    
    return response


@router.get("/folder/", response_model=DocumentFolderResponse)
async def get_document_folder(
    current_user: dict = Depends(get_current_user)
):
    """Get user's document folder"""
    
    folder = await document_service.get_citizen_folder(current_user["user_id"])
    
    # Count documents by category
    folder_structure = {}
    for category, doc_ids in folder.folder_structure.items():
        folder_structure[category] = len(doc_ids)
    
    return DocumentFolderResponse(
        citizen_id=folder.citizen_id,
        folder_name=folder.folder_name,
        description=folder.description,
        document_count=len(folder.document_ids),
        folder_structure=folder_structure,
        created_at=folder.created_at,
        updated_at=folder.updated_at
    )


@router.get("/stats/", response_model=DocumentStatsResponse)
async def get_document_stats(
    current_user: dict = Depends(get_current_user)
):
    """Get document statistics for user"""
    
    stats = await document_service.get_document_stats(current_user["user_id"])
    
    return DocumentStatsResponse(**stats)


@router.get("/reuse-suggestions/{workflow_id}", response_model=DocumentReuseResponse)
async def get_reuse_suggestions(
    workflow_id: str,
    step_id: str = Query(...),
    required_types: List[DocumentType] = Query(...),
    current_user: dict = Depends(get_current_user)
):
    """Get document reuse suggestions for a workflow step"""
    
    suggestions = await document_service.suggest_reusable_documents(
        current_user["user_id"], workflow_id, required_types
    )
    
    return DocumentReuseResponse(
        workflow_id=workflow_id,
        step_id=step_id,
        required_document_types=required_types,
        suggestions=suggestions,
        total_available=len(suggestions)
    )


@router.post("/bulk-operations", response_model=BulkDocumentOperationResponse)
async def bulk_document_operation(
    operation_request: BulkDocumentOperationRequest,
    current_user: dict = Depends(get_current_user)
):
    """Perform bulk operations on documents"""
    
    results = []
    errors = []
    successful = 0
    failed = 0
    
    for document_id in operation_request.document_ids:
        try:
            document = await document_service.get_document(document_id, current_user["user_id"])
            if not document:
                errors.append(f"Document {document_id} not found")
                failed += 1
                continue
            
            if operation_request.operation == "verify":
                # Bulk verification (admin only)
                if current_user.get("role") != "administrator":
                    errors.append(f"Insufficient permissions for document {document_id}")
                    failed += 1
                    continue
                
                await document_service.verify_document(
                    document_id,
                    current_user["user_id"],
                    VerificationMethod.MANUAL,
                    True,
                    "Bulk verification"
                )
                
            elif operation_request.operation == "delete":
                await document_service.storage_service.delete_file(document.file_path)
                await document.delete()
                
            else:
                errors.append(f"Unknown operation: {operation_request.operation}")
                failed += 1
                continue
            
            results.append({"document_id": document_id, "status": "success"})
            successful += 1
            
        except Exception as e:
            errors.append(f"Error processing document {document_id}: {str(e)}")
            failed += 1
    
    return BulkDocumentOperationResponse(
        total_requested=len(operation_request.document_ids),
        successful=successful,
        failed=failed,
        results=results,
        errors=errors
    )


# Admin endpoints

@router.get("/admin/pending-verification", response_model=DocumentListResponse)
async def get_pending_verification(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_admin: dict = Depends(get_current_admin)
):
    """Get documents pending verification (admin only)"""
    
    search_request = DocumentSearchRequest(
        statuses=[DocumentStatus.PENDING, DocumentStatus.UNDER_REVIEW],
        limit=limit,
        offset=offset
    )
    
    # For admin, search across all citizens (modify service to support this)
    documents = await DocumentModel.find({
        "status": {"$in": [DocumentStatus.PENDING, DocumentStatus.UNDER_REVIEW]}
    }).sort([("created_at", 1)]).skip(offset).limit(limit).to_list()
    
    total_count = await DocumentModel.find({
        "status": {"$in": [DocumentStatus.PENDING, DocumentStatus.UNDER_REVIEW]}
    }).count()
    
    # Convert to response models
    document_responses = []
    for doc in documents:
        response = DocumentResponse.from_orm(doc)
        response.is_expired = doc.is_expired()
        response.can_be_reused = doc.can_be_reused()
        document_responses.append(response)
    
    return DocumentListResponse(
        documents=document_responses,
        total_count=total_count,
        has_more=offset + limit < total_count,
        next_offset=offset + limit if offset + limit < total_count else None
    )


@router.get("/admin/stats/system", response_model=dict)
async def get_system_document_stats(
    current_admin: dict = Depends(get_current_admin)
):
    """Get system-wide document statistics (admin only)"""
    
    total_documents = await DocumentModel.count()
    pending_verification = await DocumentModel.find({
        "status": {"$in": [DocumentStatus.PENDING, DocumentStatus.UNDER_REVIEW]}
    }).count()
    verified_documents = await DocumentModel.find({"status": DocumentStatus.VERIFIED}).count()
    
    # Get recent activity
    from datetime import timedelta
    week_ago = datetime.utcnow() - timedelta(days=7)
    recent_uploads = await DocumentModel.find({"created_at": {"$gte": week_ago}}).count()
    recent_verifications = await DocumentModel.find({"verified_at": {"$gte": week_ago}}).count()
    
    return {
        "total_documents": total_documents,
        "pending_verification": pending_verification,
        "verified_documents": verified_documents,
        "verification_rate": (verified_documents / total_documents * 100) if total_documents > 0 else 0,
        "recent_uploads": recent_uploads,
        "recent_verifications": recent_verifications
    }