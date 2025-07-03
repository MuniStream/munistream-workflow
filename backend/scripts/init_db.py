#!/usr/bin/env python3
"""
Database initialization script for CivicStream
"""

import asyncio
import sys
import os

# Add the project root to the Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.core.database import connect_to_mongo, close_mongo_connection
from app.core.config import settings
from app.services.workflow_service import WorkflowService
from app.workflows.examples.citizen_registration import create_citizen_registration_workflow


async def init_database():
    """Initialize the database with sample data"""
    print("Initializing CivicStream database...")
    
    try:
        # Connect to MongoDB
        await connect_to_mongo()
        print(f"✅ Connected to MongoDB at {settings.MONGODB_URL}")
        
        # Create sample workflow
        print("Creating sample workflow...")
        workflow = create_citizen_registration_workflow()
        
        # Save workflow definition
        workflow_def = await WorkflowService.create_workflow_definition(
            workflow_id=workflow.workflow_id,
            name=workflow.name,
            description=workflow.description,
            version=workflow.version,
            created_by="system",
            metadata={
                "category": "citizen_services",
                "tags": ["registration", "identity", "approval"],
                "example": True
            }
        )
        print(f"✅ Created workflow definition: {workflow_def.workflow_id}")
        
        # Save workflow steps
        steps = await WorkflowService.save_workflow_steps(workflow.workflow_id, workflow)
        print(f"✅ Created {len(steps)} workflow steps")
        
        # Update workflow definition with start step
        await WorkflowService.update_workflow_definition(
            workflow.workflow_id,
            {
                "start_step_id": workflow.start_step.step_id,
                "status": "active"
            },
            updated_by="system"
        )
        print(f"✅ Activated workflow with start step: {workflow.start_step.step_id}")
        
        print("\n🎉 Database initialization completed successfully!")
        print("\nSample data created:")
        print(f"  - Workflow: {workflow.workflow_id}")
        print(f"  - Steps: {len(steps)}")
        print(f"  - Database: {settings.MONGODB_DB_NAME}")
        
    except Exception as e:
        print(f"❌ Error initializing database: {str(e)}")
        raise
    
    finally:
        await close_mongo_connection()


async def reset_database():
    """Reset the database (delete all collections)"""
    print("⚠️  WARNING: This will delete ALL data in the database!")
    confirmation = input("Type 'YES' to confirm: ")
    
    if confirmation != "YES":
        print("Database reset cancelled.")
        return
    
    try:
        await connect_to_mongo()
        
        from app.core.database import database
        
        # Get all collection names
        collections = await database.database.list_collection_names()
        
        # Drop all collections
        for collection_name in collections:
            await database.database.drop_collection(collection_name)
            print(f"✅ Dropped collection: {collection_name}")
        
        print("\n🗑️  Database reset completed!")
        
    except Exception as e:
        print(f"❌ Error resetting database: {str(e)}")
        raise
    
    finally:
        await close_mongo_connection()


async def main():
    """Main script entry point"""
    if len(sys.argv) > 1 and sys.argv[1] == "--reset":
        await reset_database()
        await init_database()
    elif len(sys.argv) > 1 and sys.argv[1] == "--reset-only":
        await reset_database()
    else:
        await init_database()


if __name__ == "__main__":
    asyncio.run(main())