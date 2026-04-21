"""Pydantic schemas for profile field definitions and user profile values."""
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

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


class FieldValidationPayload(BaseModel):
    pattern: Optional[str] = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    min: Optional[float] = None
    max: Optional[float] = None


class FieldOptionPayload(BaseModel):
    value: str
    label: str


class ProfileFieldCreate(BaseModel):
    field_id: str = Field(..., pattern=r"^[a-z][a-z0-9_]*$", min_length=1, max_length=64)
    label: str = Field(..., min_length=1, max_length=200)
    type: FieldType = "text"
    required: bool = False
    placeholder: Optional[str] = None
    help_text: Optional[str] = None
    validation: Optional[FieldValidationPayload] = None
    options: Optional[List[FieldOptionPayload]] = None
    order: int = 0


class ProfileFieldUpdate(BaseModel):
    label: Optional[str] = Field(default=None, min_length=1, max_length=200)
    type: Optional[FieldType] = None
    required: Optional[bool] = None
    placeholder: Optional[str] = None
    help_text: Optional[str] = None
    validation: Optional[FieldValidationPayload] = None
    options: Optional[List[FieldOptionPayload]] = None
    order: Optional[int] = None
    active: Optional[bool] = None


class ProfileFieldResponse(BaseModel):
    field_id: str
    label: str
    type: FieldType
    required: bool
    placeholder: Optional[str] = None
    help_text: Optional[str] = None
    validation: Optional[FieldValidationPayload] = None
    options: Optional[List[FieldOptionPayload]] = None
    order: int
    active: bool
    created_at: datetime
    updated_at: datetime


class ProfileFieldReorderItem(BaseModel):
    field_id: str
    order: int


class ProfileFieldReorderPayload(BaseModel):
    items: List[ProfileFieldReorderItem]


class ProfileSchemaResponse(BaseModel):
    fields: List[ProfileFieldResponse]


class ProfileValuesPayload(BaseModel):
    data: Dict[str, Any]


class ProfileValuesResponse(BaseModel):
    data: Dict[str, Any]
    updated_at: Optional[datetime] = None
