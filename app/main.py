"""Resido FastAPI application entrypoint."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import api_router
from app.config import settings
from app.core.logging import configure_logging, get_logger
from app.core.redis import close_redis, init_redis
from app.services.event_feed_scheduler import start_event_feed_scheduler, stop_event_feed_scheduler

configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "startup",
        app=settings.APP_NAME,
        env=settings.ENVIRONMENT,
        ai_enabled=settings.ai_enabled,
    )
    await init_redis()
    events_feed_task = start_event_feed_scheduler()
    yield
    await stop_event_feed_scheduler(events_feed_task)
    await close_redis()
    logger.info("shutdown")


app = FastAPI(title=settings.APP_NAME, version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["system"])
async def health() -> dict:
    return {"status": "ok", "app": settings.APP_NAME, "ai_enabled": settings.ai_enabled}


app.include_router(api_router, prefix="/api/v1")
