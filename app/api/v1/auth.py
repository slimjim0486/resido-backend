"""Authentication endpoints: anonymous sessions, email OTP, Apple, refresh, sign-out."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_active_user
from app.models.user import AuthSession, User
from app.schemas.auth import (
    AppleSignInRequest,
    AuthSessionOut,
    EmailOTPStartRequest,
    EmailOTPStartResponse,
    EmailOTPVerifyRequest,
    RefreshRequest,
    RegisterRequest,
    SignOutRequest,
    TokenResponse,
    UserOut,
)
from app.schemas.base import APIResponse
from app.services.auth import (
    AuthError,
    AuthUnavailable,
    authenticate_apple_identity,
    create_anonymous_user,
    create_email_otp,
    issue_session_tokens,
    optional_user_from_bearer,
    revoke_all_user_sessions,
    revoke_refresh_token,
    revoke_session_id,
    rotate_refresh_token,
    verify_email_otp,
)
from app.utils.jwt import decode_access_token, hash_password, verify_password


async def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[Depends(_no_store)])


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else None


def _user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent")


def _token_response(access_token: str, refresh_token: str) -> TokenResponse:
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


async def _issue_tokens(
    session: AsyncSession,
    *,
    user: User,
    request: Request,
) -> TokenResponse:
    access_token, refresh_token, _ = await issue_session_tokens(
        session,
        user=user,
        ip_address=_client_ip(request),
        user_agent=_user_agent(request),
    )
    return _token_response(access_token, refresh_token)


def _auth_error(exc: AuthError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/anonymous", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def anonymous_session(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
):
    user = await create_anonymous_user(session)
    return await _issue_tokens(session, user=user, request=request)


@router.post("/email/start", response_model=EmailOTPStartResponse)
async def start_email_otp(
    data: EmailOTPStartRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
):
    try:
        challenge, code = await create_email_otp(
            session,
            email=str(data.email),
            ip_address=_client_ip(request),
            user_agent=_user_agent(request),
        )
    except AuthUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except AuthError as exc:
        raise _auth_error(exc) from exc

    return EmailOTPStartResponse(
        challenge_id=challenge.id,
        expires_in_seconds=settings.OTP_EXPIRE_MINUTES * 60,
        resend_after_seconds=settings.OTP_RESEND_SECONDS,
        dev_code=(
            code
            if settings.ENVIRONMENT != "production"
            and not settings.RESEND_API_KEY
            and not settings.SMTP_HOST
            else None
        ),
    )


@router.post("/email/verify", response_model=TokenResponse)
async def complete_email_otp(
    data: EmailOTPVerifyRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
):
    current_user = await optional_user_from_bearer(session, authorization)
    try:
        user = await verify_email_otp(
            session,
            challenge_id=data.challenge_id,
            code=data.code,
            full_name=data.full_name,
            current_user=current_user,
        )
    except AuthError as exc:
        raise _auth_error(exc) from exc
    return await _issue_tokens(session, user=user, request=request)


@router.post("/apple", response_model=TokenResponse)
async def apple_sign_in(
    data: AppleSignInRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
):
    current_user = await optional_user_from_bearer(session, authorization)
    try:
        user = await authenticate_apple_identity(
            session,
            identity_token=data.identity_token,
            nonce=data.nonce,
            full_name=data.full_name,
            current_user=current_user,
        )
    except AuthUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except AuthError as exc:
        raise _auth_error(exc) from exc
    return await _issue_tokens(session, user=user, request=request)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_session(
    data: RefreshRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
):
    try:
        access_token, refresh_token, _, _ = await rotate_refresh_token(
            session,
            refresh_token=data.refresh_token,
            ip_address=_client_ip(request),
            user_agent=_user_agent(request),
        )
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return _token_response(access_token, refresh_token)


@router.post("/signout", response_model=APIResponse[dict])
async def sign_out(
    data: SignOutRequest,
    response: Response,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
):
    if data.all_sessions:
        await revoke_all_user_sessions(session, current_user.id)
    elif data.refresh_token:
        await revoke_refresh_token(session, data.refresh_token)
    elif authorization and authorization.lower().startswith("bearer "):
        payload = decode_access_token(authorization.split(" ", 1)[1].strip())
        if payload.get("sid"):
            await revoke_session_id(session, UUID(payload["sid"]), current_user.id)

    response.headers["Cache-Control"] = "no-store"
    response.headers["Clear-Site-Data"] = '"cache", "storage"'
    return APIResponse(data={"signed_out": True})


@router.get("/sessions", response_model=APIResponse[list[AuthSessionOut]])
async def list_sessions(
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    result = await session.execute(
        select(AuthSession)
        .where(
            AuthSession.user_id == current_user.id,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > func.now(),
        )
        .order_by(AuthSession.created_at.desc())
    )
    return APIResponse(data=[AuthSessionOut.model_validate(row) for row in result.scalars()])


# Legacy password endpoints kept for local/dev compatibility while mobile moves
# to email OTP + Apple. Password sign-ins still receive revocable server sessions.
@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    data: RegisterRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
):
    existing = await session.execute(select(User).where(User.email == str(data.email).lower()))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(
        email=str(data.email).lower(),
        hashed_password=hash_password(data.password),
        full_name=data.full_name,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return await _issue_tokens(session, user=user, request=request)


@router.post("/login", response_model=TokenResponse)
async def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
):
    result = await session.execute(select(User).where(User.email == form.username.lower()))
    user = result.scalar_one_or_none()
    if user is None or not user.hashed_password or not verify_password(
        form.password, user.hashed_password
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password"
        )
    return await _issue_tokens(session, user=user, request=request)


@router.get("/me", response_model=APIResponse[UserOut])
async def account(current_user: Annotated[User, Depends(get_current_active_user)]):
    return APIResponse(data=UserOut.model_validate(current_user))
