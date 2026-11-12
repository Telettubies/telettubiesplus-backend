"""HU-B10: la etapa 1 del pipeline guarda las páginas leídas y reporta las que no pudo leer."""

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.core.config import get_settings
from analizador.integrations import almacenamiento, anthropic_client
from analizador.models import Licitacion, PaginaPliego, TrabajoProcesamiento
from analizador.models.enums import EstadoTrabajo, OrigenPagina, TipoTrabajo
from analizador.pipeline import catalogo, etapas, grafo, lectura
from analizador.pipeline.errores import AnalisisError
from analizador.pipeline.estado import EstadoAnalisis
from analizador.worker import handlers
from tests.apoyo import AlmacenFalso, Identidad, crear_organizacion, mensaje_de
from tests.apoyo_pdf import PaginaDePrueba, Tabla, crear_pdf

PLIEGO = crear_pdf(
    [
        ["1. OBJETO", "Contratar el desarrollo de un sistema de información."],
        PaginaDePrueba(
            [
                "2. CAPACIDAD FINANCIERA",
                Tabla([["Indicador", "Exigido"], ["Liquidez", "Mayor o igual a 1,5"]]),
            ]
        ),
        PaginaDePrueba(["Anexo escaneado con texto de la minuta"], escaneada=True),
        PaginaDePrueba(["Sello ilegible"], escaneada=True),
    ]
)


def _ocr_por_pagina(textos: list[tuple[str, float]]) -> Any:
    """OCR falso que responde en orden a cada página escaneada."""
    pendientes = list(textos)

    def ocr(_imagen: bytes, _settings: Any) -> tuple[str, list[dict[str, Any]], float]:
        texto, confianza = pendientes.pop(0)
        palabras = [{"t": t, "x0": 0.1, "y0": 0.1, "x1": 0.2, "y1": 0.12} for t in texto.split()]
        return texto, palabras, confianza

    return ocr


@pytest.fixture
async def analisis(
    sesion: AsyncSession, almacen: AlmacenFalso
) -> tuple[TrabajoProcesamiento, EstadoAnalisis]:
    quien: Identidad = await crear_organizacion(sesion)
    clave = almacenamiento.nueva_clave_pliego(quien.organizacion_id)
    almacen.objetos[clave] = PLIEGO
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
    await sesion.commit()
    estado = EstadoAnalisis(
        trabajo_id=str(trabajo.id),
        licitacion_id=str(licitacion.id),
        organizacion_id=str(quien.organizacion_id),
    )
    return trabajo, estado


async def _paginas(sesion: AsyncSession, estado: EstadoAnalisis) -> list[PaginaPliego]:
    sesion.expire_all()
    resultado = await sesion.scalars(
        select(PaginaPliego)
        .where(PaginaPliego.licitacion_id == uuid.UUID(estado["licitacion_id"]))
        .order_by(PaginaPliego.numero)
    )
    return list(resultado)


