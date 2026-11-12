"""Publicación de trabajos desde la API (C-02 → C-04)."""

import uuid

import aio_pika
from aio_pika.abc import AbstractExchange, AbstractRobustConnection

from analizador.messaging import topologia
from analizador.messaging.mensajes import MensajeTrabajo
from analizador.models.enums import TipoTrabajo


class Publicador:
    def __init__(self, url: str) -> None:
        self._url = url
        self._conexion: AbstractRobustConnection | None = None
        self._exchange: AbstractExchange | None = None

    async def conectar(self) -> None:
        self._conexion = await aio_pika.connect_robust(self._url)
        # publisher_confirms=True (por defecto): publish() espera la confirmación del broker.
        canal = await self._conexion.channel()
        self._exchange, _ = await topologia.declarar(canal)

    async def cerrar(self) -> None:
        if self._conexion is not None:
            await self._conexion.close()

    @property
    def conectado(self) -> bool:
        return self._conexion is not None and not self._conexion.is_closed

    async def publicar(self, tipo: TipoTrabajo, trabajo_id: uuid.UUID) -> None:
        if self._exchange is None:
            raise RuntimeError("Publicador no conectado")
        cuerpo = MensajeTrabajo(trabajo_id=trabajo_id).model_dump_json().encode()
        await self._exchange.publish(
            aio_pika.Message(
                cuerpo,
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                message_id=str(trabajo_id),
            ),
            routing_key=tipo.value,
        )
