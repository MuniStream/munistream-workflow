"""Citizen-facing endpoints for the configurable user profile."""
import re
from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status

from ...models.customer import Customer
from ...models.profile_field_definition import ProfileFieldDefinition
from ...models.user_profile import UserProfile
from ...schemas.profile import (
    ProfileFieldResponse,
    ProfileSchemaResponse,
    ProfileValuesPayload,
    ProfileValuesResponse,
)
from .public_auth import get_current_customer

router = APIRouter()


async def _load_active_schema() -> list[ProfileFieldDefinition]:
    return await ProfileFieldDefinition.find({"active": True}).sort("+order").to_list()


def _to_response(field: ProfileFieldDefinition) -> ProfileFieldResponse:
    return ProfileFieldResponse(**field.to_public_dict(), created_at=field.created_at, updated_at=field.updated_at)


def _validate_values(schema: list[ProfileFieldDefinition], data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate submitted values against the active field schema.

    Ignores unknown keys (not in schema) to avoid storing stale data.
    """
    cleaned: Dict[str, Any] = {}
    for field in schema:
        raw = data.get(field.field_id)
        is_empty = raw is None or (isinstance(raw, str) and raw.strip() == "")

        if is_empty:
            if field.required:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Field '{field.field_id}' is required",
                )
            continue

        if field.type in ("text", "email", "phone", "textarea", "date", "select") and not isinstance(raw, str):
            raw = str(raw)

        if field.type == "number":
            try:
                raw = float(raw) if not isinstance(raw, (int, float)) else raw
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Field '{field.field_id}' must be numeric",
                )

        validation = field.validation
        if validation:
            if validation.pattern and isinstance(raw, str) and not re.match(validation.pattern, raw):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Field '{field.field_id}' does not match required pattern",
                )
            if validation.min_length is not None and isinstance(raw, str) and len(raw) < validation.min_length:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Field '{field.field_id}' is too short",
                )
            if validation.max_length is not None and isinstance(raw, str) and len(raw) > validation.max_length:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Field '{field.field_id}' is too long",
                )
            if validation.min is not None and isinstance(raw, (int, float)) and raw < validation.min:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Field '{field.field_id}' is below minimum",
                )
            if validation.max is not None and isinstance(raw, (int, float)) and raw > validation.max:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Field '{field.field_id}' is above maximum",
                )

        if field.type == "select" and field.options:
            allowed = {opt.value for opt in field.options}
            if raw not in allowed:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Field '{field.field_id}' has an invalid option",
                )

        cleaned[field.field_id] = raw

    return cleaned


@router.get("/schema", response_model=ProfileSchemaResponse)
async def get_profile_schema():
    schema = await _load_active_schema()
    return ProfileSchemaResponse(fields=[_to_response(f) for f in schema])


@router.get("", response_model=ProfileValuesResponse)
async def get_my_profile(current_customer: Customer = Depends(get_current_customer)):
    profile = await UserProfile.find_one({"customer_id": str(current_customer.id)})
    if not profile:
        return ProfileValuesResponse(data={}, updated_at=None)
    return ProfileValuesResponse(data=profile.data, updated_at=profile.updated_at)


@router.put("", response_model=ProfileValuesResponse)
async def upsert_my_profile(
    payload: ProfileValuesPayload,
    current_customer: Customer = Depends(get_current_customer),
):
    schema = await _load_active_schema()
    cleaned = _validate_values(schema, payload.data)

    profile = await UserProfile.find_one({"customer_id": str(current_customer.id)})
    now = datetime.utcnow()
    if profile:
        profile.data = cleaned
        profile.updated_at = now
        await profile.save()
    else:
        profile = UserProfile(
            customer_id=str(current_customer.id),
            data=cleaned,
            created_at=now,
            updated_at=now,
        )
        await profile.insert()

    return ProfileValuesResponse(data=profile.data, updated_at=profile.updated_at)
