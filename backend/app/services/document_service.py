"""
Document management service for CivicStream.
Handles file storage, verification, and document lifecycle.
"""

import os
import hashlib
import mimetypes
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, BinaryIO
from pathlib import Path
import json
import base64

from fastapi import UploadFile, HTTPException
from PIL import Image
import pypdf

try:
    import magic
    HAS_MAGIC = True
except ImportError:
    HAS_MAGIC = False

from ..models.document import (
    DocumentModel, DocumentFolderModel, DocumentShareModel,
    DocumentType, DocumentStatus, VerificationMethod, DocumentAccess,
    DocumentMetadata
)
from ..schemas.document import (
    DocumentUploadRequest, DocumentSearchRequest, DocumentReuseSuggestion
)
from ..core.config import settings


class DocumentStorageService:
    """Handles file storage operations"""
    
    def __init__(self):
        self.storage_path = Path(settings.DOCUMENT_STORAGE_PATH or "./storage/documents")
        self.storage_path.mkdir(parents=True, exist_ok=True)
        
        # Supported file types
        self.allowed_mime_types = {
            # Images
            "image/jpeg", "image/png", "image/gif", "image/bmp", "image/webp",
            # Documents
            "application/pdf", "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "text/plain", "text/csv",
            # Archives (for bulk uploads)
            "application/zip", "application/x-rar-compressed"
        }
        
        self.max_file_size = settings.MAX_DOCUMENT_SIZE_MB * 1024 * 1024  # Convert to bytes
    
    async def store_file(self, file: UploadFile, citizen_id: str, document_id: str) -> tuple[str, str, DocumentMetadata]:
        """Store uploaded file and return file path, checksum, and metadata"""
        
        # Validate file
        await self._validate_file(file)
        
        # Create directory structure: storage/citizen_id/year/month/
        now = datetime.utcnow()
        dir_path = self.storage_path / citizen_id / str(now.year) / f"{now.month:02d}"
        dir_path.mkdir(parents=True, exist_ok=True)
        
        # Generate filename
        file_extension = Path(file.filename or "").suffix
        filename = f"{document_id}_{now.strftime('%Y%m%d_%H%M%S')}{file_extension}"
        file_path = dir_path / filename
        
        # Save file and calculate checksum
        checksum = hashlib.sha256()
        file_size = 0
        
        with open(file_path, "wb") as f:
            while chunk := await file.read(8192):
                f.write(chunk)
                checksum.update(chunk)
                file_size += len(chunk)
        
        # Generate metadata
        metadata = await self._extract_metadata(file_path, file.filename, file_size)
        
        # Return relative path for database storage
        relative_path = str(file_path.relative_to(self.storage_path))
        
        return relative_path, checksum.hexdigest(), metadata
    
    async def get_file_path(self, relative_path: str) -> Path:
        """Get absolute file path from relative path"""
        return self.storage_path / relative_path
    
    async def delete_file(self, relative_path: str) -> bool:
        """Delete file from storage"""
        try:
            file_path = await self.get_file_path(relative_path)
            if file_path.exists():
                file_path.unlink()
                return True
        except Exception:
            pass
        return False
    
    async def _validate_file(self, file: UploadFile):
        """Validate uploaded file"""
        if not file.filename:
            raise HTTPException(status_code=400, detail="Filename is required")
        
        # Check file size
        file.file.seek(0, 2)  # Seek to end
        file_size = file.file.tell()
        file.file.seek(0)  # Reset to beginning
        
        if file_size > self.max_file_size:
            raise HTTPException(
                status_code=413, 
                detail=f"File too large. Maximum size: {settings.MAX_DOCUMENT_SIZE_MB}MB"
            )
        
        # Check MIME type
        mime_type = file.content_type or mimetypes.guess_type(file.filename)[0]
        if mime_type not in self.allowed_mime_types:
            raise HTTPException(
                status_code=415,
                detail=f"File type not supported: {mime_type}"
            )
    
    async def _extract_metadata(self, file_path: Path, original_filename: Optional[str], file_size: int) -> DocumentMetadata:
        """Extract metadata from file"""
        # Determine MIME type
        if HAS_MAGIC:
            mime_type = magic.from_file(str(file_path), mime=True)
        else:
            mime_type = mimetypes.guess_type(str(file_path))[0]
        
        metadata = DocumentMetadata(
            original_filename=original_filename,
            file_size=file_size,
            mime_type=mime_type
        )
        
        try:
            # Image metadata
            if metadata.mime_type and metadata.mime_type.startswith("image/"):
                with Image.open(file_path) as img:
                    metadata.width, metadata.height = img.size
            
            # PDF metadata
            elif metadata.mime_type == "application/pdf":
                with open(file_path, "rb") as f:
                    pdf_reader = pypdf.PdfReader(f)
                    metadata.pages = len(pdf_reader.pages)
                    
                    # Extract text from first page for analysis
                    if pdf_reader.pages:
                        metadata.extracted_text = pdf_reader.pages[0].extract_text()[:1000]
            
            # Text file metadata
            elif metadata.mime_type and metadata.mime_type.startswith("text/"):
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read(1000)  # First 1000 chars
                    metadata.extracted_text = content
                    metadata.encoding = "utf-8"
        
        except Exception:
            # If metadata extraction fails, continue without it
            pass
        
        return metadata


