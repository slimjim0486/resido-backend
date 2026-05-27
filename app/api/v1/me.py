"""Personal workspace endpoints: profile, checklist, deadlines."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_active_user
from app.models.user import User
from app.schemas.base import APIResponse
from app.schemas.events import EventOut
from app.schemas.favorites import (
    FavoriteCreate,
    FavoriteIdsOut,
    FavoritesOut,
    FavoriteToggleOut,
)
from app.schemas.preferences import (
    PreferenceNoteCreate,
    PreferenceNoteOut,
    PreferenceNoteUpdate,
    PreferenceOut,
    PreferenceSettingsUpdate,
    SignalIn,
)
from app.schemas.services import ServiceProviderOut
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
from app.models.preference import PreferenceProfile
from app.services import preferences, workspace

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


# ─── Favorites / saved items ─────────────────────────────────────────────────
@router.get("/favorites", response_model=APIResponse[FavoritesOut])
async def get_favorites(current_user: CurrentUser, session: Session):
    """Saved events & providers, resolved to their live rows (newest first)."""
    events, services = await workspace.list_favorites(session, current_user.id)
    return APIResponse(
        data=FavoritesOut(
            events=[EventOut.model_validate(e) for e in events],
            services=[ServiceProviderOut.model_validate(s) for s in services],
        )
    )


@router.get("/favorites/ids", response_model=APIResponse[FavoriteIdsOut])
async def get_favorite_ids(current_user: CurrentUser, session: Session):
    """Just the saved ids — cheap hydration of the heart-toggle state."""
    ids = await workspace.list_favorite_ids(session, current_user.id)
    return APIResponse(data=FavoriteIdsOut(events=ids["event"], services=ids["service"]))


@router.post(
    "/favorites", response_model=APIResponse[FavoriteToggleOut], status_code=status.HTTP_201_CREATED
)
async def add_favorite(data: FavoriteCreate, current_user: CurrentUser, session: Session):
    favorited = await workspace.add_favorite(
        session, current_user.id, item_type=data.item_type, item_id=data.item_id
    )
    return APIResponse(data=FavoriteToggleOut(favorited=favorited), message="Saved")


@router.delete("/favorites/{item_type}/{item_id}", response_model=APIResponse[FavoriteToggleOut])
async def remove_favorite(
    item_type: str, item_id: UUID, current_user: CurrentUser, session: Session
):
    favorited = await workspace.remove_favorite(
        session, current_user.id, item_type=item_type, item_id=item_id
    )
    return APIResponse(data=FavoriteToggleOut(favorited=favorited), message="Removed")


# ─── Preference memory: "What Resido knows about you" (see PERSONALIZATION.md) ───
def _preference_out(profile: PreferenceProfile) -> PreferenceOut:
    chips = preferences.derived_chips(profile)
    return PreferenceOut(
        summary=profile.summary,
        personalization_paused=profile.personalization_paused,
        signal_count=profile.signal_count,
        typical_budget_aed=profile.typical_budget_aed,
        family_bias=profile.family_bias,
        areas=chips["areas"],
        event_categories=chips["event_categories"],
        services_used=chips["services_used"],
        notes=[PreferenceNoteOut(**n) for n in (profile.notes or [])],
    )


@router.get("/preferences", response_model=APIResponse[PreferenceOut])
async def get_preferences(current_user: CurrentUser, session: Session):
    """The taste graph the co-pilot has built — derived chips, editable notes, and
    the pause state. Everything here is user-visible and user-editable by design."""
    profile = await preferences.get_or_create_preference_profile(session, current_user.id)
    return APIResponse(data=_preference_out(profile))


@router.post(
    "/preferences/notes", response_model=APIResponse[PreferenceOut], status_code=status.HTTP_201_CREATED
)
async def add_preference_note(
    data: PreferenceNoteCreate, current_user: CurrentUser, session: Session
):
    profile = await preferences.add_preference_note(
        session, current_user.id, text=data.text, source="explicit"
    )
    return APIResponse(data=_preference_out(profile), message="Remembered")


@router.patch("/preferences/notes/{note_id}", response_model=APIResponse[PreferenceOut])
async def patch_preference_note(
    note_id: str, data: PreferenceNoteUpdate, current_user: CurrentUser, session: Session
):
    profile = await preferences.update_preference_note(
        session, current_user.id, note_id, data.model_dump(exclude_unset=True)
    )
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")
    return APIResponse(data=_preference_out(profile))


@router.delete("/preferences/notes/{note_id}", response_model=APIResponse[PreferenceOut])
async def delete_preference_note(note_id: str, current_user: CurrentUser, session: Session):
    profile = await preferences.delete_preference_note(session, current_user.id, note_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")
    return APIResponse(data=_preference_out(profile), message="Forgotten")


@router.patch("/preferences", response_model=APIResponse[PreferenceOut])
async def update_preference_settings(
    data: PreferenceSettingsUpdate, current_user: CurrentUser, session: Session
):
    """Toggle the personalisation kill switch (taste is kept, just ignored while paused)."""
    profile = await preferences.set_personalization_paused(
        session, current_user.id, data.personalization_paused
    )
    msg = "Personalization paused" if profile.personalization_paused else "Personalization on"
    return APIResponse(data=_preference_out(profile), message=msg)


@router.post("/preferences/reset", response_model=APIResponse[PreferenceOut])
async def reset_preferences(current_user: CurrentUser, session: Session):
    """Forget everything — drops all signals and blanks the profile to a clean slate."""
    profile = await preferences.reset_preferences(session, current_user.id)
    return APIResponse(data=_preference_out(profile), message="Memory cleared")


@router.post(
    "/signals", response_model=APIResponse[None], status_code=status.HTTP_202_ACCEPTED
)
async def record_signal(data: SignalIn, current_user: CurrentUser, session: Session):
    """Report a lightweight interaction (the client fires this when a detail screen
    opens). Feeds the taste graph; best-effort, so it never surfaces an error."""
    await preferences.capture_interaction(
        session,
        current_user.id,
        kind=data.kind,
        item_type=data.item_type,
        item_id=data.item_id,
    )
    return APIResponse(data=None, message="ok")
