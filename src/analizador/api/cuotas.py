"""Cuotas de uso por organización (S-08, P-23, HU-B26).

Protegen los créditos de NVIDIA y el presupuesto de Anthropic: una organización no puede
agotar los recursos de las demás. La ventana es de 24 horas móviles, sin depender de husos.
"""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.core.config import get_settings
from analizador.models import EventoUso, Licitacion
from analizador.models.enums import TipoUso

VENTANA = timedelta(hours=24)


def _desde() -> datetime:
    return datetime.now(UTC) - VENTANA


async def analisis_recientes(session: AsyncSession, organizacion_id: uuid.UUID) -> int:
    """Análisis iniciados por la organización en la ventana: uno por licitación cargada."""
    total = await session.scalar(
        select(func.count())
        .select_from(Licitacion)
        .where(Licitacion.organizacion_id == organizacion_id, Licitacion.creado_en >= _desde())
    )
    return total or 0


async def preguntas_recientes(session: AsyncSession, organizacion_id: uuid.UUID) -> int:
    total = await session.scalar(
        select(func.count())
        .select_from(EventoUso)
        .where(
            EventoUso.organizacion_id == organizacion_id,
            EventoUso.tipo == TipoUso.PREGUNTA_CHAT,
            EventoUso.creado_en >= _desde(),
        )
    )
    return total or 0


def _cuota_superada(mensaje: str) -> HTTPException:
    return HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, mensaje)


async def verificar_cuota_analisis(session: AsyncSession, organizacion_id: uuid.UUID) -> None:
    cuota = get_settings().cuota_analisis_diaria
    if await analisis_recientes(session, organizacion_id) >= cuota:
        raise _cuota_superada(
            f"Su organización alcanzó el límite de {cuota} análisis en 24 horas. "
            "Podrá cargar otro pliego cuando pase ese plazo."
        )


async def verificar_cuota_chat(session: AsyncSession, organizacion_id: uuid.UUID) -> None:
    cuota = get_settings().cuota_chat_diaria
    if await preguntas_recientes(session, organizacion_id) >= cuota:
        raise _cuota_superada(
            f"Su organización alcanzó el límite de {cuota} preguntas al chat en 24 horas. "
            "Podrá preguntar de nuevo cuando pase ese plazo."
        )
