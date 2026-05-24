"""Knowledge-base chunk with a pgvector embedding for RAG retrieval.

Every chunk carries its source URL + fetched_at so the agent can cite it with a
last-verified date.
"""

from datetime import datetime
from uuid import UUID as UUIDType

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.config import settings
from app.database import Base
from app.models.base import UUIDPrimaryKeyMixin


class KBChunk(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "kb_chunks"
    __table_args__ = (Index("ix_kb_chunks_category", "category"),)

    source_id: Mapped[UUIDType | None] = mapped_column(
        ForeignKey("sources.id", ondelete="SET NULL")
    )
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str | None] = mapped_column(String(512))
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    content: Mapped[str] = mapped_column(Text(), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(settings.EMBEDDING_DIM))
    token_count: Mapped[int | None] = mapped_column(Integer)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
