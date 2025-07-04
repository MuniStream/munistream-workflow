"""
Document management models for CivicStream.
Handles citizen documents, verification, and reuse across workflows.
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from enum import Enum

from beanie import Document, Indexed
from pydantic import BaseModel, Field


class DocumentType(str, Enum):
    """Types of documents in the system"""
    # Identity Documents
    NATIONAL_ID = "national_id"
    PASSPORT = "passport" 
    DRIVERS_LICENSE = "drivers_license"
    BIRTH_CERTIFICATE = "birth_certificate"
    
    # Business Documents
    BUSINESS_LICENSE = "business_license"
    TAX_CERTIFICATE = "tax_certificate"
    INCORPORATION_CERTIFICATE = "incorporation_certificate"
    
    # Government Generated
    PERMIT = "permit"
    CERTIFICATE = "certificate"
    APPROVAL_LETTER = "approval_letter"
    REJECTION_LETTER = "rejection_letter"
    
    # Supporting Documents
    PROOF_OF_ADDRESS = "proof_of_address"
    BANK_STATEMENT = "bank_statement"
    UTILITY_BILL = "utility_bill"
    PHOTO = "photo"
    
    # Other
    OTHER = "other"


class DocumentStatus(str, Enum):
    """Document verification status"""
    PENDING = "pending"           # Uploaded, awaiting verification
    UNDER_REVIEW = "under_review" # Being reviewed by clerk/system
    VERIFIED = "verified"         # Verified and approved
    REJECTED = "rejected"         # Rejected during verification
    EXPIRED = "expired"           # Document has expired
    REVOKED = "revoked"          # Document access revoked


class VerificationMethod(str, Enum):
    """How the document was verified"""
    AUTOMATIC = "automatic"      # AI/ML verification
    MANUAL = "manual"           # Human clerk verification
    SYSTEM = "system"           # System-generated document
    EXTERNAL_API = "external_api" # Third-party verification service


class DocumentAccess(str, Enum):
    """Document access levels"""
    PRIVATE = "private"         # Only citizen can see
    WORKFLOW = "workflow"       # Available to workflow participants
    PUBLIC = "public"          # Publicly accessible
    RESTRICTED = "restricted"   # Special permissions required


class DocumentMetadata(BaseModel):
    """Document metadata and properties"""
    original_filename: Optional[str] = None
    file_size: Optional[int] = None  # Size in bytes
    mime_type: Optional[str] = None
    width: Optional[int] = None      # For images
    height: Optional[int] = None     # For images
    pages: Optional[int] = None      # For PDFs
    language: Optional[str] = None
    encoding: Optional[str] = None
    
    # Extracted information
    extracted_text: Optional[str] = None
    extracted_data: Dict[str, Any] = Field(default_factory=dict)
    
    # AI/ML analysis results
    confidence_score: Optional[float] = None
    fraud_detection_score: Optional[float] = None
    quality_score: Optional[float] = None


class DocumentSignature(BaseModel):
    """Digital signature information"""
    signer_id: str
    signer_name: str
    signer_role: str  # "citizen", "clerk", "administrator", "system"
    signature_method: str  # "digital", "electronic", "wet_signature"
    signed_at: datetime
    signature_data: Optional[str] = None  # Base64 encoded signature
    certificate_id: Optional[str] = None
    is_valid: bool = True


class DocumentVersion(BaseModel):
    """Document version information"""
    version: int
    created_at: datetime
    created_by: str
    changes: str
    file_path: str
    checksum: str


class DocumentModel(Document):
    """Main document model"""
    
    # Basic Information
    document_id: Indexed(str) = Field(default_factory=lambda: f"doc_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}")
    citizen_id: Indexed(str)  # Owner of the document
    
    # Document Properties
    document_type: DocumentType
    title: str
    description: Optional[str] = None
    status: DocumentStatus = DocumentStatus.PENDING
    access_level: DocumentAccess = DocumentAccess.PRIVATE
    
    # File Information
    file_path: str  # Path to stored file
    file_url: Optional[str] = None  # Public URL if accessible
    checksum: str  # File integrity check
    metadata: DocumentMetadata = Field(default_factory=DocumentMetadata)
    
    # Verification
    verification_method: Optional[VerificationMethod] = None
    verified_by: Optional[str] = None  # User ID who verified
    verified_at: Optional[datetime] = None
    verification_notes: Optional[str] = None
    
    # Workflow Context
    source_workflow_id: Optional[str] = None
    source_step_id: Optional[str] = None
    source_instance_id: Optional[str] = None
    generated_by_system: bool = False
    
    # Lifecycle
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None
    
    # Digital Signatures
    signatures: List[DocumentSignature] = Field(default_factory=list)
    
    # Versioning
    current_version: int = 1
    versions: List[DocumentVersion] = Field(default_factory=list)
    
    # Usage Tracking
    used_in_workflows: List[str] = Field(default_factory=list)  # Workflow IDs where this doc was used
    usage_count: int = 0
    last_used_at: Optional[datetime] = None
    
    # Tags and Categories
    tags: List[str] = Field(default_factory=list)
    category: Optional[str] = None
    
    class Settings:
        name = "documents"
        indexes = [
            [("citizen_id", 1), ("document_type", 1)],
            [("status", 1), ("created_at", -1)],
            [("source_workflow_id", 1)],
            "document_id",
            "expires_at"
        ]
    
    def add_signature(self, signer_id: str, signer_name: str, signer_role: str, 
                     signature_method: str, signature_data: Optional[str] = None):
        """Add a digital signature to the document"""
        signature = DocumentSignature(
            signer_id=signer_id,
            signer_name=signer_name,
            signer_role=signer_role,
            signature_method=signature_method,
            signed_at=datetime.utcnow(),
            signature_data=signature_data
        )
        self.signatures.append(signature)
        self.updated_at = datetime.utcnow()
    
    def mark_verified(self, verified_by: str, method: VerificationMethod, notes: Optional[str] = None):
        """Mark document as verified"""
        self.status = DocumentStatus.VERIFIED
        self.verified_by = verified_by
        self.verified_at = datetime.utcnow()
        self.verification_method = method
        self.verification_notes = notes
        self.updated_at = datetime.utcnow()
    
    def mark_used_in_workflow(self, workflow_id: str):
        """Track usage in a workflow"""
        if workflow_id not in self.used_in_workflows:
            self.used_in_workflows.append(workflow_id)
        self.usage_count += 1
        self.last_used_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()
    
    def is_expired(self) -> bool:
        """Check if document is expired"""
        if self.expires_at:
            return datetime.utcnow() > self.expires_at
        return False
    
    def can_be_reused(self) -> bool:
        """Check if document can be reused in workflows"""
        return (
            self.status == DocumentStatus.VERIFIED and 
            not self.is_expired() and 
            self.access_level in [DocumentAccess.WORKFLOW, DocumentAccess.PUBLIC]
        )


class DocumentFolderModel(Document):
    """Citizen's document folder/collection"""
    
    citizen_id: Indexed(str)
    folder_name: str = "My Documents"
    description: Optional[str] = None
    
    # Organization
    document_ids: List[str] = Field(default_factory=list)
    folder_structure: Dict[str, List[str]] = Field(default_factory=dict)  # Category -> document_ids
    
    # Settings
    auto_organize: bool = True
    default_access_level: DocumentAccess = DocumentAccess.PRIVATE
    
    # Lifecycle
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Settings:
        name = "document_folders"
        indexes = ["citizen_id"]
    
    def add_document(self, document_id: str, category: Optional[str] = None):
        """Add document to folder"""
        if document_id not in self.document_ids:
            self.document_ids.append(document_id)
            
            if category and self.auto_organize:
                if category not in self.folder_structure:
                    self.folder_structure[category] = []
                if document_id not in self.folder_structure[category]:
                    self.folder_structure[category].append(document_id)
            
            self.updated_at = datetime.utcnow()
    
    def remove_document(self, document_id: str):
        """Remove document from folder"""
        if document_id in self.document_ids:
            self.document_ids.remove(document_id)
            
            # Remove from categories
            for category, docs in self.folder_structure.items():
                if document_id in docs:
                    docs.remove(document_id)
            
            self.updated_at = datetime.utcnow()


class DocumentShareModel(Document):
    """Document sharing with other users/roles"""
    
    document_id: Indexed(str)
    citizen_id: str  # Document owner
    
    # Sharing details
    shared_with: str  # User ID or role
    shared_by: str    # Who shared it
    share_type: str   # "user", "role", "workflow", "public"
    
    # Permissions
    can_view: bool = True
    can_download: bool = False
    can_verify: bool = False
    can_sign: bool = False
    
    # Lifecycle
    shared_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    
    class Settings:
        name = "document_shares"
        indexes = [
            [("document_id", 1), ("shared_with", 1)],
            "citizen_id",
            "expires_at"
        ]