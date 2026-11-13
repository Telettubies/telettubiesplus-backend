"""Punto de entrada de la API de backend (C-02)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from analizador.api import errores
from analizador.api.routers import (
    conocimiento,
    health,
    licitaciones,
    limites,
    miembros,
    organizaciones,
    resultados,
    yo,
)
from analizador.core.config import Settings, get_settings
from analizador.core.logging import configure_logging
from analizador.db.session import get_engine
from analizador.messaging.publicador import Publicador


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    publicador = Publicador(settings.rabbitmq_url.get_secret_value())
    await publicador.conectar()
    app.state.publicador = publicador
    try:
        yield
    finally:
        await publicador.cerrar()
        await get_engine().dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    docs_activos = settings.app_env != "production" or settings.exponer_docs
    app = FastAPI(
        title="Analizador de pliegos",
        version="0.1.0",
        lifespan=lifespan,
        # En producción la documentación interactiva solo se publica con EXPONER_DOCS=true.
        docs_url="/docs" if docs_activos else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs_activos else None,
    )
    # S-13: CORS limitado al dominio del frontend.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Organizacion-Id"],
    )
    for router in (
        health.router,
        yo.router,
        miembros.router,
        organizaciones.router,
        licitaciones.router,
        resultados.router,
        conocimiento.router,
        limites.router,
    ):
        app.include_router(router)
    errores.registrar(app)
    return app


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "analizador.api.main:create_app",
        factory=True,
        host="0.0.0.0",  # noqa: S104  # dentro del contenedor; solo el proxy se publica (S-01)
        port=8000,
        reload=settings.app_env == "development",
        proxy_headers=True,
    )
