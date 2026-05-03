"""SQLAlchemy async engine + session factory + table creation."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from .models import Base


def make_engine(database_url: str):
    # In-memory SQLite needs StaticPool so all connections share the same DB.
    # File-based SQLite and Postgres use default pooling.
    kwargs = {}
    if ":memory:" in database_url:
        kwargs["poolclass"] = StaticPool
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_async_engine(database_url, echo=False, future=True, **kwargs)


def make_session_factory(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def create_all(engine) -> None:
    """Create tables if they don't exist. Used for the SQLite default deploy.

    For Postgres production deploys, prefer running Alembic migrations.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
