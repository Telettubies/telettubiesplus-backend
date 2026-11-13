"""Proveedor de modelos NVIDIA (C-09, ADR-002): filtro de secciones, embeddings y chat.

build.nvidia.com expone una API compatible con OpenAI, por eso se usa el SDK `openai`
apuntando a NVIDIA_BASE_URL. Modelos configurables por entorno (P-19); P-10 y P-11 abiertas.

Toda llamada pasa por `llamar`, que aplica el límite de peticiones por minuto y reintenta con
espera ante errores de límite de velocidad o de disponibilidad (HU-B26, R-02, Q-13).
"""

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import Any, Literal

import httpx
import openai
from openai import AsyncOpenAI

from analizador.core.config import get_settings
from analizador.integrations.anthropic_client import ModeloNoConfiguradoError
from analizador.integrations.limitador import LimitadorDeTasa

logger = logging.getLogger(__name__)


class TransitorioError(Exception):
    """Fallo pasajero de una llamada HTTP directa a NVIDIA (reranker): 429, 5xx o red."""

    def __init__(self, estado: int | None, retry_after: str | None = None) -> None:
        super().__init__(f"estado {estado}")
        self.estado = estado
        self.retry_after = retry_after


# 429, errores de red o de tiempo de espera y 5xx: tiene sentido volver a intentarlo.
REINTENTABLES = (
    openai.RateLimitError,
    openai.APIConnectionError,
    openai.InternalServerError,
    TransitorioError,
)

_dormir = asyncio.sleep


class NvidiaNoDisponibleError(RuntimeError):
    """NVIDIA no respondió tras agotar los reintentos."""


@lru_cache
def get_client() -> AsyncOpenAI:
    settings = get_settings()
    if settings.nvidia_api_key is None:
        raise ModeloNoConfiguradoError("NVIDIA_API_KEY no está configurada")
    return AsyncOpenAI(
        api_key=settings.nvidia_api_key.get_secret_value(),
        base_url=settings.nvidia_base_url,
        max_retries=0,  # los reintentos los gobierna `llamar`, con el límite por minuto
    )


@lru_cache
def _limitador() -> LimitadorDeTasa:
    return LimitadorDeTasa(get_settings().nvidia_peticiones_por_minuto)


def _espera(exc: Exception, intento: int) -> float:
    """Segundos a esperar: lo que pida el proveedor (Retry-After) o espera exponencial."""
    settings = get_settings()
    maxima = settings.nvidia_espera_maxima_segundos
    pedido: str | None = None
    if isinstance(exc, openai.APIStatusError):
        pedido = exc.response.headers.get("retry-after")
    elif isinstance(exc, TransitorioError):
        pedido = exc.retry_after
    if pedido is not None:
        try:
            return min(float(pedido), maxima)
        except ValueError:
            pass
    return min(settings.nvidia_espera_base_segundos * 2.0**intento, maxima)


async def llamar[T](operacion: Callable[[], Awaitable[T]]) -> T:
    """Ejecuta una llamada a NVIDIA respetando el límite por minuto y reintentando con espera."""
    reintentos = get_settings().nvidia_max_reintentos
    for intento in range(reintentos + 1):
        await _limitador().esperar()
        try:
            return await operacion()
        except REINTENTABLES as exc:
            if intento == reintentos:
                raise NvidiaNoDisponibleError(
                    f"NVIDIA no respondió tras {reintentos} reintentos"
                ) from exc
            espera = _espera(exc, intento)
            estado = exc.status_code if isinstance(exc, openai.APIStatusError) else None
            peticion = (
                exc.response.headers.get("x-request-id", "")[:128]
                if isinstance(exc, openai.APIStatusError)
                else ""
            )
            logger.warning(
                "NVIDIA respondio %s HTTP=%s request_id=%s; reintento %d de %d en %.0f s",
                type(exc).__name__,
                estado,
                peticion,
                intento + 1,
                reintentos,
                espera,
            )
            await _dormir(espera)
    raise AssertionError("inalcanzable")  # pragma: no cover


