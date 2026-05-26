"""Public lifestyle 'what's on' feed — Tier B (see backend/INGESTION.md).

No auth: this is content, not personal data, so the Home hero can load it
without provisioning a session.
"""

from datetime import date
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
    q: str | None = Query(default=None, description="Keyword search over title/description/venue"),
    category: str | None = Query(default=None),
    area: str | None = Query(default=None),
    family_friendly: bool | None = Query(default=None),
    max_price: float | None = Query(default=None, ge=0, description="AED budget ceiling"),
    free: bool = Query(default=False, description="Only free events"),
    weekday: str | None = Query(default=None, description="Day name, e.g. 'friday'"),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
):
    """The public lifestyle feed. With no filters it's the plain 'what's on' list;
    the same filters power the agent's `find_events` so both stay in lockstep."""
    items = await events_service.search_events(
        session,
        query=q,
        category=category,
        area=area,
        family_friendly=family_friendly,
        max_price=max_price,
        free_only=free,
        weekday=weekday,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )
    return APIResponse(data=[EventOut.model_validate(e) for e in items])
