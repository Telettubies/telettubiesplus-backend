"""Base de conocimiento de la organización (RF-10, F-02).

La carga depende de Q-05 (formato: estructurado, documentos o ambos), aún abierta.
"""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from analizador.api.deps import AdminDep, ContextoDep, SessionDep
from analizador.models import FuenteConocimiento
from analizador.schemas import FuenteConocimientoOut

router = APIRouter(prefix="/api/conocimiento", tags=["base de conocimiento"])


@router.get("/fuentes", response_model=list[FuenteConocimientoOut])
async def listar(contexto: ContextoDep, session: SessionDep) -> list[FuenteConocimiento]:
    resultado = await session.scalars(
        select(FuenteConocimiento)
        .where(FuenteConocimiento.organizacion_id == contexto.organizacion_id)
        .order_by(FuenteConocimiento.creado_en.desc())
    )
    return list(resultado)


@router.post("/fuentes", status_code=status.HTTP_501_NOT_IMPLEMENTED)
async def cargar(contexto: AdminDep) -> None:
    # TODO(F-02): guardar la fuente (S3 y/o JSON), crear TrabajoProcesamiento de tipo
    # INDEXACION y publicarlo en la cola de indexación. Bloqueado por Q-05.
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Carga de conocimiento pendiente de Q-05")