async def embeddings(textos: list[str], tipo: Literal["query", "passage"]) -> list[list[float]]:
    """Embeddings para pgvector (C-06). `tipo` distingue consultas de pasajes indexados."""
    settings = get_settings()
    modelo = settings.nvidia_model_embeddings
    if not modelo:
        raise ModeloNoConfiguradoError("NVIDIA_MODEL_EMBEDDINGS no está configurada")
    respuesta = await llamar(
        lambda: get_client().embeddings.create(
            model=modelo,
            input=textos,
            encoding_format="float",
            extra_body={"input_type": tipo, "truncate": "END"},
        )
    )
    datos = sorted(respuesta.data, key=lambda dato: dato.index)
    if [dato.index for dato in datos] != list(range(len(textos))):
        raise RuntimeError("NVIDIA devolvio indices de embeddings incompletos o duplicados")
    vectores = [dato.embedding for dato in datos]
    if (
        not vectores
        or not vectores[0]
        or len({len(v) for v in vectores}) != 1
        or any(not math.isfinite(x) for v in vectores for x in v)
    ):
        raise RuntimeError("NVIDIA devolvio embeddings invalidos")
    return vectores


async def completar(
    *, modelo: str | None, variable: str, sistema: str, usuario: str, max_tokens: int = 2000
) -> str:
    """Respuesta de texto de un modelo de chat de NVIDIA (filtro de secciones, chat).

    S-07: el contenido de los documentos va en `usuario`, delimitado como datos; `sistema`
    lleva solo las instrucciones. R-06: el endpoint puede ignorar la petición de salida en
    JSON, así que quien llama valida la respuesta contra su contrato.
    """
    if not modelo:
        raise ModeloNoConfiguradoError(f"{variable} no está configurada")
    respuesta = await llamar(
        lambda: get_client().chat.completions.create(
            model=modelo,
            messages=[
                {"role": "system", "content": sistema},
                {"role": "user", "content": usuario},
            ],
            temperature=0,
            max_tokens=max_tokens,
        )
    )
    return respuesta.choices[0].message.content or ""


def _cliente_http() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))


async def reordenar(consulta: str, pasajes: list[str]) -> list[tuple[int, float]]:
    """Reordena los pasajes por relevancia para la consulta con el reranker de NVIDIA (P-11).

    Devuelve `(índice del pasaje, puntaje)` de mayor a menor relevancia. El reranker no es
    compatible con el SDK de OpenAI: se llama por HTTP a `NVIDIA_RERANK_URL`, con el mismo
    límite por minuto y los mismos reintentos que el resto de llamadas a NVIDIA.
    """
    settings = get_settings()
    if not settings.nvidia_model_rerank or not settings.nvidia_rerank_url:
        raise ModeloNoConfiguradoError("NVIDIA_MODEL_RERANK o NVIDIA_RERANK_URL sin configurar")
    if settings.nvidia_api_key is None:
        raise ModeloNoConfiguradoError("NVIDIA_API_KEY no está configurada")
    cabeceras = {
        "Authorization": f"Bearer {settings.nvidia_api_key.get_secret_value()}",
        "Accept": "application/json",
    }
    cuerpo = {
        "model": settings.nvidia_model_rerank,
        "query": {"text": consulta},
        "passages": [{"text": pasaje} for pasaje in pasajes],
        "truncate": "END",
    }

    async def pedir() -> Any:
        try:
            async with _cliente_http() as cliente:
                respuesta = await cliente.post(
                    settings.nvidia_rerank_url or "", headers=cabeceras, json=cuerpo
                )
        except httpx.HTTPError as exc:
            raise TransitorioError(None) from exc
        if respuesta.status_code == 429 or respuesta.status_code >= 500:
            raise TransitorioError(respuesta.status_code, respuesta.headers.get("retry-after"))
        if respuesta.status_code >= 400:
            raise RuntimeError(f"El reranker respondió {respuesta.status_code}")
        return respuesta.json()

    datos = await llamar(pedir)
    try:
        orden = [(int(r["index"]), float(r["logit"])) for r in datos["rankings"]]
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("La respuesta del reranker no tiene el formato esperado") from exc
    if any(not 0 <= indice < len(pasajes) for indice, _ in orden):
        raise RuntimeError("El reranker devolvió un índice fuera de rango")
    return sorted(orden, key=lambda par: par[1], reverse=True)
