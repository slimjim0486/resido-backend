"""User profile + household context that personalizes the agent."""

from datetime import date
from uuid import UUID as UUIDType

from sqlalchemy import Date, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class Profile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "profiles"

    user_id: Mapped[UUIDType] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    nationality: Mapped[str | None] = mapped_column(String(100))
    emirate: Mapped[str | None] = mapped_column(String(100), default="Dubai")
    visa_type: Mapped[str | None] = mapped_column(String(100))
    arrival_date: Mapped[date | None] = mapped_column(Date())
    employer: Mapped[str | None] = mapped_column(String(255))
    # Flexible household context: {"adults": 2, "children": [{"age": 5}], "has_car": true, ...}
    household: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    notes: Mapped[str | None] = mapped_column(String(2000))

    user: Mapped["User"] = relationship("User", back_populates="profile")
