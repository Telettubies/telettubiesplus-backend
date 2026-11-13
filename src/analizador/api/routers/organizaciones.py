"""Alta de una organización por su primer administrador (RF-09, ADR-014).

El DAS describe cómo un administrador invita a otros (F-01, HU-B02), pero no cómo nace la
primera organización (docs/PLAN.md, sección 6). Este endpoint cubre ese hueco para el registro
de empresa: un usuario ya autenticado en Supabase, que todavía no pertenece a ninguna
organización, crea la suya y queda como su administrador.

S-04 y S-05: el rol lo fija el servidor y solo se concede a quien no tiene membresías, así que
nadie puede usarlo para entrar en una organización existente ni para escalar privilegios.
"""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select, text

from analizador.api.deps import SessionDep, UsuarioDep
from analizador.models import Miembro, Organizacion
from analizador.models.enums import Rol
from analizador.schemas import MiembroOut, OrganizacionIn

router = APIRouter(prefix="/api/organizaciones", tags=["organizaciones"])


@router.post("", response_model=MiembroOut, status_code=status.HTTP_201_CREATED)
async def crear(datos: OrganizacionIn, usuario: UsuarioDep, session: SessionDep) -> Miembro:
    if not usuario.email:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "La cuenta no tiene un correo asociado"
        )
    # Serializa las altas del mismo usuario: dos peticiones simultáneas no crean dos empresas.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:clave))"),
        {"clave": f"alta-organizacion:{usuario.usuario_id}"},
    )
    existentes = await session.scalar(
        select(func.count()).select_from(Miembro).where(Miembro.usuario_id == usuario.usuario_id)
    )
    if existentes:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Tu cuenta ya pertenece a una organización. Pide acceso a su administrador.",
        )

    organizacion = Organizacion(nombre=datos.nombre)
    session.add(organizacion)
    await session.flush()
    miembro = Miembro(
        organizacion_id=organizacion.id,
        usuario_id=usuario.usuario_id,
        email=usuario.email,
        rol=Rol.ADMINISTRADOR,
    )
    session.add(miembro)
    await session.commit()
    await session.refresh(miembro)
    return miembro
