"""Public lifestyle 'what's on' feed — Tier B (see backend/INGESTION.md).

No auth: this is content, not personal data, so the Home hero can load it
without provisioning a session.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.base import APIResponse
from app.schemas.events import EventOut
from app.services import events as events_service

router = APIRouter(prefix="/events", tags=["events"])

Session = Annotated[AsyncSession, Depends(get_db)]


@router.get("", response_model=APIResponse[list[EventOut]])
async def list_events(
    session: Session,
    category: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
):
    items = await events_service.list_active_events(session, category=category, limit=limit)
    return APIResponse(data=[EventOut.model_validate(e) for e in items])
