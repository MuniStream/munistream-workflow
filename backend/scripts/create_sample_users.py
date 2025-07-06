#!/usr/bin/env python3
"""
Script to create sample users for all roles in CivicStream.
"""

import asyncio
import sys
from pathlib import Path

# Add the parent directory to Python path to import our modules
sys.path.append(str(Path(__file__).parent.parent))

from app.core.database import connect_to_mongo, close_mongo_connection
from app.models.user import UserModel, UserRole, UserStatus, ROLE_PERMISSIONS


async def create_sample_users():
    """Create sample users for all roles"""
    
    print("Connecting to database...")
    await connect_to_mongo()
    
    sample_users = [
        {
            "email": "manager@civicstream.com",
            "username": "manager",
            "full_name": "John Manager",
            "password": "manager123",
            "role": UserRole.MANAGER,
            "department": "Operations"
        },
        {
            "email": "reviewer@civicstream.com", 
            "username": "reviewer",
            "full_name": "Jane Reviewer",
            "password": "reviewer123",
            "role": UserRole.REVIEWER,
            "department": "Document Review"
        },
        {
            "email": "approver@civicstream.com",
            "username": "approver", 
            "full_name": "Bob Approver",
            "password": "approver123",
            "role": UserRole.APPROVER,
            "department": "Approvals"
        },
        {
            "email": "viewer@civicstream.com",
            "username": "viewer",
            "full_name": "Alice Viewer", 
            "password": "viewer123",
            "role": UserRole.VIEWER,
            "department": "Analytics"
        }
    ]
    
    try:
        for user_data in sample_users:
            # Check if user already exists
            existing_user = await UserModel.find_one({"username": user_data["username"]})
            if existing_user:
                print(f"User {user_data['username']} already exists, skipping...")
                continue
            
            # Get permissions for this role
            permissions = ROLE_PERMISSIONS.get(user_data["role"], [])
            
            user = UserModel(
                email=user_data["email"],
                username=user_data["username"],
                full_name=user_data["full_name"],
                hashed_password=UserModel.hash_password(user_data["password"]),
                role=user_data["role"],
                status=UserStatus.ACTIVE,
                department=user_data["department"],
                permissions=permissions
            )
            
            await user.save()
            print(f"✅ Created user: {user.username} ({user.role}) with {len(permissions)} permissions")
            
    except Exception as e:
        print(f"❌ Error creating users: {e}")
        
    finally:
        await close_mongo_connection()


async def main():
    """Main function"""
    print("🚀 Creating Sample Users for CivicStream")
    print("=" * 45)
    
    await create_sample_users()
    
    print("\n" + "=" * 45)
    print("Sample users created successfully!")
    print("\nUser credentials:")
    print("Manager - Username: manager, Password: manager123")
    print("Reviewer - Username: reviewer, Password: reviewer123")
    print("Approver - Username: approver, Password: approver123")
    print("Viewer - Username: viewer, Password: viewer123")
    print("\n⚠️  Change all default passwords in production!")


if __name__ == "__main__":
    asyncio.run(main())