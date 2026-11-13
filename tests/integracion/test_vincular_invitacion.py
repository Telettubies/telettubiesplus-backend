"""Un revisor invitado entra aunque su cuenta de Supabase haya cambiado (RF-09, S-04, S-05)."""

import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.models import Miembro
from analizador.models.enums import Rol
from tests.apoyo import agregar_miembro, cabeceras, crear_organizacion

GOOGLE = [{"method": "oauth", "timestamp": 1}]
CONTRASENA = [{"method": "password", "timestamp": 1}]


async def _invitar(sesion: AsyncSession, email: str) -> uuid.UUID:
    admin = await crear_organizacion(sesion)
    invitado = await agregar_miembro(sesion, admin.organizacion_id, Rol.REVISOR, uuid.uuid4())
    miembro = await sesion.get(Miembro, invitado.miembro_id)
    assert miembro is not None
    miembro.email = email
    miembro.invitacion_pendiente = True
    await sesion.commit()
    return admin.organizacion_id


async def test_quien_entra_con_google_recibe_la_invitacion_de_su_correo(
    cliente: httpx.AsyncClient, sesion: AsyncSession
) -> None:
    organizacion_id = await _invitar(sesion, "revisora@empresa.co")
    auth = cabeceras(uuid.uuid4(), email="Revisora@Empresa.co", amr=GOOGLE)

    [membresia] = (await cliente.get("/api/yo", headers=auth)).json()["membresias"]
    assert membresia["organizacion_id"] == str(organizacion_id)
    assert membresia["rol"] == "revisor"
    assert (await cliente.get("/api/licitaciones", headers=auth)).status_code == 200


async def test_una_contrasena_recien_creada_no_se_queda_con_la_invitacion(
    cliente: httpx.AsyncClient, sesion: AsyncSession
) -> None:
    await _invitar(sesion, "revisora@empresa.co")
    auth = cabeceras(uuid.uuid4(), email="revisora@empresa.co", amr=CONTRASENA)

    assert (await cliente.get("/api/yo", headers=auth)).json()["membresias"] == []
