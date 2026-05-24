"""Stored document metadata (visa, EID, tenancy, insurance cards, etc.)."""

from datetime import date
from uuid import UUID as UUIDType

from sqlalchemy import Date, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class Document(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "documents"

    user_id: Mapped[UUIDType] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    doc_type: Mapped[str | None] = mapped_column(String(80))
    expiry_date: Mapped[date | None] = mapped_column(Date())
    file_url: Mapped[str | None] = mapped_column(String(1024))
    notes: Mapped[str | None] = mapped_column(Text())
