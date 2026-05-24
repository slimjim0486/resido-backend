"""Authoritative source registry that the ingestion pipeline crawls."""

from datetime import datetime
from uuid import UUID as UUIDType  # noqa: F401  (kept for symmetry / future FKs)

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class Source(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sources"

    url: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(512))
    publisher: Mapped[str | None] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refresh_ttl_days: Mapped[int] = mapped_column(Integer, default=14, nullable=False)
