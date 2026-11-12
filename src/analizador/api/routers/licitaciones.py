"""Licitaciones: carga del pliego (F-03), progreso, resultado y visor (F-04, F-09)."""

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from analizador.api.deps import ContextoDep, PublicadorDep, SessionDep
from analizador.core.config import get_settings
from analizador.integrations import almacenamiento
from analizador.models import Item, Licitacion, Requisito, Resultado, TrabajoProcesamiento
from analizador.models.enums import EstadoTrabajo, TipoTrabajo
from analizador.pipeline import progreso
from analizador.schemas import (
    LicitacionCreada,
    LicitacionDetalle,
    LicitacionResumen,
    RequisitoOut,
    TrabajoOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/licitaciones", tags=["licitaciones"])

FIRMA_PDF = b"%PDF-"


async def obtener_licitacion(
    session: AsyncSession, organizacion_id: uuid.UUID, licitacion_id: uuid.UUID
) -> Licitacion:
    """S-03: toda consulta filtra por la organización del usuario. 404 si no es suya."""
    licitacion = await session.scalar(
        select(Licitacion).where(
            Licitacion.id == licitacion_id, Licitacion.organizacion_id == organizacion_id
        )
    )
    if licitacion is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Licitación no encontrada")
    return licitacion


async def _leer_pdf(archivo: UploadFile) -> bytes:
    limite = get_settings().max_pdf_bytes
    contenido = await archivo.read(limite + 1)
    if len(contenido) > limite:
        # S-08: límite de tamaño. El límite de páginas se aplicará en la etapa 1.
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "El PDF supera el tamaño máximo")
    if not contenido.startswith(FIRMA_PDF):
        # S-09: se verifica el tipo real del archivo, no la extensión ni el content-type.
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "El archivo no es un PDF")
    return contenido


@router.post("", response_model=LicitacionCreada, status_code=status.HTTP_202_ACCEPTED)
async def cargar_pliego(
    contexto: ContextoDep,
    session: SessionDep,
    publicador: PublicadorDep,
    archivo: Annotated[UploadFile, File(description="Pliego en PDF")],
    titulo: Annotated[str | None, Form(max_length=300)] = None,
) -> LicitacionCreada:
    contenido = await _leer_pdf(archivo)
    nombre_archivo = (archivo.filename or "pliego.pdf")[:300]
    clave = almacenamiento.nueva_clave_pliego(contexto.organizacion_id)
    await almacenamiento.subir_pdf(clave, contenido)

    licitacion = Licitacion(
        organizacion_id=contexto.organizacion_id,
        creado_por=contexto.usuario.usuario_id,
        titulo=titulo or nombre_archivo,
        nombre_archivo=nombre_archivo,
        storage_key=clave,
    )
    session.add(licitacion)
    await session.flush()
    trabajo = TrabajoProcesamiento(
        organizacion_id=contexto.organizacion_id,
        tipo=TipoTrabajo.ANALISIS,
        licitacion_id=licitacion.id,
    )
    session.add(trabajo)
    await session.commit()

    # Se publica después del commit para que el worker encuentre el trabajo (ADR-015).
    try:
        await publicador.publicar(TipoTrabajo.ANALISIS, trabajo.id)
    except Exception as exc:
        logger.exception("No se pudo encolar el trabajo %s", trabajo.id)
        await progreso.fallar(trabajo.id, "No se pudo encolar el análisis")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Cola no disponible") from exc

    await session.refresh(licitacion)
    await session.refresh(trabajo)
    return LicitacionCreada(
        licitacion=LicitacionResumen.model_validate(licitacion),
        trabajo=TrabajoOut.model_validate(trabajo),
    )


@router.get("", response_model=list[LicitacionResumen])
async def listar(contexto: ContextoDep, session: SessionDep) -> list[Licitacion]:
    """Panel general de licitaciones de la organización (RF-11, RF-12)."""
    resultado = await session.scalars(
        select(Licitacion)
        .where(Licitacion.organizacion_id == contexto.organizacion_id)
        .order_by(Licitacion.creado_en.desc())
    )
    return list(resultado)


