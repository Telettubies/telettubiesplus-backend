"""Dependencias de autenticación y autorización.

- Autenticación: token de Supabase verificado en cada petición (ADR-008, S-04).
- Autorización: organización y rol SOLO desde la tabla `miembros` (ADR-014). Nunca se
  confía en un rol u organización enviados por el cliente.
"""

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.db.session import get_session
from analizador.integrations import supabase
from analizador.messaging.publicador import Publicador
from analizador.models import Miembro
from analizador.models.enums import Rol

_bearer = HTTPBearer(auto_error=False)

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def usuario_actual(
    credenciales: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> supabase.UsuarioAutenticado:
    if credenciales is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Falta el token de acceso")
    try:
        return await supabase.verificar_token(credenciales.credentials)
    except supabase.TokenInvalidoError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token inválido o expirado") from exc


UsuarioDep = Annotated[supabase.UsuarioAutenticado, Depends(usuario_actual)]


@dataclass(frozen=True)
class Contexto:
    usuario: supabase.UsuarioAutenticado
    miembro: Miembro

    @property
    def organizacion_id(self) -> uuid.UUID:
        return self.miembro.organizacion_id

    @property
    def rol(self) -> Rol:
        return self.miembro.rol


async def contexto_actual(
    usuario: UsuarioDep,
    session: SessionDep,
    x_organizacion_id: Annotated[uuid.UUID | None, Header()] = None,
) -> Contexto:
    """Resuelve la membresía con la que actúa el usuario.

    ADR-014 admite que un usuario pertenezca a varias organizaciones: si tiene más de una,
    el cliente indica cuál con `X-Organizacion-Id`, y solo se acepta si existe la membresía.
    """
    resultado = await session.scalars(
        select(Miembro).where(Miembro.usuario_id == usuario.usuario_id)
    )
    membresias = list(resultado)
    if not membresias:
        # R-15: usuario autenticado sin registro de miembro → se rechaza.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "El usuario no pertenece a ninguna organización"
        )

    if x_organizacion_id is not None:
        miembro = next((m for m in membresias if m.organizacion_id == x_organizacion_id), None)
        if miembro is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "No pertenece a esa organización")
    elif len(membresias) == 1:
        miembro = membresias[0]
    else:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "El usuario pertenece a varias organizaciones: indique X-Organizacion-Id",
        )
    return Contexto(usuario=usuario, miembro=miembro)


ContextoDep = Annotated[Contexto, Depends(contexto_actual)]


async def requiere_admin(contexto: ContextoDep) -> Contexto:
    if contexto.rol != Rol.ADMINISTRADOR:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Requiere rol de administrador")
    return contexto


AdminDep = Annotated[Contexto, Depends(requiere_admin)]


def get_publicador(request: Request) -> Publicador:
    publicador: Publicador = request.app.state.publicador
    return publicador


PublicadorDep = Annotated[Publicador, Depends(get_publicador)]
