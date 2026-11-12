"""Etapa 5: comprobar que se analizó todo el pliego (ADR-012, N-04, R-03, HU-B14).

Es el control principal de cobertura del sistema: el riesgo que más pesa no es leer despacio,
sino pasar por alto un requisito que descalifica. Tras la extracción se cuentan las secciones
procesadas, se reintentan las que quedaron en error o pendientes y, si al final alguna sigue
sin procesar, queda identificada con su numeral. Nunca se oculta: el resultado la muestra y la
recomendación queda condicionada (HU-B19).
"""

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.core.config import get_settings
from analizador.db.session import get_sessionmaker
from analizador.integrations.anthropic_client import Uso
from analizador.models import Seccion
from analizador.pipeline import extraccion, progreso
from analizador.pipeline.estado import EstadoAnalisis

logger = logging.getLogger(__name__)

MOTIVO_SIN_FILTRAR = "La sección no llegó a evaluarse"
MOTIVO_SIN_EXTRAER = "La sección no llegó a procesarse"


@dataclass(frozen=True)
class SeccionNoProcesada:
    numeral: str | None
    titulo: str | None
    pagina_inicio: int
    pagina_fin: int
    motivo: str


@dataclass(frozen=True)
class Cobertura:
    """Secciones del pliego analizadas sobre el total, p. ej. 152 de 152."""

    total: int
    procesadas: int
    # Incluidas en `procesadas`: las que el filtro descartó por no contener requisitos.
    descartadas: int
    no_procesadas: tuple[SeccionNoProcesada, ...]

    @property
    def completa(self) -> bool:
        return self.total > 0 and not self.no_procesadas


async def calcular(session: AsyncSession, licitacion_id: uuid.UUID) -> Cobertura:
    """Cobertura del pliego según el estado actual de sus secciones, sin llamar a ningún modelo."""
    secciones = list(
        await session.scalars(
            select(Seccion).where(Seccion.licitacion_id == licitacion_id).order_by(Seccion.orden)
        )
    )
    pendientes = tuple(
        SeccionNoProcesada(
            numeral=seccion.numeral,
            titulo=seccion.titulo,
            pagina_inicio=seccion.pagina_inicio,
            pagina_fin=seccion.pagina_fin,
            motivo=seccion.error
            or (MOTIVO_SIN_FILTRAR if seccion.tiene_requisitos is None else MOTIVO_SIN_EXTRAER),
        )
        for seccion in secciones
        if not seccion.procesada
    )
    return Cobertura(
        total=len(secciones),
        procesadas=len(secciones) - len(pendientes),
        descartadas=sum(1 for s in secciones if s.procesada and s.tiene_requisitos is False),
        no_procesadas=pendientes,
    )


async def _admitir_sin_filtrar(licitacion_id: uuid.UUID) -> None:
    """Una sección que nunca pasó por el filtro va a extracción: no se descarta por defecto."""
    async with get_sessionmaker()() as session:
        await session.execute(
            update(Seccion)
            .where(Seccion.licitacion_id == licitacion_id, Seccion.tiene_requisitos.is_(None))
            .values(tiene_requisitos=True, motivo_filtro=MOTIVO_SIN_FILTRAR)
        )
        await session.commit()


async def ejecutar(estado: EstadoAnalisis) -> None:
    licitacion_id = uuid.UUID(estado["licitacion_id"])
    trabajo_id = uuid.UUID(estado["trabajo_id"])
    await _admitir_sin_filtrar(licitacion_id)

    uso = Uso()
    reintentos = 0
    for _ in range(get_settings().cobertura_max_reintentos):
        pendientes = await extraccion.pendientes(licitacion_id)
        if not pendientes:
            break
        reintentos += 1
        logger.info(
            "Licitación %s: reintento %d de %d secciones sin procesar",
            licitacion_id,
            reintentos,
            len(pendientes),
        )
        uso += await extraccion.procesar(pendientes)
    if reintentos:
        await progreso.sumar_tokens(trabajo_id, "extraccion", uso)

    async with get_sessionmaker()() as session:
        cobertura = await calcular(session, licitacion_id)
    if cobertura.no_procesadas:
        logger.warning(
            "Licitación %s: %d de %d secciones sin procesar tras %d reintentos",
            licitacion_id,
            len(cobertura.no_procesadas),
            cobertura.total,
            reintentos,
        )
    await progreso.anotar_detalle(
        trabajo_id,
        cobertura={
            "procesadas": cobertura.procesadas,
            "total": cobertura.total,
            "descartadas": cobertura.descartadas,
            "reintentos": reintentos,
            "no_procesadas": [s.numeral or s.titulo or "s/n" for s in cobertura.no_procesadas],
        },
    )
