"""Lifestyle 'what's on' events — Tier B feed (see backend/INGESTION.md).

Structured, self-expiring rows served straight to the Home 'What's on' hero.
Deliberately **not** embedded into pgvector: events are time-bound, not a Q&A
corpus, so RAG would be wasted spend that goes stale.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class Event(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_live", "is_published", "expires_at"),
        Index("ix_events_category", "category"),
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text())
    category: Mapped[str] = mapped_column(String(80), default="events", nullable=False)
    venue: Mapped[str | None] = mapped_column(String(255))
    area: Mapped[str | None] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False)
    image_url: Mapped[str | None] = mapped_column(String(1024))
    # Display price as the source states it ("Free", "AED 95", "From AED 250").
    price_from: Mapped[str | None] = mapped_column(String(80))
    # The lowest AED amount parsed from `price_from` (0.0 = free), so the feed can
    # answer "under AED 500" queries. NULL = no price listed (≠ free); ingestion
    # derives it via app.core.pricing.parse_aed alongside the display string.
    price_min: Mapped[float | None] = mapped_column(Numeric(10, 2))
    # Whether this is something to bring kids to. Derived at ingestion (category,
    # keyword scan, or Claude extraction). NULL = unknown — only TRUE rows match a
    # "kid-friendly" filter, so unknowns aren't falsely promoted or excluded.
    family_friendly: Mapped[bool | None] = mapped_column(Boolean)
    # Free-form taste tags (cuisine, vibe, genre — "brunch", "jazz", "rooftop",
    # "vegetarian-friendly") extracted by Claude at ingestion. Not for filtering;
    # feeds the personalisation tag_weights so "you like jazz" can rank up. NULL/[]
    # = none (older rows until re-ingested) — degrades to no tag signal.
    tags: Mapped[list | None] = mapped_column(JSONB)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(120), nullable=False)
    # When to stop surfacing this row; the feed query filters on it so events
    # self-expire without a cleanup job.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_published: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