async def test_la_etapa_guarda_cada_pagina_con_su_origen_y_reporta_las_no_leidas(
    sesion: AsyncSession,
    analisis: tuple[TrabajoProcesamiento, EstadoAnalisis],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trabajo, estado = analisis
    monkeypatch.setattr(
        lectura,
        "_ocr",
        _ocr_por_pagina([("Anexo escaneado con texto de la minuta", 88.0), ("", 0.0)]),
    )

    await lectura.ejecutar(estado)

    paginas = await _paginas(sesion, estado)
    assert [(p.numero, p.origen) for p in paginas] == [
        (1, OrigenPagina.DIGITAL),
        (2, OrigenPagina.DIGITAL),
        (3, OrigenPagina.OCR),
        (4, OrigenPagina.NO_LEIDA),
    ]
    assert "Liquidez | Mayor o igual a 1,5" in paginas[1].texto
    assert paginas[2].palabras is not None and paginas[2].palabras[0]["t"] == "Anexo"
    assert paginas[0].palabras is None

    # El análisis informa lo que no pudo leer: no se omite en silencio.
    await sesion.refresh(trabajo)
    assert trabajo.detalle == {
        "paginas": 4,
        "paginas_ocr": 1,
        "paginas_vision": 0,
        "paginas_no_leidas": [4],
    }


async def test_repetir_la_etapa_no_duplica_paginas(
    sesion: AsyncSession,
    analisis: tuple[TrabajoProcesamiento, EstadoAnalisis],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, estado = analisis
    for _ in range(2):
        monkeypatch.setattr(lectura, "_ocr", _ocr_por_pagina([("x", 0.0), ("x", 0.0)]))
        await lectura.ejecutar(estado)

    assert [p.numero for p in await _paginas(sesion, estado)] == [1, 2, 3, 4]


async def test_el_detalle_conserva_las_cifras_de_otras_etapas(
    sesion: AsyncSession,
    analisis: tuple[TrabajoProcesamiento, EstadoAnalisis],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trabajo, estado = analisis
    trabajo.detalle = {"secciones": 12}
    await sesion.commit()
    monkeypatch.setattr(lectura, "_ocr", _ocr_por_pagina([("x", 0.0), ("x", 0.0)]))

    await lectura.ejecutar(estado)

    await sesion.refresh(trabajo)
    assert trabajo.detalle is not None
    assert trabajo.detalle["secciones"] == 12 and trabajo.detalle["paginas"] == 4


async def test_la_vision_solo_se_usa_si_hay_modelo_y_clave_configurados(
    sesion: AsyncSession,
    analisis: tuple[TrabajoProcesamiento, EstadoAnalisis],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, estado = analisis
    transcritas: list[bytes] = []

    async def transcribir(imagen: bytes) -> str | None:
        transcritas.append(imagen)
        return "Sello de la entidad contratante, legible para el modelo."

    monkeypatch.setattr(anthropic_client, "transcribir_pagina", transcribir)

    monkeypatch.setattr(lectura, "_ocr", _ocr_por_pagina([("x", 0.0), ("x", 0.0)]))
    await lectura.ejecutar(estado)  # sin ANTHROPIC_MODEL_OCR
    assert transcritas == []
    assert [p.origen for p in await _paginas(sesion, estado)][2:] == [OrigenPagina.NO_LEIDA] * 2

    monkeypatch.setenv("ANTHROPIC_MODEL_OCR", "modelo-con-vision")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "clave-de-prueba")
    get_settings.cache_clear()
    monkeypatch.setattr(lectura, "_ocr", _ocr_por_pagina([("x", 0.0), ("x", 0.0)]))
    await lectura.ejecutar(estado)

    assert len(transcritas) == 2
    assert [p.origen for p in await _paginas(sesion, estado)][2:] == [OrigenPagina.VISION] * 2


async def test_si_el_almacen_no_entrega_el_pdf_el_error_es_comprensible(
    analisis: tuple[TrabajoProcesamiento, EstadoAnalisis], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, estado = analisis

    async def sin_almacen(_clave: str) -> bytes:
        raise almacenamiento.AlmacenamientoError("el almacén respondió 503")

    monkeypatch.setattr(almacenamiento, "descargar", sin_almacen)

    with pytest.raises(AnalisisError) as exc:
        await lectura.ejecutar(estado)
    assert "503" not in exc.value.mensaje_usuario


async def test_un_pliego_demasiado_largo_falla_el_trabajo_con_el_limite_en_el_mensaje(
    sesion: AsyncSession,
    analisis: tuple[TrabajoProcesamiento, EstadoAnalisis],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Criterio 3 de HU-B07: el límite de páginas se aplica aquí, en el worker (S-09)."""
    trabajo, _ = analisis
    monkeypatch.setenv("MAX_PDF_PAGINAS", "3")
    get_settings.cache_clear()
    solo_lectura = [
        (e.clave, nodo) for e, (_, nodo) in zip(catalogo.ETAPAS, etapas.ETAPAS, strict=True)
    ][:1]

    mensaje = mensaje_de(trabajo.id)
    await handlers.procesar(mensaje, TipoTrabajo.ANALISIS, grafo.construir(etapas=solo_lectura))  # type: ignore[arg-type]

    await sesion.refresh(trabajo)
    assert trabajo.estado == EstadoTrabajo.FALLIDO
    assert trabajo.etapa_actual == "lectura_pdf"
    assert trabajo.error == "El pliego tiene 4 páginas y el máximo permitido es 3."
    assert mensaje.rechazado
