"""HU-B15: la etapa 4b guarda la ubicación de cada requisito y mide cuántas son exactas."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.integrations import almacenamiento
from analizador.models import (
    Licitacion,
    PaginaPliego,
    Requisito,
    Seccion,
    TrabajoProcesamiento,
    UbicacionPliego,
)
from analizador.models.enums import OrigenPagina, PrecisionUbicacion, TipoRequisito, TipoTrabajo
from analizador.pipeline import localizacion
from analizador.pipeline.errores import AnalisisError
from analizador.pipeline.estado import EstadoAnalisis
from tests.apoyo import AlmacenFalso, crear_organizacion
from tests.apoyo_pdf import crear_pdf

LIQUIDEZ = "El proponente deberá acreditar un índice de liquidez mayor o igual a 1,5."
POLIZA = "Póliza de cumplimiento por el veinte por ciento"
PDF = crear_pdf(
    [
        ["2. REQUISITOS HABILITANTES", LIQUIDEZ, "El plazo será de ocho meses desde el inicio."],
        [],  # página 2: escaneada, leída por OCR
        [],  # página 3: transcrita con visión, sin cajas
    ]
)
REQUISITOS = {
    "exacto": (1, LIQUIDEZ),
    "reformulado": (1, "El plazo será de ocho meses contados a partir del acta."),
    "inventado": (1, "Un requisito que no aparece en ninguna parte del pliego."),
    "escaneado": (2, POLIZA),
    "vision": (3, "Texto de una página transcrita por el modelo con visión."),
}


@pytest.fixture
async def analisis(
    sesion: AsyncSession, almacen: AlmacenFalso
) -> tuple[TrabajoProcesamiento, EstadoAnalisis]:
    quien = await crear_organizacion(sesion)
    clave = almacenamiento.nueva_clave_pliego(quien.organizacion_id)
    almacen.objetos[clave] = PDF
    licitacion = Licitacion(
        organizacion_id=quien.organizacion_id,
        creado_por=quien.usuario_id,
        titulo="Pliego",
        nombre_archivo="pliego.pdf",
        storage_key=clave,
    )
    sesion.add(licitacion)
    await sesion.flush()
    trabajo = TrabajoProcesamiento(
        organizacion_id=quien.organizacion_id,
        tipo=TipoTrabajo.ANALISIS,
        licitacion_id=licitacion.id,
    )
    sesion.add(trabajo)
    cajas = [
        {"t": palabra, "x0": 0.1 + i * 0.09, "y0": 0.4, "x1": 0.18 + i * 0.09, "y1": 0.42}
        for i, palabra in enumerate(POLIZA.split())
    ]
    sesion.add_all(
        [
            PaginaPliego(
                licitacion_id=licitacion.id, numero=1, texto="…", origen=OrigenPagina.DIGITAL
            ),
            PaginaPliego(
                licitacion_id=licitacion.id,
                numero=2,
                texto=POLIZA,
                origen=OrigenPagina.OCR,
                palabras=cajas,
            ),
            PaginaPliego(
                licitacion_id=licitacion.id, numero=3, texto="…", origen=OrigenPagina.VISION
            ),
        ]
    )
    secciones = {}
    for pagina in (1, 2, 3):
        secciones[pagina] = Seccion(
            licitacion_id=licitacion.id,
            orden=pagina,
            numeral=str(pagina),
            pagina_inicio=pagina,
            pagina_fin=pagina,
            texto="…",
            tiene_requisitos=True,
            procesada=True,
        )
    sesion.add_all(secciones.values())
    await sesion.flush()
    sesion.add_all(
        Requisito(
            licitacion_id=licitacion.id,
            seccion_id=secciones[pagina].id,
            numeral=nombre,
            tipo=TipoRequisito.HABILITANTE,
            texto_literal=texto,
        )
        for nombre, (pagina, texto) in REQUISITOS.items()
    )
    await sesion.commit()
    estado = EstadoAnalisis(
        trabajo_id=str(trabajo.id),
        licitacion_id=str(licitacion.id),
        organizacion_id=str(quien.organizacion_id),
    )
    return trabajo, estado


async def _ubicaciones(sesion: AsyncSession) -> dict[str | None, UbicacionPliego]:
    sesion.expire_all()
    filas = await sesion.execute(
        select(Requisito.numeral, UbicacionPliego).join(
            UbicacionPliego, UbicacionPliego.requisito_id == Requisito.id
        )
    )
    return {numeral: ubicacion for numeral, ubicacion in filas}


async def test_cada_requisito_queda_con_su_ubicacion_y_su_precision(
    sesion: AsyncSession, analisis: tuple[TrabajoProcesamiento, EstadoAnalisis]
) -> None:
    trabajo, estado = analisis

    await localizacion.ejecutar(estado)

    ubicaciones = await _ubicaciones(sesion)
    assert {nombre: (u.pagina, u.precision) for nombre, u in ubicaciones.items()} == {
        "exacto": (1, PrecisionUbicacion.EXACTA),
        "reformulado": (1, PrecisionUbicacion.APROXIMADA),
        "inventado": (1, PrecisionUbicacion.SOLO_PAGINA),
        "escaneado": (2, PrecisionUbicacion.EXACTA),
        "vision": (3, PrecisionUbicacion.SOLO_PAGINA),
    }
    assert ubicaciones["exacto"].zonas[0]["pagina"] == 1
    assert ubicaciones["inventado"].zonas == [] and ubicaciones["vision"].zonas == []
    # Página escaneada: las zonas salen de las cajas del OCR (criterio 4).
    assert ubicaciones["escaneado"].zonas == [
        {"pagina": 2, "x0": 0.1, "y0": 0.4, "x1": 0.81, "y1": 0.42}
    ]
    # Medida de calidad: proporción de ubicaciones exactas del pliego.
    await sesion.refresh(trabajo)
    assert trabajo.detalle == {"ubicaciones": {"exacta": 2, "aproximada": 1, "solo_pagina": 2}}


async def test_repetir_la_etapa_no_duplica_ni_recalcula_ubicaciones(
    sesion: AsyncSession,
    analisis: tuple[TrabajoProcesamiento, EstadoAnalisis],
    almacen: AlmacenFalso,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, estado = analisis
    await localizacion.ejecutar(estado)
    antes = {nombre: u.id for nombre, u in (await _ubicaciones(sesion)).items()}
    descargas: list[str] = []

    async def contar(clave: str) -> bytes:
        descargas.append(clave)
        return almacen.objetos[clave]

    monkeypatch.setattr(almacenamiento, "descargar", contar)
    await localizacion.ejecutar(estado)

    assert {nombre: u.id for nombre, u in (await _ubicaciones(sesion)).items()} == antes
    assert descargas == []  # nada pendiente: ni siquiera se descarga el PDF


async def test_solo_se_localizan_los_requisitos_nuevos(
    sesion: AsyncSession, analisis: tuple[TrabajoProcesamiento, EstadoAnalisis]
) -> None:
    """Tras un reintento de la cobertura aparecen requisitos que aún no tienen ubicación."""
    trabajo, estado = analisis
    await localizacion.ejecutar(estado)
    seccion = await sesion.scalar(select(Seccion).where(Seccion.numeral == "1"))
    assert seccion is not None
    sesion.add(
        Requisito(
            licitacion_id=uuid.UUID(estado["licitacion_id"]),
            seccion_id=seccion.id,
            numeral="nuevo",
            tipo=TipoRequisito.PLAZO,
            texto_literal="El plazo será de ocho meses desde el inicio.",
        )
    )
    await sesion.commit()

    await localizacion.ejecutar(estado)

    ubicaciones = await _ubicaciones(sesion)
    assert len(ubicaciones) == 6
    assert ubicaciones["nuevo"].precision == PrecisionUbicacion.EXACTA
    await sesion.refresh(trabajo)
    assert trabajo.detalle == {"ubicaciones": {"exacta": 3, "aproximada": 1, "solo_pagina": 2}}


async def test_si_no_se_puede_descargar_el_pdf_el_error_es_comprensible(
    analisis: tuple[TrabajoProcesamiento, EstadoAnalisis], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, estado = analisis

    async def sin_almacen(_clave: str) -> bytes:
        raise almacenamiento.AlmacenamientoError("el almacén respondió 503")

    monkeypatch.setattr(almacenamiento, "descargar", sin_almacen)

    with pytest.raises(AnalisisError) as exc:
        await localizacion.ejecutar(estado)
    assert "503" not in exc.value.mensaje_usuario
