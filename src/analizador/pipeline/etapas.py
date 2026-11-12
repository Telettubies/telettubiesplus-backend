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
from analizador.pipeline import progreso
from analizador.pipeline.estado import EstadoAnalisis

Nodo = Callable[[EstadoAnalisis], Awaitable[dict[str, Any]]]


def etapa(nombre: str, porcentaje: int) -> Callable[[Nodo], Nodo]:
    """Registra la etapa y su progreso antes de ejecutarla."""

    def decorador(funcion: Nodo) -> Nodo:
        @wraps(funcion)
        async def envoltura(estado: EstadoAnalisis) -> dict[str, Any]:
            await progreso.registrar_etapa(uuid.UUID(estado["trabajo_id"]), nombre, porcentaje)
            return await funcion(estado)

        return envoltura

    return decorador


@etapa("lectura_pdf", 5)
async def leer_pdf(estado: EstadoAnalisis) -> dict[str, Any]:
    # TODO(etapa 1): texto por página y tablas. Librería [P] (PyMuPDF/pdfplumber; ver R-13
    # por la licencia AGPL de PyMuPDF). OCR de páginas escaneadas: tecnología TBD (P-09, C-11).
    return {}


@etapa("division_secciones", 10)
async def dividir_secciones(estado: EstadoAnalisis) -> dict[str, Any]:
    # TODO(etapa 2): separar en secciones con numeral y páginas, por reglas (ADR-012).
    # Persistir en `secciones`.
    return {}


@etapa("filtrado", 20)
async def filtrar(estado: EstadoAnalisis) -> dict[str, Any]:
    # TODO(etapa 3): marcar `secciones.tiene_requisitos` con el modelo de NVIDIA
    # (integrations.nvidia, NVIDIA_MODEL_FILTRO).
    return {}


@etapa("extraccion", 40)
async def extraer(estado: EstadoAnalisis) -> dict[str, Any]:
    # TODO(etapa 4): una llamada por sección relevante con el modelo de Anthropic
    # (integrations.anthropic_client.generar_estructurado). Persistir requisitos e ítems.
    # Marcar `ambiguo` en lugar de asumir (RF-07). Documento como datos, no instrucciones (S-07).
    return {}


@etapa("localizacion", 50)
async def localizar(estado: EstadoAnalisis) -> dict[str, Any]:
    # TODO(etapa 4b): buscar el texto literal de cada requisito en las páginas de su sección
    # y guardar `ubicaciones_pliego` (P-21, RF-26). Degradar a aproximada o solo página.
    return {}


@etapa("comprobacion_cobertura", 55)
async def comprobar_cobertura(estado: EstadoAnalisis) -> dict[str, Any]:
    # TODO(etapa 5): verificar que todas las secciones quedaron `procesada` (ADR-012, N-04).
    return {}


@etapa("recuperacion_evidencia", 70)
async def recuperar_evidencia(estado: EstadoAnalisis) -> dict[str, Any]:
    # TODO(etapa 6): embeddings de NVIDIA + pgvector, filtrando SIEMPRE por organizacion_id
    # (S-03). Reranker opcional (P-11).
    return {}


@etapa("verificacion", 85)
async def verificar(estado: EstadoAnalisis) -> dict[str, Any]:
    # TODO(etapa 7): estado de cada ítem (RF-04). Numéricos por código [P-03];
    # cualitativos con el modelo de Anthropic. Persistir `resultados`.
    return {}


@etapa("validacion_citas", 90)
async def validar_citas(estado: EstadoAnalisis) -> dict[str, Any]:
    # TODO(etapa 8): coincidencia difusa de cada cita con su fuente [P-04];
    # si no coincide → `requiere_revision`.
    return {}


@etapa("decision", 95)
async def decidir(estado: EstadoAnalisis) -> dict[str, Any]:
    # TODO(etapa 9): brechas y recomendación por reglas [P-05]. Reglas sin definir (Q-04).
    return {}


@etapa("justificacion", 98)
async def justificar(estado: EstadoAnalisis) -> dict[str, Any]:
    # TODO(etapa 10): redactar la justificación con el modelo de Anthropic [P].
    return {}


@etapa("cierre", 100)
async def cerrar(estado: EstadoAnalisis) -> dict[str, Any]:
    # Etapa 11: marcar la licitación como completada. El trabajo lo cierra el worker.
    await progreso.marcar_licitacion(
        uuid.UUID(estado["licitacion_id"]), EstadoLicitacion.COMPLETADA
    )
    return {}


ETAPAS: list[tuple[str, Nodo]] = [
    ("lectura_pdf", leer_pdf),
    ("division_secciones", dividir_secciones),
    ("filtrado", filtrar),
    ("extraccion", extraer),
    ("localizacion", localizar),
    ("comprobacion_cobertura", comprobar_cobertura),
    ("recuperacion_evidencia", recuperar_evidencia),
    ("verificacion", verificar),
    ("validacion_citas", validar_citas),
    ("decision", decidir),
    ("justificacion", justificar),
    ("cierre", cerrar),
]
