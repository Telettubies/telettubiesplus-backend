"""HU-B08: consultar el progreso de un análisis (RF-08, ADR-015)."""

import uuid

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.models import Seccion, TrabajoProcesamiento
from analizador.models.enums import EstadoTrabajo, Rol
from analizador.pipeline import catalogo, progreso
from tests.apoyo import Identidad, Matriz, crear_organizacion, sembrar_matriz

PDF = b"%PDF-1.7\n% pliego\n%%EOF\n"


@pytest.fixture
async def revisor(sesion: AsyncSession) -> Identidad:
    return await crear_organizacion(sesion, rol=Rol.REVISOR)


@pytest.fixture
async def matriz(sesion: AsyncSession, revisor: Identidad) -> Matriz:
    return await sembrar_matriz(sesion, revisor, "ALFA")


async def _trabajo(sesion: AsyncSession, trabajo_id: uuid.UUID) -> TrabajoProcesamiento:
    trabajo = await sesion.get(TrabajoProcesamiento, trabajo_id)
    assert trabajo is not None
    await sesion.refresh(trabajo)
    return trabajo


async def _progreso(
    cliente: httpx.AsyncClient, quien: Identidad, licitacion_id: uuid.UUID
) -> httpx.Response:
    return await cliente.get(f"/api/licitaciones/{licitacion_id}/progreso", headers=quien.cabeceras)


# --- Criterio 1: etapa, porcentaje y secciones procesadas sobre el total --------------------


async def test_analisis_recien_cargado_esta_en_cola_sin_avance(
    cliente: httpx.AsyncClient, revisor: Identidad
) -> None:
    creada = (
        await cliente.post(
            "/api/licitaciones",
            headers=revisor.cabeceras,
            files={"archivo": ("pliego.pdf", PDF, "application/pdf")},
        )
    ).json()

    respuesta = await _progreso(cliente, revisor, creada["licitacion"]["id"])

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["trabajo_id"] == creada["trabajo"]["id"]
    assert cuerpo["estado"] == "en_cola"
    assert cuerpo["estado_licitacion"] == "pendiente"
    assert cuerpo["etapa_actual"] is None
    assert cuerpo["progreso"] == 0
    assert cuerpo["secciones"] is None
    assert cuerpo["error"] is None


async def test_durante_la_extraccion_informa_secciones_procesadas_sobre_el_total(
    cliente: httpx.AsyncClient, sesion: AsyncSession, revisor: Identidad, matriz: Matriz
) -> None:
    trabajo = await _trabajo(sesion, matriz.trabajo_id)
    trabajo.estado = EstadoTrabajo.EN_EJECUCION
    trabajo.error = None
    await sesion.commit()
    await progreso.registrar_etapa(matriz.trabajo_id, "extraccion", 40)
    # La matriz sembrada trae una sección sin procesar; se añaden tres ya procesadas.
    sesion.add_all(
        Seccion(
            licitacion_id=matriz.licitacion_id,
            orden=orden,
            numeral=f"4.{orden}",
            pagina_inicio=orden,
            pagina_fin=orden,
            texto="texto",
            procesada=True,
        )
        for orden in (2, 3, 4)
    )
    await sesion.commit()

    cuerpo = (await _progreso(cliente, revisor, matriz.licitacion_id)).json()

    assert cuerpo["estado"] == "en_ejecucion"
    assert cuerpo["etapa_actual"] == "extraccion"
    assert cuerpo["progreso"] == 40
    assert cuerpo["secciones"] == {"procesadas": 3, "total": 4}


async def test_incluye_todas_las_etapas_del_das_en_orden(
    cliente: httpx.AsyncClient, revisor: Identidad, matriz: Matriz
) -> None:
    etapas = (await _progreso(cliente, revisor, matriz.licitacion_id)).json()["etapas"]

    assert [e["clave"] for e in etapas] == [e.clave for e in catalogo.ETAPAS]
    assert etapas[0]["clave"] == "lectura_pdf" and etapas[-1]["clave"] == "cierre"
    porcentajes = [e["porcentaje"] for e in etapas]
    assert porcentajes == sorted(porcentajes) and porcentajes[-1] == 100
    assert all(e["nombre"] for e in etapas)


