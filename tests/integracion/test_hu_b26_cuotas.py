"""HU-B26, criterios 1 y 2: límites configurables y cuotas diarias por organización."""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.core.config import get_settings
from analizador.models import EventoUso, Licitacion
from analizador.models.enums import Rol, TipoUso
from tests.apoyo import (
    AlmacenFalso,
    Identidad,
    PublicadorFalso,
    crear_organizacion,
    sembrar_matriz,
)


@pytest.fixture
def cuotas_bajas(base_de_datos: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUOTA_ANALISIS_DIARIA", "2")
    monkeypatch.setenv("CUOTA_CHAT_DIARIA", "3")
    monkeypatch.setenv("CHAT_MAX_CARACTERES", "50")
    monkeypatch.setenv("MAX_PDF_PAGINAS", "120")
    get_settings.cache_clear()


@pytest.fixture
async def revisor(sesion: AsyncSession, cuotas_bajas: None) -> Identidad:
    return await crear_organizacion(sesion, rol=Rol.REVISOR)


async def _subir(cliente: httpx.AsyncClient, quien: Identidad, marca: str) -> httpx.Response:
    return await cliente.post(
        "/api/licitaciones",
        headers=quien.cabeceras,
        files={"archivo": ("pliego.pdf", f"%PDF-1.7\n% {marca}\n".encode(), "application/pdf")},
    )


async def _preguntar(
    cliente: httpx.AsyncClient, quien: Identidad, licitacion_id: uuid.UUID, pregunta: str
) -> httpx.Response:
    return await cliente.post(
        f"/api/licitaciones/{licitacion_id}/chat",
        headers=quien.cabeceras,
        json={"pregunta": pregunta},
    )


async def _registrar_preguntas(sesion: AsyncSession, quien: Identidad, cantidad: int) -> None:
    sesion.add_all(
        EventoUso(
            organizacion_id=quien.organizacion_id,
            tipo=TipoUso.PREGUNTA_CHAT,
            usuario_id=quien.usuario_id,
        )
        for _ in range(cantidad)
    )
    await sesion.commit()


# --- Criterio 2: cuota diaria de análisis ---------------------------------------------------


async def test_al_superar_la_cuota_de_analisis_responde_429_con_un_mensaje_claro(
    cliente: httpx.AsyncClient,
    sesion: AsyncSession,
    revisor: Identidad,
    almacen: AlmacenFalso,
    publicador: PublicadorFalso,
) -> None:
    assert (await _subir(cliente, revisor, "uno")).status_code == 202
    assert (await _subir(cliente, revisor, "dos")).status_code == 202

    tercero = await _subir(cliente, revisor, "tres")

    assert tercero.status_code == 429
    assert "2 análisis en 24 horas" in tercero.json()["detail"]
    # El rechazo no gasta nada: ni archivo, ni licitación, ni mensaje en la cola.
    assert len(almacen.objetos) == 2
    assert len(publicador.publicados) == 2
    assert await sesion.scalar(select(func.count()).select_from(Licitacion)) == 2


async def test_la_cuota_de_una_organizacion_no_afecta_a_las_demas(
    cliente: httpx.AsyncClient, sesion: AsyncSession, revisor: Identidad
) -> None:
    otra = await crear_organizacion(sesion, "Otra organización")
    for marca in ("uno", "dos"):
        await _subir(cliente, revisor, marca)
    assert (await _subir(cliente, revisor, "tres")).status_code == 429

    assert (await _subir(cliente, otra, "tres")).status_code == 202


async def test_un_pdf_duplicado_no_consume_cuota_ni_se_rechaza_por_ella(
    cliente: httpx.AsyncClient, revisor: Identidad
) -> None:
    await _subir(cliente, revisor, "uno")
    await _subir(cliente, revisor, "dos")

    repetido = await _subir(cliente, revisor, "uno")

    assert repetido.status_code == 200 and repetido.json()["duplicada"] is True


async def test_los_analisis_de_hace_mas_de_24_horas_no_cuentan(
    cliente: httpx.AsyncClient, sesion: AsyncSession, revisor: Identidad
) -> None:
    await _subir(cliente, revisor, "uno")
    await _subir(cliente, revisor, "dos")
    await sesion.execute(
        update(Licitacion).values(creado_en=datetime.now(UTC) - timedelta(hours=25))
    )
    await sesion.commit()

    assert (await _subir(cliente, revisor, "tres")).status_code == 202


# --- Criterio 2: cuota diaria de preguntas al chat ------------------------------------------


async def test_al_superar_la_cuota_del_chat_responde_429(
    cliente: httpx.AsyncClient, sesion: AsyncSession, revisor: Identidad
) -> None:
    matriz = await sembrar_matriz(sesion, revisor, "ALFA")
    await _registrar_preguntas(sesion, revisor, 2)
    # Con dos de tres todavía pasa la cuota: sin pliego indexado el chat responde 409, no 429.
    assert (await _preguntar(cliente, revisor, matriz.licitacion_id, "¿Plazo?")).status_code == 409

    await _registrar_preguntas(sesion, revisor, 1)
    respuesta = await _preguntar(cliente, revisor, matriz.licitacion_id, "¿Plazo?")

    assert respuesta.status_code == 429
    assert "3 preguntas al chat en 24 horas" in respuesta.json()["detail"]


async def test_la_cuota_del_chat_es_por_organizacion(
    cliente: httpx.AsyncClient, sesion: AsyncSession, revisor: Identidad
) -> None:
    otra = await crear_organizacion(sesion, "Otra organización")
    matriz_otra = await sembrar_matriz(sesion, otra, "BETA")
    await _registrar_preguntas(sesion, revisor, 3)

    respuesta = await _preguntar(cliente, otra, matriz_otra.licitacion_id, "¿Plazo?")

    assert respuesta.status_code == 409  # no la frena la cuota agotada de la otra


async def test_una_pregunta_demasiado_larga_se_rechaza_indicando_el_limite(
    cliente: httpx.AsyncClient, sesion: AsyncSession, revisor: Identidad
) -> None:
    matriz = await sembrar_matriz(sesion, revisor, "ALFA")

    respuesta = await _preguntar(cliente, revisor, matriz.licitacion_id, "x" * 51)

    assert respuesta.status_code == 422
    assert "50 caracteres" in respuesta.json()["detail"]


async def test_una_pregunta_vacia_se_rechaza(
    cliente: httpx.AsyncClient, sesion: AsyncSession, revisor: Identidad
) -> None:
    matriz = await sembrar_matriz(sesion, revisor, "ALFA")
    assert (await _preguntar(cliente, revisor, matriz.licitacion_id, "")).status_code == 422


# --- Criterio 1: límites configurables, visibles para el frontend ---------------------------


async def test_los_limites_vigentes_y_el_consumo_se_pueden_consultar(
    cliente: httpx.AsyncClient, sesion: AsyncSession, revisor: Identidad
) -> None:
    await _subir(cliente, revisor, "uno")
    await _registrar_preguntas(sesion, revisor, 2)

    respuesta = await cliente.get("/api/limites", headers=revisor.cabeceras)

    assert respuesta.status_code == 200
    assert respuesta.json() == {
        "max_pdf_mb": 1,
        "max_pdf_paginas": 120,
        "chat_max_caracteres": 50,
        "ventana_horas": 24,
        "analisis": {"usados": 1, "cuota": 2},
        "chat": {"usados": 2, "cuota": 3},
    }


async def test_el_consumo_mostrado_es_solo_el_de_la_organizacion(
    cliente: httpx.AsyncClient, sesion: AsyncSession, revisor: Identidad
) -> None:
    otra = await crear_organizacion(sesion, "Otra organización")
    await _subir(cliente, revisor, "uno")
    await _registrar_preguntas(sesion, revisor, 2)

    respuesta = await cliente.get("/api/limites", headers=otra.cabeceras)

    assert respuesta.json()["analisis"]["usados"] == 0
    assert respuesta.json()["chat"]["usados"] == 0
