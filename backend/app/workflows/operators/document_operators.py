"""
DAG Operators for working with Document entities.
These operators allow workflows to create and require documents.
"""
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from enum import Enum
import hashlib
import uuid

from .base import BaseOperator, TaskResult
from ...services.entity_service import EntityService
from ...models.legal_entity import LegalEntity, EntityRelationship


class DocumentCreationOperator(BaseOperator):
    """
    Operator that creates a Document entity when workflow completes successfully.
    Links the document to target entities and stores metadata.
    """
    
    def __init__(
        self,
        task_id: str,
        document_type: str,  # e.g., "construction_permit", "property_title"
        document_name: str = None,  # Name template, can use {context_var}
        link_to_entity: str = None,  # Context key containing entity ID to link to
        document_subtype: Optional[str] = None,  # e.g., "residential", "commercial"
        metadata_mapping: Optional[Dict[str, str]] = None,  # Map context to document metadata
        static_metadata: Optional[Dict[str, Any]] = None,  # Static metadata
        expiry_days: Optional[int] = None,  # Document validity period in days
        issuing_authority: str = "Municipal System",  # Default issuing authority
        **kwargs
    ):
        """
        Initialize document creation operator.
        
        Args:
            task_id: Unique task identifier
            document_type: Type of document to create
            document_name: Template for document name (supports {var} substitution)
            link_to_entity: Context key containing entity ID to link document to
            document_subtype: Optional subtype for categorization
            metadata_mapping: Map context fields to document metadata
            static_metadata: Static metadata to include
            expiry_days: Number of days until document expires
            issuing_authority: Name of issuing authority
        """
        super().__init__(task_id, **kwargs)
        self.document_type = document_type
        self.document_name = document_name or f"{document_type.replace('_', ' ').title()}"
        self.link_to_entity = link_to_entity
        self.document_subtype = document_subtype
        self.metadata_mapping = metadata_mapping or {}
        self.static_metadata = static_metadata or {}
        self.expiry_days = expiry_days
        self.issuing_authority = issuing_authority
    
    def execute(self, context: Dict[str, Any]) -> TaskResult:
        """Create the document entity"""
        try:
            print(f"📄 DocumentCreationOperator: Creating {self.document_type} document")
            
            # Get user ID from context
            user_id = context.get("user_id") or context.get("customer_id")
            if not user_id:
                return TaskResult(
                    status="failed",
                    error="No user_id or customer_id in context"
                )
            
            # Generate document number
            document_number = f"{self.document_type.upper()}-{datetime.now().year}-{str(uuid.uuid4())[:8].upper()}"
            
            # Process document name template
            doc_name = self.document_name
            for key in context:
                placeholder = f"{{{key}}}"
                if placeholder in doc_name:
                    doc_name = doc_name.replace(placeholder, str(context[key]))
            
            # Build document metadata
            issued_date = datetime.utcnow()
            document_data = {
                "document_type": self.document_type,
                "document_subtype": self.document_subtype,
                "document_number": document_number,
                "issued_date": issued_date.isoformat(),
                "issuing_authority": self.issuing_authority,
                "verification_status": "verified",  # System-generated docs are auto-verified
                "created_by_workflow": context.get("instance_id"),
                **self.static_metadata
            }
            
            # Add expiry date if specified
            if self.expiry_days:
                expiry_date = issued_date + timedelta(days=self.expiry_days)
                document_data["expiry_date"] = expiry_date.isoformat()
            
            # Map context fields to document data
            for context_key, data_field in self.metadata_mapping.items():
                value = context
                for key_part in context_key.split("."):
                    if isinstance(value, dict):
                        value = value.get(key_part)
                        if value is None:
                            break
                
                if value is not None:
                    document_data[data_field] = value
            
            # Store parameters for async execution
            self._document_params = {
                "entity_type": "document",
                "owner_user_id": user_id,
                "name": doc_name,
                "data": document_data,
                "created_by_workflow": context.get("instance_id")
            }
            
            # Get entity to link to (if specified)
            self._link_to_entity_id = None
            if self.link_to_entity:
                self._link_to_entity_id = context.get(self.link_to_entity)
                print(f"   Will link to entity: {self._link_to_entity_id}")
            
            return TaskResult(
                status="pending_async",
                data={}
            )
            
        except Exception as e:
            return TaskResult(
                status="failed",
                error=f"Failed to create document: {str(e)}"
            )
    
    async def execute_async(self, context: Dict[str, Any]) -> str:
        """Async execution to create document and link it"""
        # First run the regular execute to prepare parameters
        result = self.execute(context)
        
        if hasattr(self, '_document_params') and self._document_params:
            # Log operator action
            await self.log_info(
                f"Creating {self.document_type} document: {self._document_params.get('name')}",
                details={
                    "document_type": self.document_type,
                    "document_number": self._document_params['data'].get('document_number')
                }
            )
            
            try:
                # Create the document entity
                document = await EntityService.create_entity(**self._document_params)
                print(f"   ✅ Document created: {document.entity_id}")
                
                # Link to parent entity if specified
                if self._link_to_entity_id:
                    # Add relationship from parent entity to document
                    parent_entity = await EntityService.get_entity(self._link_to_entity_id)
                    if parent_entity:
                        parent_entity.add_relationship(
                            to_entity_id=document.entity_id,
                            relationship_type="has_document",
                            metadata={
                                "document_type": self.document_type,
                                "issued_date": self._document_params['data'].get('issued_date')
                            }
                        )
                        await parent_entity.save()
                        
                        # Add reverse relationship from document to parent
                        document.add_relationship(
                            to_entity_id=self._link_to_entity_id,
                            relationship_type="issued_for",
                            metadata={
                                "entity_type": parent_entity.entity_type,
                                "entity_name": parent_entity.name
                            }
                        )
                        await document.save()
                        
                        print(f"   ✅ Document linked to entity: {self._link_to_entity_id}")
                
                # Log successful creation
                await self.log_info(
                    f"Successfully created {self.document_type}: {document.entity_id}",
                    details={
                        "document_id": document.entity_id,
                        "document_name": document.name,
                        "document_number": document.data.get("document_number"),
                        "linked_to": self._link_to_entity_id
                    }
                )
                
                # Update output data
                self.state.output_data = {
                    f"{self.task_id}_document_id": document.entity_id,
                    f"{self.task_id}_document_number": document.data.get("document_number"),
                    f"created_document_{self.document_type}": document.entity_id
                }
                return "continue"
                
            except Exception as e:
                await self.log_error(
                    f"Failed to create document: {str(e)}",
                    error=str(e)
                )
                return "failed"
        
        return "continue"


