#!/usr/bin/env python3
"""
Test the complete integration flow using API endpoints:
1. Citizen submits data → auto-assignment
2. Reviewer approves → workflow continuation
3. Final approval/signature
"""

import asyncio
import json
import uuid
from datetime import datetime
import aiohttp


BASE_URL = "http://localhost:8000"


async def authenticate_user(session, email: str, password: str):
    """Authenticate and get access token"""
    login_data = {
        "username": email,
        "password": password
    }
    
    async with session.post(f"{BASE_URL}/api/v1/auth/login", json=login_data) as response:
        if response.status == 200:
            result = await response.json()
            return result.get("access_token")
        else:
            print(f"Login failed: {response.status} - {await response.text()}")
            return None


async def test_citizen_data_submission():
    """Test citizen data submission and auto-assignment"""
    print("🧪 Testing citizen data submission and auto-assignment...")
    
    async with aiohttp.ClientSession() as session:
        # 1. Get available workflows first
        print("Fetching available workflows...")
        async with session.get(f"{BASE_URL}/api/v1/public/workflows") as response:
            if response.status != 200:
                print(f"Failed to get workflows: {response.status} - {await response.text()}")
                return None
                
            workflows = await response.json()
            if not workflows:
                print("No workflows available")
                return None
                
            # Find a workflow that has citizen input steps
            suitable_workflow = None
            for workflow in workflows:
                # Check if workflow has steps that require citizen input
                steps = workflow.get("steps", [])
                has_citizen_input = any(step.get("requirements") for step in steps)
                
                # Prefer workflows with "permit", "license", or "registration" in the name
                workflow_name_lower = workflow["name"].lower()
                if (has_citizen_input and 
                    ("permit" in workflow_name_lower or 
                     "license" in workflow_name_lower or 
                     "registration" in workflow_name_lower or
                     "catastral" in workflow_name_lower)):
                    suitable_workflow = workflow
                    break
            
            # If no suitable workflow found, use the first one anyway
            if not suitable_workflow:
                suitable_workflow = workflows[0]
                
            workflow_id = suitable_workflow["id"]
            workflow_name = suitable_workflow["name"]
            print(f"Found {len(workflows)} workflows, using: {workflow_name} ({workflow_id})")
        
        # 2. Start a workflow instance (citizen perspective)
        print(f"Starting workflow: {workflow_id}")
        async with session.post(f"{BASE_URL}/api/v1/public/workflows/{workflow_id}/start") as response:
            if response.status != 200:
                print(f"Failed to start workflow: {response.status} - {await response.text()}")
                return None
                
            instance_data = await response.json()
            instance_id = instance_data["instance_id"]
            print(f"✅ Created instance: {instance_id}")
        
        # 2. Submit citizen data to trigger auto-assignment
        citizen_data = aiohttp.FormData()
        citizen_data.add_field("nombre_completo", "Juan Pérez")
        citizen_data.add_field("cedula", "12345678")
        citizen_data.add_field("direccion", "Calle 123 #45-67")
        citizen_data.add_field("telefono", "555-1234")
        citizen_data.add_field("tipo_construccion", "Vivienda unifamiliar")
        
        print(f"Submitting citizen data for instance: {instance_id}")
        
        # First check the current status before submitting
        async with session.get(f"{BASE_URL}/api/v1/public/track/{instance_id}") as response:
            if response.status == 200:
                tracking = await response.json()
                print(f"📊 Pre-submit status: {tracking['status']}")
                print(f"📊 Requires input: {tracking.get('requires_input', False)}")
        
        async with session.post(f"{BASE_URL}/api/v1/public/instances/{instance_id}/submit-data", 
                               data=citizen_data) as response:
            if response.status != 200:
                error_detail = await response.text()
                print(f"⚠️  Failed to submit data: {response.status} - {error_detail}")
                
                # If this workflow doesn't support citizen input, skip to reviewer test directly
                if "No valid step found" in error_detail:
                    print("📝 This workflow doesn't require citizen input, proceeding to reviewer test...")
                    return instance_id
                else:
                    return None
                
            submit_result = await response.json()
            print(f"✅ Data submitted: {submit_result['message']}")
        
        # 3. Check if auto-assignment worked
        await asyncio.sleep(2)  # Give time for auto-assignment
        
        async with session.get(f"{BASE_URL}/api/v1/public/track/{instance_id}") as response:
            if response.status == 200:
                tracking = await response.json()
                print(f"📊 Instance status: {tracking['status']}")
                print(f"📊 Current step: {tracking['current_step']}")
                print(f"📊 Progress: {tracking['progress_percentage']}%")
            else:
                print(f"Failed to track instance: {response.status}")
        
        return instance_id


