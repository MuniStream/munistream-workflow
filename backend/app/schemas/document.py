"""
Document management schemas for API requests/responses.
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, validator

from ..models.document import DocumentType, DocumentStatus, VerificationMethod, DocumentAccess


class DocumentUploadRequest(BaseModel):
    """Request schema for document upload"""
    document_type: DocumentType
    title: str
    description: Optional[str] = None
    access_level: DocumentAccess = DocumentAccess.PRIVATE
    tags: List[str] = Field(default_factory=list)
    category: Optional[str] = None
    expires_at: Optional[datetime] = None


class DocumentUpdateRequest(BaseModel):
    """Request schema for document updates"""
    title: Optional[str] = None
    description: Optional[str] = None
    access_level: Optional[DocumentAccess] = None
    tags: Optional[List[str]] = None
    category: Optional[str] = None
    expires_at: Optional[datetime] = None


class DocumentVerificationRequest(BaseModel):
    """Request schema for document verification"""
    verification_method: VerificationMethod
    verification_notes: Optional[str] = None
    approve: bool = True  # True for approve, False for reject


class DocumentSignatureRequest(BaseModel):
    """Request schema for document signing"""
    signature_method: str
    signature_data: Optional[str] = None  # Base64 encoded signature
    certificate_id: Optional[str] = None


class DocumentSearchRequest(BaseModel):
    """Request schema for document search"""
    document_types: Optional[List[DocumentType]] = None
    statuses: Optional[List[DocumentStatus]] = None
    tags: Optional[List[str]] = None
    category: Optional[str] = None
    from_date: Optional[datetime] = None
    to_date: Optional[datetime] = None
    verified_only: bool = False
    can_reuse: bool = False
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class DocumentShareRequest(BaseModel):
    """Request schema for document sharing"""
    shared_with: str  # User ID or role
    share_type: str   # "user", "role", "workflow", "public"
    can_view: bool = True
    can_download: bool = False
    can_verify: bool = False
    can_sign: bool = False
    expires_at: Optional[datetime] = None


# Response Schemas

class DocumentMetadataResponse(BaseModel):
    """Response schema for document metadata"""
    original_filename: Optional[str]
    file_size: Optional[int]
    mime_type: Optional[str]
    width: Optional[int]
    height: Optional[int]
    pages: Optional[int]
    confidence_score: Optional[float]
    fraud_detection_score: Optional[float]
    quality_score: Optional[float]
    extracted_data: Dict[str, Any] = Field(default_factory=dict)


class DocumentSignatureResponse(BaseModel):
    """Response schema for document signature"""
    signer_id: str
    signer_name: str
    signer_role: str
    signature_method: str
    signed_at: datetime
    is_valid: bool


class DocumentResponse(BaseModel):
    """Response schema for document details"""
    document_id: str
    citizen_id: str
    document_type: DocumentType
    title: str
    description: Optional[str]
    status: DocumentStatus
    access_level: DocumentAccess
    
    # File information
    file_url: Optional[str]
    metadata: DocumentMetadataResponse
    
    # Verification
    verification_method: Optional[VerificationMethod]
    verified_by: Optional[str]
    verified_at: Optional[datetime]
    verification_notes: Optional[str]
    
    # Workflow context
    source_workflow_id: Optional[str]
    source_step_id: Optional[str]
    generated_by_system: bool
    
    # Lifecycle
    created_at: datetime
    updated_at: datetime
    expires_at: Optional[datetime]
    
    # Signatures
    signatures: List[DocumentSignatureResponse] = Field(default_factory=list)
    
    # Usage
    used_in_workflows: List[str] = Field(default_factory=list)
    usage_count: int
    last_used_at: Optional[datetime]
    
    # Organization
    tags: List[str] = Field(default_factory=list)
    category: Optional[str]
    
    # Computed fields
    is_expired: bool = False
    can_be_reused: bool = False
    
    class Config:
        from_attributes = True


class DocumentListResponse(BaseModel):
    """Response schema for document list"""
    documents: List[DocumentResponse]
    total_count: int
    has_more: bool
    next_offset: Optional[int] = None


class DocumentFolderResponse(BaseModel):
    """Response schema for document folder"""
    citizen_id: str
    folder_name: str
    description: Optional[str]
    document_count: int
    folder_structure: Dict[str, int]  # Category -> count
    created_at: datetime
    updated_at: datetime


class DocumentStatsResponse(BaseModel):
    """Response schema for document statistics"""
    total_documents: int
    by_type: Dict[str, int]
    by_status: Dict[str, int]
    by_category: Dict[str, int]
    verified_count: int
    pending_verification: int
    expired_count: int
    recently_uploaded: int  # Last 7 days
    storage_used_bytes: int


class DocumentVerificationResponse(BaseModel):
    """Response schema for document verification result"""
    document_id: str
    status: DocumentStatus
    verification_method: VerificationMethod
    verified_by: str
    verified_at: datetime
    verification_notes: Optional[str]
    confidence_score: Optional[float]


class DocumentReuseSuggestion(BaseModel):
    """Schema for document reuse suggestions"""
    document_id: str
    document_type: DocumentType
    title: str
    verified_at: Optional[datetime]
    usage_count: int
    relevance_score: float
    reason: str  # Why this document is suggested


class DocumentReuseResponse(BaseModel):
    """Response schema for document reuse suggestions"""
    workflow_id: str
    step_id: str
    required_document_types: List[DocumentType]
    suggestions: List[DocumentReuseSuggestion]
    total_available: int


# Workflow Integration Schemas

class WorkflowDocumentRequirement(BaseModel):
    """Schema for document requirements in workflow steps"""
    document_type: DocumentType
    required: bool = True
    min_confidence_score: Optional[float] = None
    must_be_verified: bool = True
    max_age_days: Optional[int] = None  # Document age limit
    description: str


class WorkflowDocumentInput(BaseModel):
    """Schema for document input in workflow execution"""
    document_id: Optional[str] = None  # Existing document
    upload_new: bool = False
    document_data: Optional[str] = None  # Base64 encoded file data
    filename: Optional[str] = None
    
    @validator('document_id', 'document_data')
    def validate_document_source(cls, v, values):
        """Ensure either document_id or document_data is provided"""
        if not values.get('document_id') and not values.get('document_data'):
            raise ValueError('Either document_id or document_data must be provided')
        return v


class DocumentGenerationRequest(BaseModel):
    """Request schema for generating documents from templates"""
    template_id: str
    output_format: str = "pdf"  # pdf, docx, html
    data: Dict[str, Any] = Field(default_factory=dict)
    title: str
    description: Optional[str] = None
    auto_sign: bool = False
    access_level: DocumentAccess = DocumentAccess.WORKFLOW


class BulkDocumentOperationRequest(BaseModel):
    """Request schema for bulk document operations"""
    document_ids: List[str]
    operation: str  # "verify", "sign", "share", "delete", "archive"
    parameters: Dict[str, Any] = Field(default_factory=dict)


class BulkDocumentOperationResponse(BaseModel):
    """Response schema for bulk document operations"""
    total_requested: int
    successful: int
    failed: int
    results: List[Dict[str, Any]]
    errors: List[str] = Field(default_factory=list)