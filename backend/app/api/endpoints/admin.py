"""
Admin API endpoints for managing approvals, document verification, and manual reviews.
"""

from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Depends, Query, Body
from pydantic import BaseModel, Field

from ...models.document import DocumentModel, DocumentStatus, VerificationMethod
from ...models.user import UserModel, Permission
from ...services.auth_service import get_current_user, require_permission

router = APIRouter()

# Request/Response Models
class PendingApprovalResponse(BaseModel):
    instance_id: str
    workflow_name: str
    citizen_name: str
    citizen_id: str
    step_name: str
    submitted_at: datetime
    priority: str = "medium"
    approval_type: str
    context: Dict[str, Any]
    assigned_to: Optional[str] = None

class PendingDocumentResponse(BaseModel):
    document_id: str
    title: str
    document_type: str
    citizen_name: str
    citizen_id: str
    uploaded_at: datetime
    file_size: int
    mime_type: str
    status: str = "pending_verification"
    verification_priority: str = "normal"
    previous_attempts: int = 0

class PendingSignatureResponse(BaseModel):
    document_id: str
    title: str
    document_type: str
    citizen_name: str
    citizen_id: str
    workflow_name: str
    signature_type: str
    requires_signature_at: datetime
    deadline: Optional[datetime] = None

class ManualReviewResponse(BaseModel):
    review_id: str
    type: str
    citizen_name: str
    citizen_id: str
    workflow_name: str
    issue_description: str
    severity: str
    created_at: datetime
    context: Dict[str, Any]

class AdminStatsResponse(BaseModel):
    pending_approvals: int
    pending_documents: int
    pending_signatures: int
    manual_reviews: int
    total_pending: int

# Admin dependency
async def get_current_admin(current_user: UserModel = Depends(require_permission(Permission.VIEW_DOCUMENTS))):
    return current_user

@router.get("/pending-approvals", response_model=List[PendingApprovalResponse])
async def get_pending_approvals(
    admin: UserModel = Depends(get_current_admin),
    assigned_to: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0)
):
    """Get all pending workflow approvals requiring admin action."""
    try:
        # Mock data for now - in production, this would query WorkflowInstance collection
        mock_approvals = [
            PendingApprovalResponse(
                instance_id="inst_001",
                workflow_name="Citizen Registration",
                citizen_name="Maria González",
                citizen_id="citizen_001",
                step_name="Age Verification",
                submitted_at=datetime.utcnow() - timedelta(hours=2),
                priority="high",
                approval_type="age_verification",
                context={"age": 17, "requires_guardian": True},
                assigned_to=str(admin.id)
            ),
            PendingApprovalResponse(
                instance_id="inst_002",
                workflow_name="Building Permit",
                citizen_name="John Smith",
                citizen_id="citizen_002",
                step_name="Manual Review",
                submitted_at=datetime.utcnow() - timedelta(hours=4),
                priority="medium",
                approval_type="permit_approval",
                context={"permit_type": "residential", "area": "250 sqm"},
                assigned_to="admin_002"
            )
        ]
        
        return mock_approvals[offset:offset+limit]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch pending approvals: {str(e)}")

@router.get("/documents", response_model=List[PendingDocumentResponse])
async def get_all_documents(
    admin: UserModel = Depends(get_current_admin),
    status: Optional[str] = Query(None),
    document_type: Optional[str] = Query(None),
    citizen_name: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    verification_priority: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0)
):
    """Get all documents (pending, verified, rejected) with filters."""
    try:
        query = {}
        
        # Apply filters
        if status:
            if status == "verified":
                query["status"] = DocumentStatus.VERIFIED
            elif status == "rejected":
                query["status"] = DocumentStatus.REJECTED
            elif status == "pending_verification":
                query["status"] = DocumentStatus.PENDING
            else:
                query["status"] = status
                
        if document_type:
            query["document_type"] = document_type
        if verification_priority:
            query["verification_priority"] = verification_priority
        
        documents = await DocumentModel.find(query).skip(offset).limit(limit).to_list()
        
        filtered_docs = []
        for doc in documents:
            # Mock citizen name
            citizen_name_value = f"Citizen {str(doc.citizen_id)[-3:]}"
            
            # Filter by citizen name if provided
            if citizen_name and citizen_name.lower() not in citizen_name_value.lower():
                continue
                
            filtered_docs.append(PendingDocumentResponse(
                document_id=doc.document_id,
                title=doc.title,
                document_type=doc.document_type,
                citizen_name=citizen_name_value,
                citizen_id=str(doc.citizen_id),
                uploaded_at=doc.created_at,
                file_size=getattr(doc.metadata, "file_size", 1024000),  # Default 1MB
                mime_type=getattr(doc.metadata, "mime_type", "application/pdf"),  # Default PDF
                status=doc.status,  # Include actual document status
                verification_priority=getattr(doc.metadata, "verification_priority", "normal"),
                previous_attempts=getattr(doc.metadata, "verification_attempts", 0)
            ))
        
        return filtered_docs
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch documents: {str(e)}")

