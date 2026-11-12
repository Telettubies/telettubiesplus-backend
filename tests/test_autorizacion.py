"""ADR-014: resolución de la membresía con la que actúa el usuario."""

import uuid
from typing import Any

import pytest
from fastapi import HTTPException

from analizador.api.deps import contexto_actual
from analizador.integrations.supabase import UsuarioAutenticado
from analizador.models import Miembro
from analizador.models.enums import Rol


class _SesionFalsa:
    def __init__(self, miembros: list[Miembro]) -> None:
        self._miembros = miembros

    async def scalars(self, _consulta: Any) -> list[Miembro]:
        return self._miembros


def _miembro(usuario_id: uuid.UUID, organizacion_id: uuid.UUID | None = None) -> Miembro:
    return Miembro(
        id=uuid.uuid4(),
        usuario_id=usuario_id,
        organizacion_id=organizacion_id or uuid.uuid4(),
        email="x@ejemplo.co",
        rol=Rol.REVISOR,
    )


USUARIO = UsuarioAutenticado(usuario_id=uuid.uuid4(), email="x@ejemplo.co")


async def test_sin_membresia_es_403() -> None:
    with pytest.raises(HTTPException) as exc:
        await contexto_actual(USUARIO, _SesionFalsa([]), None)  # type: ignore[arg-type]
    assert exc.value.status_code == 403


async def test_una_membresia_se_usa_por_defecto() -> None:
    miembro = _miembro(USUARIO.usuario_id)
    contexto = await contexto_actual(USUARIO, _SesionFalsa([miembro]), None)  # type: ignore[arg-type]
    assert contexto.organizacion_id == miembro.organizacion_id


async def test_varias_membresias_exigen_cabecera() -> None:
    miembros = [_miembro(USUARIO.usuario_id), _miembro(USUARIO.usuario_id)]
    with pytest.raises(HTTPException) as exc:
        await contexto_actual(USUARIO, _SesionFalsa(miembros), None)  # type: ignore[arg-type]
    assert exc.value.status_code == 400

    contexto = await contexto_actual(
        USUARIO,
        _SesionFalsa(miembros),  # type: ignore[arg-type]
        miembros[1].organizacion_id,
    )
    assert contexto.organizacion_id == miembros[1].organizacion_id


async def test_cabecera_de_organizacion_ajena_es_403() -> None:
    miembros = [_miembro(USUARIO.usuario_id)]
    with pytest.raises(HTTPException) as exc:
        await contexto_actual(USUARIO, _SesionFalsa(miembros), uuid.uuid4())  # type: ignore[arg-type]
    assert exc.value.status_code == 403
