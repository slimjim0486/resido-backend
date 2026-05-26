"""FastAPI dependencies: DB session + current-user resolution (JWT)."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import AuthSession, User
from app.services.auth import now_utc, optional_user_from_bearer
from app.utils.jwt import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

CREDENTIALS_EXC = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    try:
        payload = decode_access_token(token)
        user_id = payload.get("sub")
        if not user_id:
            raise CREDENTIALS_EXC
        user = await session.get(User, UUID(user_id))
        session_id = payload.get("sid")
        if session_id:
            result = await session.execute(
                select(AuthSession).where(
                    AuthSession.id == UUID(session_id),
                    AuthSession.user_id == UUID(user_id),
                )
            )
            auth_session = result.scalar_one_or_none()
            if (
                auth_session is None
                or auth_session.revoked_at is not None
                or auth_session.expires_at <= now_utc()
            ):
                raise CREDENTIALS_EXC
    except (ValueError, KeyError):
        raise CREDENTIALS_EXC
    if user is None:
        raise CREDENTIALS_EXC
    return user


async def get_current_active_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    if not current_user.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Inactive user")
    return current_user


async def get_optional_user(
    session: Annotated[AsyncSession, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> User | None:
    """Resolve the bearer token if one is present, else None — never raises.

    For endpoints that are public (events/services feeds) but personalise when the
    caller is signed in. The Flutter client always attaches its guest token, so a
    request from the app resolves to a user; a logged-out/anonymous caller doesn't."""
    return await optional_user_from_bearer(session, authorization)