@router.get("/{licitacion_id}", response_model=LicitacionDetalle)
async def detalle(
    licitacion_id: uuid.UUID, contexto: ContextoDep, session: SessionDep
) -> LicitacionDetalle:
    """Matriz de cobertura desde la base de datos, sin modelos (F-04), y URL del PDF (F-09)."""
    licitacion = await obtener_licitacion(session, contexto.organizacion_id, licitacion_id)
    requisitos = await session.scalars(
        select(Requisito)
        .where(Requisito.licitacion_id == licitacion.id)
        .options(
            selectinload(Requisito.ubicacion),
            selectinload(Requisito.items)
            .selectinload(Item.resultado)
            .selectinload(Resultado.revisiones),
        )
        .order_by(Requisito.creado_en)
    )
    resumen = LicitacionResumen.model_validate(licitacion)
    return LicitacionDetalle(
        **resumen.model_dump(),
        justificacion=licitacion.justificacion,
        url_pdf=await almacenamiento.url_temporal(licitacion.storage_key),
        requisitos=[RequisitoOut.model_validate(r) for r in requisitos],
    )


@router.get("/{licitacion_id}/trabajos", response_model=list[TrabajoOut])
async def trabajos(
    licitacion_id: uuid.UUID, contexto: ContextoDep, session: SessionDep
) -> list[TrabajoProcesamiento]:
    """Progreso del procesamiento (ADR-015): el frontend consulta aquí, no a RabbitMQ."""
    await obtener_licitacion(session, contexto.organizacion_id, licitacion_id)
    resultado = await session.scalars(
        select(TrabajoProcesamiento)
        .where(TrabajoProcesamiento.licitacion_id == licitacion_id)
        .order_by(TrabajoProcesamiento.creado_en.desc())
    )
    return list(resultado)


@router.post(
    "/{licitacion_id}/reintentar", response_model=TrabajoOut, status_code=status.HTTP_202_ACCEPTED
)
async def reintentar(
    licitacion_id: uuid.UUID, contexto: ContextoDep, session: SessionDep, publicador: PublicadorDep
) -> TrabajoProcesamiento:
    """Reencola el último análisis fallido. Con el checkpointer (P-08) se reanuda donde quedó."""
    await obtener_licitacion(session, contexto.organizacion_id, licitacion_id)
    trabajo = await session.scalar(
        select(TrabajoProcesamiento)
        .where(
            TrabajoProcesamiento.licitacion_id == licitacion_id,
            TrabajoProcesamiento.tipo == TipoTrabajo.ANALISIS,
        )
        .order_by(TrabajoProcesamiento.creado_en.desc())
    )
    if trabajo is None or trabajo.estado != EstadoTrabajo.FALLIDO:
        raise HTTPException(status.HTTP_409_CONFLICT, "No hay un análisis fallido que reintentar")
    trabajo.estado = EstadoTrabajo.EN_COLA
    trabajo.error = None
    await session.commit()
    await publicador.publicar(TipoTrabajo.ANALISIS, trabajo.id)
    await session.refresh(trabajo)
    return trabajo


@router.post("/{licitacion_id}/chat", status_code=status.HTTP_501_NOT_IMPLEMENTED)
async def chat(licitacion_id: uuid.UUID, contexto: ContextoDep, session: SessionDep) -> None:
    # TODO(RF-13, F-06): chat con el contexto de la licitación (NVIDIA + pgvector),
    # filtrando la búsqueda vectorial por organización (S-03). Cuotas por organización (S-08).
    await obtener_licitacion(session, contexto.organizacion_id, licitacion_id)
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Chat no implementado")


@router.post("/{licitacion_id}/observaciones", status_code=status.HTTP_501_NOT_IMPLEMENTED)
async def observaciones(
    licitacion_id: uuid.UUID, contexto: ContextoDep, session: SessionDep
) -> None:
    # TODO(RF-15, F-07): borrador de observaciones a partir de requisitos ambiguos (Anthropic).
    await obtener_licitacion(session, contexto.organizacion_id, licitacion_id)
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Observaciones no implementadas")
