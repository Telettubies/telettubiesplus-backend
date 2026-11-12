"""Los errores no controlados no exponen detalles internos (S-14)."""

from fastapi.testclient import TestClient

from analizador.api.errores import MENSAJE_ERROR_INTERNO
from analizador.api.main import create_app


def test_error_no_controlado_responde_500_generico() -> None:
    app = create_app()

    @app.get("/_falla")
    async def falla() -> None:
        raise RuntimeError("clave=sk-secreta en /app/src/analizador/interno.py")

    respuesta = TestClient(app, raise_server_exceptions=False).get("/_falla")

    assert respuesta.status_code == 500
    assert respuesta.json() == {"detail": MENSAJE_ERROR_INTERNO}
    assert "sk-secreta" not in respuesta.text
    assert "interno.py" not in respuesta.text
