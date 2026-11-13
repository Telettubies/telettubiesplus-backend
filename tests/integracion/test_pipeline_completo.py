"""Todos los nodos reales, PDF y DB reales; solo proveedores externos simulados."""

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.contratos.decision import JustificacionGenerada
from analizador.contratos.extraccion import ExtraccionSeccion
from analizador.core.config import get_settings
from analizador.integrations import anthropic_client, nvidia
from analizador.models import TrabajoProcesamiento
from analizador.models.enums import EstadoTrabajo, TipoTrabajo
from analizador.pipeline import grafo
from analizador.worker import handlers
from tests.apoyo import AlmacenFalso, EmbeddingsFalsos, crear_organizacion, mensaje_de
from tests.apoyo_pdf import crear_pdf
from tests.integracion.test_hu_b06_indexacion import _cargar, _procesar
from tests.integracion.test_hu_b13_extraccion import RESPUESTA_FINANCIERA


async def test_analisis_recorrido_completo_hasta_matriz_y_exportacion(
    cliente: httpx.AsyncClient,
    sesion: AsyncSession,
    almacen: AlmacenFalso,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await crear_organizacion(sesion)
    for variable in [
        "NVIDIA_MODEL_EMBEDDINGS",
        "ANTHROPIC_MODEL_EXTRACCION",
        "ANTHROPIC_MODEL_REDACCION",
        "NVIDIA_MODEL_FILTRO",
    ]:
        monkeypatch.setenv(variable, "modelo-prueba")
    get_settings.cache_clear()
    EmbeddingsFalsos().instalar(monkeypatch)
    await _procesar(await _cargar(cliente, admin))

    async def completar(**_kwargs: Any) -> str:
        # El filtro conserva secciones ante una respuesta no clasificable.
        return "{}"

    async def generar(**kwargs: Any) -> tuple[Any, anthropic_client.Uso]:
        if kwargs["output_format"] is ExtraccionSeccion:
            return RESPUESTA_FINANCIERA, anthropic_client.Uso()
        if kwargs["output_format"] is JustificacionGenerada:
            return JustificacionGenerada(
                texto="Se evaluaron los indicadores con evidencia empresarial.", referencias=[1]
            ), anthropic_client.Uso()
        raise AssertionError("Llamada inesperada a un proveedor")

    monkeypatch.setattr(nvidia, "completar", completar)
    monkeypatch.setattr(anthropic_client, "generar_estructurado", generar)
    pdf = crear_pdf(
        [
            [
                "2.2 CAPACIDAD FINANCIERA",
                "El proponente debera acreditar un indice de liquidez mayor o igual a 1,5",
                "y un indice de endeudamiento menor o igual al 70 %.",
            ]
        ]
    )
    carga = await cliente.post(
        "/api/licitaciones",
        headers=admin.cabeceras,
        files={"archivo": ("pliego.pdf", pdf, "application/pdf")},
    )
    assert carga.status_code == 202, carga.text
    trabajo_id = uuid.UUID(carga.json()["trabajo"]["id"])
    licitacion_id = carga.json()["licitacion"]["id"]
    await handlers.procesar(mensaje_de(trabajo_id), TipoTrabajo.ANALISIS, grafo.construir())
    sesion.expire_all()
    trabajo = await sesion.get(TrabajoProcesamiento, trabajo_id)
    assert trabajo and trabajo.estado == EstadoTrabajo.COMPLETADO, (
        trabajo.error if trabajo else "sin trabajo"
    )
    assert trabajo.etapa_actual == "cierre" and trabajo.progreso == 100
    matriz = await cliente.get(
        f"/api/licitaciones/{licitacion_id}/resultado", headers=admin.cabeceras
    )
    assert matriz.status_code == 200, matriz.text
    assert matriz.json()["items"] and matriz.json()["justificacion"]
    exportacion = await cliente.get(
        f"/api/licitaciones/{licitacion_id}/exportar", headers=admin.cabeceras
    )
    assert exportacion.status_code == 200
