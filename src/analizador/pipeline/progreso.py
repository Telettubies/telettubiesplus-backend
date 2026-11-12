"""Registro del estado y progreso de los trabajos en PostgreSQL (ADR-015)."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import cast, func, update
from sqlalchemy.dialects.postgresql import JSONB

from analizador.db.session import get_sessionmaker
from analizador.integrations.anthropic_client import Uso
from analizador.models import Licitacion, TrabajoProcesamiento
from analizador.models.enums import EstadoLicitacion, EstadoTrabajo


async def actualizar_trabajo(trabajo_id: uuid.UUID, **campos: Any) -> None:
    async with get_sessionmaker()() as session:
        await session.execute(
            update(TrabajoProcesamiento)
            .where(TrabajoProcesamiento.id == trabajo_id)
            .values(**campos)
        )
        await session.commit()


async def registrar_etapa(trabajo_id: uuid.UUID, etapa: str, progreso: int) -> None:
    """Registra la etapa en curso. El progreso nunca retrocede (HU-B08).

    Si una etapa anterior vuelve a ejecutarse (reanudación sin checkpoint, entrega duplicada),
    el trabajo conserva la etapa y el porcentaje más avanzados que ya había alcanzado.
    """
    async with get_sessionmaker()() as session:
        await session.execute(
            update(TrabajoProcesamiento)
            .where(TrabajoProcesamiento.id == trabajo_id, TrabajoProcesamiento.progreso <= progreso)
            .values(etapa_actual=etapa, progreso=progreso)
        )
        await session.commit()


async def anotar_detalle(trabajo_id: uuid.UUID, **cifras: Any) -> None:
    """Añade cifras al detalle del trabajo sin borrar las que anotaron otras etapas."""
    async with get_sessionmaker()() as session:
        actual = func.coalesce(TrabajoProcesamiento.detalle, cast({}, JSONB))
        await session.execute(
            update(TrabajoProcesamiento)
            .where(TrabajoProcesamiento.id == trabajo_id)
            .values(detalle=actual.op("||")(cast(cifras, JSONB)))
        )
        await session.commit()


async def sumar_tokens(trabajo_id: uuid.UUID, etapa: str, uso: Uso) -> None:
    """Acumula en el detalle del trabajo los tokens que consumió una etapa (R-04).

    Se suman a los ya anotados: una etapa puede ejecutarse varias veces si se reanuda.
    """
    async with get_sessionmaker()() as session:
        trabajo = await session.get(TrabajoProcesamiento, trabajo_id, with_for_update=True)
        if trabajo is None:
            return
        detalle = dict(trabajo.detalle or {})
        tokens = dict(detalle.get("tokens") or {})
        previo = Uso(**tokens.get(etapa, {}))
        tokens[etapa] = (previo + uso).como_dict()
        detalle["tokens"] = tokens
        trabajo.detalle = detalle
        await session.commit()


async def marcar_licitacion(licitacion_id: uuid.UUID, estado: EstadoLicitacion) -> None:
    consulta = update(Licitacion).where(Licitacion.id == licitacion_id)
    if estado == EstadoLicitacion.PROCESANDO:
        # Una reentrega tras el cierre del grafo no debe reabrir la licitación (HU-B09).
        consulta = consulta.where(Licitacion.estado != EstadoLicitacion.COMPLETADA)
    async with get_sessionmaker()() as session:
        await session.execute(consulta.values(estado=estado))
        await session.commit()


async def iniciar(trabajo_id: uuid.UUID) -> None:
    await actualizar_trabajo(
        trabajo_id,
        estado=EstadoTrabajo.EN_EJECUCION,
        iniciado_en=datetime.now(UTC),
        intentos=TrabajoProcesamiento.intentos + 1,
        error=None,
    )


async def completar(trabajo_id: uuid.UUID) -> None:
    await actualizar_trabajo(
        trabajo_id,
        estado=EstadoTrabajo.COMPLETADO,
        progreso=100,
        finalizado_en=datetime.now(UTC),
    )


async def fallar(trabajo_id: uuid.UUID, mensaje: str) -> None:
    await actualizar_trabajo(
        trabajo_id,
        estado=EstadoTrabajo.FALLIDO,
        error=mensaje,
        finalizado_en=datetime.now(UTC),
    )
