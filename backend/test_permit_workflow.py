#!/usr/bin/env python3
"""
Demonstration of the Building Permit workflow with document management.
Shows how citizens can apply for permits using existing or new documents.
"""

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

from app.workflows.registry import step_registry
from app.services.document_service import document_service
from app.models.document import DocumentType, DocumentStatus
from app.core.database import connect_to_mongo, close_mongo_connection


async def demonstrate_permit_workflow():
    """Demonstrate the building permit workflow"""
    
    print("🏗️ CivicStream Building Permit Workflow Demo")
    print("=" * 50)
    
    # Get the building permit workflow
    permit_workflow = step_registry.get_workflow("building_permit_v1")
    if not permit_workflow:
        print("❌ Building permit workflow not found!")
        return
    
    print(f"\n📋 Workflow: {permit_workflow.name}")
    print(f"   Description: {permit_workflow.description}")
    print(f"   Total Steps: {len(permit_workflow.steps)}")
    
    # Show workflow steps
    print("\n🔄 Workflow Steps:")
    step_types = {}
    for step in permit_workflow.steps.values():
        step_type = step.__class__.__name__
        step_types[step_type] = step_types.get(step_type, 0) + 1
        
        if "document" in step.step_id.lower() or "permit" in step.step_id:
            print(f"   📄 {step.step_id}: {step.name}")
        elif "payment" in step.step_id:
            print(f"   💳 {step.step_id}: {step.name}")
        elif "approval" in step.step_id or "review" in step.step_id:
            print(f"   ✅ {step.step_id}: {step.name}")
        else:
            print(f"   ▪️ {step.step_id}: {step.name}")
    
    print("\n📊 Step Type Summary:")
    for step_type, count in sorted(step_types.items()):
        print(f"   - {step_type}: {count}")
    
    # Simulate a permit application scenario
    print("\n🎭 Scenario: John Smith applying for kitchen renovation permit")
    print("=" * 50)
    
    # Scenario 1: User has verified ID from previous interaction
    print("\n📌 Scenario 1: Citizen with existing verified documents")
    print("   ✓ National ID already verified from citizen registration")
    print("   ✓ Can reuse existing document - saves 10+ minutes")
    print("   ✗ Needs to provide proof of address")
    
    application_data = {
        "property_address": "123 Main St, Cityville, ST 12345",
        "construction_type": "renovation",
        "estimated_value": 75000,
        "project_description": "Complete kitchen renovation with new appliances and cabinets"
    }
    
    print(f"\n📝 Application Details:")
    print(f"   Property: {application_data['property_address']}")
    print(f"   Type: {application_data['construction_type'].title()}")
    print(f"   Value: ${application_data['estimated_value']:,}")
    
    # Show the workflow process
    print("\n🔄 Workflow Process:")
    
    print("\n1️⃣ Identity Verification")
    print("   → System checks for existing verified documents")
    print("   ✅ Found: National ID (verified 30 days ago)")
    print("   ❌ Missing: Proof of Address")
    print("   → User uploads utility bill as proof of address")
    print("   → AI verification: 92% confidence - Auto-approved")
    
    print("\n2️⃣ Application Validation")
    print("   → All required fields validated")
    print("   → Application ID: APP-7B3F4A2C")
    
    print("\n3️⃣ Property Verification")
    print("   → Checking property records...")
    print("   ✅ Owner verified: John Smith")
    print("   ✅ Property ID: PROP-8D5E3B1A")
    print("   → Zoning: R1-Residential")
    
    print("\n4️⃣ Zoning Compliance")
    print("   → Checking zoning regulations...")
    print("   ✅ Renovation allowed in R1 zone")
    print("   ℹ️ Special conditions: Height limit 35ft, Setback requirements")
    
    print("\n5️⃣ Fee Calculation")
    print("   → Base fee: $250 (renovation)")
    print("   → Value-based fee: $375 (0.5% of $75,000)")
    print("   → Total permit fee: $625")
    print("   💡 Expedited processing available: +$312.50")
    
    print("\n6️⃣ Payment Processing")
    print("   → Redirecting to payment gateway...")
    print("   ✅ Payment completed via credit card")
    print("   → Transaction ID: PAY-2024-0042")
    
    print("\n7️⃣ Inspection Scheduling")
    print("   → Available inspection slots:")
    print("     • Mon, Jan 15: 9:00 AM, 2:00 PM")
    print("     • Tue, Jan 16: 11:00 AM, 4:00 PM")
    print("     • Wed, Jan 17: 9:00 AM, 11:00 AM")
    print("   → Inspector will check: Property boundaries, existing structures, utilities")
    
    print("\n8️⃣ Technical Review")
    print("   → Building Inspector review...")
    print("   ✅ Approved: Meets building codes")
    print("   → City Planner review...")
    print("   ✅ Approved: No conflicts with city planning")
    
    print("\n9️⃣ Final Approval")
    print("   → Permit Supervisor review...")
    print("   ✅ APPROVED: All requirements met")
    
    print("\n🎉 Permit Generation & Delivery")
    permit_details = {
        "permit_number": "BP-2024-7F3A2B",
        "issue_date": datetime.now().strftime("%Y-%m-%d"),
        "expiry_date": (datetime.now() + timedelta(days=365)).strftime("%Y-%m-%d"),
        "digital_signatures": ["Sarah Johnson (Permit Supervisor)", "Michael Chen (Building Commissioner)"]
    }
    
    print(f"\n📄 Building Permit Generated:")
    print(f"   Permit #: {permit_details['permit_number']}")
    print(f"   Issued: {permit_details['issue_date']}")
    print(f"   Expires: {permit_details['expiry_date']}")
    print(f"   Digital Signatures: {len(permit_details['digital_signatures'])}")
    
    print("\n💼 Saved to Citizen Wallet:")
    print("   ✅ Document saved to 'Permits' folder")
    print("   📱 Available on mobile app")
    print("   🔗 Shareable link generated")
    print("   🔍 QR code for verification")
    
    print("\n📧 Notifications Sent:")
    print("   ✉️ Email with permit PDF attached")
    print("   📱 SMS with permit number and link")
    print("   📲 Mobile app push notification")
    
    print("\n⛓️ Blockchain Recording:")
    print("   ✅ Permit recorded on blockchain")
    print("   🔐 Immutable record created")
    print("   📋 Transaction hash: 0x7f3a2b4e8d5c...")
    
    # Scenario 2: New user without documents
    print("\n\n📌 Scenario 2: New citizen without verified documents")
    print("=" * 50)
    print("   ✗ No existing verified documents")
    print("   → Must upload identity document")
    print("   → Must upload proof of address")
    print("   ⏱️ Additional time: ~15 minutes for document upload and verification")
    
    # Show document reuse benefits
    print("\n\n💡 Document Reuse Benefits:")
    print("=" * 50)
    
    reuse_scenarios = [
        {
            "future_workflow": "Business License Application",
            "reusable_docs": ["National ID", "Proof of Address", "Building Permit"],
            "time_saved": "20 minutes",
            "benefit": "Skip identity verification completely"
        },
        {
            "future_workflow": "Tax Certificate Request",
            "reusable_docs": ["National ID", "Property ownership verified"],
            "time_saved": "15 minutes",
            "benefit": "Pre-verified property ownership"
        },
        {
            "future_workflow": "Construction Inspection Request",
            "reusable_docs": ["Building Permit", "Property details"],
            "time_saved": "10 minutes",
            "benefit": "Automatic permit validation"
        }
    ]
    
    for scenario in reuse_scenarios:
        print(f"\n🔄 {scenario['future_workflow']}:")
        print(f"   Reusable: {', '.join(scenario['reusable_docs'])}")
        print(f"   Time saved: {scenario['time_saved']}")
        print(f"   Benefit: {scenario['benefit']}")
    
    # Show wallet contents
    print("\n\n📱 Citizen Wallet After Permit Issuance:")
    print("=" * 50)
    
    wallet_contents = {
        "Identity Documents": ["National ID ✓", "Passport", "Driver's License"],
        "Property Documents": ["Proof of Address ✓", "Property Deed", "Tax Assessment"],
        "Permits": ["Building Permit BP-2024-7F3A2B ✓ 🆕", "Previous Permit BP-2022-3A1B"],
        "Certificates": ["Citizen Registration Certificate ✓"],
        "Other": ["Bank Statement", "Insurance Policy"]
    }
    
    for category, documents in wallet_contents.items():
        print(f"\n📁 {category}:")
        for doc in documents:
            if "✓" in doc:
                print(f"   ✅ {doc}")
            else:
                print(f"   📄 {doc}")
    
    # Performance metrics
    print("\n\n📊 Performance Metrics:")
    print("=" * 50)
    print("   Total processing time: 45 minutes (with inspection scheduling)")
    print("   Document reuse saved: 15 minutes")
    print("   Automated steps: 14 of 18 (78%)")
    print("   Human interventions: 4 (2 reviews, 1 approval, 1 signature)")
    print("   Cost savings: $120 (reduced manual processing)")
    
    # API endpoints
    print("\n\n🌐 API Endpoints Used:")
    print("=" * 50)
    print("   POST /api/v1/workflows/building_permit_v1/start")
    print("   GET  /api/v1/documents/reuse-suggestions/building_permit_v1")
    print("   POST /api/v1/documents/upload")
    print("   GET  /api/v1/documents/folder/")
    print("   GET  /api/v1/workflows/instances/{instance_id}/status")
    print("   GET  /api/v1/documents/{permit_id}/download")
    
    print("\n✅ Demo completed!")
    print("\n💡 Key Takeaways:")
    print("   • Document reuse dramatically reduces application time")
    print("   • Digital permits instantly available in citizen wallet")
    print("   • Blockchain ensures permit authenticity")
    print("   • Mobile-ready for on-site verification")
    print("   • Automated compliance checking reduces errors")


async def main():
    """Main function with database initialization"""
    try:
        # Initialize database connection
        print("📡 Connecting to database...")
        await connect_to_mongo()
        
        # Run demonstration
        await demonstrate_permit_workflow()
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        print("\n💡 Make sure MongoDB is running:")
        print("  docker-compose up -d")
    finally:
        # Close database connection
        await close_mongo_connection()


if __name__ == "__main__":
    asyncio.run(main())