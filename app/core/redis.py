"""Optional Redis client. The app boots fine without Redis available."""

from __future__ import annotations

import redis.asyncio as aioredis

from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_redis: aioredis.Redis | None = None


async def init_redis() -> aioredis.Redis | None:
    global _redis
    try:
        _redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        await _redis.ping()
        logger.info("redis_connected", url=settings.REDIS_URL)
    except Exception as exc:  # pragma: no cover - graceful degradation
        logger.warning("redis_unavailable", error=str(exc))
        _redis = None
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


def get_redis() -> aioredis.Redis | None:
    return _redis
