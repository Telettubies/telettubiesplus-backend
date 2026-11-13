"""Etapas del pipeline de análisis (sección 5.3), como nodos del grafo.

Cada nodo registra su avance en PostgreSQL (decorador `etapa`) y delega en el módulo de su
etapa. Las que todavía son stubs llevan un TODO con su responsabilidad y la decisión del DAS.
"""

import uuid
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any

from analizador.pipeline import (
    catalogo,
    citas,
    cobertura,
    decision,
    evidencia,
    extraccion,
    filtrado,
    indexacion_pliego,
    lectura,
    localizacion,
    progreso,
    secciones,
    verificacion,
)
from analizador.pipeline.estado import EstadoAnalisis

Nodo = Callable[[EstadoAnalisis], Awaitable[dict[str, Any]]]


def etapa(clave: str) -> Callable[[Nodo], Nodo]:
    """Registra la etapa y su progreso (según el catálogo) antes de ejecutarla."""
    definicion = catalogo.POR_CLAVE[clave]

    def decorador(funcion: Nodo) -> Nodo:
        @wraps(funcion)
        async def envoltura(estado: EstadoAnalisis) -> dict[str, Any]:
            await progreso.registrar_etapa(
                uuid.UUID(estado["trabajo_id"]), definicion.clave, definicion.porcentaje
            )
            return await funcion(estado)

        return envoltura

    return decorador


@etapa("lectura_pdf")
async def leer_pdf(estado: EstadoAnalisis) -> dict[str, Any]:
    # Etapa 1: texto y tablas por página; OCR y visión para las escaneadas (HU-B10).
    await lectura.ejecutar(estado)
    return {}


@etapa("division_secciones")
async def dividir_secciones(estado: EstadoAnalisis) -> dict[str, Any]:
    # Etapa 2: secciones con numeral y páginas, por reglas (ADR-012, HU-B11).
    await secciones.ejecutar(estado)
    return {}


@etapa("filtrado")
async def filtrar(estado: EstadoAnalisis) -> dict[str, Any]:
    # Etapa 3: descartar con NVIDIA las secciones sin requisitos (HU-B12).
    await filtrado.ejecutar(estado)
    return {}


@etapa("extraccion")
async def extraer(estado: EstadoAnalisis) -> dict[str, Any]:
    # Etapa 4: requisitos e ítems verificables de cada sección relevante, con Claude (HU-B13).
    await extraccion.ejecutar(estado)
    return {}


@etapa("localizacion")
async def localizar(estado: EstadoAnalisis) -> dict[str, Any]:
    # Etapa 4b: página y zonas a resaltar de cada requisito, calculadas por código (HU-B15).
    await localizacion.ejecutar(estado)
    return {}


@etapa("comprobacion_cobertura")
async def comprobar_cobertura(estado: EstadoAnalisis) -> dict[str, Any]:
    # Etapa 5: reintentar lo que falte y dejar identificado lo que no se pudo procesar (HU-B14).
    await cobertura.ejecutar(estado)
    # Los requisitos de las secciones recuperadas en los reintentos aún no tienen ubicación.
    await localizacion.ejecutar(estado)
    return {}


@etapa("recuperacion_evidencia")
async def recuperar_evidencia(estado: EstadoAnalisis) -> dict[str, Any]:
    # Etapa 6: fragmentos de la base de conocimiento más cercanos a cada ítem (HU-B16) e
    # índice del pliego para el chat, de mejor esfuerzo (HU-B24).
    await evidencia.ejecutar(estado)
    await indexacion_pliego.ejecutar(estado)
    return {}


@etapa("verificacion")
async def verificar(estado: EstadoAnalisis) -> dict[str, Any]:
    # Etapa 7: estado de cada ítem; numéricos por código, cualitativos con Claude (HU-B17).
    await verificacion.ejecutar(estado)
    return {}


@etapa("validacion_citas")
async def validar_citas(estado: EstadoAnalisis) -> dict[str, Any]:
    # Etapa 8: cada cita se comprueba contra su fuente; si no coincide, a revisión (HU-B18).
    await citas.ejecutar(estado)
    return {}


@etapa("decision")
async def decidir(estado: EstadoAnalisis) -> dict[str, Any]:
    await decision.preparar(estado)
    return {}


@etapa("justificacion")
async def justificar(estado: EstadoAnalisis) -> dict[str, Any]:
    await decision.justificar(estado)
    return {}


@etapa("cierre")
async def cerrar(estado: EstadoAnalisis) -> dict[str, Any]:
    await decision.cerrar(estado)
    return {}


_NODOS: dict[str, Nodo] = {
    "lectura_pdf": leer_pdf,
    "division_secciones": dividir_secciones,
    "filtrado": filtrar,
    "extraccion": extraer,
    "localizacion": localizar,
    "comprobacion_cobertura": comprobar_cobertura,
    "recuperacion_evidencia": recuperar_evidencia,
    "verificacion": verificar,
    "validacion_citas": validar_citas,
    "decision": decidir,
    "justificacion": justificar,
    "cierre": cerrar,
}

# El orden lo fija el catálogo; aquí solo se asocia cada etapa con su nodo.
ETAPAS: list[tuple[str, Nodo]] = [(e.clave, _NODOS[e.clave]) for e in catalogo.ETAPAS]
