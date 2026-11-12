"""Gestión de miembros de la organización (RF-09, F-01).

S-05: solo un administrador invita, y solo a su organización; un revisor no cambia roles;
no se elimina ni degrada al último administrador.
"""

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.api.deps import AdminDep, SessionDep
from analizador.integrations import supabase
from analizador.models import Miembro
from analizador.models.enums import Rol
from analizador.schemas import CambioRolIn, InvitacionIn, MiembroOut

router = APIRouter(prefix="/api/miembros", tags=["miembros"])


async def _miembro_de_org(
    session: AsyncSession, organizacion_id: uuid.UUID, miembro_id: uuid.UUID
) -> Miembro:
    miembro = await session.scalar(
        select(Miembro).where(Miembro.id == miembro_id, Miembro.organizacion_id == organizacion_id)
    )
    if miembro is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Miembro no encontrado")
    return miembro


async def _es_ultimo_admin(session: AsyncSession, miembro: Miembro) -> bool:
    if miembro.rol != Rol.ADMINISTRADOR:
        return False
    admins = await session.scalar(
        select(func.count())
        .select_from(Miembro)
        .where(Miembro.organizacion_id == miembro.organizacion_id, Miembro.rol == Rol.ADMINISTRADOR)
    )
    return admins == 1


@router.get("", response_model=list[MiembroOut])
async def listar(contexto: AdminDep, session: SessionDep) -> list[Miembro]:
    resultado = await session.scalars(
        select(Miembro)
        .where(Miembro.organizacion_id == contexto.organizacion_id)
        .order_by(Miembro.creado_en)
    )
    return list(resultado)


@router.post("/invitaciones", response_model=MiembroOut, status_code=status.HTTP_201_CREATED)
async def invitar(datos: InvitacionIn, contexto: AdminDep, session: SessionDep) -> Miembro:
    existente = await session.scalar(
        select(Miembro).where(
            Miembro.organizacion_id == contexto.organizacion_id,
            func.lower(Miembro.email) == datos.email.lower(),
        )
    )
    if existente is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ese correo ya es miembro de la organización")

    try:
        usuario_id = await supabase.invitar_usuario(datos.email)
    except supabase.SupabaseError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "No se pudo enviar la invitación") from exc

    # R-15: el registro de miembro se crea en la misma operación que la invitación.
    miembro = Miembro(
        organizacion_id=contexto.organizacion_id,
        usuario_id=usuario_id,
        email=datos.email,
        rol=datos.rol,
    )
    session.add(miembro)
    await session.commit()
    return miembro


@router.patch("/{miembro_id}/rol", response_model=MiembroOut)
async def cambiar_rol(
    miembro_id: uuid.UUID, datos: CambioRolIn, contexto: AdminDep, session: SessionDep
) -> Miembro:
    miembro = await _miembro_de_org(session, contexto.organizacion_id, miembro_id)
    if datos.rol != Rol.ADMINISTRADOR and await _es_ultimo_admin(session, miembro):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "No se puede degradar al último administrador"
        )
    miembro.rol = datos.rol
    await session.commit()
    return miembro


@router.delete("/{miembro_id}", status_code=status.HTTP_204_NO_CONTENT)
async def eliminar(miembro_id: uuid.UUID, contexto: AdminDep, session: SessionDep) -> None:
    miembro = await _miembro_de_org(session, contexto.organizacion_id, miembro_id)
    if await _es_ultimo_admin(session, miembro):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "No se puede eliminar al último administrador"
        )
    # El usuario sigue existiendo en Supabase; solo pierde el acceso a esta organización.
    await session.delete(miembro)
    await session.commit()
