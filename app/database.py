"""Async SQLAlchemy engine, session factory, and declarative Base.

Harvested from the prior project's proven setup (naming convention + async session).
"""

from collections.abc import AsyncIterator

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, declared_attr

from app.config import settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    @declared_attr.directive
    def __tablename__(cls) -> str:  # type: ignore[misc]
        return cls.__name__.lower()


engine = create_async_engine(
    settings.async_database_url,
    echo=settings.DEBUG,
    pool_pre_ping=True,
)

async_session_maker = async_sessionmaker(engine, expire_on_commit=False)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with async_session_maker() as session:
        yield session


# Alias used by FastAPI dependencies
get_db = get_db_session

__all__ = ["Base", "engine", "async_session_maker", "get_db_session", "get_db"]
