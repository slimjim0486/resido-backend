"""Schemas for monetization leads (callback/quote requests)."""

from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.base import ORMBaseModel


class LeadCreate(BaseModel):
    # A service category (cleaning, ac_repair, …) or high-value vertical
    # (insurance, banking, …). Free-form so the same endpoint serves both.
    vertical: str = Field(min_length=1, max_length=80)
    # Arbitrary context: provider_name, area, contact_preference, notes, …
    payload: dict = Field(default_factory=dict)


class LeadOut(ORMBaseModel):
    id: UUID
    vertical: str
    status: str
