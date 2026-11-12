"""Licitaciones: carga del pliego (F-03), progreso, resultado y visor (F-04, F-09)."""

import hashlib
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from analizador.api.deps import ContextoDep, PublicadorDep, SessionDep
from analizador.core.config import get_settings
from analizador.integrations import almacenamiento
from analizador.models import (
    Item,
    Licitacion,
    Requisito,
    Resultado,
    Seccion,
    TrabajoProcesamiento,
)
from analizador.models.enums import EstadoTrabajo, TipoTrabajo
from analizador.pipeline import catalogo
from analizador.schemas import (
    EtapaOut,
    LicitacionCreada,
    LicitacionDetalle,
    LicitacionResumen,
    ProgresoOut,
    RequisitoOut,
    SeccionesProgreso,
    TrabajoOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/licitaciones", tags=["licitaciones"])

FIRMA_PDF = b"%PDF-"
# La cabecera puede ir precedida de bytes ajenos dentro del primer kilobyte del archivo.
VENTANA_FIRMA = 1024


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


async def url_del_pdf(licitacion: Licitacion, organizacion_id: uuid.UUID) -> str:
    """URL temporal del PDF. Solo se firma un objeto bajo el prefijo de la organización (S-03)."""
    if not almacenamiento.pertenece_a(licitacion.storage_key, organizacion_id):
        logger.error("La licitación %s apunta a un objeto de otro prefijo", licitacion.id)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Licitación no encontrada")
    try:
        return await almacenamiento.url_temporal(licitacion.storage_key)
    except almacenamiento.AlmacenamientoError as exc:
        logger.error("No se pudo firmar la URL de la licitación %s: %s", licitacion.id, exc)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "El archivo no está disponible en este momento"
        ) from exc


async def _leer_pdf(archivo: UploadFile) -> bytes:
    """Lee el archivo aplicando el límite de tamaño y verificando que sea un PDF.

    S-09: el tipo se comprueba por el contenido, no por la extensión ni el content-type. El PDF
    no se abre aquí: se procesa solo en el worker, donde también se aplica el límite de páginas.
    """
    settings = get_settings()
    contenido = await archivo.read(settings.max_pdf_bytes + 1)
    if len(contenido) > settings.max_pdf_bytes:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"El PDF supera el tamaño máximo de {settings.max_pdf_mb} MB. "
            "Suba un archivo más pequeño.",
        )
    if FIRMA_PDF not in contenido[:VENTANA_FIRMA]:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "El archivo no es un PDF válido. Suba el pliego en formato PDF.",
        )
    return contenido


async def _analisis_por_hash(
    session: AsyncSession, organizacion_id: uuid.UUID, hash_pdf: str
) -> LicitacionCreada | None:
    """Análisis ya existente del mismo PDF en la organización (P-16), si lo hay."""
    licitacion = await session.scalar(
        select(Licitacion).where(
            Licitacion.organizacion_id == organizacion_id, Licitacion.hash_pdf == hash_pdf
        )
    )
    if licitacion is None:
        return None
    trabajo = await session.scalar(
        select(TrabajoProcesamiento)
        .where(
            TrabajoProcesamiento.licitacion_id == licitacion.id,
            TrabajoProcesamiento.tipo == TipoTrabajo.ANALISIS,
        )
        .order_by(TrabajoProcesamiento.creado_en.desc())
    )
    if trabajo is None:
        raise RuntimeError(f"La licitación {licitacion.id} no tiene trabajo de análisis")
    return LicitacionCreada(
        licitacion=LicitacionResumen.model_validate(licitacion),
        trabajo=TrabajoOut.model_validate(trabajo),
        duplicada=True,
    )


async def _descartar_objeto(clave: str) -> None:
    """Compensación: retira del almacén un PDF cuya licitación no llegó a quedar registrada."""
    try:
        await almacenamiento.eliminar(clave)
    except almacenamiento.AlmacenamientoError:
        logger.error("No se pudo retirar del almacén el objeto %s", clave)


