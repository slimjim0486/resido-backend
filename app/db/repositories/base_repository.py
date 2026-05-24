"""Generic async CRUD repository base. Lean version of the harvested pattern.

Caching can be layered on later via the Redis manager; kept dependency-free so the
app boots without Redis.
"""

from typing import Generic, TypeVar
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    model: type[ModelT]

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, id_: UUID) -> ModelT | None:
        return await self.session.get(self.model, id_)

    async def list(self, *, limit: int = 50, offset: int = 0) -> list[ModelT]:
        result = await self.session.execute(
            select(self.model).limit(limit).offset(offset)
        )
        return list(result.scalars().all())

    async def count(self) -> int:
        result = await self.session.execute(
            select(func.count()).select_from(self.model)
        )
        return result.scalar_one()

    async def add(self, obj: ModelT) -> ModelT:
        self.session.add(obj)
        await self.session.commit()
        await self.session.refresh(obj)
        return obj

    async def delete(self, obj: ModelT) -> None:
        await self.session.delete(obj)
        await self.session.commit()
