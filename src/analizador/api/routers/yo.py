"""Identidad del usuario y sus membresías."""

from fastapi import APIRouter
from sqlalchemy import select

from analizador.api.deps import SessionDep, UsuarioDep
from analizador.models import Miembro
from analizador.schemas import MiembroOut, YoOut

router = APIRouter(prefix="/api/yo", tags=["identidad"])


@router.get("", response_model=YoOut)
async def yo(usuario: UsuarioDep, session: SessionDep) -> YoOut:
    membresias = await session.scalars(
        select(Miembro).where(Miembro.usuario_id == usuario.usuario_id)
    )
    return YoOut(
        usuario_id=usuario.usuario_id,
        email=usuario.email,
        membresias=[MiembroOut.model_validate(m) for m in membresias],
    )
