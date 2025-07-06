#!/usr/bin/env python3
"""
Script to update admin user permissions.
"""

import asyncio
import sys
from pathlib import Path

# Add the parent directory to Python path to import our modules
sys.path.append(str(Path(__file__).parent.parent))

from app.core.database import connect_to_mongo, close_mongo_connection
from app.models.user import UserModel, UserRole, ROLE_PERMISSIONS


async def update_admin_permissions():
    """Update admin user with all permissions"""
    
    print("Connecting to database...")
    await connect_to_mongo()
    
    try:
        # Find admin user
        admin_user = await UserModel.find_one({"username": "admin"})
        if not admin_user:
            print("❌ Admin user not found!")
            return
        
        print(f"Found admin user: {admin_user.username}")
        print(f"Current permissions: {len(admin_user.permissions)}")
        
        # Update permissions
        admin_user.permissions = ROLE_PERMISSIONS[UserRole.ADMIN]
        await admin_user.save()
        
        print(f"✅ Updated permissions: {len(admin_user.permissions)}")
        print("Permissions granted:")
        for perm in admin_user.permissions:
            print(f"  - {perm}")
        
    except Exception as e:
        print(f"❌ Error updating admin permissions: {e}")
        
    finally:
        await close_mongo_connection()


if __name__ == "__main__":
    asyncio.run(update_admin_permissions())