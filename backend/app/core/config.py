from typing import List, Optional
from pydantic_settings import BaseSettings
from pydantic import AnyHttpUrl, validator


class Settings(BaseSettings):
    PROJECT_NAME: str = "MuniStream"
    VERSION: str = "0.1.0"
    API_V1_STR: str = "/api/v1"
    
    # CORS - using hardcoded values in main.py for development
    # BACKEND_CORS_ORIGINS: List[str] = []
    
    # MongoDB
    MONGODB_URL: str = "mongodb://localhost:27017"
    MONGODB_DB_NAME: str = "munistream"
    
    # Security
    SECRET_KEY: str = "your-secret-key-here-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # Keycloak
    KEYCLOAK_URL: str = "http://localhost:8180"
    KEYCLOAK_REALM: str = "munistream"
    KEYCLOAK_CLIENT_ID: str = "munistream-backend"
    KEYCLOAK_CLIENT_SECRET: Optional[str] = None
    # Accept tokens from multiple issuers (constructed dynamically from environment)
    # This allows the same code to work in standalone mode and in client deployments
    KEYCLOAK_VALID_ISSUERS: Optional[List[str]] = None

    @validator('KEYCLOAK_VALID_ISSUERS', pre=True, always=True)
    def build_valid_issuers(cls, v, values):
        """Build list of valid issuers from KEYCLOAK_URL and KEYCLOAK_REALM"""
        if v is not None:
            return v

        keycloak_url = values.get('KEYCLOAK_URL', 'http://localhost:8180')
        keycloak_realm = values.get('KEYCLOAK_REALM', 'munistream')

        # Build issuer URL from Keycloak settings
        issuer = f"{keycloak_url}/realms/{keycloak_realm}"

        # For local development, also accept docker internal hostname
        valid_issuers = [issuer]
        if 'localhost' in keycloak_url:
            valid_issuers.append(f"http://host.docker.internal:8180/realms/{keycloak_realm}")

        return valid_issuers
    
    # Azure
    AZURE_STORAGE_CONNECTION_STRING: Optional[str] = None
    AZURE_STORAGE_CONTAINER_NAME: str = "munistream-files"
    
    # Blockchain
    BLOCKCHAIN_API_URL: Optional[str] = None
    BLOCKCHAIN_API_KEY: Optional[str] = None
    
    # Document Storage
    DOCUMENT_STORAGE_PATH: str = "./storage/documents"
    MAX_DOCUMENT_SIZE_MB: int = 50
    DOCUMENT_BASE_URL: Optional[str] = None  # For serving files

    # AWS S3 Configuration
    AWS_ACCESS_KEY_ID: Optional[str] = None
    AWS_SECRET_ACCESS_KEY: Optional[str] = None
    AWS_DEFAULT_REGION: str = "us-east-1"
    S3_BUCKET_NAME: Optional[str] = None

    # OpenProject Configuration
    OPENPROJECT_BASE_URL: Optional[str] = None
    OPENPROJECT_API_KEY: Optional[str] = None
    OPENPROJECT_DEFAULT_PROJECT_ID: Optional[str] = None
    OPENPROJECT_PROJECT_KEY: Optional[str] = None

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()