@router.get("/pending-documents", response_model=List[PendingDocumentResponse])
async def get_pending_documents(
    admin: UserModel = Depends(get_current_admin),
    document_type: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0)
):
    """Get all documents pending verification."""
    try:
        # Query actual documents in the database
        query = {"status": DocumentStatus.PENDING}
        
        if document_type:
            query["document_type"] = document_type
            
        documents = await DocumentModel.find(query).skip(offset).limit(limit).to_list()
        
        pending_documents = []
        for doc in documents:
            # Mock citizen info for now
            citizen_name = f"Citizen {str(doc.citizen_id)[-3:]}"
            
            # Determine priority based on document type
            priority = "normal"
            if doc.document_type in ["national_id", "passport"]:
                priority = "urgent"
            elif getattr(doc.metadata, "verification_attempts", 0) > 1:
                priority = "low"
            
            pending_documents.append(PendingDocumentResponse(
                document_id=str(doc.document_id),
                title=doc.title,
                document_type=doc.document_type,
                citizen_name=citizen_name,
                citizen_id=str(doc.citizen_id),
                uploaded_at=doc.created_at,
                file_size=getattr(doc.metadata, "file_size", 0),
                mime_type=getattr(doc.metadata, "mime_type", "application/pdf"),
                status=doc.status,  # Include actual document status
                verification_priority=priority,
                previous_attempts=getattr(doc.metadata, "verification_attempts", 0)
            ))
        
        return pending_documents
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch pending documents: {str(e)}")

@router.get("/pending-signatures", response_model=List[PendingSignatureResponse])
async def get_pending_signatures(
    admin: UserModel = Depends(get_current_admin),
    assigned_to: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0)
):
    """Get all documents requiring administrative signatures."""
    try:
        # Find documents that need signatures (permits, certificates, etc.)
        query = {
            "document_type": {"$in": ["permit", "certificate", "approval_letter"]},
            "status": DocumentStatus.VERIFIED,
            "signatures": {"$size": 0}  # No signatures yet
        }
        
        documents = await DocumentModel.find(query).skip(offset).limit(limit).to_list()
        
        pending_signatures = []
        for doc in documents:
            # Mock citizen info
            citizen_name = f"Citizen {str(doc.citizen_id)[-3:]}"
            
            # Determine signature type and deadline
            signature_type = doc.document_type
            deadline = doc.created_at + timedelta(days=3)  # 3-day SLA
            
            pending_signatures.append(PendingSignatureResponse(
                document_id=str(doc.document_id),
                title=doc.title,
                document_type=doc.document_type,
                citizen_name=citizen_name,
                citizen_id=str(doc.citizen_id),
                workflow_name=getattr(doc.metadata, "workflow_name", "Unknown"),
                signature_type=signature_type,
                requires_signature_at=doc.verified_at or doc.created_at,
                deadline=deadline
            ))
        
        return pending_signatures
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch pending signatures: {str(e)}")

@router.get("/manual-reviews", response_model=List[ManualReviewResponse])
async def get_manual_reviews(
    admin: UserModel = Depends(get_current_admin),
    review_type: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0)
):
    """Get all items requiring manual review (duplicates, fraud, anomalies)."""
    try:
        # Check for documents with low confidence scores (potential fraud)
        low_confidence_docs = await DocumentModel.find({
            "metadata.confidence_score": {"$lt": 0.7},
            "status": DocumentStatus.PENDING
        }).limit(limit).to_list()
        
        manual_reviews = []
        for doc in low_confidence_docs:
            citizen_name = f"Citizen {str(doc.citizen_id)[-3:]}"
            
            manual_reviews.append(ManualReviewResponse(
                review_id=f"fraud_{doc.document_id}",
                type="fraud_check",
                citizen_name=citizen_name,
                citizen_id=str(doc.citizen_id),
                workflow_name=getattr(doc.metadata, "workflow_name", "Document Verification"),
                issue_description=f"Document authenticity flagged by automated verification. Confidence: {getattr(doc.metadata, 'confidence_score', 0):.1%}",
                severity="critical",
                created_at=doc.created_at,
                context={
                    "document_id": str(doc.document_id),
                    "document_type": doc.document_type,
                    "confidence_score": getattr(doc.metadata, "confidence_score", 0),
                    "fraud_indicators": getattr(doc.metadata, "fraud_indicators", [])
                }
            ))
        
        # Add some mock duplicate detection reviews
        if len(manual_reviews) == 0:
            manual_reviews.append(ManualReviewResponse(
                review_id="dup_001",
                type="duplicate_detection",
                citizen_name="Luis Fernando",
                citizen_id="citizen_009",
                workflow_name="Citizen Registration",
                issue_description="Potential duplicate registration detected. Same name and birth date found in system.",
                severity="high",
                created_at=datetime.utcnow() - timedelta(hours=1),
                context={
                    "duplicate_count": 2,
                    "citizen_ids": ["citizen_009", "citizen_010"],
                    "matching_fields": ["name", "date_of_birth"]
                }
            ))
        
        return manual_reviews
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch manual reviews: {str(e)}")

