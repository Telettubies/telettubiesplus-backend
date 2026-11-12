"""Enviar a revisión humana los ítems ambiguos o dudosos (RF-07, N-03, Q-14, HU-B20).

El sistema no asume: ante la duda, revisión, y nunca un estado favorable por defecto. Un ítem
pasa a revisión si:
- su requisito está marcado como ambiguo (lo marca la extracción, HU-B13);
- alguna de sus citas no se verificó contra la fuente (lo marca la validación, HU-B18);
- la confianza de su verificación está por debajo del umbral configurable;
- la verificación no pudo decidir (unidad incomparable, SMMLV sin configurar…, HU-B17).

Cada ítem en revisión indica todos sus motivos. «Revisión» no es un cuarto estado guardado
(RF-04, Q-17): es `requiere_revision` junto al estado propuesto, y `estado_visible` lo
presenta como tal. Un ítem en revisión nunca cuenta como «cumple» en la recomendación.
"""

import logging
import uuid
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from analizador.core.config import get_settings
from analizador.db.session import get_sessionmaker
from analizador.models import Item, Requisito, Resultado
from analizador.models.enums import EstadoCumplimiento
from analizador.pipeline import progreso
from analizador.pipeline.citas import agregar_motivo
from analizador.pipeline.estado import EstadoAnalisis

logger = logging.getLogger(__name__)


class EstadoItem(StrEnum):
    """Estado de un ítem tal como lo ve el usuario: los tres del reto más «revisión»."""

    CUMPLE = "cumple"
    CUMPLE_PARCIALMENTE = "cumple_parcialmente"
    NO_CUMPLE = "no_cumple"
    REVISION = "revision"


def estado_visible(estado: EstadoCumplimiento, requiere_revision: bool) -> EstadoItem:
    return EstadoItem.REVISION if requiere_revision else EstadoItem(estado.value)


def motivo_ambiguedad(motivo: str | None) -> str:
    detalle = (motivo or "").strip().rstrip(".")
    return f"El requisito es ambiguo: {detalle}." if detalle else "El requisito es ambiguo."


def motivo_confianza(confianza: float, minimo: float) -> str:
    return (
        f"La confianza de la verificación ({confianza:.0%}) es menor que el mínimo exigido "
        f"({minimo:.0%})."
    )


async def ejecutar(estado: EstadoAnalisis) -> None:
    """Aplica las reglas de revisión a los resultados de la licitación. Idempotente."""
    minimo = get_settings().revision_confianza_minima
    licitacion_id = uuid.UUID(estado["licitacion_id"])
    async with get_sessionmaker()() as session:
        filas = await session.execute(
            select(Resultado, Requisito)
            .join(Item, Resultado.item_id == Item.id)
            .join(Requisito, Item.requisito_id == Requisito.id)
            .where(Requisito.licitacion_id == licitacion_id)
            .options(selectinload(Resultado.revisiones))
        )
        resultados = [(resultado, requisito) for resultado, requisito in filas]
        for resultado, requisito in resultados:
            if resultado.revisiones:
                continue  # ya lo decidió una persona: el sistema no lo vuelve a poner en duda
            motivos: list[str] = []
            if requisito.ambiguo:
                motivos.append(motivo_ambiguedad(requisito.motivo_ambiguedad))
            if resultado.confianza is not None and resultado.confianza < minimo:
                motivos.append(motivo_confianza(resultado.confianza, minimo))
            for motivo in motivos:
                resultado.requiere_revision = True
                resultado.motivo_revision = agregar_motivo(resultado.motivo_revision, motivo)
            if resultado.requiere_revision and not resultado.motivo_revision:
                # Criterio 2: todo ítem en revisión indica el motivo.
                resultado.motivo_revision = "El sistema no pudo decidir este ítem."
        await session.commit()
        en_revision = sum(1 for resultado, _ in resultados if resultado.requiere_revision)
        total = len(resultados)

    await progreso.anotar_detalle(
        uuid.UUID(estado["trabajo_id"]), revision={"items": total, "en_revision": en_revision}
    )
    if en_revision:
        logger.info("Licitación %s: %d de %d ítems en revisión", licitacion_id, en_revision, total)
