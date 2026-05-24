"""Authentication endpoints: register, login (OAuth2 password flow), account info."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_active_user
from app.models.user import User
from app.schemas.auth import RegisterRequest, TokenResponse, UserOut
from app.schemas.base import APIResponse
from app.utils.jwt import (
    create_access_token,
    create_refresh_token,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _issue_tokens(user: User) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token({"sub": str(user.id)}),
        refresh_token=create_refresh_token(str(user.id)),
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    data: RegisterRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
):
    existing = await session.execute(select(User).where(User.email == data.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(
        email=data.email,
        hashed_password=hash_password(data.password),
        full_name=data.full_name,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return _issue_tokens(user)


@router.post("/login", response_model=TokenResponse)
async def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    # OAuth2 form sends the email in the `username` field.
    result = await session.execute(select(User).where(User.email == form.username))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(form.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password"
        )
    return _issue_tokens(user)


@router.get("/me", response_model=APIResponse[UserOut])
async def account(current_user: Annotated[User, Depends(get_current_active_user)]):
    return APIResponse(data=UserOut.model_validate(current_user))
