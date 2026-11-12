from fastapi.testclient import TestClient

from analizador.api.main import create_app


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
