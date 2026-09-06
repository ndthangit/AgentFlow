"""Async PostgreSQL session management for the AgentFlow control plane."""

import os
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def database_url() -> str:
    return os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://agentflow:agentflow@localhost:5432/agentflow",
    )


engine = create_async_engine(database_url(), pool_pre_ping=True)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        yield session
