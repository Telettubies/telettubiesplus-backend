"""Punto de entrada del worker de análisis (C-03)."""

import asyncio
import contextlib
import logging
import signal
from collections.abc import AsyncIterator
from functools import partial
from typing import Any

import aio_pika
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from analizador.core.config import get_settings
from analizador.core.logging import configure_logging
from analizador.messaging import topologia
from analizador.pipeline import grafo
from analizador.worker.handlers import procesar

logger = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def _checkpointer() -> AsyncIterator[AsyncPostgresSaver | None]:
    settings = get_settings()
    if not settings.pipeline_checkpointer_enabled:
        yield None
        return
    async with AsyncPostgresSaver.from_conn_string(settings.psycopg_database_url) as saver:
        await saver.setup()  # crea sus tablas si no existen (idempotente)
        yield saver


async def _main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    parar = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, parar.set)

    async with _checkpointer() as checkpointer:
        grafo_analisis: Any = grafo.construir(checkpointer)
        conexion = await aio_pika.connect_robust(settings.rabbitmq_url.get_secret_value())
        async with conexion:
            canal = await conexion.channel()
            # R-09: un trabajo a la vez en la VM (2 vCPU / 8 GB).
            await canal.set_qos(prefetch_count=1)
            _, colas = await topologia.declarar(canal)
            for tipo, cola in colas.items():
                await cola.consume(partial(procesar, tipo=tipo, grafo_analisis=grafo_analisis))
            logger.info("Worker escuchando: %s", ", ".join(c.name for c in colas.values()))
            await parar.wait()
    logger.info("Worker detenido")


def run() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    run()
