"""Licitaciones: carga del pliego (F-03), progreso, resultado y visor (F-04, F-09)."""

import hashlib
import logging
import uuid
from typing import Annotated, Literal

import anthropic
from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from analizador.api import chat as chat_licitacion
from analizador.api import cuotas, exportacion, matriz
from analizador.api import observaciones as borradores
from analizador.api.deps import ContextoDep, PublicadorDep, SessionDep
from analizador.contratos.decision import Recomendacion
from analizador.core.config import get_settings
from analizador.integrations import almacenamiento, nvidia
from analizador.integrations.anthropic_client import (
    ModeloNoConfiguradoError,
    RespuestaRechazadaError,
)
from analizador.models import (
    EventoUso,
    Item,
    Licitacion,
    Requisito,
    Resultado,
    Seccion,
    TrabajoProcesamiento,
)
from analizador.models.enums import EstadoLicitacion, EstadoTrabajo, TipoTrabajo, TipoUso
from analizador.pipeline import catalogo
from analizador.schemas import (
    ErrorResultadoOut,
    EtapaOut,
    LicitacionCreada,
    LicitacionDetalle,
    LicitacionResumen,
    ObservacionIn,
    ObservacionOut,
    PreguntaIn,
    ProgresoOut,
    RequisitoOut,
    RespuestaChatOut,
    ResultadoMatrizOut,
    SeccionesProgreso,
    TrabajoOut,
    UrlPdfOut,
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

    # Solo un análisis nuevo consume cuota; un duplicado no gasta créditos (S-08).
    await cuotas.verificar_cuota_analisis(session, contexto.organizacion_id)

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
async def listar(
    contexto: ContextoDep,
    session: SessionDep,
    orden: Annotated[
        Literal["recientes", "antiguas"],
        Query(description="Orden por fecha de carga: primero las más recientes o las más antiguas"),
    ] = "recientes",
) -> list[Licitacion]:
    """Panel de licitaciones de la organización con estado y recomendación (RF-11, HU-B22)."""
    fecha = Licitacion.creado_en.desc() if orden == "recientes" else Licitacion.creado_en.asc()
    resultado = await session.scalars(
        select(Licitacion)
        .where(Licitacion.organizacion_id == contexto.organizacion_id)
        .order_by(fecha, Licitacion.id)
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


@router.get("/{licitacion_id}/pdf-url", response_model=UrlPdfOut)
async def renovar_url_pdf(
    licitacion_id: uuid.UUID, contexto: ContextoDep, session: SessionDep, response: Response
) -> UrlPdfOut:
    """Renueva el acceso temporal al pliego del visor (HU-B21, RF-26)."""
    licitacion = await obtener_licitacion(session, contexto.organizacion_id, licitacion_id)
    url = await url_del_pdf(licitacion, contexto.organizacion_id)
    response.headers["Cache-Control"] = "no-store"
    return UrlPdfOut(pdf_url_temporal=url)


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
            TrabajoProcesamiento.organizacion_id == contexto.organizacion_id,
            TrabajoProcesamiento.tipo == TipoTrabajo.ANALISIS,
        )
        .order_by(TrabajoProcesamiento.creado_en.desc(), TrabajoProcesamiento.id.desc())
    )
    if trabajo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Licitación no encontrada")
    return await _progreso_de(session, licitacion, trabajo)


