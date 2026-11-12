"""Manejo de cada tipo de trabajo (F-02, F-03, F-05)."""

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from aio_pika.abc import AbstractIncomingMessage
from pydantic import ValidationError

from analizador.db.session import get_sessionmaker
from analizador.messaging.mensajes import MensajeTrabajo
from analizador.models import TrabajoProcesamiento
from analizador.models.enums import EstadoLicitacion, EstadoTrabajo, TipoTrabajo
from analizador.pipeline import etapas, grafo, progreso
from analizador.pipeline.estado import EstadoAnalisis

logger = logging.getLogger(__name__)

Handler = Callable[[TrabajoProcesamiento, Any], Awaitable[None]]


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
    # TODO(F-02): fragmentar la fuente de conocimiento, generar embeddings (NVIDIA) y
    # guardarlos en `fragmentos`. Depende de Q-05 (formato de carga) y P-10 (modelo).
    logger.warning("Indexación sin implementar (trabajo %s)", trabajo.id)


HANDLERS: dict[TipoTrabajo, Handler] = {
    TipoTrabajo.ANALISIS: _analisis,
    TipoTrabajo.REEVALUACION: _reevaluacion,
    TipoTrabajo.INDEXACION: _indexacion,
}


async def procesar(
    mensaje: AbstractIncomingMessage, tipo: TipoTrabajo, grafo_analisis: Any
) -> None:
    try:
        trabajo_id = MensajeTrabajo.model_validate_json(mensaje.body).trabajo_id
    except ValidationError:
        logger.error("Mensaje no procesable en la cola %s; enviado a fallidos", tipo.value)
        await mensaje.reject(requeue=False)
        return

    async with get_sessionmaker()() as session:
        trabajo = await session.get(TrabajoProcesamiento, trabajo_id)

    if trabajo is None or trabajo.estado == EstadoTrabajo.COMPLETADO:
        # Mensaje huérfano o duplicado: nada que hacer.
        await mensaje.ack()
        return

    logger.info("Trabajo %s (%s) iniciado", trabajo_id, tipo.value)
    await progreso.iniciar(trabajo_id)
    try:
        await HANDLERS[tipo](trabajo, grafo_analisis)
    except Exception:
        logger.exception("Trabajo %s (%s) fallido", trabajo_id, tipo.value)
        await progreso.fallar(trabajo_id, "El procesamiento falló. Puede reintentarse.")
        if tipo == TipoTrabajo.ANALISIS and trabajo.licitacion_id is not None:
            await progreso.marcar_licitacion(trabajo.licitacion_id, EstadoLicitacion.FALLIDA)
        # Sin reencolar: va a la cola de fallidos (P-15). Política de reintentos: pendiente.
        await mensaje.reject(requeue=False)
        return

    await progreso.completar(trabajo_id)
    await mensaje.ack()
    logger.info("Trabajo %s (%s) completado", trabajo_id, tipo.value)
