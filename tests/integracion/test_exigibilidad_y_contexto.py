"""Exigibilidad de los ítems y contexto del evaluador, contra PostgreSQL (HU-B17, HU-B19, HU-B21).

- Solo lo histórico se verifica contra la base; lo demás va a la lista de chequeo y no cuenta
  en la decisión.
- Si la base cabe, el evaluador la recibe completa y agrupada por entidad, aunque la búsqueda
  vectorial no haya traído nada para el ítem.
- El evaluador recibe el texto de los numerales del pliego que cita el requisito.
"""

import json
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from analizador.contratos.decision import JustificacionGenerada
from analizador.contratos.verificacion import VerificacionItem
from analizador.core.config import get_settings
from analizador.integrations import anthropic_client
from analizador.integrations.anthropic_client import Uso
from analizador.models import Item, Requisito, Resultado, Seccion
from analizador.models.enums import (
    EstadoCumplimiento,
    TipoExigibilidad,
    TipoFuenteConocimiento,
    TipoRequisito,
)
from analizador.pipeline import etapas, progreso, verificacion
from analizador.pipeline.errores import AnalisisError
from tests.apoyo import AlmacenFalso, texto_del_mensaje
from tests.apoyo import ExtractorFalso as ModeloFalso
from tests.apoyo_analisis import Analisis, agregar_fragmento, agregar_requisito, crear_analisis

HISTORICO = "Experiencia en gestión documental"
FACTURA = "Radicar factura electrónica por cada mensualidad"
SUBSANAR = "Presentar subsanaciones entre el 1 y el 3 de diciembre de 2026"

FRAGMENTO_CONTRATO = "Contrato C-2023-014. Objeto: migración de 1,2 millones de documentos."
FRAGMENTO_PERSONA = "Ana Gómez, ingeniera de sistemas con especialización en archivística."

CUMPLE = VerificacionItem(
    estado=EstadoCumplimiento.CUMPLE,
    fragmento=2,
    cita="ingeniera de sistemas con especialización en archivística",
    faltante=None,
    confianza=0.9,
)


@pytest.fixture
def modelo(base_de_datos: None, monkeypatch: pytest.MonkeyPatch) -> ModeloFalso:
    monkeypatch.setenv("ANTHROPIC_MODEL_VERIFICACION", "modelo-de-verificacion")
    get_settings.cache_clear()
    falso = ModeloFalso(respuestas={f"Condición a verificar: {HISTORICO}": [CUMPLE]})
    monkeypatch.setattr(anthropic_client, "generar_estructurado", falso)
    return falso


async def _requisito_mixto(sesion: AsyncSession, analisis: Analisis) -> Requisito:
    requisito = await agregar_requisito(
        sesion,
        analisis,
        numeral="7.1",
        texto="Acreditar la experiencia descrita en el numeral 4 y facturar mensualmente.",
        items=[HISTORICO, FACTURA, SUBSANAR],
    )
    requisito.items[1].exigibilidad = TipoExigibilidad.COMPROMISO_EJECUCION
    requisito.items[2].exigibilidad = TipoExigibilidad.HITO_CRONOGRAMA
    sesion.add(
        Seccion(
            licitacion_id=analisis.licitacion_id,
            orden=2,
            numeral="4",
            titulo="OBJETO",
            pagina_inicio=2,
            pagina_fin=2,
            texto="Objeto: digitalización y archivo electrónico de expedientes.",
            tiene_requisitos=False,  # descartada por el filtro
            procesada=True,
        )
    )
    await sesion.commit()
    return requisito


async def _resultados(sesion: AsyncSession) -> dict[str, Resultado | None]:
    sesion.expire_all()
    filas = await sesion.scalars(select(Item).options(selectinload(Item.resultado)))
    return {item.descripcion: item.resultado for item in filas}