class DocumentRequirementOperator(BaseOperator):
    """
    Operator that checks if an entity has required documents before proceeding.
    Can check for document type, validity, expiry status.
    """
    
    def __init__(
        self,
        task_id: str,
        entity_id_source: str,  # Context key containing entity ID to check
        required_documents: List[Dict[str, Any]],  # List of document requirements
        on_missing: str = "fail",  # "fail" or "request" (pause for upload)
        store_documents_as: Optional[str] = None,  # Store found document IDs in context
        **kwargs
    ):
        """
        Initialize document requirement operator.
        
        Args:
            task_id: Unique task identifier
            entity_id_source: Context key containing the entity ID to check
            required_documents: List of document requirements, each dict can have:
                - type: Document type required
                - max_age_days: Maximum age of document in days
                - must_be_verified: Document must have verified status
                - check_expiry: Check if document is not expired
            on_missing: Action when documents are missing ("fail" or "request")
            store_documents_as: Key to store found document IDs in context
        """
        super().__init__(task_id, **kwargs)
        self.entity_id_source = entity_id_source
        self.required_documents = required_documents
        self.on_missing = on_missing
        self.store_documents_as = store_documents_as or f"{task_id}_documents"
    
    def execute(self, context: Dict[str, Any]) -> TaskResult:
        """Check for required documents - synchronous part"""
        try:
            # Get entity ID from context
            entity_id = context.get(self.entity_id_source)
            if not entity_id:
                return TaskResult(
                    status="failed",
                    error=f"No entity ID found in context at '{self.entity_id_source}'"
                )
            
            # Store for async execution
            self._entity_id = entity_id
            
            return TaskResult(
                status="pending_async",
                data={}
            )
            
        except Exception as e:
            return TaskResult(
                status="failed",
                error=f"Document requirement check failed: {str(e)}"
            )
    
    async def execute_async(self, context: Dict[str, Any]) -> str:
        """Async execution to check document requirements"""
        print(f"📋 DocumentRequirementOperator: Checking documents for entity {self._entity_id}")
        
        try:
            # Get the entity
            entity = await EntityService.get_entity(self._entity_id)
            if not entity:
                await self.log_error(f"Entity not found: {self._entity_id}")
                return "failed"
            
            # Log check start
            await self.log_info(
                f"Checking document requirements for {entity.name}",
                details={
                    "entity_id": self._entity_id,
                    "entity_type": entity.entity_type,
                    "required_documents": self.required_documents
                }
            )
            
            # Get all document relationships for this entity
            document_relationships = entity.get_relationships(
                relationship_type="has_document",
                active_only=True
            )
            
            # Fetch actual document entities
            documents = []
            for rel in document_relationships:
                doc = await EntityService.get_entity(rel.to_entity_id)
                if doc and doc.entity_type == "document":
                    documents.append(doc)
            
            # Check each requirement
            missing_documents = []
            found_documents = {}
            
            for requirement in self.required_documents:
                doc_type = requirement.get("type")
                max_age_days = requirement.get("max_age_days")
                must_be_verified = requirement.get("must_be_verified", False)
                check_expiry = requirement.get("check_expiry", True)
                
                # Find matching document
                matching_doc = None
                for doc in documents:
                    if doc.data.get("document_type") != doc_type:
                        continue
                    
                    # Check verification status
                    if must_be_verified and doc.data.get("verification_status") != "verified":
                        continue
                    
                    # Check age
                    if max_age_days:
                        issued_date_str = doc.data.get("issued_date")
                        if issued_date_str:
                            issued_date = datetime.fromisoformat(issued_date_str.replace('Z', '+00:00'))
                            age_days = (datetime.utcnow() - issued_date).days
                            if age_days > max_age_days:
                                continue
                    
                    # Check expiry
                    if check_expiry:
                        expiry_date_str = doc.data.get("expiry_date")
                        if expiry_date_str:
                            expiry_date = datetime.fromisoformat(expiry_date_str.replace('Z', '+00:00'))
                            if expiry_date < datetime.utcnow():
                                continue
                    
                    # This document meets all requirements
                    matching_doc = doc
                    break
                
                if matching_doc:
                    found_documents[doc_type] = matching_doc.entity_id
                    print(f"   ✅ Found required document: {doc_type} ({matching_doc.entity_id})")
                else:
                    missing_documents.append(doc_type)
                    print(f"   ❌ Missing required document: {doc_type}")
            
            # Handle results
            if missing_documents:
                error_msg = f"Missing required documents: {', '.join(missing_documents)}"
                
                await self.log_warning(
                    error_msg,
                    details={
                        "missing_documents": missing_documents,
                        "found_documents": found_documents
                    }
                )
                
                if self.on_missing == "request":
                    # Pause workflow for document upload
                    # Store what's needed in context for UI
                    self.state.output_data = {
                        "requires_documents": True,
                        "missing_documents": missing_documents,
                        "found_documents": found_documents
                    }
                    return "waiting"  # Pause for user input
                else:
                    # Fail the task
                    await self.log_error(error_msg)
                    return "failed"
            else:
                # All requirements met
                await self.log_info(
                    "All document requirements satisfied",
                    details={
                        "found_documents": found_documents
                    }
                )
                
                # Store found documents in context
                self.state.output_data = {
                    self.store_documents_as: found_documents,
                    f"{self.task_id}_all_requirements_met": True
                }
                
                return "continue"
                
        except Exception as e:
            await self.log_error(
                f"Document requirement check failed: {str(e)}",
                error=str(e)
            )
            return "failed"