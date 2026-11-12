"""HU-B11: la etapa 2 guarda las secciones del pliego pendientes de procesar."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.models import Licitacion, PaginaPliego, Requisito, Seccion, TrabajoProcesamiento
from analizador.models.enums import OrigenPagina, TipoRequisito, TipoTrabajo
from analizador.pipeline import secciones
from analizador.pipeline.errores import AnalisisError
from analizador.pipeline.estado import EstadoAnalisis
from tests.apoyo import crear_organizacion

PAGINAS = [
    "PLIEGO DE CONDICIONES\nProceso LP-004-2026",
    "1. OBJETO\nContratar el desarrollo de un sistema.\n1.1 Alcance\nIncluye soporte.",
    "",  # página no leída
    "2. REQUISITOS HABILITANTES\nAcreditar experiencia.",
]


@pytest.fixture
async def analisis(sesion: AsyncSession) -> tuple[TrabajoProcesamiento, EstadoAnalisis]:
    quien = await crear_organizacion(sesion)
    licitacion = Licitacion(
        organizacion_id=quien.organizacion_id,
        creado_por=quien.usuario_id,
        titulo="Pliego",
        nombre_archivo="pliego.pdf",
        storage_key=f"organizaciones/{quien.organizacion_id}/pliegos/{uuid.uuid4()}.pdf",
    )
    sesion.add(licitacion)
    await sesion.flush()
    trabajo = TrabajoProcesamiento(
        organizacion_id=quien.organizacion_id,
        tipo=TipoTrabajo.ANALISIS,
        licitacion_id=licitacion.id,
    )
    sesion.add(trabajo)
    sesion.add_all(
        PaginaPliego(
            licitacion_id=licitacion.id,
            numero=numero,
            texto=texto,
            origen=OrigenPagina.DIGITAL if texto else OrigenPagina.NO_LEIDA,
        )
        for numero, texto in enumerate(PAGINAS, start=1)
    )
    await sesion.commit()
    estado = EstadoAnalisis(
        trabajo_id=str(trabajo.id),
        licitacion_id=str(licitacion.id),
        organizacion_id=str(quien.organizacion_id),
    )
    return trabajo, estado


async def _secciones(sesion: AsyncSession, estado: EstadoAnalisis) -> list[Seccion]:
    sesion.expire_all()
    resultado = await sesion.scalars(
        select(Seccion)
        .where(Seccion.licitacion_id == uuid.UUID(estado["licitacion_id"]))
        .order_by(Seccion.orden)
    )
    return list(resultado)


async def test_las_secciones_se_guardan_pendientes_de_procesar(
    sesion: AsyncSession, analisis: tuple[TrabajoProcesamiento, EstadoAnalisis]
) -> None:
    trabajo, estado = analisis

    await secciones.ejecutar(estado)

    guardadas = await _secciones(sesion, estado)
    assert [(s.numeral, s.titulo, s.pagina_inicio, s.pagina_fin) for s in guardadas] == [
        (None, "Preámbulo", 1, 1),
        ("1", "OBJETO", 2, 2),
        ("1.1", "Alcance", 2, 2),
        ("2", "REQUISITOS HABILITANTES", 4, 4),
    ]
    # Criterio 3: pendientes, sin filtrar ni procesar todavía.
    assert all(s.procesada is False and s.tiene_requisitos is None for s in guardadas)
    # Completitud: la suma del texto de las secciones equivale al texto del pliego.
    assert "".join(s.texto for s in guardadas) == "\n".join(PAGINAS)
    await sesion.refresh(trabajo)
    assert trabajo.detalle == {"secciones": 4}


async def test_repetir_la_etapa_reemplaza_las_secciones_sin_duplicarlas(
    sesion: AsyncSession, analisis: tuple[TrabajoProcesamiento, EstadoAnalisis]
) -> None:
    _, estado = analisis
    await secciones.ejecutar(estado)
    primera = (await _secciones(sesion, estado))[1]
    sesion.add(
        Requisito(
            licitacion_id=primera.licitacion_id,
            seccion_id=primera.id,
            numeral="1",
            tipo=TipoRequisito.TECNICO,
            texto_literal="requisito de una ejecución anterior",
        )
    )
    await sesion.commit()

    await secciones.ejecutar(estado)

    assert len(await _secciones(sesion, estado)) == 4
    # Lo que colgaba de las secciones anteriores desaparece con ellas.
    assert (await sesion.scalars(select(Requisito))).all() == []


async def test_un_pliego_sin_ningun_texto_legible_falla_con_un_mensaje_claro(
    sesion: AsyncSession, analisis: tuple[TrabajoProcesamiento, EstadoAnalisis]
) -> None:
    _, estado = analisis
    for pagina in await sesion.scalars(select(PaginaPliego)):
        pagina.texto = "  "
    await sesion.commit()

    with pytest.raises(AnalisisError, match="No se pudo leer texto"):
        await secciones.ejecutar(estado)
