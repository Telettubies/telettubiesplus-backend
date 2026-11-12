"""HU-B26, criterio 3: límite de peticiones por minuto y reintentos con espera hacia NVIDIA."""

from types import SimpleNamespace
from typing import Any

import httpx2
import openai
import pytest

from analizador.core.config import get_settings
from analizador.integrations import nvidia
from analizador.integrations.limitador import LimitadorDeTasa


class Reloj:
    """Tiempo simulado: dormir adelanta el reloj en lugar de esperar."""

    def __init__(self) -> None:
        self.ahora = 0.0
        self.esperas: list[float] = []

    def __call__(self) -> float:
        return self.ahora

    async def dormir(self, segundos: float) -> None:
        self.esperas.append(segundos)
        self.ahora += segundos


def _respuesta(estado: int, **cabeceras: str) -> httpx2.Response:
    peticion = httpx2.Request("POST", "https://integrate.api.nvidia.com/v1/embeddings")
    return httpx2.Response(estado, headers=cabeceras, request=peticion)


def _limite(**cabeceras: str) -> openai.RateLimitError:
    return openai.RateLimitError("límite", response=_respuesta(429, **cabeceras), body=None)


class Operacion:
    """Llamada simulada que falla con los errores indicados antes de responder."""

    def __init__(self, *errores: Exception) -> None:
        self.errores = list(errores)
        self.llamadas = 0

    async def __call__(self) -> str:
        self.llamadas += 1
        if self.errores:
            raise self.errores.pop(0)
        return "ok"


@pytest.fixture
def reloj(monkeypatch: pytest.MonkeyPatch) -> Reloj:
    """Instala tiempo simulado en el limitador y en las esperas de reintento."""
    reloj = Reloj()
    limitador = LimitadorDeTasa(
        get_settings().nvidia_peticiones_por_minuto, reloj=reloj, dormir=reloj.dormir
    )
    monkeypatch.setattr(nvidia, "_limitador", lambda: limitador)
    monkeypatch.setattr(nvidia, "_dormir", reloj.dormir)
    return reloj


# --- Limitador ------------------------------------------------------------------------------


async def test_el_limitador_deja_pasar_hasta_el_maximo_sin_esperar() -> None:
    reloj = Reloj()
    limitador = LimitadorDeTasa(3, ventana=60, reloj=reloj, dormir=reloj.dormir)

    for _ in range(3):
        await limitador.esperar()

    assert reloj.esperas == []


async def test_el_limitador_hace_esperar_a_la_peticion_que_supera_el_maximo() -> None:
    reloj = Reloj()
    limitador = LimitadorDeTasa(3, ventana=60, reloj=reloj, dormir=reloj.dormir)
    for _ in range(3):
        await limitador.esperar()
    reloj.ahora = 10.0

    await limitador.esperar()

    # La más antigua salió en t=0: hay que esperar hasta t=60.
    assert reloj.esperas == [50.0]
    assert reloj.ahora == 60.0


async def test_el_limitador_nunca_supera_el_maximo_en_ninguna_ventana() -> None:
    reloj = Reloj()
    limitador = LimitadorDeTasa(5, ventana=60, reloj=reloj, dormir=reloj.dormir)
    salidas: list[float] = []

    for _ in range(23):
        await limitador.esperar()
        salidas.append(reloj.ahora)
        reloj.ahora += 1.0

    for inicio in salidas:
        en_ventana = [t for t in salidas if inicio <= t < inicio + 60]
        assert len(en_ventana) <= 5


def test_el_limitador_exige_un_maximo_positivo() -> None:
    with pytest.raises(ValueError):
        LimitadorDeTasa(0)


def test_el_limite_por_minuto_sale_de_la_configuracion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_PETICIONES_POR_MINUTO", "7")
    get_settings.cache_clear()
    nvidia._limitador.cache_clear()
    try:
        assert nvidia._limitador()._maximo == 7
    finally:
        nvidia._limitador.cache_clear()


# --- Reintentos -----------------------------------------------------------------------------


async def test_un_429_se_reintenta_con_espera_y_termina_bien(reloj: Reloj) -> None:
    operacion = Operacion(_limite(), _limite())

    assert await nvidia.llamar(operacion) == "ok"

    assert operacion.llamadas == 3
    assert reloj.esperas == [2.0, 4.0]  # espera exponencial: base 2 s


