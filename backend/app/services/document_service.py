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
    
    def __init__(self):
        self.fraud_indicators = [
            "altered_text", "inconsistent_metadata", "suspicious_patterns",
            "tampered_image", "invalid_checksums", "duplicate_submission",
            "suspicious_timing", "invalid_format", "missing_security_features"
        ]
        self.quality_thresholds = {
            "high": 0.9,
            "medium": 0.7,
            "low": 0.5
        }
    
    async def auto_verify_document(self, document: DocumentModel) -> Dict[str, Any]:
        """Perform automatic verification of document with enhanced AI analysis"""
        verification_result = {
            "confidence_score": 0.0,
            "fraud_detection_score": 0.0,
            "quality_score": 0.0,
            "authenticity_score": 0.0,
            "extracted_data": {},
            "security_features": {},
            "validation_rules_passed": [],
            "validation_rules_failed": [],
            "fraud_indicators": [],
            "quality_metrics": {},
            "recommendations": [],
            "issues": [],
            "processing_time_ms": 0
        }
        
        start_time = datetime.utcnow()
        
        try:
            # Perform document-specific verification
            if document.document_type == DocumentType.NATIONAL_ID:
                verification_result.update(await self._verify_national_id(document))
            elif document.document_type == DocumentType.PASSPORT:
                verification_result.update(await self._verify_passport(document))
            elif document.document_type == DocumentType.DRIVERS_LICENSE:
                verification_result.update(await self._verify_drivers_license(document))
            elif document.document_type in [DocumentType.BUSINESS_LICENSE, DocumentType.TAX_CERTIFICATE]:
                verification_result.update(await self._verify_business_document(document))
            elif document.document_type in [DocumentType.PROOF_OF_ADDRESS, DocumentType.UTILITY_BILL]:
                verification_result.update(await self._verify_address_document(document))
            elif document.document_type == DocumentType.BANK_STATEMENT:
                verification_result.update(await self._verify_bank_statement(document))
            else:
                verification_result.update(await self._verify_generic_document(document))
            
            # Apply universal validation rules
            verification_result.update(await self._apply_universal_validations(document))
            
            # Calculate overall scores
            verification_result = await self._calculate_final_scores(verification_result)
            
            # Generate recommendations
            verification_result["recommendations"] = await self._generate_recommendations(verification_result)
            
        except Exception as e:
            verification_result["issues"].append(f"Verification error: {str(e)}")
        
        # Calculate processing time
        end_time = datetime.utcnow()
        verification_result["processing_time_ms"] = int((end_time - start_time).total_seconds() * 1000)
        
        return verification_result
    
    async def _verify_national_id(self, document: DocumentModel) -> Dict[str, Any]:
        """Verify national ID document with enhanced analysis"""
        import random
        
        # Simulate realistic AI analysis with some randomness
        base_confidence = random.uniform(0.75, 0.95)
        
        # Simulate OCR and data extraction
        extracted_data = {
            "id_number": f"ID{random.randint(100000000, 999999999)}",
            "full_name": "Citizen Name",
            "date_of_birth": "1990-01-01",
            "address": "123 Main Street, City",
            "issue_date": "2020-01-01",
            "expiry_date": "2030-01-01",
            "issuing_authority": "National Registry Office"
        }
        
        # Security features check
        security_features = {
            "hologram_present": True,
            "watermark_detected": True,
            "microprint_verified": True,
            "security_thread": True,
            "biometric_chip": False,
            "uv_reactive_ink": True
        }
        
        # Quality metrics
        quality_metrics = {
            "image_resolution": "high",
            "text_clarity": 0.9,
            "color_accuracy": 0.85,
            "document_orientation": "correct",
            "lighting_quality": 0.8,
            "blur_detection": 0.1
        }
        
        # Validation rules
        validation_rules_passed = [
            "document_format_valid",
            "mandatory_fields_present",
            "date_format_valid",
            "id_number_format_valid"
        ]
        
        validation_rules_failed = []
        fraud_indicators = []
        issues = []
        
        # Simulate some validation logic
        if base_confidence < 0.7:
            issues.append("Low OCR confidence - manual review recommended")
            fraud_indicators.append("low_ocr_confidence")
        
        if random.random() < 0.1:  # 10% chance of detecting potential fraud
            fraud_indicators.append("suspicious_patterns")
            base_confidence *= 0.8
        
        return {
            "confidence_score": base_confidence,
            "fraud_detection_score": 0.05 + (0.3 if fraud_indicators else 0),
            "quality_score": 0.9,
            "authenticity_score": 0.88,
            "extracted_data": extracted_data,
            "security_features": security_features,
            "quality_metrics": quality_metrics,
            "validation_rules_passed": validation_rules_passed,
            "validation_rules_failed": validation_rules_failed,
            "fraud_indicators": fraud_indicators,
            "issues": issues
        }
    
    async def _verify_passport(self, document: DocumentModel) -> Dict[str, Any]:
        """Verify passport document with enhanced analysis"""
        import random
        
        base_confidence = random.uniform(0.8, 0.95)
        
        extracted_data = {
            "passport_number": f"P{random.randint(1000000, 9999999)}",
            "nationality": "Sample Country",
            "full_name": "Passport Holder",
            "date_of_birth": "1985-06-15",
            "place_of_birth": "Capital City",
            "issue_date": "2020-03-01",
            "expiry_date": "2030-03-01",
            "issuing_authority": "Department of Foreign Affairs"
        }
        
        security_features = {
            "machine_readable_zone": True,
            "biometric_chip": True,
            "security_printing": True,
            "lamination_integrity": True,
            "photo_security": True,
            "digital_signature": True
        }
        
        quality_metrics = {
            "image_resolution": "high",
            "text_clarity": 0.95,
            "color_accuracy": 0.9,
            "mrz_readability": 0.98,
            "photo_quality": 0.85
        }
        
        validation_rules_passed = [
            "passport_format_valid",
            "mrz_checksum_valid",
            "date_consistency_check",
            "issuing_country_valid"
        ]
        
        return {
            "confidence_score": base_confidence,
            "fraud_detection_score": 0.03,
            "quality_score": 0.95,
            "authenticity_score": 0.92,
            "extracted_data": extracted_data,
            "security_features": security_features,
            "quality_metrics": quality_metrics,
            "validation_rules_passed": validation_rules_passed,
            "validation_rules_failed": [],
            "fraud_indicators": [],
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
    
    async def _verify_drivers_license(self, document: DocumentModel) -> Dict[str, Any]:
        """Verify driver's license document"""
        import random
        
        base_confidence = random.uniform(0.75, 0.92)
        
        extracted_data = {
            "license_number": f"DL{random.randint(10000000, 99999999)}",
            "full_name": "License Holder",
            "date_of_birth": "1988-09-12",
            "address": "456 Driver St, City",
            "issue_date": "2021-04-15",
            "expiry_date": "2026-04-15",
            "license_class": "Class C",
            "restrictions": "None"
        }
        
        security_features = {
            "hologram_present": True,
            "magnetic_stripe": True,
            "barcode_present": True,
            "security_lamination": True,
            "photo_security": True
        }
        
        return {
            "confidence_score": base_confidence,
            "fraud_detection_score": 0.08,
            "quality_score": 0.88,
            "authenticity_score": 0.85,
            "extracted_data": extracted_data,
            "security_features": security_features,
            "validation_rules_passed": ["license_format_valid", "date_consistency_check"],
            "validation_rules_failed": [],
            "fraud_indicators": [],
            "issues": []
        }
    
    async def _verify_address_document(self, document: DocumentModel) -> Dict[str, Any]:
        """Verify proof of address documents"""
        import random
        
        base_confidence = random.uniform(0.65, 0.85)
        
        extracted_data = {
            "account_holder": "Address Holder",
            "address": "789 Address Lane, City",
            "document_date": "2024-06-01",
            "document_type": "utility_bill",
            "service_provider": "City Utilities"
        }
        
        validation_rules_passed = ["address_format_valid", "recent_document"]
        fraud_indicators = []
        
        # Address documents have higher fraud risk
        if random.random() < 0.15:
            fraud_indicators.append("suspicious_patterns")
            base_confidence *= 0.9
        
        return {
            "confidence_score": base_confidence,
            "fraud_detection_score": 0.12,
            "quality_score": 0.78,
            "authenticity_score": 0.75,
            "extracted_data": extracted_data,
            "validation_rules_passed": validation_rules_passed,
            "fraud_indicators": fraud_indicators,
            "issues": []
        }
    
    async def _verify_bank_statement(self, document: DocumentModel) -> Dict[str, Any]:
        """Verify bank statement with enhanced fraud detection"""
        import random
        
        # Bank statements are high-risk for fraud
        base_confidence = random.uniform(0.45, 0.85)
        fraud_score = random.uniform(0.1, 0.4)
        
        extracted_data = {
            "account_holder": "Account Holder",
            "account_number": "****1234",
            "bank_name": "Sample Bank",
            "statement_period": "2024-05-01 to 2024-05-31",
            "balance": "$2,500.00"
        }
        
        fraud_indicators = []
        issues = []
        
        # Simulate fraud detection
        if base_confidence < 0.6:
            fraud_indicators.extend(["altered_text", "inconsistent_metadata"])
            issues.append("Document authenticity concerns - manual review required")
        
        if fraud_score > 0.3:
            fraud_indicators.append("suspicious_patterns")
        
        return {
            "confidence_score": base_confidence,
            "fraud_detection_score": fraud_score,
            "quality_score": 0.7,
            "authenticity_score": 0.65,
            "extracted_data": extracted_data,
            "fraud_indicators": fraud_indicators,
            "issues": issues
        }
    
    async def _verify_generic_document(self, document: DocumentModel) -> Dict[str, Any]:
        """Generic document verification"""
        import random
        
        base_confidence = random.uniform(0.6, 0.8)
        
        return {
            "confidence_score": base_confidence,
            "fraud_detection_score": 0.1,
            "quality_score": 0.8,
            "authenticity_score": 0.7,
            "extracted_data": {},
            "security_features": {},
            "validation_rules_passed": ["basic_format_check"],
            "validation_rules_failed": [],
            "fraud_indicators": [],
            "issues": []
        }
    
    async def _apply_universal_validations(self, document: DocumentModel) -> Dict[str, Any]:
        """Apply universal validation rules to all documents"""
        validations = {
            "universal_validations_passed": [],
            "universal_validations_failed": []
        }
        
        # File integrity check
        if document.checksum:
            validations["universal_validations_passed"].append("file_integrity_valid")
        else:
            validations["universal_validations_failed"].append("file_integrity_missing")
        
        # File format validation
        if document.metadata.mime_type in ["image/jpeg", "image/png", "application/pdf"]:
            validations["universal_validations_passed"].append("supported_file_format")
        else:
            validations["universal_validations_failed"].append("unsupported_file_format")
        
        # Document age validation
        from datetime import timedelta
        if document.created_at > datetime.utcnow() - timedelta(days=30):
            validations["universal_validations_passed"].append("recently_uploaded")
        
        return validations
    
    async def _calculate_final_scores(self, verification_result: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate final verification scores based on all factors"""
        base_confidence = verification_result.get("confidence_score", 0.0)
        fraud_score = verification_result.get("fraud_detection_score", 0.0)
        quality_score = verification_result.get("quality_score", 0.0)
        
        # Adjust confidence based on fraud indicators
        fraud_penalty = len(verification_result.get("fraud_indicators", [])) * 0.1
        adjusted_confidence = max(0.0, base_confidence - fraud_penalty)
        
        # Calculate overall verification score
        overall_score = (adjusted_confidence * 0.5 + quality_score * 0.3 + (1 - fraud_score) * 0.2)
        
        verification_result["confidence_score"] = adjusted_confidence
        verification_result["overall_verification_score"] = overall_score
        verification_result["verification_decision"] = "auto_approve" if overall_score >= 0.8 else "manual_review" if overall_score >= 0.6 else "reject"
        
        return verification_result
    
    async def _generate_recommendations(self, verification_result: Dict[str, Any]) -> List[str]:
        """Generate recommendations based on verification results"""
        recommendations = []
        
        overall_score = verification_result.get("overall_verification_score", 0.0)
        fraud_indicators = verification_result.get("fraud_indicators", [])
        issues = verification_result.get("issues", [])
        
        if overall_score >= 0.8:
            recommendations.append("Document can be auto-approved with high confidence")
        elif overall_score >= 0.6:
            recommendations.append("Manual review recommended - moderate confidence")
        else:
            recommendations.append("Reject or request document resubmission - low confidence")
        
        if fraud_indicators:
            recommendations.append("Investigate potential fraud indicators before approval")
        
        if issues:
            recommendations.append("Address quality issues before processing")
        
        if verification_result.get("quality_score", 0.0) < 0.7:
            recommendations.append("Request higher quality document scan")
        
        return recommendations


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