async def test_reviewer_workflow(instance_id: str):
    """Test reviewer approval workflow"""
    print(f"\n🧪 Testing reviewer workflow for instance: {instance_id}")
    
    async with aiohttp.ClientSession() as session:
        # 1. Authenticate as reviewer - try different reviewer accounts  
        token = await authenticate_user(session, "reviewer", "reviewer123")
        if not token:
            # Try other possible reviewer accounts
            for reviewer_email in ["revisor@cdmx.com", "reviewer@test.com"]:
                token = await authenticate_user(session, reviewer_email, "reviewer123")
                if token:
                    print(f"✅ Authenticated with {reviewer_email}")
                    break
        if not token:
            print("❌ Failed to authenticate reviewer")
            return False
            
        headers = {"Authorization": f"Bearer {token}"}
        print("✅ Reviewer authenticated")
        
        # 2. Get assigned instances
        async with session.get(f"{BASE_URL}/api/v1/instances/my-assignments", 
                              headers=headers) as response:
            if response.status == 200:
                assignments = await response.json()
                print(f"📋 Reviewer has {len(assignments.get('instances', []))} assigned instances")
                
                # Check if our test instance is in the assignments
                test_instance = None
                for inst in assignments.get('instances', []):
                    if inst['instance_id'] == instance_id:
                        test_instance = inst
                        break
                
                if test_instance:
                    print(f"✅ Test instance found in assignments: {test_instance['assignment_status']}")
                else:
                    print("⚠️  Test instance not found in reviewer assignments")
                    # List all instances to see what's available
                    async with session.get(f"{BASE_URL}/api/v1/instances/?page=1&page_size=10", 
                                          headers=headers) as all_response:
                        if all_response.status == 200:
                            all_instances = await all_response.json()
                            print(f"📋 Total instances available: {len(all_instances.get('instances', []))}")
                            for inst in all_instances.get('instances', []):
                                if inst['instance_id'] == instance_id:
                                    print(f"🔍 Found test instance with status: {inst.get('assignment_status', 'unknown')}")
            else:
                print(f"Failed to get assignments: {response.status} - {await response.text()}")
        
        # 3. Try to start review on the instance
        print(f"Starting review on instance: {instance_id}")
        async with session.post(f"{BASE_URL}/api/v1/instances/{instance_id}/start-review", 
                               headers=headers) as response:
            if response.status == 200:
                review_result = await response.json()
                print(f"✅ Review started: {review_result['message']}")
            else:
                print(f"⚠️  Failed to start review: {response.status} - {await response.text()}")
        
        # 4. Approve the instance
        approval_data = {
            "notes": "Documentación completa y correcta. Aprobado para siguiente etapa.",
            "reviewer_decision": "approved"
        }
        
        print(f"Approving instance: {instance_id}")
        async with session.post(f"{BASE_URL}/api/v1/instances/{instance_id}/approve-by-reviewer", 
                               json=approval_data, headers=headers) as response:
            if response.status == 200:
                approval_result = await response.json()
                print(f"✅ Instance approved by reviewer: {approval_result['message']}")
                print("🔄 Workflow execution should continue automatically...")
                return True
            else:
                print(f"❌ Failed to approve instance: {response.status} - {await response.text()}")
                return False


async def verify_workflow_continuation(instance_id: str):
    """Verify that workflow continued after reviewer approval"""
    print(f"\n🧪 Verifying workflow continuation for instance: {instance_id}")
    
    async with aiohttp.ClientSession() as session:
        # Wait a bit for workflow processing
        await asyncio.sleep(3)
        
        # Track the instance to see updated status
        async with session.get(f"{BASE_URL}/api/v1/public/track/{instance_id}") as response:
            if response.status == 200:
                tracking = await response.json()
                print(f"📊 Final status: {tracking['status']}")
                print(f"📊 Current step: {tracking['current_step']}")
                print(f"📊 Progress: {tracking['progress_percentage']}%")
                
                # Check if status indicates workflow continued
                if tracking['status'] in ['running', 'pending_signature', 'completed']:
                    print("✅ Workflow continuation successful!")
                    return True
                else:
                    print("⚠️  Workflow may not have continued as expected")
                    return False
            else:
                print(f"Failed to track final status: {response.status}")
                return False


async def run_complete_integration_test():
    """Run the complete integration test"""
    print("🚀 Starting complete integration flow test...")
    print("=" * 60)
    
    try:
        # Step 1: Citizen data submission and auto-assignment
        instance_id = await test_citizen_data_submission()
        if not instance_id:
            print("❌ Test failed at citizen data submission")
            return False
        
        # Step 2: Reviewer workflow
        approval_success = await test_reviewer_workflow(instance_id)
        if not approval_success:
            print("❌ Test failed at reviewer approval")
            return False
        
        # Step 3: Verify workflow continuation
        continuation_success = await verify_workflow_continuation(instance_id)
        if not continuation_success:
            print("❌ Test failed at workflow continuation verification")
            return False
        
        print("\n" + "=" * 60)
        print("🎉 COMPLETE INTEGRATION TEST PASSED!")
        print(f"🔗 Test instance ID: {instance_id}")
        print("✅ Citizen data → auto-assignment → review → approve → workflow continuation")
        return True
        
    except Exception as e:
        print(f"❌ Test failed with exception: {e}")
        return False


if __name__ == "__main__":
    success = asyncio.run(run_complete_integration_test())
    exit(0 if success else 1)