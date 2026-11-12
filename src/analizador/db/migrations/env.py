"""Entorno de Alembic (async). La URL se lee de DATABASE_URL (.env o entorno)."""

import asyncio
from logging.config import fileConfig

from alembic import context
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

import analizador.models  # noqa: F401  # registra todas las tablas en Base.metadata
from analizador.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Tablas que gestiona el checkpointer de LangGraph (P-08) con su propio setup().
TABLAS_EXTERNAS = {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"}


class _DbSettings(BaseSettings):
    """Solo lo necesario para migrar, sin exigir el resto de la configuración."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: SecretStr


def _database_url() -> str:
    return _DbSettings().database_url.get_secret_value()  # type: ignore[call-arg]


def _include_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    return not (type_ == "table" and name in TABLAS_EXTERNAS)


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=_include_object,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = create_async_engine(_database_url(), poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
