"""Proveedor de modelos Anthropic (C-10, ADR-002): extracción y verificación.

Las salidas se validan contra esquemas Pydantic (S-07, R-06) mediante `messages.parse`.
Los modelos se eligen por entorno (P-19); P-10 sigue abierta.
"""

from functools import lru_cache

import anthropic
from anthropic.types import MessageParam
from pydantic import BaseModel

from analizador.core.config import get_settings


class ModeloNoConfiguradoError(RuntimeError):
    pass


class RespuestaRechazadaError(RuntimeError):
    """El modelo se negó a responder o la salida no se pudo validar."""


@lru_cache
def get_client() -> anthropic.AsyncAnthropic:
    settings = get_settings()
    if settings.anthropic_api_key is None:
        raise ModeloNoConfiguradoError("ANTHROPIC_API_KEY no está configurada")
    return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())


def modelo(nombre: str | None, variable: str) -> str:
    if not nombre:
        raise ModeloNoConfiguradoError(f"{variable} no está configurada (P-10 / P-19)")
    return nombre


async def generar_estructurado[T: BaseModel](
    *,
    model: str,
    system: str,
    messages: list[MessageParam],
    output_format: type[T],
    max_tokens: int = 16000,
) -> T:
    """Llamada con salida estructurada validada.

    S-07: el contenido de los documentos debe ir en `messages` delimitado como datos,
    nunca concatenado a las instrucciones de `system`.
    """
    respuesta = await get_client().messages.parse(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=messages,
        output_format=output_format,
    )
    if respuesta.stop_reason == "refusal":
        raise RespuestaRechazadaError("El modelo rechazó la solicitud")
    if respuesta.stop_reason == "max_tokens":
        raise RespuestaRechazadaError("Respuesta truncada por max_tokens")
    if respuesta.parsed_output is None:
        raise RespuestaRechazadaError("La salida no cumple el esquema")
    return respuesta.parsed_output
