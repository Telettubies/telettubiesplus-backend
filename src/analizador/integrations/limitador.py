"""Límite de peticiones por minuto hacia un proveedor externo (S-08, R-02, HU-B26)."""

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable


class LimitadorDeTasa:
    """Ventana deslizante: como máximo `maximo` llamadas en cualquier tramo de `ventana` segundos.

    Quien llama espera su turno con `await limitador.esperar()`. Vale para un solo proceso: la
    API y el worker tienen cada uno el suyo.
    """

    def __init__(
        self,
        maximo: int,
        ventana: float = 60.0,
        reloj: Callable[[], float] = time.monotonic,
        dormir: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if maximo <= 0:
            raise ValueError("El máximo de peticiones debe ser positivo")
        self._maximo = maximo
        self._ventana = ventana
        self._reloj = reloj
        self._dormir = dormir
        self._marcas: deque[float] = deque()
        self._candado = asyncio.Lock()

    async def esperar(self) -> None:
        async with self._candado:
            while True:
                ahora = self._reloj()
                while self._marcas and ahora - self._marcas[0] >= self._ventana:
                    self._marcas.popleft()
                if len(self._marcas) < self._maximo:
                    self._marcas.append(ahora)
                    return
                await self._dormir(self._ventana - (ahora - self._marcas[0]))
