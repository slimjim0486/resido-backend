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
    DocumentCreate,
    DocumentOut,
    DocumentUpdate,
    ProfileOut,
    ProfileUpdate,
    VisaAnchorIn,
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


# ─── Renewals / tracked documents (see DOCUMENTS.md) ─────────────────────────
@router.get("/documents", response_model=APIResponse[list[DocumentOut]])
async def get_documents(current_user: CurrentUser, session: Session):
    docs = await workspace.list_documents(session, current_user.id)
    return APIResponse(data=[DocumentOut.model_validate(d) for d in docs])


@router.post(
    "/documents", response_model=APIResponse[DocumentOut], status_code=status.HTTP_201_CREATED
)
async def create_document(data: DocumentCreate, current_user: CurrentUser, session: Session):
    doc = await workspace.add_document(
        session,
        current_user.id,
        doc_type=data.doc_type,
        expiry_date=data.expiry_date,
        confidence=data.confidence or "confirmed",
        title=data.title,
        notes=data.notes,
    )
    return APIResponse(data=DocumentOut.model_validate(doc), message="Tracking this renewal")


@router.patch("/documents/{document_id}", response_model=APIResponse[DocumentOut])
async def patch_document(
    document_id: UUID, data: DocumentUpdate, current_user: CurrentUser, session: Session
):
    doc = await workspace.update_document(
        session, current_user.id, document_id, data.model_dump(exclude_unset=True)
    )
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return APIResponse(data=DocumentOut.model_validate(doc))


@router.delete("/documents/{document_id}", response_model=APIResponse[None])
async def delete_document(document_id: UUID, current_user: CurrentUser, session: Session):
    ok = await workspace.delete_document(session, current_user.id, document_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return APIResponse(data=None, message="Stopped tracking")


@router.post("/visa-anchor", response_model=APIResponse[list[DocumentOut]])
async def set_visa_anchor(data: VisaAnchorIn, current_user: CurrentUser, session: Session):
    """One visa expiry date → the visa cluster (visa + EID + health insurance)."""
    cluster = await workspace.apply_visa_anchor(
        session, current_user.id, expiry_date=data.expiry_date
    )
    return APIResponse(
        data=[DocumentOut.model_validate(d) for d in cluster],
        message="Set up your visa renewals",
    )
