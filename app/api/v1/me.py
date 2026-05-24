"""Personal workspace endpoints: profile, checklist, deadlines."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_active_user
from app.models.user import User
from app.schemas.base import APIResponse
from app.schemas.workspace import (
    ChecklistItemCreate,
    ChecklistItemOut,
    ChecklistItemUpdate,
    DeadlineCreate,
    DeadlineOut,
    ProfileOut,
    ProfileUpdate,
)
from app.services import workspace

router = APIRouter(prefix="/me", tags=["me"])

CurrentUser = Annotated[User, Depends(get_current_active_user)]
Session = Annotated[AsyncSession, Depends(get_db)]


@router.get("/profile", response_model=APIResponse[ProfileOut])
async def get_profile(current_user: CurrentUser, session: Session):
    profile = await workspace.get_or_create_profile(session, current_user.id)
    return APIResponse(data=ProfileOut.model_validate(profile))


@router.put("/profile", response_model=APIResponse[ProfileOut])
async def update_profile(data: ProfileUpdate, current_user: CurrentUser, session: Session):
    profile = await workspace.update_profile(
        session, current_user.id, data.model_dump(exclude_unset=True)
    )
    return APIResponse(data=ProfileOut.model_validate(profile), message="Profile updated")


@router.get("/checklist", response_model=APIResponse[list[ChecklistItemOut]])
async def get_checklist(current_user: CurrentUser, session: Session):
    items = await workspace.list_checklist(session, current_user.id)
    return APIResponse(data=[ChecklistItemOut.model_validate(i) for i in items])


@router.post(
    "/checklist", response_model=APIResponse[ChecklistItemOut], status_code=status.HTTP_201_CREATED
)
async def create_checklist_item(data: ChecklistItemCreate, current_user: CurrentUser, session: Session):
    item = await workspace.add_checklist_item(
        session,
        current_user.id,
        title=data.title,
        category=data.category,
        due_date=data.due_date,
        notes=data.notes,
        source_url=data.source_url,
    )
    return APIResponse(data=ChecklistItemOut.model_validate(item), message="Added to your checklist")


@router.patch("/checklist/{item_id}", response_model=APIResponse[ChecklistItemOut])
async def patch_checklist_item(
    item_id: UUID, data: ChecklistItemUpdate, current_user: CurrentUser, session: Session
):
    item = await workspace.update_checklist_item(
        session, current_user.id, item_id, data.model_dump(exclude_unset=True)
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    return APIResponse(data=ChecklistItemOut.model_validate(item))


@router.get("/deadlines", response_model=APIResponse[list[DeadlineOut]])
async def get_deadlines(current_user: CurrentUser, session: Session):
    items = await workspace.list_deadlines(session, current_user.id)
    return APIResponse(data=[DeadlineOut.model_validate(d) for d in items])


@router.post(
    "/deadlines", response_model=APIResponse[DeadlineOut], status_code=status.HTTP_201_CREATED
)
async def create_deadline(data: DeadlineCreate, current_user: CurrentUser, session: Session):
    deadline = await workspace.add_deadline(
        session,
        current_user.id,
        title=data.title,
        due_date=data.due_date,
        category=data.category,
        recurrence=data.recurrence,
        source_url=data.source_url,
    )
    return APIResponse(data=DeadlineOut.model_validate(deadline), message="Reminder set")
