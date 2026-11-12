"""Manejo consistente de errores de la API.

Los errores previstos se lanzan como `HTTPException` con un mensaje para el usuario. Cualquier
otra excepción responde 500 con un mensaje genérico: el detalle queda solo en el log del
servidor, nunca en la respuesta (S-14; HU-B08: sin trazas, claves ni rutas internas).
"""

import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

MENSAJE_ERROR_INTERNO = "Error interno del servidor"


async def _error_no_controlado(request: Request, exc: Exception) -> JSONResponse:
    logger.error("Error no controlado en %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": MENSAJE_ERROR_INTERNO},
    )


def registrar(app: FastAPI) -> None:
    app.add_exception_handler(Exception, _error_no_controlado)
