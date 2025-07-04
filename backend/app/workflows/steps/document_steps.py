"""
Document-related workflow steps for CivicStream.
These steps handle document upload, verification, and management within workflows.
"""

from typing import Dict, Any, Optional, List
from datetime import datetime

from ..base import BaseStep, StepResult, StepStatus, ValidationResult
from ...models.document import DocumentModel, DocumentType, DocumentStatus, VerificationMethod
from ...schemas.document import WorkflowDocumentInput, DocumentUploadRequest
from ...services.document_service import document_service


class DocumentUploadStep(BaseStep):
    """Step for uploading documents during workflow execution"""
    
    def __init__(self, 
                 step_id: str,
                 name: str,
                 required_document_type: DocumentType,
                 **kwargs):
        super().__init__(step_id, name, **kwargs)
        self.required_document_type = required_document_type
        self.required_inputs = ["citizen_id", "document_input"]
    
    async def execute(self, inputs: Dict[str, Any], context: Dict[str, Any]) -> StepResult:
        result = StepResult(
            step_id=self.step_id,
            status=StepStatus.RUNNING,
            started_at=datetime.utcnow()
        )
        
        try:
            citizen_id = inputs["citizen_id"]
            document_input = WorkflowDocumentInput(**inputs["document_input"])
            
            if document_input.document_id:
                # Use existing document
                document = await document_service.get_document(document_input.document_id, citizen_id)
                if not document:
                    result.status = StepStatus.FAILED
                    result.error = "Document not found"
                    return result
                
                # Verify document type matches requirement
                if document.document_type != self.required_document_type:
                    result.status = StepStatus.FAILED
                    result.error = f"Document type mismatch. Expected {self.required_document_type}, got {document.document_type}"
                    return result
                
                # Mark as used in this workflow
                workflow_id = context.get("workflow_id", "unknown")
                await document_service.mark_document_used(document.document_id, workflow_id)
                
            elif document_input.upload_new and document_input.document_data:
                # Upload new document (this would need proper file handling in real implementation)
                result.status = StepStatus.FAILED
                result.error = "New document upload not implemented in workflow step"
                return result
            
            else:
                result.status = StepStatus.FAILED
                result.error = "No document provided"
                return result
            
            result.outputs = {
                "document_id": document.document_id,
                "document_type": document.document_type.value,
                "document_status": document.status.value,
                "document_verified": document.status == DocumentStatus.VERIFIED,
                "document_title": document.title
            }
            result.status = StepStatus.COMPLETED
            
        except Exception as e:
            result.status = StepStatus.FAILED
            result.error = str(e)
        finally:
            result.completed_at = datetime.utcnow()
            result.calculate_duration()
        
        return result


class DocumentVerificationStep(BaseStep):
    """Step for manual document verification by clerks/administrators"""
    
    def __init__(self,
                 step_id: str,
                 name: str,
                 verifier_roles: List[str] = None,
                 auto_verify_threshold: float = 0.9,
                 **kwargs):
        super().__init__(step_id, name, **kwargs)
        self.verifier_roles = verifier_roles or ["clerk", "administrator"]
        self.auto_verify_threshold = auto_verify_threshold
        self.required_inputs = ["document_id"]
    
    async def execute(self, inputs: Dict[str, Any], context: Dict[str, Any]) -> StepResult:
        result = StepResult(
            step_id=self.step_id,
            status=StepStatus.RUNNING,
            started_at=datetime.utcnow()
        )
        
        try:
            document_id = inputs["document_id"]
            document = await document_service.get_document(document_id)
            
            if not document:
                result.status = StepStatus.FAILED
                result.error = "Document not found"
                return result
            
            # Check if document is already verified
            if document.status == DocumentStatus.VERIFIED:
                result.outputs = {
                    "verification_status": "already_verified",
                    "verified_by": document.verified_by,
                    "verified_at": document.verified_at.isoformat() if document.verified_at else None,
                    "verification_method": document.verification_method.value if document.verification_method else None
                }
                result.status = StepStatus.COMPLETED
                return result
            
            # Auto-verify if confidence score is high enough
            if (document.metadata.confidence_score and 
                document.metadata.confidence_score >= self.auto_verify_threshold):
                
                await document_service.verify_document(
                    document_id,
                    verifier_id="system",
                    method=VerificationMethod.AUTOMATIC,
                    approve=True,
                    notes=f"Auto-verified with confidence score {document.metadata.confidence_score:.2f}"
                )
                
                result.outputs = {
                    "verification_status": "auto_verified",
                    "verified_by": "system",
                    "verified_at": datetime.utcnow().isoformat(),
                    "verification_method": VerificationMethod.AUTOMATIC.value,
                    "confidence_score": document.metadata.confidence_score
                }
                result.status = StepStatus.COMPLETED
                
            else:
                # Requires manual verification
                document.status = DocumentStatus.UNDER_REVIEW
                await document.save()
                
                result.outputs = {
                    "verification_status": "manual_review_required",
                    "required_verifier_roles": self.verifier_roles,
                    "confidence_score": document.metadata.confidence_score,
                    "review_url": f"/admin/documents/{document_id}/verify"
                }
                result.status = StepStatus.PENDING  # Waiting for manual verification
                
        except Exception as e:
            result.status = StepStatus.FAILED
            result.error = str(e)
        finally:
            result.completed_at = datetime.utcnow()
            result.calculate_duration()
        
        return result


