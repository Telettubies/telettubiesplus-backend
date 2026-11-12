"""Revisión humana de un resultado y recálculo de la recomendación (RF-14, F-05)."""

import logging
import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from analizador.api.deps import ContextoDep, PublicadorDep, SessionDep
from analizador.models import Item, Licitacion, Requisito, Resultado, Revision, TrabajoProcesamiento
from analizador.models.enums import TipoTrabajo
from analizador.pipeline import progreso
from analizador.schemas import RevisionCreada, RevisionIn, RevisionOut, TrabajoOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/resultados", tags=["revisión"])


@router.post(
    "/{resultado_id}/revisiones", response_model=RevisionCreada, status_code=status.HTTP_201_CREATED
)
async def revisar(
    resultado_id: uuid.UUID,
    datos: RevisionIn,
    contexto: ContextoDep,
    session: SessionDep,
    publicador: PublicadorDep,
) -> RevisionCreada:
    # S-03: el resultado se alcanza a través de su licitación, filtrando por organización.
    fila = (
        await session.execute(
            select(Resultado, Licitacion.id)
            .join(Item, Resultado.item_id == Item.id)
            .join(Requisito, Item.requisito_id == Requisito.id)
            .join(Licitacion, Requisito.licitacion_id == Licitacion.id)
            .where(
                Resultado.id == resultado_id, Licitacion.organizacion_id == contexto.organizacion_id
            )
        )
    ).first()
    if fila is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Resultado no encontrado")
    resultado, licitacion_id = fila

    # N-01: la revisión queda registrada con autor, estado anterior y motivo.
    revision = Revision(
        resultado_id=resultado.id,
        usuario_id=contexto.usuario.usuario_id,
        estado_anterior=resultado.estado,
        estado_nuevo=datos.estado_nuevo,
        comentario=datos.comentario,
    )
    resultado.estado = datos.estado_nuevo
    resultado.requiere_revision = False
    trabajo = TrabajoProcesamiento(
        organizacion_id=contexto.organizacion_id,
        tipo=TipoTrabajo.REEVALUACION,
        licitacion_id=licitacion_id,
    )
    session.add_all([revision, trabajo])
    await session.commit()

    try:
        await publicador.publicar(TipoTrabajo.REEVALUACION, trabajo.id)
    except Exception as exc:
        logger.exception("No se pudo encolar la reevaluación %s", trabajo.id)
        await progreso.fallar(trabajo.id, "No se pudo encolar la reevaluación")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Cola no disponible") from exc

    await session.refresh(revision)
    await session.refresh(trabajo)
    return RevisionCreada(
        revision=RevisionOut.model_validate(revision), trabajo=TrabajoOut.model_validate(trabajo)
    )
