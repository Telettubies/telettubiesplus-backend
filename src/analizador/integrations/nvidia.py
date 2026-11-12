"""Proveedor de modelos NVIDIA (C-09, ADR-002): filtro de secciones, embeddings y chat.

build.nvidia.com expone una API compatible con OpenAI, por eso se usa el SDK `openai`
apuntando a NVIDIA_BASE_URL. Modelos configurables por entorno (P-19); P-10 y P-11 abiertas.
R-02: créditos y límites de velocidad por confirmar (Q-13).
"""

from functools import lru_cache
from typing import Literal

from openai import AsyncOpenAI

from analizador.core.config import get_settings
from analizador.integrations.anthropic_client import ModeloNoConfiguradoError


@lru_cache
def get_client() -> AsyncOpenAI:
    settings = get_settings()
    if settings.nvidia_api_key is None:
        raise ModeloNoConfiguradoError("NVIDIA_API_KEY no está configurada")
    return AsyncOpenAI(
        api_key=settings.nvidia_api_key.get_secret_value(),
        base_url=settings.nvidia_base_url,
    )


async def embeddings(textos: list[str], tipo: Literal["query", "passage"]) -> list[list[float]]:
    """Embeddings para pgvector (C-06). `tipo` distingue consultas de pasajes indexados."""
    settings = get_settings()
    if not settings.nvidia_model_embeddings:
        raise ModeloNoConfiguradoError("NVIDIA_MODEL_EMBEDDINGS no está configurada")
    respuesta = await get_client().embeddings.create(
        model=settings.nvidia_model_embeddings,
        input=textos,
        encoding_format="float",
        extra_body={"input_type": tipo, "truncate": "END"},
    )
    return [dato.embedding for dato in respuesta.data]
