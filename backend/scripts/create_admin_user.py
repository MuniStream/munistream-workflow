#!/usr/bin/env python3
"""
Script to create initial admin user for CivicStream.
"""

import asyncio
import sys
from pathlib import Path

# Add the parent directory to Python path to import our modules
sys.path.append(str(Path(__file__).parent.parent))

from app.core.database import connect_to_mongo, close_mongo_connection
from app.models.user import UserModel, UserRole, UserStatus


async def create_admin_user():
    """Create initial admin user"""
    
    print("Connecting to database...")
    await connect_to_mongo()
    
    try:
        # Check if admin user already exists
        existing_admin = await UserModel.find_one({"username": "admin"})
        if existing_admin:
            print("Admin user already exists!")
            print(f"Username: {existing_admin.username}")
            print(f"Email: {existing_admin.email}")
            print(f"Role: {existing_admin.role}")
            print(f"Status: {existing_admin.status}")
            return
        
        # Create admin user with all permissions
        from app.models.user import ROLE_PERMISSIONS
        
        admin_user = UserModel(
            email="admin@civicstream.com",
            username="admin",
            full_name="System Administrator",
            hashed_password=UserModel.hash_password("admin123"),  # Change this in production!
            role=UserRole.ADMIN,
            status=UserStatus.ACTIVE,
            department="IT Administration",
            permissions=ROLE_PERMISSIONS[UserRole.ADMIN]  # Give admin all permissions
        )
        
        await admin_user.save()
        
        print("✅ Admin user created successfully!")
        print(f"Username: {admin_user.username}")
        print(f"Email: {admin_user.email}")
        print(f"Password: admin123")  # Change this in production!
        print(f"Role: {admin_user.role}")
        print(f"Status: {admin_user.status}")
        print("\n⚠️  IMPORTANT: Change the default password after first login!")
        
    except Exception as e:
        print(f"❌ Error creating admin user: {e}")
        
    finally:
        await close_mongo_connection()


async def create_sample_users():
    """Create sample users for testing"""
    
    print("Creating sample users...")
    
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
    
    for user_data in sample_users:
        try:
            # Check if user already exists
            existing_user = await UserModel.find_one({"username": user_data["username"]})
            if existing_user:
                print(f"User {user_data['username']} already exists, skipping...")
                continue
            
            user = UserModel(
                email=user_data["email"],
                username=user_data["username"],
                full_name=user_data["full_name"],
                hashed_password=UserModel.hash_password(user_data["password"]),
                role=user_data["role"],
                status=UserStatus.ACTIVE,
                department=user_data["department"]
            )
            
            await user.save()
            print(f"✅ Created user: {user.username} ({user.role})")
            
        except Exception as e:
            print(f"❌ Error creating user {user_data['username']}: {e}")


async def main():
    """Main function"""
    print("🚀 CivicStream User Initialization")
    print("=" * 40)
    
    await create_admin_user()
    print()
    await create_sample_users()
    
    print("\n" + "=" * 40)
    print("User initialization complete!")
    print("\nDefault credentials:")
    print("Admin - Username: admin, Password: admin123")
    print("Manager - Username: manager, Password: manager123")
    print("Reviewer - Username: reviewer, Password: reviewer123")
    print("Approver - Username: approver, Password: approver123") 
    print("Viewer - Username: viewer, Password: viewer123")
    print("\n⚠️  Change all default passwords in production!")


if __name__ == "__main__":
    asyncio.run(main())