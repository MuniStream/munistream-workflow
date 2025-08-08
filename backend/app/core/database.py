from motor.motor_asyncio import AsyncIOMotorClient
from beanie import init_beanie
from typing import Optional

from ..core.config import settings
from ..models.workflow import (
    WorkflowDefinition,
    WorkflowStep,
    WorkflowInstance,
    StepExecution,
    ApprovalRequest,
    WorkflowAuditLog,
    IntegrationLog
)
from ..models.document import DocumentModel, DocumentFolderModel, DocumentShareModel
from ..models.user import UserModel, RefreshTokenModel
from ..models.category import WorkflowCategory
from ..models.team import TeamModel
from ..models.customer import Customer, CustomerSession


class Database:
    client: Optional[AsyncIOMotorClient] = None
    database = None


database = Database()


async def connect_to_mongo():
    """Create database connection"""
    database.client = AsyncIOMotorClient(settings.MONGODB_URL)
    database.database = database.client[settings.MONGODB_DB_NAME]
    
    # Initialize Beanie with the models
    await init_beanie(
        database=database.database,
        document_models=[
            WorkflowDefinition,
            WorkflowStep,
            WorkflowInstance,
            StepExecution,
            ApprovalRequest,
            WorkflowAuditLog,
            IntegrationLog,
            DocumentModel,
            DocumentFolderModel,
            DocumentShareModel,
            UserModel,
            RefreshTokenModel,
            WorkflowCategory,
            TeamModel,
            Customer,
            CustomerSession
        ]
    )
    
    print(f"Connected to MongoDB: {settings.MONGODB_URL}")


async def close_mongo_connection():
    """Close database connection"""
    if database.client:
        database.client.close()
        print("Disconnected from MongoDB")


async def get_database():
    """Get database instance"""
    return database.database