@router.post(
    "",
    response_model=LicitacionCreada,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        status.HTTP_200_OK: {
            "model": LicitacionCreada,
            "description": "El mismo PDF ya fue analizado en la organización: se devuelve ese "
            "análisis (`duplicada: true`) sin iniciar uno nuevo.",
        }
    },
)
async def cargar_pliego(
    contexto: ContextoDep,
    session: SessionDep,
    publicador: PublicadorDep,
    respuesta: Response,
    archivo: Annotated[UploadFile, File(description="Pliego en PDF")],
    titulo: Annotated[str | None, Form(max_length=300)] = None,
) -> LicitacionCreada:
    """Guarda el pliego, crea la licitación y su trabajo y encola el análisis (HU-B07).

    Si falla el almacén, la base de datos o la cola, se deshace lo ya hecho: no queda una
    licitación sin trabajo ni un trabajo sin mensaje.
    """
    contenido = await _leer_pdf(archivo)
    hash_pdf = hashlib.sha256(contenido).hexdigest()

    existente = await _analisis_por_hash(session, contexto.organizacion_id, hash_pdf)
    if existente is not None:
        respuesta.status_code = status.HTTP_200_OK
        return existente

    clave = almacenamiento.nueva_clave_pliego(contexto.organizacion_id)
    try:
        await almacenamiento.subir_pdf(clave, contenido)
    except almacenamiento.AlmacenamientoError as exc:
        logger.error("No se pudo guardar el pliego: %s", exc)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "No se pudo guardar el archivo. Intente de nuevo en unos minutos.",
        ) from exc

    nombre_archivo = (archivo.filename or "pliego.pdf")[:300]
    licitacion = Licitacion(
        organizacion_id=contexto.organizacion_id,
        creado_por=contexto.usuario.usuario_id,
        titulo=titulo or nombre_archivo,
        nombre_archivo=nombre_archivo,
        storage_key=clave,
        hash_pdf=hash_pdf,
    )
    try:
        # Licitación y trabajo en la misma transacción: nunca existe una sin el otro.
        session.add(licitacion)
        await session.flush()
        trabajo = TrabajoProcesamiento(
            organizacion_id=contexto.organizacion_id,
            tipo=TipoTrabajo.ANALISIS,
            licitacion_id=licitacion.id,
        )
        session.add(trabajo)
        await session.commit()
    except IntegrityError:
        # Otra petición cargó el mismo PDF a la vez: se devuelve su análisis.
        await session.rollback()
        await _descartar_objeto(clave)
        existente = await _analisis_por_hash(session, contexto.organizacion_id, hash_pdf)
        if existente is None:
            raise
        respuesta.status_code = status.HTTP_200_OK
        return existente
    except Exception:
        await session.rollback()
        await _descartar_objeto(clave)
        raise

    # Se publica después del commit para que el worker encuentre el trabajo (ADR-015).
    try:
        await publicador.publicar(TipoTrabajo.ANALISIS, trabajo.id)
    except Exception as exc:
        logger.exception("No se pudo encolar el trabajo %s", trabajo.id)
        await session.delete(licitacion)  # el trabajo cae por la clave foránea
        await session.commit()
        await _descartar_objeto(clave)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "No se pudo iniciar el análisis. Intente de nuevo en unos minutos.",
        ) from exc

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
        url_pdf=await url_del_pdf(licitacion, contexto.organizacion_id),
        requisitos=[RequisitoOut.model_validate(r) for r in requisitos],
    )


@router.get("/{licitacion_id}/progreso", response_model=ProgresoOut)
async def progreso(
    licitacion_id: uuid.UUID, contexto: ContextoDep, session: SessionDep
) -> ProgresoOut:
    """Etapa, porcentaje y secciones procesadas del análisis más reciente (HU-B08).

    Todo sale de PostgreSQL (ADR-015): este endpoint no consulta la cola.
    """
    licitacion = await obtener_licitacion(session, contexto.organizacion_id, licitacion_id)
    trabajo = await session.scalar(
        select(TrabajoProcesamiento)
        .where(
            TrabajoProcesamiento.licitacion_id == licitacion.id,
            TrabajoProcesamiento.tipo == TipoTrabajo.ANALISIS,
        )
        .order_by(TrabajoProcesamiento.creado_en.desc())
    )
    if trabajo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Licitación no encontrada")

    total, procesadas = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(Seccion.procesada.is_(True)),
            ).where(Seccion.licitacion_id == licitacion.id)
        )
    ).one()
    return ProgresoOut(
        licitacion_id=licitacion.id,
        estado_licitacion=licitacion.estado,
        trabajo_id=trabajo.id,
        estado=trabajo.estado,
        etapa_actual=trabajo.etapa_actual,
        progreso=trabajo.progreso,
        secciones=SeccionesProgreso(procesadas=procesadas, total=total) if total else None,
        error=trabajo.error if trabajo.estado == EstadoTrabajo.FALLIDO else None,
        intentos=trabajo.intentos,
        iniciado_en=trabajo.iniciado_en,
        finalizado_en=trabajo.finalizado_en,
        actualizado_en=trabajo.actualizado_en,
        etapas=[
            EtapaOut(clave=e.clave, nombre=e.nombre, porcentaje=e.porcentaje)
            for e in catalogo.ETAPAS
        ],
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
    error_previo = trabajo.error
    trabajo.estado = EstadoTrabajo.EN_COLA
    trabajo.error = None
    await session.commit()
    try:
        await publicador.publicar(TipoTrabajo.ANALISIS, trabajo.id)
    except Exception as exc:
        # Sin mensaje en la cola el trabajo no puede quedar "en cola": vuelve a fallido.
        logger.exception("No se pudo reencolar el trabajo %s", trabajo.id)
        trabajo.estado = EstadoTrabajo.FALLIDO
        trabajo.error = error_previo
        await session.commit()
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "No se pudo iniciar el análisis. Intente de nuevo en unos minutos.",
        ) from exc
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
