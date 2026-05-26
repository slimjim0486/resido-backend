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


class QuoteDraftRequest(BaseModel):
    """The Services "Get quotes" flow: which provider + what the user needs."""

    provider_id: UUID
    need: str = Field(min_length=1, max_length=500)
    when_pref: str | None = Field(default=None, max_length=120)
    budget_aed: int | None = Field(default=None, ge=0, le=1_000_000)
    # Passed from the client's local/account name so the draft can be signed; the
    # backend Profile has no name field. Optional — the draft works without it.
    customer_name: str | None = Field(default=None, max_length=120)


class QuoteDraftOut(BaseModel):
    """A drafted message the user sends themselves (one tap to WhatsApp/SMS)."""

    lead_id: UUID
    message: str
    channel: str  # "whatsapp" | "sms" | "none"
    # Digits-only number the app builds the wa.me / sms: deep link from; null when
    # the provider has no reachable channel (UI falls back to copy-to-clipboard).
    to_number: str | None = None
