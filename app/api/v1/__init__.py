"""API v1 router aggregator. Feature routers are registered here as they're built."""

from fastapi import APIRouter

from app.api.v1 import auth, chat, events, leads, me, services

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(chat.router)
api_router.include_router(events.router)
api_router.include_router(leads.router)
api_router.include_router(me.router)
api_router.include_router(services.router)
