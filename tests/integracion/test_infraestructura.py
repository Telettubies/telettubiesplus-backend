"""La infraestructura de pruebas usa PostgreSQL con pgvector real y aísla cada caso."""

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.models import Organizacion
from tests.apoyo import PublicadorFalso, crear_organizacion


async def test_pgvector_disponible(sesion: AsyncSession) -> None:
    distancia = await sesion.scalar(text("SELECT '[1,0]'::vector <=> '[0,1]'::vector"))
    assert distancia == 1.0


async def test_health_ready_con_base_real(cliente: httpx.AsyncClient) -> None:
    respuesta = await cliente.get("/health/ready")
    assert respuesta.status_code == 200
    assert respuesta.json()["checks"] == {"postgres": True, "rabbitmq": True}


async def test_health_ready_degradado_sin_cola(
    cliente: httpx.AsyncClient, publicador: PublicadorFalso
) -> None:
    publicador.conectado = False
    respuesta = await cliente.get("/health/ready")
    assert respuesta.status_code == 503
    assert respuesta.json()["estado"] == "degradado"


async def test_cada_caso_empieza_con_la_base_vacia_1(sesion: AsyncSession) -> None:
    assert await sesion.scalar(select(func.count()).select_from(Organizacion)) == 0
    await crear_organizacion(sesion)


async def test_cada_caso_empieza_con_la_base_vacia_2(sesion: AsyncSession) -> None:
    assert await sesion.scalar(select(func.count()).select_from(Organizacion)) == 0
    await crear_organizacion(sesion)
