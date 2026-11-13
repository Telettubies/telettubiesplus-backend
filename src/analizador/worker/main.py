"""Punto de entrada del worker de análisis (C-03)."""

import asyncio
import contextlib
import logging
import signal
import sys
from collections.abc import AsyncIterator
from functools import partial
from typing import Any

import aio_pika
from aio_pika.abc import AbstractChannel
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from analizador.core.config import get_settings
from analizador.core.logging import configure_logging
from analizador.messaging import topologia
from analizador.pipeline import grafo
from analizador.worker.handlers import procesar

logger = logging.getLogger(__name__)

MENSAJES_SIMULTANEOS = 1


@contextlib.asynccontextmanager
async def _checkpointer() -> AsyncIterator[AsyncPostgresSaver | None]:
    settings = get_settings()
    if not settings.pipeline_checkpointer_enabled:
        yield None
        return
    async with AsyncPostgresSaver.from_conn_string(settings.psycopg_database_url) as saver:
        await saver.setup()  # crea sus tablas si no existen (idempotente)
        yield saver


async def suscribir(canal: AbstractChannel, grafo_analisis: Any) -> None:
    """Consume las tres colas de trabajos, un mensaje a la vez en todo el worker.

    R-09 y HU-B09: la VM tiene 2 vCPU y 8 GB, así que solo corre un análisis a la vez. El
    prefetch de 1 por canal hace que RabbitMQ no entregue otro mensaje hasta confirmar el actual.
    """
    await canal.set_qos(prefetch_count=MENSAJES_SIMULTANEOS)
    _, colas = await topologia.declarar(canal)
    for tipo, cola in colas.items():
        await cola.consume(partial(procesar, tipo=tipo, grafo_analisis=grafo_analisis))
    logger.info("Worker escuchando: %s", ", ".join(c.name for c in colas.values()))


async def _main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    parar = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, parar.set)
        except NotImplementedError:
            # Windows no implementa add_signal_handler en sus bucles de asyncio.
            signal.signal(sig, lambda _sig, _frame: loop.call_soon_threadsafe(parar.set))

    async with _checkpointer() as checkpointer:
        grafo_analisis: Any = grafo.construir(checkpointer)
        conexion = await aio_pika.connect_robust(settings.rabbitmq_url.get_secret_value())
        async with conexion:
            await suscribir(await conexion.channel(), grafo_analisis)
            await parar.wait()
    logger.info("Worker detenido")


def run() -> None:
    if sys.platform == "win32":
        # Desarrollo local en Windows: psycopg async no admite el ProactorEventLoop por defecto.
        asyncio.run(_main(), loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(_main())


if __name__ == "__main__":
    run()
