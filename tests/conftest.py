"""Configuración común de tests: entorno mínimo sin servicios externos."""

from collections.abc import Iterator

import pytest

from analizador.core.config import get_settings

ENTORNO_TEST = {
    "APP_ENV": "test",
    "DATABASE_URL": "postgresql+asyncpg://test:test@localhost:5432/test",
    "RABBITMQ_URL": "amqp://test:test@localhost:5672/",
    "SUPABASE_URL": "https://proyecto-test.supabase.co",
    "SUPABASE_SERVICE_ROLE_KEY": "clave-de-servicio-de-prueba",
    "SUPABASE_STORAGE_BUCKET": "pliegos-test",
    "MAX_PDF_MB": "1",
}


@pytest.fixture(autouse=True)
def entorno(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for clave, valor in ENTORNO_TEST.items():
        monkeypatch.setenv(clave, valor)
    # Evita que un .env local altere los tests.
    monkeypatch.chdir("/")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
