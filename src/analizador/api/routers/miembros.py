"""Gestión de miembros de la organización (RF-09, F-01, HU-B02, HU-B03).

S-05: solo un administrador invita, y solo a su organización; un revisor no cambia roles;
no se elimina ni degrada al último administrador. Cada invitación, cambio de rol y retiro
queda en `auditoria_miembros` con su autor y su fecha.
"""

import logging
import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.api.deps import AdminDep, Contexto, SessionDep
from analizador.integrations import supabase
from analizador.models import AuditoriaMiembro, Miembro
from analizador.models.enums import AccionMiembro, Rol
from analizador.schemas import CambioRolIn, InvitacionIn, MiembroOut

logger = logging.getLogger(__name__)

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
    """Bloquea las filas de administradores para que dos cambios simultáneos no los agoten."""
    if miembro.rol != Rol.ADMINISTRADOR:
        return False
    admins = await session.scalars(
        select(Miembro.id)
        .where(Miembro.organizacion_id == miembro.organizacion_id, Miembro.rol == Rol.ADMINISTRADOR)
        .with_for_update()
    )
    return len(list(admins)) == 1


def _auditar(
    contexto: Contexto,
    accion: AccionMiembro,
    miembro: Miembro,
    *,
    rol_anterior: Rol | None = None,
    rol_nuevo: Rol | None = None,
) -> AuditoriaMiembro:
    return AuditoriaMiembro(
        organizacion_id=contexto.organizacion_id,
        accion=accion,
        actor_usuario_id=contexto.usuario.usuario_id,
        miembro_usuario_id=miembro.usuario_id,
        email=miembro.email,
        rol_anterior=rol_anterior,
        rol_nuevo=rol_nuevo,
    )


@router.get("", response_model=list[MiembroOut])
async def listar(contexto: AdminDep, session: SessionDep) -> list[Miembro]:
    """Miembros de la organización con su rol y si la invitación sigue pendiente (HU-B03)."""
    resultado = await session.scalars(
        select(Miembro)
        .where(Miembro.organizacion_id == contexto.organizacion_id)
        .order_by(Miembro.creado_en)
    )
    return list(resultado)


@router.post("/invitaciones", response_model=MiembroOut, status_code=status.HTTP_201_CREATED)
async def invitar(datos: InvitacionIn, contexto: AdminDep, session: SessionDep) -> Miembro:
    """Invita un correo con un rol a la organización del administrador (HU-B02)."""
    email = datos.email.strip()
    existente = await session.scalar(
        select(Miembro.id).where(
            Miembro.organizacion_id == contexto.organizacion_id,
            func.lower(Miembro.email) == email.lower(),
        )
    )
    if existente is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ese correo ya es miembro de la organización")

    cuenta_nueva = True
    try:
        usuario_id = await supabase.invitar_usuario(email)
    except supabase.UsuarioExistenteError:
        # La persona ya tiene cuenta (p. ej. entró con Google): se le da acceso con esa cuenta.
        cuenta_nueva = False
        try:
            usuario_id = await supabase.id_de_usuario_existente(email)
        except supabase.SupabaseError as exc:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, "No se pudo dar acceso a esa cuenta"
            ) from exc
    except supabase.SupabaseError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "No se pudo enviar la invitación") from exc

    # R-15: si el miembro no se puede guardar, se deshace la cuenta recién invitada.
    miembro = Miembro(
        organizacion_id=contexto.organizacion_id,
        usuario_id=usuario_id,
        email=email,
        rol=datos.rol,
        invitacion_pendiente=True,
    )
    session.add_all(
        [miembro, _auditar(contexto, AccionMiembro.INVITACION, miembro, rol_nuevo=datos.rol)]
    )
    try:
        await session.commit()
    except Exception as exc:
        await session.rollback()
        logger.exception("No se pudo guardar el miembro invitado %s", usuario_id)
        if cuenta_nueva:  # nunca se borra una cuenta que ya existía
            try:
                await supabase.eliminar_usuario(usuario_id)
            except supabase.SupabaseError:
                logger.exception("No se pudo deshacer la invitación del usuario %s", usuario_id)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "No se pudo completar la invitación. Intente de nuevo.",
        ) from exc
    return miembro


@router.patch("/{miembro_id}/rol", response_model=MiembroOut)
async def cambiar_rol(
    miembro_id: uuid.UUID, datos: CambioRolIn, contexto: AdminDep, session: SessionDep
) -> Miembro:
    """Cambia el rol de un miembro de la organización (HU-B03)."""
    miembro = await _miembro_de_org(session, contexto.organizacion_id, miembro_id)
    if datos.rol == miembro.rol:
        return miembro
    if await _es_ultimo_admin(session, miembro):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "No se puede degradar al último administrador"
        )
    anterior = miembro.rol
    miembro.rol = datos.rol
    session.add(
        _auditar(
            contexto, AccionMiembro.CAMBIO_ROL, miembro, rol_anterior=anterior, rol_nuevo=datos.rol
        )
    )
    await session.commit()
    return miembro


@router.delete("/{miembro_id}", status_code=status.HTTP_204_NO_CONTENT)
async def eliminar(miembro_id: uuid.UUID, contexto: AdminDep, session: SessionDep) -> None:
    """Retira a un miembro: pierde el acceso en su siguiente petición (HU-B03)."""
    miembro = await _miembro_de_org(session, contexto.organizacion_id, miembro_id)
    if await _es_ultimo_admin(session, miembro):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "No se puede eliminar al último administrador"
        )
    # El usuario sigue existiendo en Supabase; solo pierde el acceso a esta organización.
    session.add(_auditar(contexto, AccionMiembro.RETIRO, miembro, rol_anterior=miembro.rol))
    await session.delete(miembro)
    await session.commit()
