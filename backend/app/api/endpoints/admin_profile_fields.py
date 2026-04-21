"""Admin CRUD for ProfileFieldDefinition (configurable citizen profile fields)."""
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status

from ...auth.provider import require_admin
from ...models.profile_field_definition import (
    FieldOption,
    FieldValidation,
    ProfileFieldDefinition,
)
from ...schemas.profile import (
    ProfileFieldCreate,
    ProfileFieldReorderPayload,
    ProfileFieldResponse,
    ProfileFieldUpdate,
)

router = APIRouter(prefix="/profile-fields", tags=["Admin - Profile Fields"])


def _to_response(field: ProfileFieldDefinition) -> ProfileFieldResponse:
    return ProfileFieldResponse(
        **field.to_public_dict(),
        created_at=field.created_at,
        updated_at=field.updated_at,
    )


@router.get("", response_model=List[ProfileFieldResponse])
async def list_profile_fields(current_user: dict = Depends(require_admin)):
    fields = await ProfileFieldDefinition.find_all().sort("+order").to_list()
    return [_to_response(f) for f in fields]


@router.post("", response_model=ProfileFieldResponse, status_code=status.HTTP_201_CREATED)
async def create_profile_field(
    payload: ProfileFieldCreate,
    current_user: dict = Depends(require_admin),
):
    existing = await ProfileFieldDefinition.find_one({"field_id": payload.field_id})
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Field '{payload.field_id}' already exists",
        )

    now = datetime.utcnow()
    field = ProfileFieldDefinition(
        field_id=payload.field_id,
        label=payload.label,
        type=payload.type,
        required=payload.required,
        placeholder=payload.placeholder,
        help_text=payload.help_text,
        validation=FieldValidation(**payload.validation.dict()) if payload.validation else None,
        options=[FieldOption(**o.dict()) for o in payload.options] if payload.options else None,
        order=payload.order,
        active=True,
        created_at=now,
        updated_at=now,
    )
    await field.insert()
    return _to_response(field)


@router.put("/reorder", response_model=List[ProfileFieldResponse])
async def reorder_profile_fields(
    payload: ProfileFieldReorderPayload,
    current_user: dict = Depends(require_admin),
):
    now = datetime.utcnow()
    for item in payload.items:
        field = await ProfileFieldDefinition.find_one({"field_id": item.field_id})
        if field:
            field.order = item.order
            field.updated_at = now
            await field.save()

    fields = await ProfileFieldDefinition.find_all().sort("+order").to_list()
    return [_to_response(f) for f in fields]


@router.put("/{field_id}", response_model=ProfileFieldResponse)
async def update_profile_field(
    field_id: str,
    payload: ProfileFieldUpdate,
    current_user: dict = Depends(require_admin),
):
    field = await ProfileFieldDefinition.find_one({"field_id": field_id})
    if not field:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Field not found")

    data = payload.dict(exclude_unset=True)
    for key, value in data.items():
        if key == "validation" and value is not None:
            field.validation = FieldValidation(**value)
        elif key == "options" and value is not None:
            field.options = [FieldOption(**o) for o in value]
        else:
            setattr(field, key, value)

    field.updated_at = datetime.utcnow()
    await field.save()
    return _to_response(field)


@router.delete("/{field_id}", response_model=ProfileFieldResponse)
async def soft_delete_profile_field(
    field_id: str,
    current_user: dict = Depends(require_admin),
):
    field = await ProfileFieldDefinition.find_one({"field_id": field_id})
    if not field:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Field not found")

    field.active = False
    field.updated_at = datetime.utcnow()
    await field.save()
    return _to_response(field)
