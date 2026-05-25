"""Tracked renewals (visa, EID, tenancy, insurance, …). See DOCUMENTS.md.

This is a *renewal radar*, not a vault: we store the expiry date and type, never
the document itself. `file_url` is intentionally left unpopulated — do not wire
it up without an explicit decision to change the privacy posture.
"""

from datetime import date
from uuid import UUID as UUIDType

from sqlalchemy import Date, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class Document(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (Index("ix_documents_user_expiry", "user_id", "expiry_date"),)

    user_id: Mapped[UUIDType] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    doc_type: Mapped[str | None] = mapped_column(String(80))  # key into renewals.RENEWAL_TYPES
    expiry_date: Mapped[date | None] = mapped_column(Date())
    # "confirmed" = user-stated; "estimated" = derived (e.g. visa-anchor cascade).
    confidence: Mapped[str] = mapped_column(String(20), default="confirmed", nullable=False)
    # Reserved; intentionally unused — we are not a document vault (see DOCUMENTS.md).
    file_url: Mapped[str | None] = mapped_column(String(1024))
    # Dedupe key for the future reminder job; lead-time itself is derived from the catalog.
    last_reminded_on: Mapped[date | None] = mapped_column(Date())
    notes: Mapped[str | None] = mapped_column(Text())
