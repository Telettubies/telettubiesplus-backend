"""Base de conocimiento de la organización (RF-03, RF-10, F-02, HU-B05).

Se carga una vez como un archivo JSON con la estructura de `contratos.conocimiento` y la
comparten todos los usuarios y licitaciones de la organización. Cada carga reemplaza a la
anterior por completo y encola su indexación (HU-B06).
"""

import json
import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.api.deps import AdminDep, ContextoDep, PublicadorDep, SessionDep
from analizador.contratos.conocimiento import BaseConocimiento
from analizador.contratos.errores import ErrorDeCampo, errores_de_validacion
from analizador.core.config import get_settings
from analizador.messaging.publicador import Publicador
from analizador.models import Fragmento, FuenteConocimiento, TrabajoProcesamiento
from analizador.models.enums import (
    EstadoTrabajo,
    OrigenFragmento,
    TipoFuenteConocimiento,
    TipoTrabajo,
)
from analizador.pipeline import progreso
from analizador.schemas import (
    ConocimientoCargado,
    FuenteConocimientoOut,
    ResumenConocimiento,
    TrabajoOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/conocimiento", tags=["base de conocimiento"])

MENSAJE_RECHAZO = "El archivo no cumple la estructura acordada. No se guardó nada."
MENSAJE_SIN_COLA = (
    "La base de conocimiento se guardó, pero no se pudo iniciar su indexación. "
    "Vuelva a lanzarla en unos minutos."
)


def _rechazo(errores: list[ErrorDeCampo]) -> HTTPException:
    """422 con todos los errores a la vez, cada uno con su campo y ubicación."""
    return HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        {"mensaje": MENSAJE_RECHAZO, "errores": [error.model_dump() for error in errores]},
    )


def _error_de_archivo(mensaje: str) -> HTTPException:
    return _rechazo([ErrorDeCampo(campo="(archivo)", ubicacion=[], mensaje=mensaje)])


