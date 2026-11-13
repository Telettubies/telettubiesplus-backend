"""Alta de la organización propia desde el registro (RF-09, ADR-014, S-04, S-05)."""

import asyncio
import uuid

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.models import Miembro, Organizacion
from tests.apoyo import cabeceras, crear_organizacion


async def test_usuario_sin_membresia_crea_su_empresa_y_queda_como_administrador(
    cliente: httpx.AsyncClient,
) -> None:
    usuario_id = uuid.uuid4()
    auth = cabeceras(usuario_id, email="fundadora@empresa.co")

    creada = await cliente.post(
        "/api/organizaciones", json={"nombre": "  Mi Empresa S.A.S.  "}, headers=auth
    )
    assert creada.status_code == 201
    assert creada.json()["rol"] == "administrador"
    assert creada.json()["email"] == "fundadora@empresa.co"

    [membresia] = (await cliente.get("/api/yo", headers=auth)).json()["membresias"]
    assert membresia["organizacion_id"] == creada.json()["organizacion_id"]
    # Ya actúa como administrador de su organización.
    assert (await cliente.get("/api/miembros", headers=auth)).status_code == 200


async def test_el_nombre_se_guarda_recortado(
    cliente: httpx.AsyncClient, sesion: AsyncSession
) -> None:
    creada = await cliente.post(
        "/api/organizaciones",
        json={"nombre": "  Constructora Andina  "},
        headers=cabeceras(uuid.uuid4()),
    )
    organizacion = await sesion.get(Organizacion, uuid.UUID(creada.json()["organizacion_id"]))
    assert organizacion is not None
    assert organizacion.nombre == "Constructora Andina"


async def test_quien_ya_pertenece_a_una_organizacion_no_puede_crear_otra(
    cliente: httpx.AsyncClient, sesion: AsyncSession
) -> None:
    revisor = await crear_organizacion(sesion)

    respuesta = await cliente.post(
        "/api/organizaciones", json={"nombre": "Otra"}, headers=revisor.cabeceras
    )
    assert respuesta.status_code == 409
    total = await sesion.scalar(
        select(func.count()).select_from(Miembro).where(Miembro.usuario_id == revisor.usuario_id)
    )
    assert total == 1


async def test_dos_altas_simultaneas_del_mismo_usuario_crean_una_sola_empresa(
    cliente: httpx.AsyncClient, sesion: AsyncSession
) -> None:
    usuario_id = uuid.uuid4()
    auth = cabeceras(usuario_id)

    respuestas = await asyncio.gather(
        cliente.post("/api/organizaciones", json={"nombre": "Una"}, headers=auth),
        cliente.post("/api/organizaciones", json={"nombre": "Dos"}, headers=auth),
    )
    assert sorted(r.status_code for r in respuestas) == [201, 409]
    total = await sesion.scalar(
        select(func.count()).select_from(Miembro).where(Miembro.usuario_id == usuario_id)
    )
    assert total == 1


async def test_el_cliente_no_elige_el_rol(cliente: httpx.AsyncClient) -> None:
    respuesta = await cliente.post(
        "/api/organizaciones",
        json={"nombre": "Empresa", "rol": "revisor", "organizacion_id": str(uuid.uuid4())},
        headers=cabeceras(uuid.uuid4()),
    )
    assert respuesta.status_code == 201
    assert respuesta.json()["rol"] == "administrador"


async def test_nombre_invalido_responde_422(cliente: httpx.AsyncClient) -> None:
    auth = cabeceras(uuid.uuid4())
    for nombre in ["", " ", "A", "x" * 201]:
        respuesta = await cliente.post("/api/organizaciones", json={"nombre": nombre}, headers=auth)
        assert respuesta.status_code == 422, nombre


async def test_sin_token_responde_401(cliente: httpx.AsyncClient) -> None:
    respuesta = await cliente.post("/api/organizaciones", json={"nombre": "Empresa"})
    assert respuesta.status_code == 401
