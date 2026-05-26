"""Preference memory — the co-pilot's 'taste graph'. See backend/PERSONALIZATION.md.

Two user-scoped tables, deliberately separate from `profiles` (durable identity
facts) so taste can be reset/paused without touching who the user is:

- ``preference_signals`` — an append-only behavioural log (favorited, requested a
  lead, viewed, dismissed). We **denormalise the item's preference-relevant
  attributes into ``attrs``** at capture time rather than relying on ``item_id``:
  events self-expire and providers soft-delete (same reason favorites carry no
  content FK), but the *taste* a signal represents must outlive the item.
- ``preference_profile`` — the 1:1 distilled view that actually gets read: the
  time-decayed weight maps the ranker uses, plus a human summary and editable
  notes. Derived from the signals (rules in Phase 1; Haiku in Phase 2).
"""

from datetime import datetime
from uuid import UUID as UUIDType

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class PreferenceSignal(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "preference_signals"
    __table_args__ = (
        Index("ix_preference_signals_user_created", "user_id", "created_at"),
        Index("ix_preference_signals_user_domain", "user_id", "domain"),
    )

    user_id: Mapped[UUIDType] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # favorite | unfavorite | lead | view | dismiss | contact | search
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    # 'event' | 'service'
    domain: Mapped[str] = mapped_column(String(20), nullable=False)
    # The event/provider UUID when applicable — NOT a FK (content churns; see the
    # favorites model docstring). May be null for search/intent-only signals.
    item_id: Mapped[UUIDType | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    # Denormalised snapshot of the preference-relevant attributes at capture time:
    # {category, area, price_min, family_friendly, weekday, tags, query}.
    attrs: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    # Base strength of the signal before time decay (lead > favorite > view; a
    # dismiss/unfavorite is negative). The recompute applies the decay.
    weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)


class PreferenceProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "preference_profile"

    user_id: Mapped[UUIDType] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    # ── Machine-usable weight maps (rules-derived, normalised ~[-1, 1]) ──
    # {area: weight} — applies across both events and services.
    area_weights: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    # {event_category: weight} — dining/family/nightlife/…
    event_category_weights: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    # {service_category: weight} — cleaning/ac_repair/… (which trades they actually use)
    service_category_affinity: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    # {tag: weight} — free-form tastes (jazz, brunch, vegetarian). Populated in
    # Phase 3 once events carry tags; kept here so the schema is stable.
    tag_weights: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    # {dow: weight} — Sunday=0 … Saturday=6, when they tend to go out.
    weekday_weights: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    # Typical AED budget inferred from liked/requested items (None = unknown).
    typical_budget_aed: Mapped[float | None] = mapped_column(Float, nullable=True)
    # True/False = known kid-friendly lean; None = unknown (never false-promotes).
    family_bias: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # ── Human-readable layer (templated in Phase 1, Haiku-distilled in Phase 2) ──
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # List of {id, text, source: inferred|explicit|chat, confidence, pinned} — the
    # editable bullets shown in the "What Resido knows about you" surface.
    notes: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    # ── Bookkeeping ──
    # Number of signals behind this profile — the ranker's confidence proxy; it
    # scales how strongly personalisation is applied (Bayesian-style shrinkage).
    signal_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # User kill-switch: when true the ranker ignores this profile entirely.
    personalization_paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Last time the Haiku distillation ran (None until Phase 2 / no AI key).
    distilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
