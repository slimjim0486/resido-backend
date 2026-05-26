"""Local service providers — the Services vertical (see backend/INGESTION.md).

This is Resido's **monetization surface**: high-intent local providers (cleaning,
AC repair, handyman, movers, …) discovered via the Apify Google Maps actor,
ranked by a Bayesian trust score, and surfaced with a lead-gen CTA.

Modeled as **Tier A-style durable data**, not a churny feed: a bounded
(category × area) grid is scraped on a slow monthly TTL and the rows persist —
the opposite economics of the events feed (Tier B), which self-expires.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class ServiceProvider(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "service_providers"
    __table_args__ = (
        # Browse is always scoped to a (category, area) cell.
        Index("ix_service_providers_cat_area", "category", "area"),
        # Default ranking is score desc within a category (see migration: DESC).
        Index("ix_service_providers_cat_score", "category", "score"),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Our grid category key (cleaning, ac_repair, …), not Google's freeform label.
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    area: Mapped[str | None] = mapped_column(String(120))
    address: Mapped[str | None] = mapped_column(Text())
    lat: Mapped[float | None] = mapped_column(Float())
    lng: Mapped[float | None] = mapped_column(Float())
    rating: Mapped[float | None] = mapped_column(Float())
    reviews_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    phone: Mapped[str | None] = mapped_column(String(64))
    whatsapp: Mapped[str | None] = mapped_column(String(64))
    website: Mapped[str | None] = mapped_column(String(1024))
    maps_url: Mapped[str | None] = mapped_column(String(1024))
    # Google's stable place identifier — the upsert key (dedupes across cells).
    place_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    price_level: Mapped[str | None] = mapped_column(String(16))  # "$", "$$", …
    photo_url: Mapped[str | None] = mapped_column(String(1024))
    hours: Mapped[dict | None] = mapped_column(JSONB)  # opening hours as scraped
    # Short feature chips distilled from the scrape's `additionalInfo` (e.g.
    # "Online estimates", "Onsite services") — a lightweight "Highlights" block on
    # the provider detail. JSONB list of strings; null/empty when Google has none.
    highlights: Mapped[list | None] = mapped_column(JSONB)
    # Order the provider appeared in the Google Maps scrape (1 = top); a
    # tiebreaker behind score.
    google_rank: Mapped[int | None] = mapped_column(Integer)
    # Bayesian trust score (computed on upsert); the default sort key.
    score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    is_sponsored: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Retirement flag: a provider is soft-hidden (not deleted) once it's missed
    # ~2 monthly scrapes of its cell (see services refresh sweep). Reappearing in
    # a later scrape flips it back to True. Reads filter on it.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    source: Mapped[str] = mapped_column(String(120), default="google_maps", nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