@router.get(
    "/{licitacion_id}/resultado",
    response_model=ResultadoMatrizOut,
    responses={202: {"model": ProgresoOut}, 409: {"model": ProgresoOut | ErrorResultadoOut}},
)
async def resultado_matriz(
    licitacion_id: uuid.UUID, contexto: ContextoDep, session: SessionDep, response: Response
) -> ResultadoMatrizOut | JSONResponse:
    """Entrega el resultado consolidado, o el avance cuando el trabajo sigue activo (HU-B21)."""
    licitacion = await obtener_licitacion(session, contexto.organizacion_id, licitacion_id)
    trabajo = await session.scalar(
        select(TrabajoProcesamiento)
        .where(
            TrabajoProcesamiento.licitacion_id == licitacion.id,
            TrabajoProcesamiento.organizacion_id == contexto.organizacion_id,
            TrabajoProcesamiento.tipo.in_([TipoTrabajo.ANALISIS, TipoTrabajo.REEVALUACION]),
        )
        .order_by(TrabajoProcesamiento.creado_en.desc(), TrabajoProcesamiento.id.desc())
        .limit(1)
    )
    if trabajo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Resultado no encontrado")
    if (
        trabajo.estado != EstadoTrabajo.COMPLETADO
        or licitacion.estado != EstadoLicitacion.COMPLETADA
    ):
        avance = await _progreso_de(session, licitacion, trabajo)
        codigo = (
            status.HTTP_409_CONFLICT
            if trabajo.estado in (EstadoTrabajo.FALLIDO, EstadoTrabajo.COMPLETADO)
            else status.HTTP_202_ACCEPTED
        )
        return JSONResponse(status_code=codigo, content=avance.model_dump(mode="json"))
    if licitacion.recomendacion not in tuple(Recomendacion) or not licitacion.justificacion:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "El análisis no contiene un resultado consolidado."
        )
    url = await url_del_pdf(licitacion, contexto.organizacion_id)
    response.headers["Cache-Control"] = "no-store"
    return await matriz.construir(
        session,
        licitacion,
        contexto.organizacion_id,
        Recomendacion(licitacion.recomendacion),
        licitacion.justificacion,
        url,
    )


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get(
    "/{licitacion_id}/exportar",
    response_class=Response,
    responses={200: {"content": {XLSX: {}}, "description": "Matriz en Excel"}},
)
async def exportar_matriz(
    licitacion_id: uuid.UUID, contexto: ContextoDep, session: SessionDep
) -> Response:
    """Descarga la matriz en Excel: una fila por ítem y una hoja de resumen (HU-B29)."""
    licitacion = await obtener_licitacion(session, contexto.organizacion_id, licitacion_id)
    if (
        licitacion.estado != EstadoLicitacion.COMPLETADA
        or licitacion.recomendacion not in tuple(Recomendacion)
        or not licitacion.justificacion
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "La matriz se puede exportar cuando el análisis termine."
        )
    items = await matriz.items_de(session, licitacion, contexto.organizacion_id)
    contenido = exportacion.libro(
        licitacion.titulo,
        Recomendacion(licitacion.recomendacion),
        licitacion.justificacion,
        items,
    )
    return Response(
        content=contenido,
        media_type=XLSX,
        headers={
            # El nombre no usa el título: así ningún dato del usuario llega a la cabecera.
            "Content-Disposition": f'attachment; filename="matriz-{licitacion.id}.xlsx"',
            "Cache-Control": "no-store",
        },
    )


async def _progreso_de(
    session: AsyncSession, licitacion: Licitacion, trabajo: TrabajoProcesamiento
) -> ProgresoOut:
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
    """Reencola el último análisis o recálculo fallido (HU-B09, HU-B23).

    Con el checkpointer (P-08) un análisis se reanuda donde quedó; un recálculo tras una
    revisión humana solo repite brechas, recomendación y justificación.
    """
    await obtener_licitacion(session, contexto.organizacion_id, licitacion_id)
    trabajo = await session.scalar(
        select(TrabajoProcesamiento)
        .where(
            TrabajoProcesamiento.licitacion_id == licitacion_id,
            TrabajoProcesamiento.organizacion_id == contexto.organizacion_id,
            TrabajoProcesamiento.tipo.in_([TipoTrabajo.ANALISIS, TipoTrabajo.REEVALUACION]),
        )
        .order_by(TrabajoProcesamiento.creado_en.desc(), TrabajoProcesamiento.id.desc())
        .limit(1)
    )
    if trabajo is None or trabajo.estado != EstadoTrabajo.FALLIDO:
        raise HTTPException(status.HTTP_409_CONFLICT, "No hay un análisis fallido que reintentar")
    error_previo, intentos_previos = trabajo.error, trabajo.intentos
    trabajo.estado = EstadoTrabajo.EN_COLA
    trabajo.error = None
    trabajo.intentos = 0  # el límite de entregas es por reintento, no de por vida
    await session.commit()
    try:
        await publicador.publicar(trabajo.tipo, trabajo.id)
    except Exception as exc:
        # Sin mensaje en la cola el trabajo no puede quedar "en cola": vuelve a fallido.
        logger.exception("No se pudo reencolar el trabajo %s", trabajo.id)
        trabajo.estado = EstadoTrabajo.FALLIDO
        trabajo.error, trabajo.intentos = error_previo, intentos_previos
        await session.commit()
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "No se pudo iniciar el análisis. Intente de nuevo en unos minutos.",
        ) from exc
    await session.refresh(trabajo)
    return trabajo


