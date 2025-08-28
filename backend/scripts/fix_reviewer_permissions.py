#!/usr/bin/env python3
"""
Script to fix reviewer permissions - ensure all reviewer users have correct permissions
"""

import asyncio
import sys
import os

# Add the parent directory to sys.path to import app modules
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from motor.motor_asyncio import AsyncIOMotorClient
from app.core.config import settings
from app.models.user import UserModel, UserRole, Permission, ROLE_PERMISSIONS

async def fix_reviewer_permissions():
    """Fix permissions for all reviewer users"""
    # Connect to MongoDB
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    await UserModel.init_beanie(database=client[settings.MONGODB_DB_NAME])
    
    try:
        # Find all reviewer users
        reviewers = await UserModel.find({"role": UserRole.REVIEWER}).to_list()
        
        print(f"Found {len(reviewers)} reviewer users")
        
        for reviewer in reviewers:
            print(f"\n--- Updating reviewer: {reviewer.username} ({reviewer.full_name}) ---")
            
            # Get current permissions
            current_perms = [str(p) for p in reviewer.permissions] if reviewer.permissions else []
            print(f"Current permissions: {current_perms}")
            
            # Get role-based permissions
            role_perms = ROLE_PERMISSIONS.get(UserRole.REVIEWER, [])
            role_perm_values = [p.value for p in role_perms]
            print(f"Should have role permissions: {role_perm_values}")
            
            # Update user status to active if needed
            if reviewer.status != "active":
                print(f"Updating status from {reviewer.status} to active")
                reviewer.status = "active"
            
            # Clear explicit permissions since they should come from role
            # (The convert_user_to_response function will add role permissions automatically)
            reviewer.permissions = []
            
            # Update the user
            await reviewer.save()
            print(f"✅ Updated reviewer: {reviewer.username}")
            
            # Verify the fix by checking effective permissions
            from app.api.endpoints.auth import convert_user_to_response
            user_response = convert_user_to_response(reviewer)
            print(f"Effective permissions after update: {user_response.permissions}")
        
        print(f"\n✅ Successfully updated {len(reviewers)} reviewer users")
        
    except Exception as e:
        print(f"❌ Error updating reviewer permissions: {e}")
        raise
    finally:
        client.close()

async def main():
    """Main function"""
    print("🔧 Fixing reviewer permissions...")
    await fix_reviewer_permissions()
    print("✅ Done!")

if __name__ == "__main__":
    asyncio.run(main())