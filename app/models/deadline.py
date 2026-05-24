"""Reminders / deadlines (visa & EID renewals, fines, school terms, etc.)."""

from datetime import date
from uuid import UUID as UUIDType

from sqlalchemy import Boolean, Date, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class Deadline(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "deadlines"
    __table_args__ = (Index("ix_deadlines_user_due", "user_id", "due_date"),)

    user_id: Mapped[UUIDType] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(80))
    due_date: Mapped[date] = mapped_column(Date(), nullable=False)
    recurrence: Mapped[str | None] = mapped_column(String(40))  # none|yearly|monthly
    reminder_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(1024))
