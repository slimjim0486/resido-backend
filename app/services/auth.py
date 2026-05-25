"""Passwordless auth services: OTP, Apple identity, and refresh sessions."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from uuid import UUID, uuid4

import httpx
import jwt
from jwt import InvalidTokenError, PyJWKClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.models.user import AuthIdentity, AuthSession, EmailOTPChallenge, User
from app.utils.jwt import create_access_token

logger = get_logger(__name__)

APPLE_KEYS_URL = "https://appleid.apple.com/auth/keys"
EMAIL_PROVIDER = "email"
APPLE_PROVIDER = "apple"


class AuthError(ValueError):
    """Expected auth failure safe to surface as a 4xx."""


class AuthUnavailable(RuntimeError):
    """Auth provider is not configured."""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _digest(value: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()


def hash_refresh_token(token: str) -> str:
    return _digest(f"refresh:{token}")


def hash_otp(email: str, code: str) -> str:
    return _digest(f"otp:{normalize_email(email)}:{code}")


def generate_otp_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


async def rate_limit_or_raise(key: str, *, limit: int, window_seconds: int) -> None:
    redis = get_redis()
    if redis is None:
        return
    current = await redis.incr(key)
    if current == 1:
        await redis.expire(key, window_seconds)
    if current > limit:
        raise AuthError("Too many attempts. Please try again later.")


async def create_email_otp(
    session: AsyncSession,
    *,
    email: str,
    ip_address: str | None,
    user_agent: str | None,
) -> tuple[EmailOTPChallenge, str]:
    normalized = normalize_email(email)
    limiter_key = f"auth:otp:start:{normalized}:{ip_address or 'unknown'}"
    resend_key = f"auth:otp:resend:{normalized}"
    await rate_limit_or_raise(resend_key, limit=1, window_seconds=settings.OTP_RESEND_SECONDS)
    await rate_limit_or_raise(
        limiter_key, limit=settings.OTP_REQUESTS_PER_HOUR, window_seconds=60 * 60
    )

    code = generate_otp_code()
    challenge = EmailOTPChallenge(
        email=normalized,
        code_hash=hash_otp(normalized, code),
        purpose="sign_in",
        expires_at=now_utc() + timedelta(minutes=settings.OTP_EXPIRE_MINUTES),
        ip_address=ip_address,
        user_agent=user_agent[:512] if user_agent else None,
        delivery_target=normalized,
    )
    session.add(challenge)
    await session.commit()
    await session.refresh(challenge)
    await send_otp_email(normalized, code)
    return challenge, code


async def send_otp_email(email: str, code: str) -> None:
    if settings.RESEND_API_KEY:
        await send_otp_email_resend(email, code)
        return
    if not settings.SMTP_HOST:
        logger.info("otp_email_dev_delivery", email=email, code=code)
        return
    await send_otp_email_smtp(email, code)


def _otp_text(code: str) -> str:
    return (
        f"Your Resido sign-in code is {code}.\n\n"
        f"It expires in {settings.OTP_EXPIRE_MINUTES} minutes. "
        "If you did not request it, you can ignore this email."
    )


def _otp_html(code: str) -> str:
    return (
        "<div style=\"font-family:Arial,sans-serif;line-height:1.5;color:#1f2937\">"
        "<h2>Your Resido sign-in code</h2>"
        f"<p style=\"font-size:28px;font-weight:700;letter-spacing:4px\">{code}</p>"
        f"<p>This code expires in {settings.OTP_EXPIRE_MINUTES} minutes.</p>"
        "<p>If you did not request it, you can ignore this email.</p>"
        "</div>"
    )


async def send_otp_email_resend(email: str, code: str) -> None:
    payload = {
        "from": settings.RESEND_FROM_EMAIL,
        "to": [email],
        "subject": "Your Resido sign-in code",
        "html": _otp_html(code),
        "text": _otp_text(code),
    }
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {settings.RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    if response.status_code >= 400:
        logger.warning("resend_email_failed", status=response.status_code, body=response.text[:500])
        raise AuthUnavailable("Email delivery is temporarily unavailable.")
    logger.info("resend_email_sent", email=email)


async def send_otp_email_smtp(email: str, code: str) -> None:
    message = EmailMessage()
    message["Subject"] = "Your Resido sign-in code"
    message["From"] = settings.OTP_EMAIL_FROM
    message["To"] = email
    message.set_content(_otp_text(code))
    message.add_alternative(_otp_html(code), subtype="html")

    def _send() -> None:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as smtp:
            if settings.SMTP_USE_TLS:
                smtp.starttls()
            if settings.SMTP_USERNAME or settings.SMTP_PASSWORD:
                smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            smtp.send_message(message)

    await asyncio.to_thread(_send)


async def optional_user_from_bearer(session: AsyncSession, authorization: str | None) -> User | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except InvalidTokenError:
        return None
    if payload.get("type") != "access" or not payload.get("sub"):
        return None
    try:
        return await session.get(User, UUID(payload["sub"]))
    except ValueError:
        return None


async def verify_email_otp(
    session: AsyncSession,
    *,
    challenge_id: UUID,
    code: str,
    full_name: str | None,
    current_user: User | None = None,
) -> User:
    challenge = await session.get(EmailOTPChallenge, challenge_id)
    if challenge is None:
        raise AuthError("Invalid sign-in code.")
    if challenge.consumed_at is not None:
        raise AuthError("This sign-in code has already been used.")
    if challenge.expires_at <= now_utc():
        raise AuthError("This sign-in code has expired.")
    if challenge.attempts >= settings.OTP_MAX_ATTEMPTS:
        raise AuthError("Too many incorrect code attempts.")

    if not hmac.compare_digest(challenge.code_hash, hash_otp(challenge.email, code.strip())):
        challenge.attempts += 1
        await session.commit()
        raise AuthError("Invalid sign-in code.")

    challenge.consumed_at = now_utc()
    user = await get_or_create_email_user(
        session, email=challenge.email, full_name=full_name, current_user=current_user
    )
    user.email_verified_at = user.email_verified_at or now_utc()
    user.is_anonymous = False
    user.last_login_at = now_utc()
    await session.commit()
    await session.refresh(user)
    return user


async def get_or_create_email_user(
    session: AsyncSession,
    *,
    email: str,
    full_name: str | None,
    current_user: User | None = None,
) -> User:
    normalized = normalize_email(email)
    identity_result = await session.execute(
        select(AuthIdentity).where(
            AuthIdentity.provider == EMAIL_PROVIDER,
            AuthIdentity.provider_subject == normalized,
        )
    )
    identity = identity_result.scalar_one_or_none()
    if identity is not None:
        user = await session.get(User, identity.user_id)
        if user is None:
            raise AuthError("Invalid account identity.")
        return user

    existing_result = await session.execute(select(User).where(User.email == normalized))
    existing = existing_result.scalar_one_or_none()
    if existing is not None:
        user = existing
    elif current_user is not None and current_user.is_anonymous:
        user = current_user
        user.email = normalized
    else:
        user = User(email=normalized)
        session.add(user)
        await session.flush()

    if full_name and not user.full_name:
        user.full_name = full_name
    session.add(
        AuthIdentity(
            user_id=user.id,
            provider=EMAIL_PROVIDER,
            provider_subject=normalized,
            email=normalized,
        )
    )
    return user


async def authenticate_apple_identity(
    session: AsyncSession,
    *,
    identity_token: str,
    nonce: str | None,
    full_name: str | None,
    current_user: User | None = None,
) -> User:
    claims = await asyncio.to_thread(verify_apple_identity_token, identity_token, nonce=nonce)
    apple_sub = claims.get("sub")
    if not apple_sub:
        raise AuthError("Apple identity token is missing a subject.")

    identity_result = await session.execute(
        select(AuthIdentity).where(
            AuthIdentity.provider == APPLE_PROVIDER,
            AuthIdentity.provider_subject == apple_sub,
        )
    )
    identity = identity_result.scalar_one_or_none()
    if identity is not None:
        user = await session.get(User, identity.user_id)
        if user is None:
            raise AuthError("Invalid Apple identity.")
        user.last_login_at = now_utc()
        await session.commit()
        await session.refresh(user)
        return user

    email = normalize_email(claims["email"]) if claims.get("email") else None
    user: User | None = None
    if email:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
    if user is None and current_user is not None and current_user.is_anonymous:
        user = current_user
    if user is None:
        user = User(email=email)
        session.add(user)
        await session.flush()

    if email and not user.email:
        user.email = email
    if full_name and not user.full_name:
        user.full_name = full_name
    if email and claims.get("email_verified") in (True, "true", "1"):
        user.email_verified_at = user.email_verified_at or now_utc()
    user.is_anonymous = False
    user.last_login_at = now_utc()

    session.add(
        AuthIdentity(
            user_id=user.id,
            provider=APPLE_PROVIDER,
            provider_subject=apple_sub,
            email=email,
        )
    )
    await session.commit()
    await session.refresh(user)
    return user


def verify_apple_identity_token(identity_token: str, *, nonce: str | None) -> dict:
    if not settings.APPLE_CLIENT_ID:
        raise AuthUnavailable("Sign in with Apple is not configured.")
    jwks_client = PyJWKClient(APPLE_KEYS_URL)
    try:
        signing_key = jwks_client.get_signing_key_from_jwt(identity_token)
        claims = jwt.decode(
            identity_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.APPLE_CLIENT_ID,
            issuer="https://appleid.apple.com",
        )
    except InvalidTokenError as exc:
        raise AuthError("Invalid Apple identity token.") from exc

    if nonce:
        expected_hash = hashlib.sha256(nonce.encode()).hexdigest()
        token_nonce = claims.get("nonce")
        if token_nonce not in {nonce, expected_hash}:
            raise AuthError("Invalid Apple nonce.")
    return claims


async def create_anonymous_user(session: AsyncSession) -> User:
    user = User(full_name="Guest", is_anonymous=True)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def issue_session_tokens(
    session: AsyncSession,
    *,
    user: User,
    ip_address: str | None,
    user_agent: str | None,
) -> tuple[str, str, AuthSession]:
    refresh_token = secrets.token_urlsafe(48)
    auth_session = AuthSession(
        user_id=user.id,
        refresh_token_hash=hash_refresh_token(refresh_token),
        refresh_token_family=uuid4().hex,
        user_agent=user_agent[:512] if user_agent else None,
        ip_address=ip_address,
        expires_at=now_utc() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        last_seen_at=now_utc(),
    )
    session.add(auth_session)
    await session.commit()
    await session.refresh(auth_session)
    access_token = create_access_token({"sub": str(user.id), "sid": str(auth_session.id)})
    return access_token, refresh_token, auth_session


async def rotate_refresh_token(
    session: AsyncSession,
    *,
    refresh_token: str,
    ip_address: str | None,
    user_agent: str | None,
) -> tuple[str, str, AuthSession, User]:
    token_hash = hash_refresh_token(refresh_token)
    result = await session.execute(
        select(AuthSession).where(AuthSession.refresh_token_hash == token_hash)
    )
    old = result.scalar_one_or_none()
    if old is None:
        raise AuthError("Invalid refresh token.")
    if old.revoked_at is not None:
        await revoke_session_family(session, old.refresh_token_family)
        raise AuthError("Refresh token has been revoked.")
    if old.expires_at <= now_utc():
        old.revoked_at = now_utc()
        await session.commit()
        raise AuthError("Refresh token has expired.")

    user = await session.get(User, old.user_id)
    if user is None or not user.is_active:
        raise AuthError("Invalid account.")

    new_refresh = secrets.token_urlsafe(48)
    new_session = AuthSession(
        user_id=user.id,
        refresh_token_hash=hash_refresh_token(new_refresh),
        refresh_token_family=old.refresh_token_family,
        user_agent=user_agent[:512] if user_agent else old.user_agent,
        ip_address=ip_address or old.ip_address,
        expires_at=now_utc() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        last_seen_at=now_utc(),
    )
    session.add(new_session)
    await session.flush()
    old.revoked_at = now_utc()
    old.replaced_by_session_id = new_session.id
    await session.commit()
    await session.refresh(new_session)
    access_token = create_access_token({"sub": str(user.id), "sid": str(new_session.id)})
    return access_token, new_refresh, new_session, user


async def revoke_refresh_token(session: AsyncSession, refresh_token: str) -> None:
    result = await session.execute(
        select(AuthSession).where(AuthSession.refresh_token_hash == hash_refresh_token(refresh_token))
    )
    auth_session = result.scalar_one_or_none()
    if auth_session is None:
        return
    auth_session.revoked_at = auth_session.revoked_at or now_utc()
    await session.commit()


async def revoke_session_id(session: AsyncSession, session_id: UUID, user_id: UUID) -> None:
    auth_session = await session.get(AuthSession, session_id)
    if auth_session is None or auth_session.user_id != user_id:
        return
    auth_session.revoked_at = auth_session.revoked_at or now_utc()
    await session.commit()


async def revoke_all_user_sessions(session: AsyncSession, user_id: UUID) -> None:
    await session.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now_utc())
    )
    await session.commit()


async def revoke_session_family(session: AsyncSession, family: str) -> None:
    await session.execute(
        update(AuthSession)
        .where(AuthSession.refresh_token_family == family, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now_utc())
    )
    await session.commit()
