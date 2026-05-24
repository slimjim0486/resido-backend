"""Schemas for the user's personal 'Dubai life' workspace."""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.base import ORMBaseModel


class ProfileUpdate(BaseModel):
    nationality: str | None = None
    emirate: str | None = None
    visa_type: str | None = None
    arrival_date: date | None = None
    employer: str | None = None
    household: dict | None = None
    notes: str | None = None


class ProfileOut(ORMBaseModel):
    id: UUID
    nationality: str | None = None
    emirate: str | None = None
    visa_type: str | None = None
    arrival_date: date | None = None
    employer: str | None = None
    household: dict = Field(default_factory=dict)
    notes: str | None = None


class ChecklistItemCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    category: str | None = None
    due_date: date | None = None
    notes: str | None = None
    source_url: str | None = None


class ChecklistItemUpdate(BaseModel):
    title: str | None = None
    category: str | None = None
    status: str | None = None
    due_date: date | None = None
    notes: str | None = None


class ChecklistItemOut(ORMBaseModel):
    id: UUID
    title: str
    category: str | None = None
    status: str
    due_date: date | None = None
    notes: str | None = None
    source_url: str | None = None
    created_at: datetime


class DeadlineCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    due_date: date
    category: str | None = None
    recurrence: str | None = None
    source_url: str | None = None


class DeadlineOut(ORMBaseModel):
    id: UUID
    title: str
    category: str | None = None
    due_date: date
    recurrence: str | None = None
    source_url: str | None = None
