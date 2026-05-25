"""Saved (liked) events & service providers.

A polymorphic reference, *not* a snapshot: we store only (item_type, item_id),
never a copy of the event/provider. There is intentionally **no FK** to
`events` / `service_providers` — events self-expire and providers soft-delete,
so a content FK would either cascade-delete a user's saves or block ingestion.
On read we join to the live row and drop anything that no longer resolves.
"""

from uuid import UUID as UUIDType

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class Favorite(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "favorites"
    __table_args__ = (
        UniqueConstraint("user_id", "item_type", "item_id", name="uq_favorites_user_item"),
        Index("ix_favorites_user", "user_id"),
    )

    user_id: Mapped[UUIDType] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    item_type: Mapped[str] = mapped_column(String(20), nullable=False)  # 'event' | 'service'
    # The event/provider UUID. Deliberately not a FK (see module docstring).
    item_id: Mapped[UUIDType] = mapped_column(PGUUID(as_uuid=True), nullable=False)