@router.post("/instances/{instance_id}/approve")
async def process_approval(
    instance_id: str,
    decision: str = Body(...),
    comments: str = Body(...),
    admin: UserModel = Depends(get_current_admin)
):
    """Process a workflow approval decision."""
    try:
        # Mock approval processing for now
        print(f"Processing approval for instance {instance_id}: {decision} - {comments}")
        
        return {"message": "Approval processed successfully", "instance_id": instance_id}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to process approval: {str(e)}")

@router.post("/documents/{document_id}/admin-verify")
async def process_document_verification(
    document_id: str,
    decision: str = Body(...),
    comments: str = Body(...),
    admin: UserModel = Depends(get_current_admin)
):
    """Process document verification decision."""
    try:
        # Find document by document_id field (not MongoDB _id)
        document = await DocumentModel.find_one(DocumentModel.document_id == document_id)
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        
        if decision == "approve":
            document.status = DocumentStatus.VERIFIED
            document.verification_method = VerificationMethod.MANUAL
            document.verified_by = str(admin.id)
            document.verified_at = datetime.utcnow()
            document.verification_notes = comments
            # Update metadata safely
            if hasattr(document.metadata, 'confidence_score'):
                document.metadata.confidence_score = 1.0
        else:
            document.status = DocumentStatus.REJECTED
            document.verification_notes = comments
            
        # Update attempts safely
        if hasattr(document.metadata, 'verification_attempts'):
            document.metadata.verification_attempts = getattr(document.metadata, "verification_attempts", 0) + 1
        
        document.updated_at = datetime.utcnow()
        await document.save()
        
        return {"message": "Document verification processed", "document_id": document_id}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to process verification: {str(e)}")

@router.post("/manual-reviews/{review_id}/resolve")
async def resolve_manual_review(
    review_id: str,
    resolution: str = Body(...),
    resolution_notes: str = Body(...),
    priority: str = Body("normal"),
    admin: UserModel = Depends(get_current_admin)
):
    """Resolve a manual review item."""
    try:
        # Mock resolution for now
        print(f"Resolving review {review_id}: {resolution} - {resolution_notes}")
        
        return {"message": "Manual review resolved", "review_id": review_id}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to resolve review: {str(e)}")

@router.get("/stats", response_model=AdminStatsResponse)
async def get_admin_stats(admin: dict = Depends(get_current_admin)):
    """Get administrative dashboard statistics."""
    try:
        # Count pending documents (real data)
        pending_documents = await DocumentModel.find({
            "status": DocumentStatus.PENDING
        }).count()
        
        # Count documents needing signatures (real data)
        pending_signatures = await DocumentModel.find({
            "document_type": {"$in": ["permit", "certificate", "approval_letter"]},
            "status": DocumentStatus.VERIFIED,
            "signatures": {"$size": 0}
        }).count()
        
        # Mock data for workflow approvals and manual reviews
        pending_approvals = 2  # From mock data above
        manual_reviews = 1    # From mock data above
        
        return AdminStatsResponse(
            pending_approvals=pending_approvals,
            pending_documents=pending_documents,
            pending_signatures=pending_signatures,
            manual_reviews=manual_reviews,
            total_pending=pending_approvals + pending_documents + pending_signatures + manual_reviews
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get admin stats: {str(e)}")

@router.post("/documents/{document_id}/sign")
async def sign_document(
    document_id: str,
    signature_method: str = Body(...),
    signature_data: str = Body(...),
    comments: str = Body(None),
    admin: UserModel = Depends(get_current_admin)
):
    """Add administrative signature to a document."""
    try:
        document = await DocumentModel.get(document_id)
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        
        # Add signature to document metadata for now
        if "signatures" not in document.metadata:
            document.metadata["signatures"] = []
        
        document.metadata["signatures"].append({
            "signer_id": str(admin.id),
            "signer_name": admin.full_name,
            "signer_role": admin.role,
            "signature_method": signature_method,
            "signature_data": signature_data,
            "signed_at": datetime.utcnow().isoformat(),
            "comments": comments
        })
        
        document.updated_at = datetime.utcnow()
        await document.save()
        
        return {"message": "Document signed successfully", "document_id": document_id}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to sign document: {str(e)}")