async def test_se_respeta_el_retry_after_del_proveedor(reloj: Reloj) -> None:
    operacion = Operacion(_limite(**{"retry-after": "7"}))

    await nvidia.llamar(operacion)

    assert reloj.esperas == [7.0]


async def test_la_espera_tiene_un_maximo(reloj: Reloj, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_ESPERA_MAXIMA_SEGUNDOS", "5")
    get_settings.cache_clear()
    operacion = Operacion(_limite(**{"retry-after": "3600"}), _limite(), _limite(), _limite())

    await nvidia.llamar(operacion)

    assert reloj.esperas == [5.0, 4.0, 5.0, 5.0]


async def test_errores_de_red_y_del_servidor_tambien_se_reintentan(reloj: Reloj) -> None:
    peticion = httpx2.Request("POST", "https://integrate.api.nvidia.com/v1/embeddings")
    operacion = Operacion(
        openai.APIConnectionError(request=peticion),
        openai.APITimeoutError(request=peticion),
        openai.InternalServerError("caído", response=_respuesta(503), body=None),
    )

    assert await nvidia.llamar(operacion) == "ok"
    assert operacion.llamadas == 4


async def test_si_no_lo_logra_queda_en_error_visible(
    reloj: Reloj, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NVIDIA_MAX_REINTENTOS", "2")
    get_settings.cache_clear()
    operacion = Operacion(*[_limite() for _ in range(5)])

    with pytest.raises(nvidia.NvidiaNoDisponibleError):
        await nvidia.llamar(operacion)

    assert operacion.llamadas == 3  # el intento inicial y dos reintentos


async def test_un_error_de_la_peticion_no_se_reintenta(reloj: Reloj) -> None:
    operacion = Operacion(
        openai.BadRequestError("modelo inválido", response=_respuesta(400), body=None)
    )

    with pytest.raises(openai.BadRequestError):
        await nvidia.llamar(operacion)

    assert operacion.llamadas == 1 and reloj.esperas == []


async def test_cada_intento_pasa_por_el_limite_por_minuto(
    reloj: Reloj, monkeypatch: pytest.MonkeyPatch
) -> None:
    turnos = 0

    class Contador:
        async def esperar(self) -> None:
            nonlocal turnos
            turnos += 1

    monkeypatch.setattr(nvidia, "_limitador", lambda: Contador())
    operacion = Operacion(_limite(), _limite())

    await nvidia.llamar(operacion)

    assert turnos == operacion.llamadas == 3


# --- Las llamadas reales usan el envoltorio --------------------------------------------------


async def test_los_embeddings_pasan_por_el_limite_y_los_reintentos(
    reloj: Reloj, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NVIDIA_MODEL_EMBEDDINGS", "modelo-de-embeddings")
    get_settings.cache_clear()
    recibidas: list[dict[str, Any]] = []
    fallos = [_limite()]

    class _Embeddings:
        async def create(self, **kwargs: Any) -> Any:
            recibidas.append(kwargs)
            if fallos:
                raise fallos.pop()

            dato = SimpleNamespace(embedding=[0.1, 0.2])
            return SimpleNamespace(data=[dato, dato])

    class _Cliente:
        embeddings = _Embeddings()

    monkeypatch.setattr(nvidia, "get_client", lambda: _Cliente())

    vectores = await nvidia.embeddings(["uno", "dos"], "passage")

    assert vectores == [[0.1, 0.2], [0.1, 0.2]]
    assert len(recibidas) == 2 and reloj.esperas == [2.0]
    assert recibidas[0]["model"] == "modelo-de-embeddings"
    assert recibidas[0]["input"] == ["uno", "dos"]
    assert recibidas[0]["extra_body"]["input_type"] == "passage"


def test_el_cliente_no_reintenta_por_su_cuenta(monkeypatch: pytest.MonkeyPatch) -> None:
    """Los reintentos del SDK se saltarían el límite por minuto."""
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-prueba")
    get_settings.cache_clear()
    nvidia.get_client.cache_clear()
    try:
        assert nvidia.get_client().max_retries == 0
    finally:
        nvidia.get_client.cache_clear()
