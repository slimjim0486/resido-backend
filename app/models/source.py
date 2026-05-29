"""Authoritative source registry that the ingestion pipeline crawls."""

from datetime import datetime
from uuid import UUID as UUIDType  # noqa: F401  (kept for symmetry / future FKs)

from sqlalchemy import DateTime, Integer, String, Text
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
    # sha256 of the last-ingested page text; lets refresh skip re-embedding when
    # a refetched page is byte-identical (the main recurring-cost lever).
    content_hash: Mapped[str | None] = mapped_column(String(64))
    # Failed pages should not monopolize every cron run. The refresh worker uses
    # these to apply a short exponential backoff while keeping last_fetched_at as
    # the last successful ingest time.
    last_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text())
