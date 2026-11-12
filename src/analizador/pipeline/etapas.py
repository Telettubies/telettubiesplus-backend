"""Etapas del pipeline de análisis (sección 5.3).

Todas son STUBS: registran su avance en PostgreSQL y no hacen nada más. Cada TODO indica
la responsabilidad, el ejecutor y el estado de la decisión según el DAS (v0.3).
Reparto (10.5): etapas 1-5 y 4b → persona 2; etapas 6-10 → persona 3.
"""

import uuid
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any

from analizador.models.enums import EstadoLicitacion
from analizador.pipeline import catalogo, lectura, progreso, secciones
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
    # TODO(etapa 3): marcar `secciones.tiene_requisitos` con el modelo de NVIDIA
    # (integrations.nvidia, NVIDIA_MODEL_FILTRO).
    return {}


@etapa("extraccion")
async def extraer(estado: EstadoAnalisis) -> dict[str, Any]:
    # TODO(etapa 4): una llamada por sección relevante con el modelo de Anthropic
    # (integrations.anthropic_client.generar_estructurado). Persistir requisitos e ítems.
    # Marcar `ambiguo` en lugar de asumir (RF-07). Documento como datos, no instrucciones (S-07).
    return {}


@etapa("localizacion")
async def localizar(estado: EstadoAnalisis) -> dict[str, Any]:
    # TODO(etapa 4b): buscar el texto literal de cada requisito en las páginas de su sección
    # y guardar `ubicaciones_pliego` (P-21, RF-26). Degradar a aproximada o solo página.
    return {}


@etapa("comprobacion_cobertura")
async def comprobar_cobertura(estado: EstadoAnalisis) -> dict[str, Any]:
    # TODO(etapa 5): verificar que todas las secciones quedaron `procesada` (ADR-012, N-04).
    return {}


@etapa("recuperacion_evidencia")
async def recuperar_evidencia(estado: EstadoAnalisis) -> dict[str, Any]:
    # TODO(etapa 6): embeddings de NVIDIA + pgvector, filtrando SIEMPRE por organizacion_id
    # (S-03). Reranker opcional (P-11).
    return {}


@etapa("verificacion")
async def verificar(estado: EstadoAnalisis) -> dict[str, Any]:
    # TODO(etapa 7): estado de cada ítem (RF-04). Numéricos por código [P-03];
    # cualitativos con el modelo de Anthropic. Persistir `resultados`.
    return {}


@etapa("validacion_citas")
async def validar_citas(estado: EstadoAnalisis) -> dict[str, Any]:
    # TODO(etapa 8): coincidencia difusa de cada cita con su fuente [P-04];
    # si no coincide → `requiere_revision`.
    return {}


@etapa("decision")
async def decidir(estado: EstadoAnalisis) -> dict[str, Any]:
    # TODO(etapa 9): brechas y recomendación por reglas [P-05]. Reglas sin definir (Q-04).
    return {}


@etapa("justificacion")
async def justificar(estado: EstadoAnalisis) -> dict[str, Any]:
    # TODO(etapa 10): redactar la justificación con el modelo de Anthropic [P].
    return {}


@etapa("cierre")
async def cerrar(estado: EstadoAnalisis) -> dict[str, Any]:
    # Etapa 11: marcar la licitación como completada. El trabajo lo cierra el worker.
    await progreso.marcar_licitacion(
        uuid.UUID(estado["licitacion_id"]), EstadoLicitacion.COMPLETADA
    )
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
