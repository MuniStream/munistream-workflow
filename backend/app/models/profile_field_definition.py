"""Configurable profile fields per tenant."""
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from beanie import Document, Indexed
from pydantic import BaseModel, Field


FieldType = Literal[
    "text",
    "email",
    "phone",
    "textarea",
    "date",
    "number",
    "select",
]


class FieldValidation(BaseModel):
    pattern: Optional[str] = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    min: Optional[float] = None
    max: Optional[float] = None


class FieldOption(BaseModel):
    value: str
    label: str


class ProfileFieldDefinition(Document):
    field_id: Indexed(str, unique=True)
    label: str
    type: FieldType = "text"
    required: bool = False
    placeholder: Optional[str] = None
    help_text: Optional[str] = None
    validation: Optional[FieldValidation] = None
    options: Optional[List[FieldOption]] = None
    order: int = 0
    active: bool = True

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "profile_field_definitions"
        indexes = ["field_id", "active", "order"]

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "field_id": self.field_id,
            "label": self.label,
            "type": self.type,
            "required": self.required,
            "placeholder": self.placeholder,
            "help_text": self.help_text,
            "validation": self.validation.dict(exclude_none=True) if self.validation else None,
            "options": [o.dict() for o in self.options] if self.options else None,
            "order": self.order,
            "active": self.active,
        }
