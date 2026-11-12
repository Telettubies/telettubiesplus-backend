"""Reranker de NVIDIA (P-11): petición HTTP, orden de la respuesta, reintentos y errores."""

import json
from collections.abc import Callable

import httpx
import pytest

from analizador.core.config import get_settings
from analizador.integrations import nvidia
from analizador.integrations.anthropic_client import ModeloNoConfiguradoError
from analizador.integrations.limitador import LimitadorDeTasa

URL = "https://reranker.test/v1/ranking"
Manejador = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def reranker(monkeypatch: pytest.MonkeyPatch) -> Callable[[Manejador], list[httpx.Request]]:
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-prueba")
    monkeypatch.setenv("NVIDIA_MODEL_RERANK", "modelo-de-rerank")
    monkeypatch.setenv("NVIDIA_RERANK_URL", URL)
    monkeypatch.setenv("NVIDIA_MAX_REINTENTOS", "2")
    get_settings.cache_clear()
    esperas: list[float] = []

    async def dormir(segundos: float) -> None:
        esperas.append(segundos)

    monkeypatch.setattr(nvidia, "_dormir", dormir)
    limitador = LimitadorDeTasa(1000)
    monkeypatch.setattr(nvidia, "_limitador", lambda: limitador)

    def instalar(manejador: Manejador) -> list[httpx.Request]:
        recibidas: list[httpx.Request] = []

        def registrar(peticion: httpx.Request) -> httpx.Response:
            recibidas.append(peticion)
            return manejador(peticion)

        monkeypatch.setattr(
            nvidia,
            "_cliente_http",
            lambda: httpx.AsyncClient(transport=httpx.MockTransport(registrar)),
        )
        return recibidas

    return instalar


def _rankings(*pares: tuple[int, float]) -> httpx.Response:
    return httpx.Response(200, json={"rankings": [{"index": i, "logit": p} for i, p in pares]})


async def test_envia_la_consulta_y_los_pasajes_y_devuelve_el_orden_por_relevancia(
    reranker: Callable[[Manejador], list[httpx.Request]],
) -> None:
    recibidas = reranker(lambda _: _rankings((1, -3.5), (2, 4.25), (0, 0.5)))

    orden = await nvidia.reordenar("experiencia en software", ["uno", "dos", "tres"])

    assert orden == [(2, 4.25), (0, 0.5), (1, -3.5)]
    [peticion] = recibidas
    assert peticion.method == "POST" and str(peticion.url) == URL
    assert peticion.headers["authorization"] == "Bearer nvapi-prueba"
    assert json.loads(peticion.content) == {
        "model": "modelo-de-rerank",
        "query": {"text": "experiencia en software"},
        "passages": [{"text": "uno"}, {"text": "dos"}, {"text": "tres"}],
        "truncate": "END",
    }


async def test_un_429_se_reintenta_respetando_el_retry_after(
    reranker: Callable[[Manejador], list[httpx.Request]],
) -> None:
    respuestas = [httpx.Response(429, headers={"retry-after": "3"}), _rankings((0, 1.0))]
    recibidas = reranker(lambda _: respuestas.pop(0))

    assert await nvidia.reordenar("consulta", ["uno"]) == [(0, 1.0)]
    assert len(recibidas) == 2


async def test_si_no_responde_tras_los_reintentos_es_un_error_de_disponibilidad(
    reranker: Callable[[Manejador], list[httpx.Request]],
) -> None:
    recibidas = reranker(lambda _: httpx.Response(503))

    with pytest.raises(nvidia.NvidiaNoDisponibleError):
        await nvidia.reordenar("consulta", ["uno"])
    assert len(recibidas) == 3  # el intento y dos reintentos


async def test_un_error_de_red_tambien_se_reintenta(
    reranker: Callable[[Manejador], list[httpx.Request]],
) -> None:
    intentos = 0

    def manejador(peticion: httpx.Request) -> httpx.Response:
        nonlocal intentos
        intentos += 1
        if intentos == 1:
            raise httpx.ConnectError("sin red", request=peticion)
        return _rankings((0, 2.0))

    reranker(manejador)

    assert await nvidia.reordenar("consulta", ["uno"]) == [(0, 2.0)]


@pytest.mark.parametrize(
    "respuesta",
    [
        pytest.param(
            httpx.Response(400, json={"detail": "modelo inválido"}), id="peticion-invalida"
        ),
        pytest.param(httpx.Response(200, json={"otra": "forma"}), id="sin-rankings"),
        pytest.param(_rankings((7, 1.0)), id="indice-fuera-de-rango"),
    ],
)
async def test_una_respuesta_inutilizable_es_un_error_que_no_se_reintenta(
    reranker: Callable[[Manejador], list[httpx.Request]], respuesta: httpx.Response
) -> None:
    recibidas = reranker(lambda _: respuesta)

    with pytest.raises(RuntimeError) as exc:
        await nvidia.reordenar("consulta", ["uno", "dos"])
    assert "modelo inválido" not in str(exc.value)
    assert len(recibidas) == 1


async def test_sin_modelo_o_sin_url_no_se_llama(
    reranker: Callable[[Manejador], list[httpx.Request]], monkeypatch: pytest.MonkeyPatch
) -> None:
    recibidas = reranker(lambda _: _rankings((0, 1.0)))
    monkeypatch.setenv("NVIDIA_RERANK_URL", "")
    get_settings.cache_clear()

    with pytest.raises(ModeloNoConfiguradoError):
        await nvidia.reordenar("consulta", ["uno"])
    assert recibidas == []
