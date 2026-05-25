"""Auth request/response schemas."""

from datetime import datetime
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
    expires_in: int


class UserOut(ORMBaseModel):
    id: UUID
    email: EmailStr | None = None
    full_name: str | None = None
    is_active: bool
    is_anonymous: bool = False
    email_verified_at: datetime | None = None


class EmailOTPStartRequest(BaseModel):
    email: EmailStr


class EmailOTPStartResponse(BaseModel):
    challenge_id: UUID
    expires_in_seconds: int
    resend_after_seconds: int
    dev_code: str | None = None


class EmailOTPVerifyRequest(BaseModel):
    challenge_id: UUID
    code: str = Field(min_length=6, max_length=12)
    full_name: str | None = Field(default=None, max_length=255)


class AppleSignInRequest(BaseModel):
    identity_token: str = Field(min_length=20)
    nonce: str | None = Field(default=None, max_length=255)
    full_name: str | None = Field(default=None, max_length=255)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=32)


class SignOutRequest(BaseModel):
    refresh_token: str | None = Field(default=None, min_length=32)
    all_sessions: bool = False


class AuthSessionOut(ORMBaseModel):
    id: UUID
    user_agent: str | None = None
    ip_address: str | None = None
    expires_at: datetime
    created_at: datetime
    last_seen_at: datetime | None = None
