"""Personal 'Dubai life' checklist items (agent can create/edit these)."""

from datetime import date
from uuid import UUID as UUIDType

from sqlalchemy import Date, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class ChecklistItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "checklist_items"
    __table_args__ = (Index("ix_checklist_items_user_status", "user_id", "status"),)

    user_id: Mapped[UUIDType] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default="todo", nullable=False)  # todo|doing|done
    due_date: Mapped[date | None] = mapped_column(Date())
    notes: Mapped[str | None] = mapped_column(Text())
    source_url: Mapped[str | None] = mapped_column(String(1024))
