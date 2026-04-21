"""Per-citizen profile values keyed by ProfileFieldDefinition.field_id."""
from datetime import datetime
from typing import Any, Dict

from beanie import Document, Indexed
from pydantic import Field


class UserProfile(Document):
    customer_id: Indexed(str, unique=True)
    data: Dict[str, Any] = Field(default_factory=dict)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "user_profiles"
        indexes = ["customer_id"]
