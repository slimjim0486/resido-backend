"""Public lifestyle 'what's on' feed — Tier B (see backend/INGESTION.md).

Optional auth: this is content, not personal data, so the Home hero loads it
without a session. The default public feed stays strict newest-scrape first; a
caller can opt into taste re-ranking when it wants a personalised listing.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_optional_user
from app.models.user import User
from app.schemas.base import APIResponse
from app.schemas.events import EventOut
from app.services import events as events_service
from app.services import preferences as preferences_service

router = APIRouter(prefix="/events", tags=["events"])

Session = Annotated[AsyncSession, Depends(get_db)]
OptionalUser = Annotated[User | None, Depends(get_optional_user)]


@router.get("", response_model=APIResponse[list[EventOut]])
async def list_events(
    session: Session,
    user: OptionalUser,
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
    personalized: bool = Query(default=False, description="Opt into taste-based re-ranking"),
):
    """The public lifestyle feed. With no filters it's the latest-scraped 'what's on'
    list; the same filters power the agent's `find_events` so both stay in
    lockstep. Personalisation is opt-in so Home/Explore remain newest-scraped
    by default."""
    personalize = (
        await preferences_service.get_preference_profile(session, user.id)
        if personalized and user
        else None
    )
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
        personalize=personalize,
    )
    out = []
    for e in items:
        eo = EventOut.model_validate(e)
        eo.reason = preferences_service.explain_event(personalize, e)
        out.append(eo)
    return APIResponse(data=out)
