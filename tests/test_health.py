import pytest
from fastapi.testclient import TestClient

from analizador.api.main import create_app
from analizador.core.config import get_settings


def test_health_responde_ok() -> None:
    # Sin `with`: no se ejecuta el lifespan, así que no se conecta a RabbitMQ.
    cliente = TestClient(create_app())
    respuesta = cliente.get("/health")
    assert respuesta.status_code == 200
    assert respuesta.json() == {"estado": "ok"}


def test_endpoints_protegidos_exigen_token() -> None:
    cliente = TestClient(create_app())
    assert cliente.get("/api/licitaciones").status_code == 401
    assert cliente.get("/api/yo").status_code == 401


def _app(monkeypatch: pytest.MonkeyPatch, **entorno: str) -> TestClient:
    for clave, valor in entorno.items():
        monkeypatch.setenv(clave, valor)
    get_settings.cache_clear()
    return TestClient(create_app())


def test_docs_activos_fuera_de_produccion(monkeypatch: pytest.MonkeyPatch) -> None:
    cliente = _app(monkeypatch, APP_ENV="development")
    assert cliente.get("/docs").status_code == 200
    assert cliente.get("/openapi.json").status_code == 200


def test_docs_ocultos_en_produccion_por_defecto(monkeypatch: pytest.MonkeyPatch) -> None:
    cliente = _app(monkeypatch, APP_ENV="production")
    assert cliente.get("/docs").status_code == 404
    assert cliente.get("/openapi.json").status_code == 404


def test_docs_en_produccion_con_exponer_docs(monkeypatch: pytest.MonkeyPatch) -> None:
    cliente = _app(monkeypatch, APP_ENV="production", EXPONER_DOCS="true")
    assert cliente.get("/docs").status_code == 200
    assert cliente.get("/openapi.json").status_code == 200
