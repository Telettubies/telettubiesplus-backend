"""Motor y sesiones async de SQLAlchemy, compartidos por la API (C-02) y el worker (C-03)."""

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from analizador.core.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        # R-09: la VM tiene 2 vCPU y 8 GB compartidos; pool pequeño.
        pool_size=5,
        max_overflow=5,
    )


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Dependencia de FastAPI: una sesión por petición."""
    async with get_sessionmaker()() as session:
        yield session
