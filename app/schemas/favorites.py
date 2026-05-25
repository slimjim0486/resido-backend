"""Schemas for saved (liked) events & service providers."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.schemas.base import ORMBaseModel
from app.schemas.events import EventOut
from app.schemas.services import ServiceProviderOut

FavoriteItemType = Literal["event", "service"]


class FavoriteCreate(BaseModel):
    item_type: FavoriteItemType
    item_id: UUID


class FavoritesOut(ORMBaseModel):
    """Saved items resolved to their live event/provider rows."""

    events: list[EventOut]
    services: list[ServiceProviderOut]


class FavoriteIdsOut(BaseModel):
    """Just the ids — cheap hydration of the heart-toggle state."""

    events: list[UUID]
    services: list[UUID]


class FavoriteToggleOut(BaseModel):
    favorited: bool