async def _leer_base(archivo: UploadFile) -> BaseConocimiento:
    settings = get_settings()
    limite = settings.max_conocimiento_mb * 1024 * 1024
    contenido = await archivo.read(limite + 1)
    if len(contenido) > limite:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"El archivo supera el tamaño máximo de {settings.max_conocimiento_mb} MB.",
        )
    try:
        datos = json.loads(contenido.decode("utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise _error_de_archivo("El archivo debe estar codificado en UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise _error_de_archivo(
            f"No es un JSON válido (línea {exc.lineno}, columna {exc.colno})"
        ) from exc
    try:
        return BaseConocimiento.model_validate(datos)
    except ValidationError as exc:
        raise _rechazo(errores_de_validacion(exc)) from exc


def _fuentes(base: BaseConocimiento, organizacion_id: uuid.UUID) -> list[FuenteConocimiento]:
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


async def _resumen(session: AsyncSession, organizacion_id: uuid.UUID) -> ResumenConocimiento:
    filas = await session.execute(
        select(FuenteConocimiento.tipo, func.count(), func.max(FuenteConocimiento.creado_en))
        .where(FuenteConocimiento.organizacion_id == organizacion_id)
        .group_by(FuenteConocimiento.tipo)
    )
    conteos: dict[TipoFuenteConocimiento, int] = {}
    cargada_en = None
    for tipo, cantidad, creada in filas:
        conteos[tipo] = cantidad
        cargada_en = creada if cargada_en is None else max(cargada_en, creada)

    empresa = await session.scalar(
        select(FuenteConocimiento.titulo).where(
            FuenteConocimiento.organizacion_id == organizacion_id,
            FuenteConocimiento.tipo == TipoFuenteConocimiento.EMPRESA,
        )
    )
    indexacion = await session.scalar(
        select(TrabajoProcesamiento)
        .where(
            TrabajoProcesamiento.organizacion_id == organizacion_id,
            TrabajoProcesamiento.tipo == TipoTrabajo.INDEXACION,
        )
        .order_by(TrabajoProcesamiento.creado_en.desc())
    )
    fragmentos = await session.scalar(
        select(func.count())
        .select_from(Fragmento)
        .where(
            Fragmento.organizacion_id == organizacion_id,
            Fragmento.origen == OrigenFragmento.EMPRESA,
        )
    )
    tipos = TipoFuenteConocimiento
    return ResumenConocimiento(
        cargada=bool(conteos),
        empresa=empresa,
        cargada_en=cargada_en,
        anios_financieros=conteos.get(tipos.FINANCIERO, 0),
        contratos=conteos.get(tipos.CONTRATO, 0),
        personas=conteos.get(tipos.HOJA_DE_VIDA, 0),
        certificaciones=conteos.get(tipos.CERTIFICACION, 0),
        capacidades=conteos.get(tipos.CAPACIDAD, 0),
        fragmentos_indexados=fragmentos or 0,
        indexacion=TrabajoOut.model_validate(indexacion) if indexacion else None,
    )


async def _encolar_indexacion(publicador: Publicador, trabajo_id: uuid.UUID) -> None:
    """Publica el trabajo; si la cola no está, lo deja en error visible y recuperable."""
    try:
        await publicador.publicar(TipoTrabajo.INDEXACION, trabajo_id)
    except Exception as exc:
        logger.exception("No se pudo encolar la indexación %s", trabajo_id)
        await progreso.fallar(trabajo_id, "No se pudo iniciar la indexación. Vuelva a lanzarla.")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, MENSAJE_SIN_COLA) from exc


@router.get("", response_model=ResumenConocimiento)
async def resumen(contexto: ContextoDep, session: SessionDep) -> ResumenConocimiento:
    """Qué hay cargado y en qué estado está su indexación (HU-F04)."""
    return await _resumen(session, contexto.organizacion_id)


@router.get("/fuentes", response_model=list[FuenteConocimientoOut])
async def listar(contexto: ContextoDep, session: SessionDep) -> list[FuenteConocimiento]:
    resultado = await session.scalars(
        select(FuenteConocimiento)
        .where(FuenteConocimiento.organizacion_id == contexto.organizacion_id)
        .order_by(FuenteConocimiento.tipo, FuenteConocimiento.titulo)
    )
    return list(resultado)


@router.post("", response_model=ConocimientoCargado, status_code=status.HTTP_202_ACCEPTED)
async def cargar(
    contexto: AdminDep,
    session: SessionDep,
    publicador: PublicadorDep,
    archivo: Annotated[UploadFile, File(description="Base de conocimiento en JSON")],
) -> ConocimientoCargado:
    """Valida el archivo completo, reemplaza la base anterior y encola la indexación.

    El reemplazo es atómico: o queda guardada la versión nueva completa, o se conserva la
    anterior. Si el archivo no cumple el contrato se rechaza entero.
    """
    base = await _leer_base(archivo)

    # Una sola transacción: borrar la versión anterior (y sus fragmentos, por la clave
    # foránea), insertar la nueva y crear el trabajo de indexación.
    await session.execute(
        delete(FuenteConocimiento).where(
            FuenteConocimiento.organizacion_id == contexto.organizacion_id
        )
    )
    session.add_all(_fuentes(base, contexto.organizacion_id))
    trabajo = TrabajoProcesamiento(
        organizacion_id=contexto.organizacion_id, tipo=TipoTrabajo.INDEXACION
    )
    session.add(trabajo)
    await session.commit()

    await _encolar_indexacion(publicador, trabajo.id)
    return ConocimientoCargado(
        resumen=await _resumen(session, contexto.organizacion_id),
        trabajo=TrabajoOut.model_validate(trabajo),
    )


@router.post("/indexacion", response_model=TrabajoOut, status_code=status.HTTP_202_ACCEPTED)
async def reindexar(
    contexto: AdminDep, session: SessionDep, publicador: PublicadorDep
) -> TrabajoProcesamiento:
    """Vuelve a indexar la base ya cargada, por ejemplo tras un fallo de la indexación."""
    hay_base = await session.scalar(
        select(FuenteConocimiento.id)
        .where(FuenteConocimiento.organizacion_id == contexto.organizacion_id)
        .limit(1)
    )
    if hay_base is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "No hay una base de conocimiento cargada")
    en_curso = await session.scalar(
        select(TrabajoProcesamiento.id).where(
            TrabajoProcesamiento.organizacion_id == contexto.organizacion_id,
            TrabajoProcesamiento.tipo == TipoTrabajo.INDEXACION,
            TrabajoProcesamiento.estado.in_([EstadoTrabajo.EN_COLA, EstadoTrabajo.EN_EJECUCION]),
        )
    )
    if en_curso is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ya hay una indexación en curso")
    trabajo = TrabajoProcesamiento(
        organizacion_id=contexto.organizacion_id, tipo=TipoTrabajo.INDEXACION
    )
    session.add(trabajo)
    await session.commit()
    await _encolar_indexacion(publicador, trabajo.id)
    await session.refresh(trabajo)
    return trabajo
