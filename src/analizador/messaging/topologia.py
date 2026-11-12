"""Topología de RabbitMQ (C-04, P-15).

    exchange "analizador" (direct)
        ├─ analisis      → cola analizador.analisis      (F-03)
        ├─ indexacion    → cola analizador.indexacion    (F-02)
        └─ reevaluacion  → cola analizador.reevaluacion  (F-05)

    Un mensaje rechazado sin reencolar va al exchange "analizador.dlx" (fanout)
    → cola analizador.fallidos, para inspección manual.

La API y el worker declaran la misma topología al arrancar; la declaración es idempotente.
"""

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractExchange, AbstractQueue

from analizador.models.enums import TipoTrabajo

EXCHANGE = "analizador"
EXCHANGE_DLX = "analizador.dlx"
COLA_FALLIDOS = "analizador.fallidos"


def nombre_cola(tipo: TipoTrabajo) -> str:
    return f"analizador.{tipo.value}"


async def declarar(
    channel: AbstractChannel,
) -> tuple[AbstractExchange, dict[TipoTrabajo, AbstractQueue]]:
    dlx = await channel.declare_exchange(EXCHANGE_DLX, aio_pika.ExchangeType.FANOUT, durable=True)
    fallidos = await channel.declare_queue(COLA_FALLIDOS, durable=True)
    await fallidos.bind(dlx)

    exchange = await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.DIRECT, durable=True)
    colas: dict[TipoTrabajo, AbstractQueue] = {}
    for tipo in TipoTrabajo:
        cola = await channel.declare_queue(
            nombre_cola(tipo),
            durable=True,
            arguments={"x-dead-letter-exchange": EXCHANGE_DLX},
        )
        await cola.bind(exchange, routing_key=tipo.value)
        colas[tipo] = cola
    return exchange, colas
