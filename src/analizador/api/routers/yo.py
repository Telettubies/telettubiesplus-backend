"""Identidad del usuario y sus membresías."""

from fastapi import APIRouter
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.api.deps import SessionDep, UsuarioDep
from analizador.integrations.supabase import UsuarioAutenticado
from analizador.models import Miembro, Organizacion
from analizador.schemas import MembresiaOut, MiembroOut, YoOut

router = APIRouter(prefix="/api/yo", tags=["identidad"])


async def _vincular_invitaciones(usuario: UsuarioAutenticado, session: AsyncSession) -> None:
    """Asigna al usuario las invitaciones pendientes de su correo cuando su cuenta cambió.

    Ocurre si la cuenta invitada se borró de Supabase o si la persona entra con otra cuenta del
    mismo correo (p. ej. Google). Solo con un correo comprobado: con una contraseña recién creada
    cualquiera podría quedarse con la invitación de otro (S-04, S-05).
    """
    if not usuario.correo_comprobado or not usuario.email:
        return
    tiene = await session.scalar(
        select(func.count()).select_from(Miembro).where(Miembro.usuario_id == usuario.usuario_id)
    )
    if tiene:
        return
    resultado = await session.execute(
        update(Miembro)
        .where(
            func.lower(Miembro.email) == usuario.email.lower(),
            Miembro.invitacion_pendiente.is_(True),
        )
        .values(usuario_id=usuario.usuario_id)
    )
    if resultado.rowcount:  # type: ignore[attr-defined]
        await session.commit()


@router.get("", response_model=YoOut)
async def yo(usuario: UsuarioDep, session: SessionDep) -> YoOut:
    await _vincular_invitaciones(usuario, session)
    filas = await session.execute(
        select(Miembro, Organizacion.nombre)
        .join(Organizacion, Organizacion.id == Miembro.organizacion_id)
        .where(Miembro.usuario_id == usuario.usuario_id)
    )
    return YoOut(
        usuario_id=usuario.usuario_id,
        email=usuario.email,
        membresias=[
            MembresiaOut(
                **MiembroOut.model_validate(miembro).model_dump(), organizacion_nombre=nombre
            )
            for miembro, nombre in filas.all()
        ],
    )
