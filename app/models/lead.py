"""Monetization: service leads (insurance, banking, schooling, real estate, ...)."""

from uuid import UUID as UUIDType

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class Lead(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "leads"

    user_id: Mapped[UUIDType] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    vertical: Mapped[str] = mapped_column(String(80), nullable=False)  # insurance|banking|schooling|...
    status: Mapped[str] = mapped_column(String(30), default="new", nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