class DocumentVerificationService:
    """Handles document verification and AI analysis"""
    
    async def auto_verify_document(self, document: DocumentModel) -> Dict[str, Any]:
        """Perform automatic verification of document"""
        verification_result = {
            "confidence_score": 0.0,
            "fraud_detection_score": 0.0,
            "quality_score": 0.0,
            "extracted_data": {},
            "issues": []
        }
        
        try:
            # Simulate AI verification logic
            # In production, this would integrate with ML models
            
            if document.document_type == DocumentType.NATIONAL_ID:
                verification_result.update(await self._verify_national_id(document))
            elif document.document_type == DocumentType.PASSPORT:
                verification_result.update(await self._verify_passport(document))
            elif document.document_type in [DocumentType.BUSINESS_LICENSE, DocumentType.TAX_CERTIFICATE]:
                verification_result.update(await self._verify_business_document(document))
            else:
                verification_result.update(await self._verify_generic_document(document))
            
        except Exception as e:
            verification_result["issues"].append(f"Verification error: {str(e)}")
        
        return verification_result
    
    async def _verify_national_id(self, document: DocumentModel) -> Dict[str, Any]:
        """Verify national ID document"""
        # Simulate ID verification
        confidence = 0.85  # Would be determined by ML model
        
        # Extract data from ID (simulated)
        extracted_data = {
            "id_number": "123456789",
            "full_name": "John Doe",
            "date_of_birth": "1990-01-01",
            "address": "123 Main St, City",
            "issue_date": "2020-01-01",
            "expiry_date": "2030-01-01"
        }
        
        issues = []
        if confidence < 0.7:
            issues.append("Low confidence in document authenticity")
        
        return {
            "confidence_score": confidence,
            "fraud_detection_score": 0.1,
            "quality_score": 0.9,
            "extracted_data": extracted_data,
            "issues": issues
        }
    
    async def _verify_passport(self, document: DocumentModel) -> Dict[str, Any]:
        """Verify passport document"""
        return {
            "confidence_score": 0.8,
            "fraud_detection_score": 0.05,
            "quality_score": 0.95,
            "extracted_data": {
                "passport_number": "AB1234567",
                "nationality": "Country",
                "expiry_date": "2030-12-31"
            },
            "issues": []
        }
    
    async def _verify_business_document(self, document: DocumentModel) -> Dict[str, Any]:
        """Verify business-related document"""
        return {
            "confidence_score": 0.75,
            "fraud_detection_score": 0.2,
            "quality_score": 0.8,
            "extracted_data": {
                "business_name": "Example Corp",
                "registration_number": "REG123456",
                "issue_date": "2023-01-01"
            },
            "issues": []
        }
    
    async def _verify_generic_document(self, document: DocumentModel) -> Dict[str, Any]:
        """Generic document verification"""
        return {
            "confidence_score": 0.7,
            "fraud_detection_score": 0.1,
            "quality_score": 0.8,
            "extracted_data": {},
            "issues": []
        }


