"""Proveedor de modelos NVIDIA (C-09, ADR-002): filtro de secciones, embeddings y chat.

build.nvidia.com expone una API compatible con OpenAI, por eso se usa el SDK `openai`
apuntando a NVIDIA_BASE_URL. Modelos configurables por entorno (P-19); P-10 y P-11 abiertas.

Toda llamada pasa por `llamar`, que aplica el límite de peticiones por minuto y reintenta con
espera ante errores de límite de velocidad o de disponibilidad (HU-B26, R-02, Q-13).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import Literal

import openai
from openai import AsyncOpenAI

from analizador.core.config import get_settings
from analizador.integrations.anthropic_client import ModeloNoConfiguradoError
from analizador.integrations.limitador import LimitadorDeTasa

logger = logging.getLogger(__name__)

# 429, errores de red o de tiempo de espera y 5xx: tiene sentido volver a intentarlo.
REINTENTABLES = (openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError)

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
    if isinstance(exc, openai.APIStatusError):
        try:
            return min(float(exc.response.headers["retry-after"]), maxima)
        except (KeyError, ValueError):
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
            logger.warning(
                "NVIDIA respondió %s; reintento %d de %d en %.0f s",
                type(exc).__name__,
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
    return [dato.embedding for dato in respuesta.data]
