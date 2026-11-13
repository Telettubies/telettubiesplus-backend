"""Decisión persistida y contrato del resultado contra PostgreSQL (HU-B19 y HU-B21)."""

import json
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.contratos.decision import JustificacionGenerada
from analizador.core.config import get_settings
from analizador.integrations import anthropic_client
from analizador.integrations.anthropic_client import Uso
from analizador.models import (
    Licitacion,
    PaginaPliego,
    Resultado,
    Seccion,
    UbicacionPliego,
)
from analizador.models.enums import (
    EstadoCumplimiento,
    EstadoLicitacion,
    OrigenPagina,
    PrecisionUbicacion,
    TipoRequisito,
)
from analizador.pipeline import etapas, progreso
from analizador.pipeline.errores import AnalisisError
from tests.apoyo import AlmacenFalso, crear_organizacion
from tests.apoyo_analisis import agregar_requisito, crear_analisis


async def test_resultado_completo_usa_postgresql_y_protege_la_matriz(
    sesion: AsyncSession,
    cliente: httpx.AsyncClient,
    almacen: AlmacenFalso,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analisis = await crear_analisis(sesion)
    ruta = f"/api/licitaciones/{analisis.licitacion_id}/resultado"
    otro = await crear_organizacion(sesion, "Otra organización")
    previo = await cliente.get(ruta, headers=analisis.quien.cabeceras)
    assert previo.status_code == 202
    assert previo.json()["estado"] == "en_cola"
    assert "items" not in previo.json()
    assert almacen.firmadas == []

    tecnico = await agregar_requisito(
        sesion, analisis, numeral="3.1", texto="Requisito técnico", items=["Brecha técnica"]
    )
    habilitante = await agregar_requisito(
        sesion,
        analisis,
        numeral="3.2",
        texto="Requisito habilitante",
        items=["Brecha habilitante"],
        tipo=TipoRequisito.HABILITANTE,
    )
    revision = await agregar_requisito(
        sesion, analisis, numeral="3.3", texto="Requisito dudoso", items=["Requiere revisión"]
    )
    sesion.add_all(
        [
            Resultado(
                item_id=tecnico.items[0].id,
                estado=EstadoCumplimiento.NO_CUMPLE,
                faltante="Falta una certificación",
            ),
            Resultado(
                item_id=habilitante.items[0].id,
                estado=EstadoCumplimiento.CUMPLE_PARCIALMENTE,
                faltante="Falta vigencia",
            ),
            Resultado(
                item_id=revision.items[0].id,
                estado=EstadoCumplimiento.CUMPLE,
                requiere_revision=True,
                motivo_revision="Verificar el soporte",
                evidencia=[
                    {
                        "tipo": "cita",
                        "fuente": "Certificado",
                        "cita": "Texto citado",
                        "texto": "Fragmento completo",
                        "verificada": False,
                        "similitud": 12.0,
                    }
                ],
            ),
            UbicacionPliego(
                requisito_id=revision.id,
                pagina=1,
                precision=PrecisionUbicacion.EXACTA,
                zonas=[{"pagina": 1, "x0": 0.25, "x1": 0.5, "y0": 0.125, "y1": 0.25}],
            ),
            PaginaPliego(
                licitacion_id=analisis.licitacion_id,
                numero=1,
                texto="Página",
                origen=OrigenPagina.DIGITAL,
            ),
            Seccion(
                licitacion_id=analisis.licitacion_id,
                orden=2,
                numeral="4.1",
                pagina_inicio=2,
                pagina_fin=2,
                texto="Sección pendiente",
                procesada=False,
            ),
        ]
    )
    await sesion.commit()
    await etapas.decidir(analisis.estado)
    with pytest.raises(AnalisisError, match="Falta consolidar"):
        await etapas.cerrar(analisis.estado)
    licitacion = await sesion.get(Licitacion, analisis.licitacion_id)
    assert licitacion is not None
    assert licitacion.estado != EstadoLicitacion.COMPLETADA

    llamadas = 0
    monkeypatch.setattr(get_settings(), "anthropic_model_redaccion", "modelo-doble")

    async def generar(**kwargs: Any) -> tuple[JustificacionGenerada, Uso]:
        nonlocal llamadas
        llamadas += 1
        datos = json.loads(kwargs["messages"][0]["content"])
        assert datos["recomendacion"] == "condicionada"
        assert datos["referencias"] == [[1, "3.3"], [2, "4.1"]]
        return JustificacionGenerada(
            texto="Conviene completar la revisión y la lectura.", referencias=[1, 2]
        ), Uso(entrada=20)

    monkeypatch.setattr(anthropic_client, "generar_estructurado", generar)
    await etapas.justificar(analisis.estado)
    await etapas.justificar(analisis.estado)
    await etapas.cerrar(analisis.estado)
    await progreso.completar(analisis.trabajo_id)

    ajeno = await cliente.get(ruta, headers=otro.cabeceras)
    assert ajeno.status_code == 404
    assert almacen.firmadas == []
    respuesta = await cliente.get(ruta, headers=analisis.quien.cabeceras)
    assert respuesta.status_code == 200
    assert respuesta.headers["cache-control"] == "no-store"
    datos = respuesta.json()
    assert datos["recomendacion"] == "condicionada"
    assert "Numerales del pliego: 3.3, 4.1." in datos["justificacion"]
    assert datos["cobertura"] == {
        "secciones_procesadas": 1,
        "secciones_total": 2,
        "numerales_no_procesados": ["4.1"],
    }
    assert datos["resumen_estados"]["revision"] == 1
    assert datos["resumen_estados"]["cumple"] == 0
    assert datos["categorias"]["habilitante"]["cumple_parcialmente"] == 1
    assert [brecha["numeral"] for brecha in datos["brechas"]] == ["3.2", "3.1"]
    item = next(item for item in datos["items"] if item["numeral"] == "3.3")
    assert item["estado"] == "revision"
    assert item["estado_propuesto"] == "cumple"
    assert item["motivo_revision"] == "Verificar el soporte"
    assert item["evidencia"]["cita_literal"] == "Texto citado"
    assert item["evidencia"]["cita_verificada"] is False
    assert item["ubicacion"]["zonas"][0]["width"] == 0.25
    assert datos["pdf_paginas"] == 1
    assert len(almacen.firmadas) == 1
    assert llamadas == 1


async def test_analisis_antiguo_sin_decision_no_se_entrega_como_matriz(
    sesion: AsyncSession, cliente: httpx.AsyncClient, almacen: AlmacenFalso
) -> None:
    analisis = await crear_analisis(sesion)
    licitacion = await sesion.get(Licitacion, analisis.licitacion_id)
    assert licitacion is not None
    licitacion.estado = EstadoLicitacion.COMPLETADA
    await sesion.commit()
    await progreso.completar(analisis.trabajo_id)

    respuesta = await cliente.get(
        f"/api/licitaciones/{analisis.licitacion_id}/resultado",
        headers=analisis.quien.cabeceras,
    )

    assert respuesta.status_code == 409
    assert "resultado consolidado" in respuesta.json()["detail"]
    assert almacen.firmadas == []