# --- Criterio 2: un análisis fallido devuelve el estado y un mensaje comprensible ----------


async def test_analisis_fallido_devuelve_estado_y_mensaje(
    cliente: httpx.AsyncClient, revisor: Identidad, matriz: Matriz
) -> None:
    await progreso.fallar(matriz.trabajo_id, "No se pudo leer el PDF. Puede reintentar.")

    cuerpo = (await _progreso(cliente, revisor, matriz.licitacion_id)).json()

    assert cuerpo["estado"] == "fallido"
    assert cuerpo["error"] == "No se pudo leer el PDF. Puede reintentar."
    assert cuerpo["finalizado_en"] is not None


async def test_el_mensaje_de_error_solo_se_muestra_si_el_analisis_esta_fallido(
    cliente: httpx.AsyncClient, sesion: AsyncSession, revisor: Identidad, matriz: Matriz
) -> None:
    trabajo = await _trabajo(sesion, matriz.trabajo_id)
    trabajo.estado = EstadoTrabajo.EN_EJECUCION  # conserva el error de un intento anterior
    await sesion.commit()

    cuerpo = (await _progreso(cliente, revisor, matriz.licitacion_id)).json()

    assert cuerpo["estado"] == "en_ejecucion"
    assert cuerpo["error"] is None


# --- Criterio 3: el estado se lee de PostgreSQL, nunca de RabbitMQ --------------------------


class _ColaInaccesible:
    def __getattr__(self, nombre: str) -> object:
        raise AssertionError(f"el progreso no debe consultar la cola (accedió a {nombre})")


async def test_el_progreso_no_consulta_la_cola(
    app: FastAPI, cliente: httpx.AsyncClient, revisor: Identidad, matriz: Matriz
) -> None:
    app.state.publicador = _ColaInaccesible()

    respuesta = await _progreso(cliente, revisor, matriz.licitacion_id)

    assert respuesta.status_code == 200


# --- Calidad: el progreso nunca retrocede salvo en un reintento explícito -------------------


async def test_el_progreso_no_retrocede_si_se_repite_una_etapa_anterior(
    sesion: AsyncSession, matriz: Matriz
) -> None:
    await progreso.registrar_etapa(matriz.trabajo_id, "extraccion", 40)
    await progreso.registrar_etapa(matriz.trabajo_id, "lectura_pdf", 5)

    trabajo = await _trabajo(sesion, matriz.trabajo_id)
    assert (trabajo.etapa_actual, trabajo.progreso) == ("extraccion", 40)

    await progreso.registrar_etapa(matriz.trabajo_id, "localizacion", 50)
    trabajo = await _trabajo(sesion, matriz.trabajo_id)
    assert (trabajo.etapa_actual, trabajo.progreso) == ("localizacion", 50)


async def test_un_reintento_vuelve_a_en_cola_y_limpia_el_error(
    cliente: httpx.AsyncClient, revisor: Identidad, matriz: Matriz
) -> None:
    await progreso.registrar_etapa(matriz.trabajo_id, "verificacion", 85)
    await progreso.fallar(matriz.trabajo_id, "El análisis falló en la verificación.")

    reintento = await cliente.post(
        f"/api/licitaciones/{matriz.licitacion_id}/reintentar", headers=revisor.cabeceras
    )
    assert reintento.status_code == 202

    cuerpo = (await _progreso(cliente, revisor, matriz.licitacion_id)).json()
    assert cuerpo["estado"] == "en_cola"
    assert cuerpo["error"] is None
    # El análisis se reanuda desde la última etapa completada: el avance se conserva.
    assert (cuerpo["etapa_actual"], cuerpo["progreso"]) == ("verificacion", 85)


async def test_licitacion_inexistente_es_404(
    cliente: httpx.AsyncClient, revisor: Identidad
) -> None:
    assert (await _progreso(cliente, revisor, uuid.uuid4())).status_code == 404
