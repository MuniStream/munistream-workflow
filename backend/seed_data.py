#!/usr/bin/env python3
"""
Script to seed the CivicStream database with sample data for demonstration.
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add the app directory to the Python path
sys.path.append(str(Path(__file__).parent / "app"))

from app.core.database import connect_to_mongo
from app.models.document import DocumentModel, DocumentStatus, DocumentType, VerificationMethod
from app.models.workflow import WorkflowDefinition, WorkflowStep

async def create_sample_documents():
    """Create sample documents for testing admin interface."""
    
    sample_documents = [
        {
            "title": "National ID Card",
            "document_type": DocumentType.NATIONAL_ID,
            "status": DocumentStatus.PENDING,
            "citizen_id": "citizen_001",
            "access_level": "private",
            "tags": ["identity", "government"],
            "category": "identification",
            "metadata": {
                "original_filename": "national_id_001.jpg",
                "file_size": 2048576,
                "mime_type": "image/jpeg",
                "confidence_score": 0.85,
                "verification_attempts": 0,
                "workflow_name": "Citizen Registration"
            }
        },
        {
            "title": "Passport Document",
            "document_type": DocumentType.PASSPORT,
            "status": DocumentStatus.PENDING,
            "citizen_id": "citizen_002",
            "access_level": "private",
            "tags": ["travel", "identity"],
            "category": "identification",
            "metadata": {
                "original_filename": "passport_002.pdf",
                "file_size": 1536000,
                "mime_type": "application/pdf",
                "confidence_score": 0.92,
                "verification_attempts": 0,
                "workflow_name": "Building Permit Application"
            }
        },
        {
            "title": "Proof of Address",
            "document_type": DocumentType.PROOF_OF_ADDRESS,
            "status": DocumentStatus.PENDING,
            "citizen_id": "citizen_003",
            "access_level": "private",
            "tags": ["address", "utility"],
            "category": "residence",
            "metadata": {
                "original_filename": "utility_bill_003.pdf",
                "file_size": 892416,
                "mime_type": "application/pdf",
                "confidence_score": 0.78,
                "verification_attempts": 1,
                "workflow_name": "Citizen Registration with Documents"
            }
        },
        {
            "title": "Building Permit Certificate",
            "document_type": "permit",
            "status": DocumentStatus.VERIFIED,
            "citizen_id": "citizen_004",
            "access_level": "public",
            "tags": ["permit", "construction"],
            "category": "permits",
            "verified_by": "admin_001",
            "verified_at": datetime.utcnow() - timedelta(hours=1),
            "verification_method": VerificationMethod.MANUAL,
            "metadata": {
                "original_filename": "building_permit_004.pdf",
                "file_size": 1024000,
                "mime_type": "application/pdf",
                "confidence_score": 1.0,
                "workflow_name": "Building Permit Application",
                "signatures": []  # Empty - needs admin signature
            }
        },
        {
            "title": "Citizenship Certificate",
            "document_type": "certificate",
            "status": DocumentStatus.VERIFIED,
            "citizen_id": "citizen_005",
            "access_level": "private",
            "tags": ["citizenship", "certificate"],
            "category": "certificates",
            "verified_by": "admin_002",
            "verified_at": datetime.utcnow() - timedelta(hours=2),
            "verification_method": VerificationMethod.MANUAL,
            "metadata": {
                "original_filename": "citizenship_cert_005.pdf",
                "file_size": 756000,
                "mime_type": "application/pdf",
                "confidence_score": 1.0,
                "workflow_name": "Citizen Registration",
                "signatures": []  # Empty - needs admin signature
            }
        },
        {
            "title": "Suspicious Bank Statement",
            "document_type": DocumentType.BANK_STATEMENT,
            "status": DocumentStatus.PENDING,
            "citizen_id": "citizen_006",
            "access_level": "private",
            "tags": ["financial", "suspicious"],
            "category": "financial",
            "metadata": {
                "original_filename": "bank_statement_006.pdf",
                "file_size": 1200000,
                "mime_type": "application/pdf",
                "confidence_score": 0.45,  # Low confidence - triggers manual review
                "verification_attempts": 0,
                "workflow_name": "Financial Verification",
                "fraud_indicators": ["altered_text", "inconsistent_metadata"]
            }
        }
    ]
    
    created_docs = []
    for doc_data in sample_documents:
        doc = DocumentModel(**doc_data)
        await doc.save()
        created_docs.append(doc)
        print(f"Created document: {doc.title} (ID: {doc.document_id})")
    
    return created_docs

async def create_sample_workflows():
    """Create sample workflow definitions."""
    
    sample_workflows = [
        {
            "workflow_id": "citizen_registration_v1",
            "name": "Citizen Registration",
            "description": "Complete workflow for registering new citizens with age-based routing",
            "version": "1.0",
            "steps": {
                "validate_identity": {
                    "name": "Validate Identity",
                    "step_type": "validation",
                    "required_inputs": ["national_id", "birth_certificate"],
                    "next_steps": ["identity_check"]
                },
                "identity_check": {
                    "name": "Identity Check",
                    "step_type": "automated_check",
                    "required_inputs": ["identity_data"],
                    "next_steps": ["age_verification"]
                },
                "age_verification": {
                    "name": "Age Verification",
                    "step_type": "conditional",
                    "required_inputs": ["date_of_birth"],
                    "next_steps": ["adult_approval", "minor_guardian_check"]
                }
            },
            "metadata": {
                "category": "citizen_services",
                "estimated_duration": "2-5 days",
                "complexity": "medium"
            }
        },
        {
            "workflow_id": "building_permit_v1",
            "name": "Building Permit Application",
            "description": "Apply for building permit with automated document verification",
            "version": "1.0",
            "steps": {
                "submit_application": {
                    "name": "Submit Application",
                    "step_type": "data_collection",
                    "required_inputs": ["property_details", "construction_plans"],
                    "next_steps": ["document_verification"]
                },
                "document_verification": {
                    "name": "Document Verification",
                    "step_type": "automated_verification",
                    "required_inputs": ["submitted_documents"],
                    "next_steps": ["manual_review", "auto_approval"]
                },
                "manual_review": {
                    "name": "Manual Review",
                    "step_type": "human_review",
                    "required_inputs": ["verification_results"],
                    "next_steps": ["approval", "rejection"]
                }
            },
            "metadata": {
                "category": "permits",
                "estimated_duration": "3-7 days",
                "complexity": "high"
            }
        }
    ]
    
    created_workflows = []
    for workflow_data in sample_workflows:
        workflow = WorkflowDefinition(**workflow_data)
        await workflow.save()
        created_workflows.append(workflow)
        print(f"Created workflow: {workflow.name} (ID: {workflow.workflow_id})")
    
    return created_workflows

async def main():
    """Main function to seed the database."""
    
    # Connect to MongoDB using the same method as the main app
    await connect_to_mongo()
    
    print("🌱 Seeding CivicStream database with sample data...")
    print("=" * 50)
    
    # Create sample documents
    print("\n📄 Creating sample documents...")
    documents = await create_sample_documents()
    
    # Create sample workflows
    print("\n🔄 Creating sample workflows...")
    workflows = await create_sample_workflows()
    
    print("\n" + "=" * 50)
    print(f"✅ Database seeded successfully!")
    print(f"   - {len(documents)} documents created")
    print(f"   - {len(workflows)} workflows created")
    print(f"   - Admin interface should now show real data")
    
    # Show summary of what was created
    print("\n📊 Summary of created data:")
    print(f"   • Pending documents: {len([d for d in documents if d.status == DocumentStatus.PENDING])}")
    print(f"   • Verified documents: {len([d for d in documents if d.status == DocumentStatus.VERIFIED])}")
    print(f"   • Documents needing signatures: {len([d for d in documents if d.status == DocumentStatus.VERIFIED and not d.metadata.get('signatures')])}")
    print(f"   • Low-confidence documents: {len([d for d in documents if d.metadata.get('confidence_score', 1.0) < 0.7])}")

if __name__ == "__main__":
    asyncio.run(main())