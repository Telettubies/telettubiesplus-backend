"""HU-B20: enviar a revisión los ítems ambiguos o dudosos (RF-07, N-03, Q-14)."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from analizador.core.config import get_settings
from analizador.models import Item, Resultado, Revision, TrabajoProcesamiento
from analizador.models.enums import EstadoCumplimiento
from analizador.pipeline import citas, etapas, revision
from analizador.pipeline.revision import EstadoItem, estado_visible
from tests.apoyo_analisis import Analisis, agregar_requisito, crear_analisis

FRAGMENTO = "Contrato C-2023-014. Objeto: desarrollo de un sistema de información documental."
MOTIVO_AMBIGUO = "No define qué es un proyecto similar"


def _cita(cita: str) -> dict[str, object]:
    return {"tipo": "cita", "fuente": "C-1", "texto": FRAGMENTO, "cita": cita, "verificada": None}


@pytest.fixture
async def analisis(sesion: AsyncSession) -> Analisis:
    analisis = await crear_analisis(sesion)
    await agregar_requisito(
        sesion,
        analisis,
        numeral="claro",
        texto="Requisito claro.",
        items=["seguro", "confianza baja", "cita inventada", "numerico"],
    )
    await agregar_requisito(
        sesion,
        analisis,
        numeral="ambiguo",
        texto="Experiencia en proyectos similares.",
        items=["ambiguo uno", "ambiguo dos"],
        ambiguo=True,
        motivo_ambiguedad=MOTIVO_AMBIGUO,
    )
    casos: dict[str, tuple[float | None, list[dict[str, object]]]] = {
        "seguro": (0.95, [_cita("desarrollo de un sistema de información")]),
        "confianza baja": (0.55, [_cita("desarrollo de un sistema de información")]),
        "cita inventada": (0.95, [_cita("veinte años de experiencia certificada en banca")]),
        "numerico": (None, [{"tipo": "dato", "texto": "Liquidez: 2,35", "verificada": True}]),
        "ambiguo uno": (0.95, [_cita("sistema de información documental")]),
        "ambiguo dos": (0.4, [_cita("sistema de información documental")]),
    }
    for item in await sesion.scalars(select(Item)):
        confianza, evidencia = casos[item.descripcion]
        sesion.add(
            Resultado(
                item_id=item.id,
                estado=EstadoCumplimiento.CUMPLE,
                evidencia=evidencia,
                confianza=confianza,
            )
        )
    await sesion.commit()
    return analisis


async def _resultados(sesion: AsyncSession) -> dict[str, Resultado]:
    sesion.expire_all()
    filas = await sesion.scalars(select(Item).options(selectinload(Item.resultado)))
    return {item.descripcion: item.resultado for item in filas if item.resultado is not None}


async def _etapa_completa(analisis: Analisis) -> None:
    """Como en el pipeline: primero se validan las citas y después se decide la revisión."""
    await citas.ejecutar(analisis.estado)
    await revision.ejecutar(analisis.estado)


# --- Criterio 1: ambiguo, cita no verificada o confianza baja → revisión ---------------------


async def test_pasan_a_revision_los_items_ambiguos_con_cita_sin_verificar_o_poca_confianza(
    sesion: AsyncSession, analisis: Analisis
) -> None:
    await _etapa_completa(analisis)

    resultados = await _resultados(sesion)
    assert {d: r.requiere_revision for d, r in resultados.items()} == {
        "seguro": False,
        "confianza baja": True,
        "cita inventada": True,
        "numerico": False,  # sin confianza de modelo: no aplica el umbral
        "ambiguo uno": True,
        "ambiguo dos": True,
    }


@pytest.mark.parametrize(
    ("confianza", "en_revision"),
    [(0.69, True), (0.7, False), (0.71, False)],  # justo por debajo, igual y por encima del mínimo
)
async def test_el_umbral_de_confianza_en_sus_limites(
    sesion: AsyncSession, analisis: Analisis, confianza: float, en_revision: bool
) -> None:
    resultado = (await _resultados(sesion))["seguro"]
    resultado.confianza = confianza
    await sesion.commit()

    await _etapa_completa(analisis)

    assert (await _resultados(sesion))["seguro"].requiere_revision is en_revision


async def test_el_umbral_de_confianza_es_configurable(
    sesion: AsyncSession, analisis: Analisis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REVISION_CONFIANZA_MINIMA", "0.5")
    get_settings.cache_clear()

    await _etapa_completa(analisis)

    assert not (await _resultados(sesion))["confianza baja"].requiere_revision


# --- Criterio 2: cada ítem en revisión indica el motivo -------------------------------------


async def test_cada_item_en_revision_indica_su_motivo(
    sesion: AsyncSession, analisis: Analisis
) -> None:
    await _etapa_completa(analisis)

    resultados = await _resultados(sesion)
    assert resultados["confianza baja"].motivo_revision == (
        "La confianza de la verificación (55%) es menor que el mínimo exigido (70%)."
    )
    assert resultados["cita inventada"].motivo_revision == citas.MOTIVO_CITA_NO_VERIFICADA
    assert resultados["ambiguo uno"].motivo_revision == (
        f"El requisito es ambiguo: {MOTIVO_AMBIGUO}."
    )
    for resultado in resultados.values():
        assert bool(resultado.motivo_revision) == resultado.requiere_revision


async def test_un_item_con_varios_motivos_los_indica_todos(
    sesion: AsyncSession, analisis: Analisis
) -> None:
    await _etapa_completa(analisis)

    motivos = ((await _resultados(sesion))["ambiguo dos"].motivo_revision or "").split(" | ")
    assert motivos == [
        f"El requisito es ambiguo: {MOTIVO_AMBIGUO}.",
        "La confianza de la verificación (40%) es menor que el mínimo exigido (70%).",
    ]


async def test_un_item_que_la_verificacion_no_pudo_decidir_conserva_su_motivo(
    sesion: AsyncSession, analisis: Analisis
) -> None:
    resultado = (await _resultados(sesion))["numerico"]
    resultado.requiere_revision = True
    resultado.motivo_revision = "Falta configurar el valor del SMMLV para convertir lo exigido."
    await sesion.commit()

    await _etapa_completa(analisis)

    numerico = (await _resultados(sesion))["numerico"]
    assert numerico.requiere_revision
    assert (
        numerico.motivo_revision == "Falta configurar el valor del SMMLV para convertir lo exigido."
    )


# --- Criterio 3: un ítem en revisión no cuenta como «cumple» --------------------------------


async def test_un_item_en_revision_no_se_presenta_como_cumple(
    sesion: AsyncSession, analisis: Analisis
) -> None:
    await _etapa_completa(analisis)

    visibles = {
        d: estado_visible(r.estado, r.requiere_revision)
        for d, r in (await _resultados(sesion)).items()
    }
    # Todos tenían «cumple» como estado propuesto.
    assert visibles == {
        "seguro": EstadoItem.CUMPLE,
        "confianza baja": EstadoItem.REVISION,
        "cita inventada": EstadoItem.REVISION,
        "numerico": EstadoItem.CUMPLE,
        "ambiguo uno": EstadoItem.REVISION,
        "ambiguo dos": EstadoItem.REVISION,
    }


def test_estado_visible() -> None:
    assert estado_visible(EstadoCumplimiento.NO_CUMPLE, False) == EstadoItem.NO_CUMPLE
    assert estado_visible(EstadoCumplimiento.CUMPLE_PARCIALMENTE, False) == "cumple_parcialmente"
    assert estado_visible(EstadoCumplimiento.NO_CUMPLE, True) == EstadoItem.REVISION


# --- Registro, idempotencia y revisión humana previa ----------------------------------------


async def test_la_etapa_del_pipeline_valida_las_citas_y_despues_aplica_la_revision(
    sesion: AsyncSession, analisis: Analisis
) -> None:
    await etapas.validar_citas(analisis.estado)

    resultados = await _resultados(sesion)
    assert resultados["cita inventada"].requiere_revision
    assert resultados["confianza baja"].requiere_revision
    trabajo = await sesion.get(TrabajoProcesamiento, analisis.trabajo_id)
    assert trabajo is not None and trabajo.detalle is not None
    assert trabajo.detalle["revision"] == {"items": 6, "en_revision": 4}
    assert trabajo.etapa_actual == "validacion_citas"


async def test_repetir_no_duplica_los_motivos(sesion: AsyncSession, analisis: Analisis) -> None:
    await _etapa_completa(analisis)
    antes = {d: r.motivo_revision for d, r in (await _resultados(sesion)).items()}

    await _etapa_completa(analisis)

    assert {d: r.motivo_revision for d, r in (await _resultados(sesion)).items()} == antes


async def test_un_item_ya_revisado_por_una_persona_no_vuelve_a_revision(
    sesion: AsyncSession, analisis: Analisis
) -> None:
    resultado = (await _resultados(sesion))["ambiguo uno"]
    sesion.add(
        Revision(
            resultado_id=resultado.id,
            usuario_id=analisis.quien.usuario_id,
            estado_anterior=EstadoCumplimiento.CUMPLE,
            estado_nuevo=EstadoCumplimiento.CUMPLE,
            comentario="Confirmado con el área técnica.",
        )
    )
    await sesion.commit()

    await _etapa_completa(analisis)

    assert not (await _resultados(sesion))["ambiguo uno"].requiere_revision
