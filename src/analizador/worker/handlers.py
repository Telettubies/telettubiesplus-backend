"""Manejo de cada tipo de trabajo (F-02, F-03, F-05) y política ante fallos (HU-B09).

- El mensaje se confirma a RabbitMQ solo cuando el trabajo termina.
- Si el trabajo falla queda en error, con la etapa en que falló, y el mensaje se rechaza sin
  reencolar (va a la cola de fallidos, P-15): no se reintenta solo. El usuario lo reintenta
  desde la API y el análisis continúa donde quedó.
- Si el worker muere a mitad, RabbitMQ vuelve a entregar el mensaje. Tras
  `PIPELINE_MAX_INTENTOS` entregas el trabajo se da por fallido: un mensaje que tumba el
  worker no se reintenta indefinidamente.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from aio_pika.abc import AbstractIncomingMessage
from pydantic import ValidationError

from analizador.core.config import get_settings
from analizador.db.session import get_sessionmaker
from analizador.messaging.mensajes import MensajeTrabajo
from analizador.models import TrabajoProcesamiento
from analizador.models.enums import EstadoLicitacion, EstadoTrabajo, TipoTrabajo
from analizador.pipeline import catalogo, etapas, grafo, indexacion, progreso
from analizador.pipeline.errores import AnalisisError
from analizador.pipeline.estado import EstadoAnalisis

logger = logging.getLogger(__name__)

Handler = Callable[[TrabajoProcesamiento, Any], Awaitable[None]]

MENSAJE_GENERICO = "El procesamiento falló. Puede reintentarlo."
MENSAJE_DEMASIADOS_INTENTOS = (
    "El procesamiento se interrumpió varias veces y se detuvo. Puede reintentarlo."
)
MENSAJE_TIEMPO_AGOTADO = "El procesamiento superó el tiempo máximo. Puede reintentarlo."


def _estado(trabajo: TrabajoProcesamiento) -> EstadoAnalisis:
    if trabajo.licitacion_id is None:
        raise ValueError(f"El trabajo {trabajo.id} no tiene licitación asociada")
    return EstadoAnalisis(
        trabajo_id=str(trabajo.id),
        licitacion_id=str(trabajo.licitacion_id),
        organizacion_id=str(trabajo.organizacion_id),
    )


async def _analisis(trabajo: TrabajoProcesamiento, grafo_analisis: Any) -> None:
    estado = _estado(trabajo)
    await progreso.marcar_licitacion(
        uuid.UUID(estado["licitacion_id"]), EstadoLicitacion.PROCESANDO
    )
    await grafo.ejecutar(grafo_analisis, estado)


async def _reevaluacion(trabajo: TrabajoProcesamiento, _grafo: Any) -> None:
    # F-05: tras una revisión humana se recalculan brechas y recomendación (RF-14).
    estado = _estado(trabajo)
    await etapas.decidir(estado)
    await etapas.justificar(estado)


async def _indexacion(trabajo: TrabajoProcesamiento, _grafo: Any) -> None:
    # F-02: fragmentar la base de conocimiento, vectorizarla y guardarla en pgvector.
    await indexacion.indexar(trabajo)


HANDLERS: dict[TipoTrabajo, Handler] = {
    TipoTrabajo.ANALISIS: _analisis,
    TipoTrabajo.REEVALUACION: _reevaluacion,
    TipoTrabajo.INDEXACION: _indexacion,
}


async def _leer_trabajo(trabajo_id: uuid.UUID) -> TrabajoProcesamiento | None:
    async with get_sessionmaker()() as session:
        return await session.get(TrabajoProcesamiento, trabajo_id)


async def _mensaje_de_fallo(trabajo_id: uuid.UUID, exc: BaseException) -> str:
    """Mensaje para el usuario: nunca incluye el texto de la excepción (S-14)."""
    if isinstance(exc, AnalisisError):
        return exc.mensaje_usuario
    if isinstance(exc, TimeoutError):
        return MENSAJE_TIEMPO_AGOTADO
    trabajo = await _leer_trabajo(trabajo_id)
    etapa = catalogo.POR_CLAVE.get(trabajo.etapa_actual or "") if trabajo else None
    if etapa is None:
        return MENSAJE_GENERICO
    return f"El análisis falló en la etapa «{etapa.nombre}». Puede reintentarlo."


async def _fallar(trabajo: TrabajoProcesamiento, tipo: TipoTrabajo, mensaje: str) -> None:
    await progreso.fallar(trabajo.id, mensaje)
    if tipo == TipoTrabajo.ANALISIS and trabajo.licitacion_id is not None:
        await progreso.marcar_licitacion(trabajo.licitacion_id, EstadoLicitacion.FALLIDA)


async def procesar(
    mensaje: AbstractIncomingMessage, tipo: TipoTrabajo, grafo_analisis: Any
) -> None:
    try:
        trabajo_id = MensajeTrabajo.model_validate_json(mensaje.body).trabajo_id
    except ValidationError:
        logger.error("Mensaje no procesable en la cola %s; enviado a fallidos", tipo.value)
        await mensaje.reject(requeue=False)
        return

    trabajo = await _leer_trabajo(trabajo_id)
    if trabajo is None or trabajo.estado == EstadoTrabajo.COMPLETADO:
        # Mensaje huérfano o entregado dos veces: no hay nada que repetir.
        await mensaje.ack()
        return

    settings = get_settings()
    if trabajo.intentos >= settings.pipeline_max_intentos:
        logger.error("Trabajo %s abandonado tras %d entregas", trabajo_id, trabajo.intentos)
        await _fallar(trabajo, tipo, MENSAJE_DEMASIADOS_INTENTOS)
        await mensaje.reject(requeue=False)
        return

    logger.info("Trabajo %s (%s) iniciado", trabajo_id, tipo.value)
    await progreso.iniciar(trabajo_id)
    try:
        async with asyncio.timeout(settings.pipeline_timeout_segundos):
            await HANDLERS[tipo](trabajo, grafo_analisis)
    except Exception as exc:
        # Un fallo (de un proveedor externo, de una etapa) no detiene el worker: el trabajo
        # queda en error y se atiende el siguiente mensaje.
        logger.exception("Trabajo %s (%s) fallido", trabajo_id, tipo.value)
        await _fallar(trabajo, tipo, await _mensaje_de_fallo(trabajo_id, exc))
        await mensaje.reject(requeue=False)
        return

    await progreso.completar(trabajo_id)
    await mensaje.ack()
    logger.info("Trabajo %s (%s) completado", trabajo_id, tipo.value)
