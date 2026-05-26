"""Schemas for the preference memory surface ("What Resido knows about you").

Read model (``PreferenceOut``) is assembled from the PreferenceProfile in the API
layer; the request bodies drive the note CRUD + the pause control.
"""

from pydantic import BaseModel, Field


class PreferenceNoteOut(BaseModel):
    id: str
    text: str
    source: str  # explicit | chat | inferred
    confidence: float | None = None
    pinned: bool = False
    created_at: str | None = None


class PreferenceOut(BaseModel):
    """The full taste picture shown to the user — derived chips + editable notes +
    the controls. Empty maps/notes for a user with no memory yet."""

    summary: str | None = None
    personalization_paused: bool = False
    signal_count: int = 0
    typical_budget_aed: float | None = None
    family_bias: bool | None = None
    # Positive, human-facing chips (top liked areas / event categories / trades used).
    areas: list[str] = Field(default_factory=list)
    event_categories: list[str] = Field(default_factory=list)
    services_used: list[str] = Field(default_factory=list)
    notes: list[PreferenceNoteOut] = Field(default_factory=list)


class PreferenceNoteCreate(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class PreferenceNoteUpdate(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=500)
    pinned: bool | None = None


class PreferenceSettingsUpdate(BaseModel):
    personalization_paused: bool
