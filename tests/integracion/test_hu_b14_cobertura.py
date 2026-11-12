"""HU-B14: comprobar la cobertura del pliego (ADR-012, N-04, R-03)."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.contratos.extraccion import ExtraccionSeccion, ItemExtraido, RequisitoExtraido
from analizador.core.config import get_settings
from analizador.integrations import anthropic_client
from analizador.models import Licitacion, Requisito, Seccion, TrabajoProcesamiento
from analizador.models.enums import TipoRequisito, TipoTrabajo, TipoVerificacion
from analizador.pipeline import cobertura, extraccion
from analizador.pipeline.estado import EstadoAnalisis
from tests.apoyo import ExtractorFalso, crear_organizacion


def _respuesta(numeral: str) -> ExtraccionSeccion:
    return ExtraccionSeccion(
        requisitos=[
            RequisitoExtraido(
                numeral=numeral,
                tipo=TipoRequisito.TECNICO,
                texto_literal=f"Requisito del numeral {numeral}.",
                ambiguo=False,
                items=[
                    ItemExtraido(
                        descripcion=f"Condición del numeral {numeral}",
                        tipo_verificacion=TipoVerificacion.CUALITATIVO,
                    )
                ],
            )
        ]
    )


FALLO = anthropic_client.RespuestaRechazadaError("La salida no cumple el esquema")


@pytest.fixture
def extractor(base_de_datos: None, monkeypatch: pytest.MonkeyPatch) -> ExtractorFalso:
    monkeypatch.setenv("ANTHROPIC_MODEL_EXTRACCION", "modelo-de-extraccion")
    monkeypatch.setenv("COBERTURA_MAX_REINTENTOS", "2")
    get_settings.cache_clear()
    falso = ExtractorFalso(
        respuestas={
            f"Texto de la sección {n}": [_respuesta(n)] for n in ("2.1", "2.2", "2.3", "9.9")
        }
    )
    monkeypatch.setattr(anthropic_client, "generar_estructurado", falso)
    return falso


@pytest.fixture
async def analisis(sesion: AsyncSession) -> tuple[TrabajoProcesamiento, EstadoAnalisis]:
    """Pliego de cinco secciones: una descartada por el filtro y cuatro relevantes."""
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
    for orden, (numeral, relevante) in enumerate(
        [("1.1", False), ("2.1", True), ("2.2", True), ("2.3", True), ("9.9", None)], start=1
    ):
        sesion.add(
            Seccion(
                licitacion_id=licitacion.id,
                orden=orden,
                numeral=numeral,
                titulo=f"Sección {numeral}",
                pagina_inicio=orden,
                pagina_fin=orden + 1,
                texto=f"Texto de la sección {numeral}",
                tiene_requisitos=relevante,
                procesada=relevante is False,
            )
        )
    await sesion.commit()
    estado = EstadoAnalisis(
        trabajo_id=str(trabajo.id),
        licitacion_id=str(licitacion.id),
        organizacion_id=str(quien.organizacion_id),
    )
    return trabajo, estado


async def _secciones(sesion: AsyncSession) -> dict[str | None, Seccion]:
    sesion.expire_all()
    return {s.numeral: s for s in await sesion.scalars(select(Seccion))}


async def _cobertura(sesion: AsyncSession, estado: EstadoAnalisis) -> cobertura.Cobertura:
    sesion.expire_all()
    return await cobertura.calcular(sesion, uuid.UUID(estado["licitacion_id"]))


# --- Criterios 1 y 4: secciones procesadas sobre el total -----------------------------------


async def test_con_todo_procesado_la_cobertura_es_completa(
    sesion: AsyncSession,
    analisis: tuple[TrabajoProcesamiento, EstadoAnalisis],
    extractor: ExtractorFalso,
) -> None:
    trabajo, estado = analisis
    await extraccion.ejecutar(estado)

    await cobertura.ejecutar(estado)

    resultado = await _cobertura(sesion, estado)
    assert (resultado.procesadas, resultado.total) == (5, 5)
    assert resultado.completa and resultado.no_procesadas == ()
    # Las descartadas por el filtro cuentan como procesadas, y se sabe cuántas son.
    assert resultado.descartadas == 1
    await sesion.refresh(trabajo)
    assert trabajo.detalle is not None
    assert trabajo.detalle["cobertura"] == {
        "procesadas": 5,
        "total": 5,
        "descartadas": 1,
        "reintentos": 1,  # la sección 9.9 no había pasado por el filtro
        "no_procesadas": [],
    }


async def test_una_seccion_que_no_paso_por_el_filtro_no_se_descarta_va_a_extraccion(
    sesion: AsyncSession,
    analisis: tuple[TrabajoProcesamiento, EstadoAnalisis],
    extractor: ExtractorFalso,
) -> None:
    _, estado = analisis
    await extraccion.ejecutar(estado)
    assert extractor.enviadas("sección 9.9") == 0  # la extracción solo toma las ya filtradas

    await cobertura.ejecutar(estado)

    seccion = (await _secciones(sesion))["9.9"]
    assert seccion.tiene_requisitos is True and seccion.procesada is True
    assert extractor.enviadas("sección 9.9") == 1
    assert await sesion.scalar(select(Requisito).where(Requisito.numeral == "9.9")) is not None


# --- Criterio 2: las secciones en error o pendientes se reintentan hasta un máximo -----------


async def test_las_secciones_en_error_se_reintentan_y_se_recuperan(
    sesion: AsyncSession,
    analisis: tuple[TrabajoProcesamiento, EstadoAnalisis],
    extractor: ExtractorFalso,
) -> None:
    _, estado = analisis
    # En la extracción falla el intento y su reintento; en la comprobación ya responde bien.
    extractor.respuestas["Texto de la sección 2.2"] = [FALLO, FALLO, _respuesta("2.2")]
    await extraccion.ejecutar(estado)
    assert (await _secciones(sesion))["2.2"].error is not None

    await cobertura.ejecutar(estado)

    seccion = (await _secciones(sesion))["2.2"]
    assert seccion.procesada is True and seccion.error is None
    assert (await _cobertura(sesion, estado)).completa
    # Solo se reenvía lo que faltaba: las secciones ya procesadas no se pagan otra vez.
    assert extractor.enviadas("sección 2.1") == 1 and extractor.enviadas("sección 2.3") == 1


async def test_los_reintentos_tienen_un_maximo_configurable(
    sesion: AsyncSession,
    analisis: tuple[TrabajoProcesamiento, EstadoAnalisis],
    extractor: ExtractorFalso,
) -> None:
    trabajo, estado = analisis
    extractor.respuestas["Texto de la sección 2.2"] = [FALLO]  # falla siempre
    await extraccion.ejecutar(estado)
    enviadas_antes = extractor.enviadas("sección 2.2")

    await cobertura.ejecutar(estado)

    # Dos rondas de reintento (COBERTURA_MAX_REINTENTOS), cada una con su intento y su reintento.
    assert extractor.enviadas("sección 2.2") - enviadas_antes == 4
    await sesion.refresh(trabajo)
    assert trabajo.detalle is not None and trabajo.detalle["cobertura"]["reintentos"] == 2


async def test_con_cero_reintentos_configurados_no_se_reenvia_nada(
    sesion: AsyncSession,
    analisis: tuple[TrabajoProcesamiento, EstadoAnalisis],
    extractor: ExtractorFalso,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, estado = analisis
    monkeypatch.setenv("COBERTURA_MAX_REINTENTOS", "0")
    get_settings.cache_clear()
    extractor.respuestas["Texto de la sección 2.2"] = [FALLO]
    await extraccion.ejecutar(estado)
    antes = len(extractor.llamadas)

    await cobertura.ejecutar(estado)

    assert len(extractor.llamadas) == antes
    assert not (await _cobertura(sesion, estado)).completa


# --- Criterio 3: lo que quede sin procesar se muestra con su numeral ------------------------


async def test_las_secciones_sin_procesar_quedan_identificadas_con_su_numeral(
    sesion: AsyncSession,
    analisis: tuple[TrabajoProcesamiento, EstadoAnalisis],
    extractor: ExtractorFalso,
) -> None:
    trabajo, estado = analisis
    extractor.respuestas["Texto de la sección 2.2"] = [FALLO]
    extractor.respuestas["Texto de la sección 2.3"] = [ConnectionError("sin red")]
    await extraccion.ejecutar(estado)

    await cobertura.ejecutar(estado)  # no falla: deja constancia

    resultado = await _cobertura(sesion, estado)
    assert (resultado.procesadas, resultado.total) == (3, 5)
    assert not resultado.completa
    assert [
        (s.numeral, s.titulo, s.pagina_inicio, s.pagina_fin) for s in resultado.no_procesadas
    ] == [
        ("2.2", "Sección 2.2", 3, 4),
        ("2.3", "Sección 2.3", 4, 5),
    ]
    assert resultado.no_procesadas[0].motivo == extraccion.ERROR_CONTRATO
    assert resultado.no_procesadas[1].motivo == extraccion.ERROR_PROVEEDOR
    # Nunca ocultas: también quedan en el detalle del trabajo.
    await sesion.refresh(trabajo)
    assert trabajo.detalle is not None
    assert trabajo.detalle["cobertura"]["no_procesadas"] == ["2.2", "2.3"]
    assert (trabajo.detalle["cobertura"]["procesadas"], trabajo.detalle["cobertura"]["total"]) == (
        3,
        5,
    )


async def test_un_pliego_sin_secciones_no_tiene_cobertura_completa(
    sesion: AsyncSession, analisis: tuple[TrabajoProcesamiento, EstadoAnalisis]
) -> None:
    _, estado = analisis
    for seccion in await sesion.scalars(select(Seccion)):
        await sesion.delete(seccion)
    await sesion.commit()

    resultado = await _cobertura(sesion, estado)

    assert resultado.total == 0 and not resultado.completa


async def test_la_cobertura_se_calcula_sin_llamar_a_ningun_modelo(
    sesion: AsyncSession,
    analisis: tuple[TrabajoProcesamiento, EstadoAnalisis],
    extractor: ExtractorFalso,
) -> None:
    _, estado = analisis

    resultado = await _cobertura(sesion, estado)

    assert extractor.llamadas == []
    # Antes de extraer: solo la descartada está procesada; la que no se filtró lo dice.
    assert (resultado.procesadas, resultado.total, resultado.descartadas) == (1, 5, 1)
    assert {s.numeral: s.motivo for s in resultado.no_procesadas}[
        "9.9"
    ] == cobertura.MOTIVO_SIN_FILTRAR
    assert {s.numeral: s.motivo for s in resultado.no_procesadas}[
        "2.1"
    ] == cobertura.MOTIVO_SIN_EXTRAER
