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
    score: float
    is_sponsored: bool
    source: str
