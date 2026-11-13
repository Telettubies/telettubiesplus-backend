"""HU-B29: exportar la matriz a Excel (RF-22, S-12)."""

from io import BytesIO

import httpx
import pytest
from openpyxl import load_workbook
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.models import Licitacion, Resultado, Revision
from analizador.models.enums import EstadoCumplimiento, EstadoLicitacion, TipoRequisito
from tests.apoyo import crear_organizacion
from tests.apoyo_analisis import Analisis, agregar_requisito, crear_analisis

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
COLUMNAS = [
    "Numeral",
    "Página",
    "Categoría",
    "Requisito",
    "Ítem verificable",
    "Estado",
    "Evidencia",
    "Evidencia verificada",
    "Faltante",
    "Revisado por una persona",
    "Revisor",
]


async def _analisis_con_matriz(sesion: AsyncSession) -> Analisis:
    """Licitación terminada con tres ítems, uno con textos que parecen fórmulas."""
    analisis = await crear_analisis(sesion)
    requisito = await agregar_requisito(
        sesion,
        analisis,
        numeral="3.1",
        texto='=HYPERLINK("http://ataque","clic")',
        items=["+cmd|' /C calc'!A0", "Ítem normal"],
        tipo=TipoRequisito.HABILITANTE,
    )
    otro = await agregar_requisito(
        sesion, analisis, numeral="3.2", texto="Experiencia en obras", items=["Experiencia"]
    )
    revisado = Resultado(
        item_id=requisito.items[0].id,
        estado=EstadoCumplimiento.NO_CUMPLE,
        faltante="-2 contratos",
        evidencia=[{"tipo": "cita", "cita": "@SUM(A1:A9)", "verificada": True}],
    )
    revisado.revisiones.append(
        Revision(
            usuario_id=analisis.quien.usuario_id,
            estado_anterior=EstadoCumplimiento.CUMPLE,
            estado_nuevo=EstadoCumplimiento.NO_CUMPLE,
            comentario="Revisado.",
        )
    )
    sesion.add_all(
        [
            revisado,
            Resultado(item_id=requisito.items[1].id, estado=EstadoCumplimiento.CUMPLE),
            Resultado(item_id=otro.items[0].id, estado=EstadoCumplimiento.CUMPLE_PARCIALMENTE),
        ]
    )
    licitacion = await sesion.get(Licitacion, analisis.licitacion_id)
    assert licitacion is not None
    licitacion.estado = EstadoLicitacion.COMPLETADA
    licitacion.recomendacion = "no_presentarse"
    licitacion.justificacion = "=1+1 Hay una brecha habilitante."
    await sesion.commit()
    return analisis


async def _exportar(cliente: httpx.AsyncClient, analisis: Analisis) -> httpx.Response:
    return await cliente.get(
        f"/api/licitaciones/{analisis.licitacion_id}/exportar", headers=analisis.quien.cabeceras
    )


# --- Criterio 1: una fila por ítem con las columnas pedidas --------------------------------


async def test_el_archivo_trae_una_fila_por_item_con_sus_columnas(
    cliente: httpx.AsyncClient, sesion: AsyncSession
) -> None:
    analisis = await _analisis_con_matriz(sesion)

    respuesta = await _exportar(cliente, analisis)

    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"] == XLSX
    assert respuesta.headers["content-disposition"] == (
        f'attachment; filename="matriz-{analisis.licitacion_id}.xlsx"'
    )
    assert respuesta.headers["cache-control"] == "no-store"
    libro = load_workbook(BytesIO(respuesta.content))
    hoja = libro["Matriz"]
    filas = list(hoja.iter_rows(values_only=True))
    assert list(filas[0]) == COLUMNAS
    assert len(filas) - 1 == 3  # [Completitud funcional] filas = ítems de la matriz
    primera = dict(zip(COLUMNAS, filas[1], strict=True))
    assert primera["Numeral"] == "3.1"
    assert primera["Página"] == 1
    assert primera["Categoría"] == "habilitante"
    assert primera["Estado"] == "No cumple"
    assert primera["Evidencia verificada"] == "Sí"
    assert primera["Revisado por una persona"] == "Sí"
    assert primera["Revisor"]
    assert dict(zip(COLUMNAS, filas[3], strict=True))["Estado"] == "Cumple parcialmente"
    resumen = {fila[0]: fila[1] for fila in libro["Resumen"].iter_rows(values_only=True)}
    assert resumen["Recomendación"] == "No presentarse"
    assert resumen["Ítems"] == 3


# --- Criterio 2: solo licitaciones de la organización --------------------------------------


async def test_solo_exporta_licitaciones_de_la_organizacion(
    cliente: httpx.AsyncClient, sesion: AsyncSession
) -> None:
    analisis = await _analisis_con_matriz(sesion)
    ajeno = await crear_organizacion(sesion, "Otra")

    respuesta = await cliente.get(
        f"/api/licitaciones/{analisis.licitacion_id}/exportar", headers=ajeno.cabeceras
    )

    assert respuesta.status_code == 404
    assert "HYPERLINK" not in respuesta.text


# --- Criterio 3: inyección de fórmulas (S-12) ----------------------------------------------


async def test_los_valores_que_parecen_formulas_se_neutralizan(
    cliente: httpx.AsyncClient, sesion: AsyncSession
) -> None:
    analisis = await _analisis_con_matriz(sesion)

    libro = load_workbook(BytesIO((await _exportar(cliente, analisis)).content))

    celdas = [c for hoja in libro.worksheets for fila in hoja.iter_rows() for c in fila]
    assert all(celda.data_type != "f" for celda in celdas)
    textos = [c.value for c in celdas if isinstance(c.value, str)]
    assert all(not texto.startswith(("=", "+", "-", "@")) for texto in textos)
    assert '\'=HYPERLINK("http://ataque","clic")' in textos
    assert "'+cmd|' /C calc'!A0" in textos
    assert "'-2 contratos" in textos
    assert "'@SUM(A1:A9)" in textos
    assert "'=1+1 Hay una brecha habilitante." in textos


@pytest.mark.parametrize(
    "estado", [EstadoLicitacion.PENDIENTE, EstadoLicitacion.PROCESANDO, EstadoLicitacion.FALLIDA]
)
async def test_solo_se_exporta_un_analisis_terminado(
    cliente: httpx.AsyncClient, sesion: AsyncSession, estado: EstadoLicitacion
) -> None:
    analisis = await _analisis_con_matriz(sesion)
    licitacion = await sesion.get(Licitacion, analisis.licitacion_id)
    assert licitacion is not None
    licitacion.estado = estado
    await sesion.commit()

    respuesta = await _exportar(cliente, analisis)

    assert respuesta.status_code == 409
