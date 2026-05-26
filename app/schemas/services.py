"""Schemas for the Services vertical (ranked local providers)."""

from datetime import datetime
from uuid import UUID

from app.schemas.base import ORMBaseModel


class ServiceProviderOut(ORMBaseModel):
    id: UUID
    name: str
    category: str
    area: str | None = None
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    rating: float | None = None
    reviews_count: int
    phone: str | None = None
    whatsapp: str | None = None
    website: str | None = None
    maps_url: str | None = None
    price_level: str | None = None
    photo_url: str | None = None
    hours: list | dict | None = None
    highlights: list[str] | None = None
    score: float
    is_sponsored: bool
    source: str
    # When this row was last seen in a scrape — drives the "Updated <month>"
    # freshness signal in the app (turns age into a trust cue, per INGESTION.md).
    fetched_at: datetime