class DocumentExistenceCheckStep(BaseStep):
    """Step to check if citizen has required documents already"""
    
    def __init__(self,
                 step_id: str,
                 name: str,
                 required_document_types: List[DocumentType],
                 require_verified: bool = True,
                 **kwargs):
        super().__init__(step_id, name, **kwargs)
        self.required_document_types = required_document_types
        self.require_verified = require_verified
        self.required_inputs = ["citizen_id"]
    
    async def execute(self, inputs: Dict[str, Any], context: Dict[str, Any]) -> StepResult:
        result = StepResult(
            step_id=self.step_id,
            status=StepStatus.RUNNING,
            started_at=datetime.utcnow()
        )
        
        try:
            citizen_id = inputs["citizen_id"]
            workflow_id = context.get("workflow_id", "unknown")
            
            # Get suggestions for reusable documents
            suggestions = await document_service.suggest_reusable_documents(
                citizen_id, workflow_id, self.required_document_types
            )
            
            found_documents = {}
            missing_documents = []
            
            for doc_type in self.required_document_types:
                # Find best suggestion for this document type
                best_suggestion = None
                for suggestion in suggestions:
                    if suggestion.document_type == doc_type:
                        if not best_suggestion or suggestion.relevance_score > best_suggestion.relevance_score:
                            best_suggestion = suggestion
                
                if best_suggestion:
                    found_documents[doc_type.value] = {
                        "document_id": best_suggestion.document_id,
                        "title": best_suggestion.title,
                        "relevance_score": best_suggestion.relevance_score,
                        "reason": best_suggestion.reason,
                        "can_reuse": True
                    }
                else:
                    missing_documents.append(doc_type.value)
            
            result.outputs = {
                "found_documents": found_documents,
                "missing_documents": missing_documents,
                "total_required": len(self.required_document_types),
                "total_found": len(found_documents),
                "all_documents_available": len(missing_documents) == 0,
                "reuse_suggestions": [s.dict() for s in suggestions]
            }
            result.status = StepStatus.COMPLETED
            
        except Exception as e:
            result.status = StepStatus.FAILED
            result.error = str(e)
        finally:
            result.completed_at = datetime.utcnow()
            result.calculate_duration()
        
        return result


