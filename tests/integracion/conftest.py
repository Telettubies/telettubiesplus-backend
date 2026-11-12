"""Pruebas de integración: PostgreSQL con pgvector real y proveedores externos simulados.

El esquema se crea con las migraciones de Alembic, no con `create_all`.

La base se toma de TEST_DATABASE_URL (así corre el CI). Si no está definida, se deriva de las
credenciales del `.env` local apuntando a la base `analizador_test` del Compose de desarrollo,
que se crea si no existe. El nombre de la base debe terminar en `_test`: estas pruebas vacían
las tablas después de cada caso.
"""

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from fastapi import FastAPI
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

import analizador.models  # noqa: F401  # registra las tablas en Base.metadata
from analizador.api.main import create_app
from analizador.core.config import get_settings
from analizador.db.base import Base
from analizador.db.session import get_engine, get_sessionmaker
from analizador.integrations import supabase
from tests.apoyo import AlmacenFalso, JwksFalso, PublicadorFalso

RAIZ = Path(__file__).resolve().parents[2]
BASE_LOCAL = "analizador_test"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "integracion" in item.path.parts:
            item.add_marker(pytest.mark.integracion)


def _url_de_pruebas() -> tuple[URL | None, bool]:
    """Devuelve la URL de la base de pruebas y si fue indicada explícitamente."""
    explicita = os.environ.get("TEST_DATABASE_URL")
    if explicita:
        return make_url(explicita), True
    local = dotenv_values(RAIZ / ".env")
    usuario, clave = local.get("POSTGRES_USER"), local.get("POSTGRES_PASSWORD")
    if not usuario or not clave:
        return None, False
    return (
        URL.create(
            "postgresql+asyncpg",
            username=usuario,
            password=clave,
            host="localhost",
            port=5432,
            database=BASE_LOCAL,
        ),
        False,
    )


def _crear_base_si_falta(url: URL) -> None:
    mantenimiento = create_engine(
        url.set(drivername="postgresql+psycopg", database="postgres"),
        isolation_level="AUTOCOMMIT",
    )
    try:
        with mantenimiento.connect() as conexion:
            existe = conexion.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :nombre"),
                {"nombre": url.database},
            )
            if not existe:
                conexion.execute(text(f'CREATE DATABASE "{url.database}"'))
    finally:
        mantenimiento.dispose()


def _migrar(url: URL) -> None:
    """Crea el esquema con las migraciones de Alembic, igual que en producción.

    Así una migración incompleta (por ejemplo, un CHECK que Alembic no detecta) hace fallar
    las pruebas en lugar de pasar inadvertida. Sin archivo .ini para no tocar el logging.
    """
    configuracion = Config()
    configuracion.set_main_option("script_location", str(RAIZ / "src/analizador/db/migrations"))
    anterior = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)
    try:
        command.upgrade(configuracion, "head")
    finally:
        if anterior is None:
            del os.environ["DATABASE_URL"]
        else:
            os.environ["DATABASE_URL"] = anterior


@pytest.fixture(scope="session")
def motor_de_pruebas() -> Iterator[tuple[URL, Engine]]:
    url, explicita = _url_de_pruebas()
    if url is None:
        pytest.skip("Sin TEST_DATABASE_URL ni credenciales de PostgreSQL en .env")
    if not (url.database or "").endswith("_test"):
        raise RuntimeError("La base de pruebas debe terminar en _test: las pruebas la vacían")
    try:
        _crear_base_si_falta(url)
    except OperationalError:
        if explicita:
            raise
        pytest.skip(
            "PostgreSQL local no disponible: docker compose -f docker-compose.dev.yml up -d --wait"
        )
    motor = create_engine(url.set(drivername="postgresql+psycopg"))
    with motor.begin() as conexion:
        conexion.execute(text("DROP SCHEMA public CASCADE"))
        conexion.execute(text("CREATE SCHEMA public"))
    _migrar(url)
    yield url, motor
    motor.dispose()


@pytest.fixture(autouse=True)
async def base_de_datos(
    entorno: None, motor_de_pruebas: tuple[URL, Engine], monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[None]:
    """Apunta la aplicación a la base de pruebas y la deja vacía al terminar cada caso."""
    url, motor = motor_de_pruebas
    monkeypatch.setenv("DATABASE_URL", url.render_as_string(hide_password=False))
    for cache in (get_settings, get_engine, get_sessionmaker):
        cache.cache_clear()
    yield
    await get_engine().dispose()
    for cache in (get_settings, get_engine, get_sessionmaker):
        cache.cache_clear()
    tablas = ", ".join(f'"{tabla.name}"' for tabla in Base.metadata.sorted_tables)
    with motor.begin() as conexion:
        conexion.execute(text(f"TRUNCATE TABLE {tablas} RESTART IDENTITY CASCADE"))


@pytest.fixture(autouse=True)
def jwks_falso(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supabase, "_jwks_client", lambda: JwksFalso())


@pytest.fixture(autouse=True)
def almacen(monkeypatch: pytest.MonkeyPatch) -> AlmacenFalso:
    """Ninguna prueba de integración llega a Supabase Storage."""
    falso = AlmacenFalso()
    falso.instalar(monkeypatch)
    return falso


@pytest.fixture
async def sesion(base_de_datos: None) -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as sesion:
        yield sesion


@pytest.fixture
def publicador() -> PublicadorFalso:
    return PublicadorFalso()


@pytest.fixture
def app(base_de_datos: None, publicador: PublicadorFalso) -> FastAPI:
    app = create_app()
    # Sin lifespan: el publicador real (RabbitMQ) se sustituye por el doble.
    app.state.publicador = publicador
    return app


@pytest.fixture
async def cliente(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transporte = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transporte, base_url="http://test") as cliente:
        yield cliente