async def test_solo_lo_historico_se_verifica_y_con_la_base_completa(
    sesion: AsyncSession, modelo: ModeloFalso
) -> None:
    analisis = await crear_analisis(sesion)
    await _requisito_mixto(sesion, analisis)
    await agregar_fragmento(sesion, analisis.quien, FRAGMENTO_CONTRATO, [0.1] * 4, titulo="C-1")
    await agregar_fragmento(
        sesion,
        analisis.quien,
        FRAGMENTO_PERSONA,
        [0.2] * 4,
        titulo="Ana Gómez",
        tipo=TipoFuenteConocimiento.HOJA_DE_VIDA,
    )
    # Otra organización: su base nunca entra en el perfil.
    otra = await crear_analisis(sesion)
    await agregar_fragmento(sesion, otra.quien, "Fragmento AJENO", [0.3] * 4, titulo="Ajeno")

    await verificacion.ejecutar(analisis.estado)

    resultados = await _resultados(sesion)
    assert resultados[FACTURA] is None and resultados[SUBSANAR] is None
    assert modelo.enviadas(FACTURA) == 0 and modelo.enviadas(SUBSANAR) == 0
    historico = resultados[HISTORICO]
    assert historico is not None and historico.estado == EstadoCumplimiento.CUMPLE
    # Sin candidatos recuperados, el ítem igual se evalúa: la base completa es la evidencia.
    [llamada] = modelo.llamadas
    contenido = texto_del_mensaje(llamada)
    assert '<evidencia completa="si">' in contenido
    assert FRAGMENTO_CONTRATO in contenido and FRAGMENTO_PERSONA in contenido
    assert "AJENO" not in contenido
    # Agrupada por entidad: los contratos antes que las hojas de vida.
    assert contenido.index('tipo="contrato"') < contenido.index('tipo="hoja_de_vida"')
    assert llamada["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    # El numeral 4 que cita el requisito llega como contexto del pliego.
    assert "digitalización y archivo electrónico" in contenido
    [cita] = historico.evidencia
    assert cita["texto"] == FRAGMENTO_PERSONA and cita["fuente"] == "Ana Gómez"
    assert historico.regla is not None and "base completa" in historico.regla


async def test_sin_perfil_completo_se_usa_solo_lo_recuperado(
    sesion: AsyncSession, modelo: ModeloFalso, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Con el perfil completo desactivado (o una base que no cabe) vuelve el top-k."""
    monkeypatch.setattr(get_settings(), "verificacion_perfil_max_caracteres", 0)
    analisis = await crear_analisis(sesion)
    await _requisito_mixto(sesion, analisis)
    await agregar_fragmento(sesion, analisis.quien, FRAGMENTO_CONTRATO, [0.1] * 4, titulo="C-1")

    await verificacion.ejecutar(analisis.estado)

    historico = (await _resultados(sesion))[HISTORICO]
    assert historico is not None and historico.estado == EstadoCumplimiento.NO_CUMPLE
    assert historico.faltante == verificacion.FALTANTE_SIN_EVIDENCIA
    assert modelo.llamadas == []


async def test_sin_base_activa_la_verificacion_no_inventa_incumplimientos(
    sesion: AsyncSession, modelo: ModeloFalso
) -> None:
    analisis = await crear_analisis(sesion)
    await _requisito_mixto(sesion, analisis)

    with pytest.raises(AnalisisError, match="No hay una base empresarial activa"):
        await verificacion.ejecutar(analisis.estado)

    assert all(r is None for r in (await _resultados(sesion)).values())


async def test_la_lista_de_chequeo_no_cuenta_en_la_decision_ni_en_la_matriz(
    sesion: AsyncSession,
    cliente: httpx.AsyncClient,
    almacen: AlmacenFalso,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analisis = await crear_analisis(sesion)
    requisito = await _requisito_mixto(sesion, analisis)
    sesion.add(
        Resultado(
            item_id=requisito.items[0].id,
            estado=EstadoCumplimiento.CUMPLE,
            evidencia=[{"tipo": "dato", "fuente": "C-1", "texto": "dato", "verificada": True}],
        )
    )
    await sesion.commit()

    monkeypatch.setattr(get_settings(), "anthropic_model_redaccion", "modelo-doble")

    async def generar(**kwargs: Any) -> tuple[JustificacionGenerada, Uso]:
        datos = json.loads(kwargs["messages"][0]["content"])
        # Los ítems sin resultado de la lista de chequeo no dejan la decisión condicionada.
        assert datos["recomendacion"] == "presentarse"
        return JustificacionGenerada(texto="Cumple lo evaluado.", referencias=[1]), Uso()

    monkeypatch.setattr(anthropic_client, "generar_estructurado", generar)
    for etapa in (etapas.decidir, etapas.justificar, etapas.cerrar):
        await etapa(analisis.estado)
    await progreso.completar(analisis.trabajo_id)

    respuesta = await cliente.get(
        f"/api/licitaciones/{analisis.licitacion_id}/resultado",
        headers=analisis.quien.cabeceras,
    )

    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert datos["recomendacion"] == "presentarse"
    assert [item["descripcion_item"] for item in datos["items"]] == [HISTORICO]
    assert sum(datos["resumen_estados"].values()) == 1
    assert datos["brechas"] == []
    chequeo = {item["descripcion_item"]: item for item in datos["lista_chequeo"]}
    assert set(chequeo) == {FACTURA, SUBSANAR}
    assert chequeo[FACTURA]["exigibilidad"] == "compromiso_ejecucion"
    assert "carta de presentación" in chequeo[FACTURA]["nota"]
    assert chequeo[SUBSANAR]["exigibilidad"] == "hito_cronograma"
    assert chequeo[SUBSANAR]["numeral"] == "7.1"
    assert chequeo[SUBSANAR]["categoria"] == TipoRequisito.TECNICO.value