class DocumentGenerationStep(BaseStep):
    """Step for generating documents from templates"""
    
    def __init__(self,
                 step_id: str,
                 name: str,
                 template_id: str,
                 output_document_type: DocumentType,
                 **kwargs):
        super().__init__(step_id, name, **kwargs)
        self.template_id = template_id
        self.output_document_type = output_document_type
        self.required_inputs = ["citizen_id", "template_data"]
    
    async def execute(self, inputs: Dict[str, Any], context: Dict[str, Any]) -> StepResult:
        result = StepResult(
            step_id=self.step_id,
            status=StepStatus.RUNNING,
            started_at=datetime.utcnow()
        )
        
        try:
            citizen_id = inputs["citizen_id"]
            template_data = inputs["template_data"]
            workflow_id = context.get("workflow_id", "unknown")
            instance_id = context.get("instance_id", "unknown")
            
            # Generate document (simplified - in real implementation, this would use a template engine)
            document_title = f"Generated {self.output_document_type.value.replace('_', ' ').title()}"
            
            # Create document record
            document = DocumentModel(
                citizen_id=citizen_id,
                document_type=self.output_document_type,
                title=document_title,
                description=f"System-generated document from workflow {workflow_id}",
                status=DocumentStatus.VERIFIED,  # System-generated documents are auto-verified
                access_level=DocumentAccess.WORKFLOW,
                generated_by_system=True,
                source_workflow_id=workflow_id,
                source_step_id=self.step_id,
                source_instance_id=instance_id,
                verification_method=VerificationMethod.SYSTEM,
                verified_by="system",
                verified_at=datetime.utcnow(),
                file_path=f"generated/{workflow_id}/{instance_id}/{self.step_id}.pdf",  # Placeholder
                checksum="generated_document_checksum"  # Placeholder
            )
            
            # In real implementation, generate actual file using template
            # document.file_path = await self._generate_from_template(template_id, template_data)
            
            await document.insert()
            
            # Add system signature
            document.add_signature(
                signer_id="system",
                signer_name="CivicStream System",
                signer_role="system",
                signature_method="digital"
            )
            await document.save()
            
            result.outputs = {
                "generated_document_id": document.document_id,
                "document_title": document.title,
                "document_type": document.document_type.value,
                "file_path": document.file_path,
                "signed_by_system": True
            }
            result.status = StepStatus.COMPLETED
            
        except Exception as e:
            result.status = StepStatus.FAILED
            result.error = str(e)
        finally:
            result.completed_at = datetime.utcnow()
            result.calculate_duration()
        
        return result


class DocumentSigningStep(BaseStep):
    """Step for document signing by authorized personnel"""
    
    def __init__(self,
                 step_id: str,
                 name: str,
                 required_signers: List[str],
                 signature_type: str = "digital",
                 **kwargs):
        super().__init__(step_id, name, **kwargs)
        self.required_signers = required_signers
        self.signature_type = signature_type
        self.required_inputs = ["document_id"]
    
    async def execute(self, inputs: Dict[str, Any], context: Dict[str, Any]) -> StepResult:
        result = StepResult(
            step_id=self.step_id,
            status=StepStatus.RUNNING,
            started_at=datetime.utcnow()
        )
        
        try:
            document_id = inputs["document_id"]
            signer_id = inputs.get("signer_id")
            signer_name = inputs.get("signer_name", "Unknown Signer")
            
            document = await document_service.get_document(document_id)
            if not document:
                result.status = StepStatus.FAILED
                result.error = "Document not found"
                return result
            
            if signer_id:
                # Add signature
                document.add_signature(
                    signer_id=signer_id,
                    signer_name=signer_name,
                    signer_role=inputs.get("signer_role", "official"),
                    signature_method=self.signature_type,
                    signature_data=inputs.get("signature_data")
                )
                await document.save()
                
                result.outputs = {
                    "signed": True,
                    "signer_id": signer_id,
                    "signer_name": signer_name,
                    "signature_count": len(document.signatures),
                    "all_required_signatures": len(document.signatures) >= len(self.required_signers)
                }
                result.status = StepStatus.COMPLETED
            else:
                # Waiting for signature
                result.outputs = {
                    "signed": False,
                    "required_signers": self.required_signers,
                    "signature_type": self.signature_type,
                    "sign_url": f"/admin/documents/{document_id}/sign"
                }
                result.status = StepStatus.PENDING
                
        except Exception as e:
            result.status = StepStatus.FAILED
            result.error = str(e)
        finally:
            result.completed_at = datetime.utcnow()
            result.calculate_duration()
        
        return result