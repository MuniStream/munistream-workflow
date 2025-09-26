"""
Customer model for public portal authentication.
Completely separate from admin users to maintain clean architecture.
"""

from datetime import datetime
from typing import Optional, Dict, Any
from enum import Enum
from pydantic import BaseModel, Field, EmailStr
from beanie import Document, Indexed


class CustomerStatus(str, Enum):
    """Customer account status"""
    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"
    PENDING_VERIFICATION = "pending_verification"


class Customer(Document):
    """
    Customer model for public portal users.
    Completely separate from admin UserModel to maintain separation of concerns.
    """
    
    # Basic Information
    email: Indexed(EmailStr, unique=True)
    password_hash: str
    full_name: str
    phone: Optional[str] = None
    document_number: Optional[str] = None
    keycloak_id: Optional[str] = None  # Keycloak user ID for SSO users
    
    # Account Status
    status: CustomerStatus = CustomerStatus.ACTIVE
    is_active: bool = True
    email_verified: bool = False
    
    # Metadata
    preferences: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_login_at: Optional[datetime] = None
    
    class Settings:
        name = "customers"  # Separate MongoDB collection
        indexes = [
            "email",
            "document_number",
            "created_at"
        ]
    
    def update_last_login(self):
        """Update last login timestamp"""
        self.last_login_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()
    
    def is_verified(self) -> bool:
        """Check if customer account is verified"""
        return self.email_verified and self.status == CustomerStatus.ACTIVE
    
    def to_public_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for public API responses"""
        return {
            "id": str(self.id),
            "email": self.email,
            "full_name": self.full_name,
            "phone": self.phone,
            "document_number": self.document_number,
            "status": self.status,
            "email_verified": self.email_verified,
            "created_at": self.created_at,
            "last_login_at": self.last_login_at
        }


class CustomerSession(Document):
    """
    Session tracking for customers (optional - for enhanced security)
    """
    
    customer_id: str
    session_token: str
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime
    is_active: bool = True
    
    class Settings:
        name = "customer_sessions"
        indexes = [
            "customer_id",
            "session_token",
            "expires_at"
        ]