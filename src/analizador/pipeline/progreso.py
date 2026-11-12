"""Registro del estado y progreso de los trabajos en PostgreSQL (ADR-015)."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import update

from analizador.db.session import get_sessionmaker
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
    await actualizar_trabajo(trabajo_id, etapa_actual=etapa, progreso=progreso)


async def marcar_licitacion(licitacion_id: uuid.UUID, estado: EstadoLicitacion) -> None:
    async with get_sessionmaker()() as session:
        await session.execute(
            update(Licitacion).where(Licitacion.id == licitacion_id).values(estado=estado)
        )
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