@router.post("/{licitacion_id}/chat", response_model=RespuestaChatOut)
async def chat(
    licitacion_id: uuid.UUID, datos: PreguntaIn, contexto: ContextoDep, session: SessionDep
) -> RespuestaChatOut:
    """Responde una pregunta sobre el pliego y su resultado, citando numerales (HU-B24)."""
    licitacion = await obtener_licitacion(session, contexto.organizacion_id, licitacion_id)
    maximo = get_settings().chat_max_caracteres
    if len(datos.pregunta) > maximo:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"La pregunta supera el máximo de {maximo} caracteres.",
        )
    await cuotas.verificar_cuota_chat(session, contexto.organizacion_id)
    try:
        respuesta = await chat_licitacion.responder(
            session, licitacion, contexto.organizacion_id, datos.pregunta
        )
    except chat_licitacion.ChatNoDisponibleError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "El chat de esta licitación estará disponible cuando termine su análisis.",
        ) from exc
    except (nvidia.NvidiaNoDisponibleError, ModeloNoConfiguradoError) as exc:
        logger.warning("Chat no disponible para la licitación %s", licitacion.id)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "El chat no está disponible en este momento. Intente de nuevo en unos minutos.",
        ) from exc
    except chat_licitacion.RespuestaInvalidaError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "No se pudo generar una respuesta con citas verificables. Reformule la pregunta.",
        ) from exc
    # Solo cuenta para la cuota una pregunta respondida (HU-B26).
    session.add(
        EventoUso(
            organizacion_id=contexto.organizacion_id,
            tipo=TipoUso.PREGUNTA_CHAT,
            usuario_id=contexto.usuario.usuario_id,
        )
    )
    await session.commit()
    return respuesta


@router.post("/{licitacion_id}/observaciones", response_model=ObservacionOut)
async def observaciones(
    licitacion_id: uuid.UUID, datos: ObservacionIn, contexto: ContextoDep, session: SessionDep
) -> ObservacionOut:
    """Borrador de observación para un requisito ambiguo; no se envía a la entidad (HU-B25)."""
    licitacion = await obtener_licitacion(session, contexto.organizacion_id, licitacion_id)
    fila = (
        await session.execute(
            select(Requisito, Seccion)
            .join(Seccion, Requisito.seccion_id == Seccion.id)
            .where(Requisito.id == datos.requisito_id, Requisito.licitacion_id == licitacion.id)
        )
    ).first()
    if fila is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Requisito no encontrado")
    requisito, seccion = fila
    if not requisito.ambiguo:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Solo se generan observaciones para requisitos marcados como ambiguos.",
        )
    await cuotas.verificar_cuota_observaciones(session, contexto.organizacion_id)
    try:
        borrador = await borradores.generar(licitacion, requisito, seccion)
    except (ModeloNoConfiguradoError, anthropic.APIError) as exc:
        logger.warning("Borrador de observación no disponible para %s", requisito.id)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "La generación de observaciones no está disponible. Intente de nuevo más tarde.",
        ) from exc
    except (RespuestaRechazadaError, ValidationError) as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "No se pudo generar el borrador. Intente de nuevo."
        ) from exc
    session.add(
        EventoUso(
            organizacion_id=contexto.organizacion_id,
            tipo=TipoUso.OBSERVACION,
            usuario_id=contexto.usuario.usuario_id,
        )
    )
    await session.commit()
    return borrador
