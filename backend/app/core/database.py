from motor.motor_asyncio import AsyncIOMotorClient
from beanie import init_beanie
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

from ..core.config import settings
from ..models.workflow import (
    WorkflowDefinition,
    WorkflowStep,
    WorkflowInstance,
    StepExecution,
    ApprovalRequest,
    WorkflowAuditLog,
    IntegrationLog,
    WorkflowEvent,
    WorkflowHook,
)
from ..models.document import DocumentModel, DocumentFolderModel, DocumentShareModel
from ..models.user import UserModel, RefreshTokenModel
from ..models.category import WorkflowCategory
from ..models.team import TeamModel
from ..models.customer import Customer, CustomerSession
from ..models.legal_entity import EntityType, LegalEntity
from ..models.catalog import Catalog, CatalogData, CatalogRow
from ..models.profile_field_definition import ProfileFieldDefinition
from ..models.user_profile import UserProfile
from ..models.atencion import Feedback, SolicitudAtencion
from ..models.reportes import AlertaSemaforo, ConfiguracionReportes, ReporteDesempeno
from ..models.envio_cliente import EnvioCliente
from ..notifier.models import (
    NotificationChannelConfig,
    NotificationTemplate,
    NotificationTrigger,
    NotificationDelivery,
)


class Database:
    client: Optional[AsyncIOMotorClient] = None
    database = None


database = Database()


def _sin_credenciales(url: str) -> str:
    """La URL de Mongo sin usuario ni contraseña, para el log."""
    partes = urlsplit(url or "")
    if "@" not in partes.netloc:
        return url
    return urlunsplit(partes._replace(netloc="***@" + partes.netloc.rsplit("@", 1)[1]))


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
            WorkflowEvent,
            WorkflowHook,
            DocumentModel,
            DocumentFolderModel,
            DocumentShareModel,
            UserModel,
            RefreshTokenModel,
            WorkflowCategory,
            TeamModel,
            Customer,
            CustomerSession,
            EntityType,
            LegalEntity,
            Catalog,
            CatalogData,
            CatalogRow,
            ProfileFieldDefinition,
            UserProfile,
            Feedback,
            SolicitudAtencion,
            ReporteDesempeno,
            ConfiguracionReportes,
            AlertaSemaforo,
            EnvioCliente,
            NotificationChannelConfig,
            NotificationTemplate,
            NotificationTrigger,
            NotificationDelivery,
        ]
    )
    
    print(f"Connected to MongoDB: {_sin_credenciales(settings.MONGODB_URL)}")


async def close_mongo_connection():
    """Close database connection"""
    if database.client:
        database.client.close()
        print("Disconnected from MongoDB")


async def get_database():
    """Get database instance"""
    return database.database