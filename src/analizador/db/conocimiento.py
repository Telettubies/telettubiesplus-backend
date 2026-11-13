"""Selección de versiones de conocimiento, incluida la base anterior a esta función."""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.contratos.conocimiento import BaseConocimiento
from analizador.models import CambioConocimiento, FuenteConocimiento, TrabajoProcesamiento
from analizador.models.enums import TipoFuenteConocimiento


async def version_activa(session: AsyncSession, organizacion_id: uuid.UUID) -> str:
    version = await session.scalar(
        select(CambioConocimiento.id)
        .where(
            CambioConocimiento.organizacion_id == organizacion_id,
            CambioConocimiento.estado == "activado",
        )
        .order_by(CambioConocimiento.secuencia.desc())
        .limit(1)
    )
    return str(version) if version else "legacy"


def filtro_version(version: str) -> Any:
    campo = FuenteConocimiento.datos["_revision"].astext
    return campo.is_(None) if version == "legacy" else campo == version


async def fijar_version(
    session: AsyncSession, trabajo_id: uuid.UUID, organizacion_id: uuid.UUID
) -> str:
    trabajo = await session.get(TrabajoProcesamiento, trabajo_id)
    if trabajo is None or trabajo.organizacion_id != organizacion_id:
        raise ValueError("Trabajo no encontrado")
    detalle = dict(trabajo.detalle or {})
    if "version_empresa" not in detalle:
        detalle["version_empresa"] = await version_activa(session, organizacion_id)
        trabajo.detalle = detalle
        await session.commit()
    return str(detalle["version_empresa"])


def crear_fuentes(base: BaseConocimiento, organizacion_id: uuid.UUID) -> list[FuenteConocimiento]:
    """Una fuente por registro: es la unidad que después se indexa y se cita como evidencia."""

    def fuente(tipo: TipoFuenteConocimiento, titulo: str, registro: Any) -> FuenteConocimiento:
        return FuenteConocimiento(
            organizacion_id=organizacion_id,
            tipo=tipo,
            titulo=titulo[:300],
            datos=registro.model_dump(mode="json"),
        )

    tipos = TipoFuenteConocimiento
    return [
        fuente(tipos.EMPRESA, base.empresa.razon_social, base.empresa),
        *(
            fuente(tipos.FINANCIERO, f"Indicadores financieros {f.anio}", f)
            for f in base.financiero
        ),
        *(fuente(tipos.CONTRATO, f"{c.id} · {c.objeto}", c) for c in base.contratos),
        *(fuente(tipos.HOJA_DE_VIDA, p.nombre, p) for p in base.personas),
        *(fuente(tipos.CERTIFICACION, c.nombre, c) for c in base.certificaciones),
        *(fuente(tipos.CAPACIDAD, c.nombre, c) for c in base.capacidades),
    ]
