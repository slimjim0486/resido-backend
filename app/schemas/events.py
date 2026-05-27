"""Schemas for the lifestyle 'what's on' feed (Tier B)."""

from datetime import datetime
from uuid import UUID

from app.schemas.base import ORMBaseModel


class EventOut(ORMBaseModel):
    id: UUID
    title: str
    description: str | None = None
    category: str
    venue: str | None = None
    area: str | None = None
    url: str
    image_url: str | None = None
    price_from: str | None = None
    # Lowest AED amount (0 = free, null = no price listed) — drives budget filters.
    price_min: float | None = None
    # Kid-friendly verdict (null = unknown); lets the app badge family picks.
    family_friendly: bool | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    source: str
    # Why this ranked up for the signed-in user, e.g. "In Dubai Marina" — set only
    # on a personalised feed (null otherwise). Drives the "Because you…" chip.
    reason: str | None = None
