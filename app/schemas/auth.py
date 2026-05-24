"""Auth request/response schemas."""

from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from app.schemas.base import ORMBaseModel


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserOut(ORMBaseModel):
    id: UUID
    email: EmailStr
    full_name: str | None = None
    is_active: bool