class DocumentService:
    """Main document management service"""
    
    def __init__(self):
        self.storage_service = DocumentStorageService()
        self.verification_service = DocumentVerificationService()
    
    async def upload_document(
        self, 
        file: UploadFile, 
        citizen_id: str, 
        request: DocumentUploadRequest
    ) -> DocumentModel:
        """Upload and process a new document"""
        
        # Create document record
        document = DocumentModel(
            citizen_id=citizen_id,
            document_type=request.document_type,
            title=request.title,
            description=request.description,
            access_level=request.access_level,
            tags=request.tags,
            category=request.category,
            expires_at=request.expires_at
        )
        
        # Store file
        file_path, checksum, metadata = await self.storage_service.store_file(
            file, citizen_id, document.document_id
        )
        
        document.file_path = file_path
        document.checksum = checksum
        document.metadata = metadata
        
        # Auto-verification for supported document types
        if request.document_type in [DocumentType.NATIONAL_ID, DocumentType.PASSPORT]:
            verification_result = await self.verification_service.auto_verify_document(document)
            
            # Update metadata with verification results
            document.metadata.confidence_score = verification_result["confidence_score"]
            document.metadata.fraud_detection_score = verification_result["fraud_detection_score"]
            document.metadata.quality_score = verification_result["quality_score"]
            document.metadata.extracted_data.update(verification_result["extracted_data"])
            
            # Auto-verify if confidence is high enough
            if verification_result["confidence_score"] >= 0.8:
                document.mark_verified(
                    verified_by="system",
                    method=VerificationMethod.AUTOMATIC,
                    notes="Auto-verified based on AI analysis"
                )
            else:
                document.status = DocumentStatus.UNDER_REVIEW
        
        # Save document
        await document.insert()
        
        # Add to citizen's folder
        await self._add_to_citizen_folder(citizen_id, document.document_id, request.category)
        
        return document
    
    async def get_document(self, document_id: str, citizen_id: Optional[str] = None) -> Optional[DocumentModel]:
        """Get document by ID"""
        query = {"document_id": document_id}
        if citizen_id:
            query["citizen_id"] = citizen_id
        
        return await DocumentModel.find_one(query)
    
    async def search_documents(
        self, 
        citizen_id: str, 
        search: DocumentSearchRequest
    ) -> tuple[List[DocumentModel], int]:
        """Search documents for a citizen"""
        
        query = {"citizen_id": citizen_id}
        
        # Apply filters
        if search.document_types:
            query["document_type"] = {"$in": search.document_types}
        
        if search.statuses:
            query["status"] = {"$in": search.statuses}
        
        if search.tags:
            query["tags"] = {"$in": search.tags}
        
        if search.category:
            query["category"] = search.category
        
        if search.from_date or search.to_date:
            date_query = {}
            if search.from_date:
                date_query["$gte"] = search.from_date
            if search.to_date:
                date_query["$lte"] = search.to_date
            query["created_at"] = date_query
        
        if search.verified_only:
            query["status"] = DocumentStatus.VERIFIED
        
        if search.can_reuse:
            query["status"] = DocumentStatus.VERIFIED
            query["$or"] = [
                {"expires_at": None},
                {"expires_at": {"$gt": datetime.utcnow()}}
            ]
        
        # Count total
        total = await DocumentModel.find(query).count()
        
        # Get paginated results
        documents = await DocumentModel.find(query)\
            .sort([("created_at", -1)])\
            .skip(search.offset)\
            .limit(search.limit)\
            .to_list()
        
        return documents, total
    
    async def verify_document(
        self, 
        document_id: str, 
        verifier_id: str, 
        method: VerificationMethod,
        approve: bool = True,
        notes: Optional[str] = None
    ) -> DocumentModel:
        """Verify or reject a document"""
        
        document = await self.get_document(document_id)
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        
        if approve:
            document.mark_verified(verifier_id, method, notes)
        else:
            document.status = DocumentStatus.REJECTED
            document.verification_notes = notes
            document.updated_at = datetime.utcnow()
        
        await document.save()
        return document
    
    async def suggest_reusable_documents(
        self, 
        citizen_id: str, 
        workflow_id: str,
        required_types: List[DocumentType]
    ) -> List[DocumentReuseSuggestion]:
        """Suggest documents that can be reused for a workflow"""
        
        suggestions = []
        
        for doc_type in required_types:
            # Find verified documents of this type
            documents = await DocumentModel.find({
                "citizen_id": citizen_id,
                "document_type": doc_type,
                "status": DocumentStatus.VERIFIED,
                "$or": [
                    {"expires_at": None},
                    {"expires_at": {"$gt": datetime.utcnow()}}
                ]
            }).sort([("verified_at", -1)]).to_list()
            
            for doc in documents:
                # Calculate relevance score based on verification confidence, usage, and age
                relevance_score = 0.0
                reason_parts = []
                
                if doc.metadata.confidence_score:
                    relevance_score += doc.metadata.confidence_score * 0.4
                    reason_parts.append(f"High confidence ({doc.metadata.confidence_score:.1%})")
                
                if doc.verified_at:
                    days_since_verification = (datetime.utcnow() - doc.verified_at).days
                    if days_since_verification < 30:
                        relevance_score += 0.3
                        reason_parts.append("Recently verified")
                    elif days_since_verification < 90:
                        relevance_score += 0.2
                
                if doc.usage_count > 0:
                    relevance_score += min(doc.usage_count * 0.1, 0.3)
                    reason_parts.append(f"Used {doc.usage_count} times")
                
                if not reason_parts:
                    reason_parts.append("Available for reuse")
                
                suggestions.append(DocumentReuseSuggestion(
                    document_id=doc.document_id,
                    document_type=doc.document_type,
                    title=doc.title,
                    verified_at=doc.verified_at,
                    usage_count=doc.usage_count,
                    relevance_score=relevance_score,
                    reason=", ".join(reason_parts)
                ))
        
        # Sort by relevance score
        suggestions.sort(key=lambda x: x.relevance_score, reverse=True)
        
        return suggestions
    
    async def mark_document_used(self, document_id: str, workflow_id: str):
        """Mark document as used in a workflow"""
        document = await self.get_document(document_id)
        if document:
            document.mark_used_in_workflow(workflow_id)
            await document.save()
    
    async def _add_to_citizen_folder(self, citizen_id: str, document_id: str, category: Optional[str]):
        """Add document to citizen's folder"""
        folder = await DocumentFolderModel.find_one({"citizen_id": citizen_id})
        
        if not folder:
            folder = DocumentFolderModel(citizen_id=citizen_id)
            await folder.insert()
        
        folder.add_document(document_id, category)
        await folder.save()
    
    async def get_citizen_folder(self, citizen_id: str) -> DocumentFolderModel:
        """Get or create citizen's document folder"""
        folder = await DocumentFolderModel.find_one({"citizen_id": citizen_id})
        
        if not folder:
            folder = DocumentFolderModel(citizen_id=citizen_id)
            await folder.insert()
        
        return folder
    
    async def get_document_stats(self, citizen_id: str) -> Dict[str, Any]:
        """Get document statistics for a citizen"""
        documents = await DocumentModel.find({"citizen_id": citizen_id}).to_list()
        
        stats = {
            "total_documents": len(documents),
            "by_type": {},
            "by_status": {},
            "by_category": {},
            "verified_count": 0,
            "pending_verification": 0,
            "expired_count": 0,
            "recently_uploaded": 0,
            "storage_used_bytes": 0
        }
        
        week_ago = datetime.utcnow() - timedelta(days=7)
        
        for doc in documents:
            # Count by type
            doc_type = doc.document_type.value
            stats["by_type"][doc_type] = stats["by_type"].get(doc_type, 0) + 1
            
            # Count by status
            status = doc.status.value
            stats["by_status"][status] = stats["by_status"].get(status, 0) + 1
            
            # Count by category
            if doc.category:
                stats["by_category"][doc.category] = stats["by_category"].get(doc.category, 0) + 1
            
            # Other counts
            if doc.status == DocumentStatus.VERIFIED:
                stats["verified_count"] += 1
            elif doc.status in [DocumentStatus.PENDING, DocumentStatus.UNDER_REVIEW]:
                stats["pending_verification"] += 1
            
            if doc.is_expired():
                stats["expired_count"] += 1
            
            if doc.created_at >= week_ago:
                stats["recently_uploaded"] += 1
            
            if doc.metadata.file_size:
                stats["storage_used_bytes"] += doc.metadata.file_size
        
        return stats


# Global service instance
document_service = DocumentService()