#!/usr/bin/env python3
"""
Create test workflow instances for demo purposes
"""
import asyncio
import uuid
from datetime import datetime
from typing import Dict, Any

import motor.motor_asyncio
from beanie import init_beanie

from app.models.workflow import WorkflowInstance, AssignmentStatus, AssignmentType
from app.models.user import UserModel
from app.models.teams import Team

# Database connection
MONGO_URL = "mongodb://admin:munistream123@localhost:27017/munistream?authSource=admin"

async def create_test_instances():
    """Create test workflow instances for demo"""
    
    # Initialize database connection
    client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URL)
    
    # Initialize Beanie
    await init_beanie(
        database=client.munistream,
        document_models=[WorkflowInstance, UserModel, Team]
    )
    
    print("Connected to database successfully")
    
    # Get reviewer user
    reviewer_user = await UserModel.find_one(UserModel.email == "reviewer@example.com")
    if not reviewer_user:
        print("Error: Reviewer user not found. Creating one...")
        reviewer_user = UserModel(
            user_id=str(uuid.uuid4()),
            email="reviewer@example.com",
            full_name="Test Reviewer",
            role="reviewer",
            password_hash="$2b$12$hash",
            is_active=True,
            permissions=["review_instances"]
        )
        await reviewer_user.create()
    
    # Get a team
    team = await Team.find_one()
    if not team:
        print("Error: No teams found. Creating test team...")
        team = Team(
            team_id=str(uuid.uuid4()),
            name="Review Team",
            description="Test review team",
            members=[{
                "user_id": reviewer_user.user_id,
                "role": "reviewer",
                "joined_at": datetime.utcnow()
            }]
        )
        await team.create()
    
    # Test workflows to create instances for
    test_workflows = [
        {
            "workflow_id": "certificado_libertad_gravamen",
            "name": "Certificado de Libertad de Gravamen",
            "user_id": "citizen1@test.com"
        },
        {
            "workflow_id": "actualizacion_catastral", 
            "name": "Actualización Catastral",
            "user_id": "citizen2@test.com"
        },
        {
            "workflow_id": "avaluo_catastral",
            "name": "Avalúo Catastral", 
            "user_id": "citizen3@test.com"
        }
    ]
    
    created_instances = []
    
    for i, workflow_config in enumerate(test_workflows):
        # Create instance in different states
        instance_id = str(uuid.uuid4())
        
        # Create instance data
        instance_data = {
            "instance_id": instance_id,
            "workflow_id": workflow_config["workflow_id"],
            "workflow_version": "1.0.0",
            "user_id": workflow_config["user_id"],
            "status": "awaiting_input",  # Citizen has submitted data, waiting for review
            "current_step": "validation_step",
            "context": {
                "citizen_data": {
                    "nombre": f"Ciudadano {i+1}",
                    "cedula": f"1234567{i+1}",
                    "direccion": f"Calle {i+1} #123",
                    "telefono": f"555-000{i+1}"
                },
                "submission_date": datetime.utcnow().isoformat(),
                "documents_uploaded": ["cedula.pdf", "solicitud.pdf"]
            },
            "user_data": {
                "name": f"Ciudadano {i+1}",
                "email": workflow_config["user_id"]
            },
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
            "started_at": datetime.utcnow(),
        }
        
        # Assign different states to showcase the workflow
        if i == 0:
            # Unassigned instance
            instance_data.update({
                "assignment_status": AssignmentStatus.UNASSIGNED,
                "assignment_type": AssignmentType.AUTOMATIC
            })
        elif i == 1:
            # Assigned to user, pending review
            instance_data.update({
                "assigned_user_id": reviewer_user.user_id,
                "assigned_team_id": team.team_id,
                "assignment_status": AssignmentStatus.PENDING_REVIEW,
                "assignment_type": AssignmentType.AUTOMATIC,
                "assigned_at": datetime.utcnow(),
                "assigned_by": "system",
                "assignment_notes": "Auto-assigned for review"
            })
        else:
            # Assigned to team only
            instance_data.update({
                "assigned_team_id": team.team_id,
                "assignment_status": AssignmentStatus.PENDING_REVIEW,
                "assignment_type": AssignmentType.AUTOMATIC,
                "assigned_at": datetime.utcnow(),
                "assigned_by": "system",
                "assignment_notes": "Team assignment for review"
            })
        
        # Create the instance
        instance = WorkflowInstance(**instance_data)
        await instance.create()
        created_instances.append(instance)
        
        print(f"✅ Created instance {instance.instance_id} for {workflow_config['name']}")
        print(f"   Status: {instance.assignment_status}")
        print(f"   Assigned to: User={instance.assigned_user_id}, Team={instance.assigned_team_id}")
    
    print(f"\n🎉 Successfully created {len(created_instances)} test instances!")
    print("\nTest with reviewer credentials:")
    print("  Email: reviewer@example.com")
    print("  Password: reviewer123")
    print("\nAccess the frontend at: http://localhost:3000")
    print("Go to Instance Assignment section to see the test instances.")
    
    return created_instances

if __name__ == "__main__":
    asyncio.run(create_test_instances())