"""HU-B18: la etapa 8 marca cada evidencia como verificada o no y envía a revisión."""

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from analizador.core.config import get_settings
from analizador.models import Item, Requisito, Resultado, TrabajoProcesamiento
from analizador.models.enums import EstadoCumplimiento
from analizador.pipeline import citas
from tests.apoyo_analisis import Analisis, agregar_requisito, crear_analisis

SECCION = (
    "2. REQUISITOS\nEl proponente deberá acreditar experiencia en desarrollo de software "
    "y contar con certificación ISO 9001 vigente.\nEl índice de liquidez será mayor o igual a 1,5."
)
FRAGMENTO = "Contrato C-2023-014. Objeto: desarrollo de un sistema de información documental."


def _cita(cita: str, texto: str = FRAGMENTO) -> dict[str, Any]:
    return {
        "tipo": "cita",
        "fragmento_id": "f1",
        "fuente": "C-2023-014",
        "texto": texto,
        "cita": cita,
        "verificada": None,
    }


DATO = {"tipo": "dato", "fuente": "Indicadores 2025", "texto": "Índice: 2,35", "verificada": True}


@pytest.fixture
async def analisis(sesion: AsyncSession) -> Analisis:
    analisis = await crear_analisis(sesion, texto_seccion=SECCION)
    await agregar_requisito(
        sesion,
        analisis,
        numeral="literal",
        texto="El proponente deberá acreditar experiencia en desarrollo de software",
        items=["cita real", "cita inventada", "sin cita"],
    )
    await agregar_requisito(
        sesion,
        analisis,
        numeral="reformulado",
        texto="La empresa oferente tendrá que demostrar trayectoria construyendo aplicaciones",
        items=[("liquidez", {"campo": "indice_liquidez", "operador": ">=", "valor": 1.5})],
    )
    resultados = {
        "cita real": (
            EstadoCumplimiento.CUMPLE,
            [_cita("desarrollo de un sistema de información")],
        ),
        "cita inventada": (
            EstadoCumplimiento.CUMPLE,
            [_cita("la empresa tiene veinte años de experiencia certificada")],
        ),
        "sin cita": (EstadoCumplimiento.NO_CUMPLE, []),
        "liquidez": (EstadoCumplimiento.CUMPLE, [DATO]),
    }
    for item in await sesion.scalars(select(Item)):
        estado, evidencia = resultados[item.descripcion]
        sesion.add(
            Resultado(
                item_id=item.id,
                estado=estado,
                evidencia=evidencia,
                faltante=None if estado == EstadoCumplimiento.CUMPLE else "Falta evidencia.",
                confianza=0.9,
            )
        )
    await sesion.commit()
    return analisis


async def _resultados(sesion: AsyncSession) -> dict[str, Resultado]:
    sesion.expire_all()
    filas = await sesion.scalars(select(Item).options(selectinload(Item.resultado)))
    return {item.descripcion: item.resultado for item in filas if item.resultado is not None}


# --- Criterios 1 y 4: cada cita se compara con su fragmento y queda marcada -----------------


async def test_cada_evidencia_indica_si_fue_verificada(
    sesion: AsyncSession, analisis: Analisis
) -> None:
    await citas.ejecutar(analisis.estado)

    resultados = await _resultados(sesion)
    real = resultados["cita real"].evidencia[0]
    assert real["verificada"] is True and real["similitud"] == 100.0
    inventada = resultados["cita inventada"].evidencia[0]
    assert inventada["verificada"] is False and inventada["similitud"] < 90
    # Un dato estructurado ya estaba verificado y no se toca.
    assert resultados["liquidez"].evidencia == [DATO]


# --- Criterio 2: por debajo del umbral, el ítem pasa a revisión -----------------------------


async def test_una_cita_no_verificada_envia_el_item_a_revision(
    sesion: AsyncSession, analisis: Analisis
) -> None:
    await citas.ejecutar(analisis.estado)

    resultados = await _resultados(sesion)
    inventada = resultados["cita inventada"]
    assert inventada.requiere_revision is True
    assert inventada.motivo_revision == citas.MOTIVO_CITA_NO_VERIFICADA
    # Los demás no: ni la cita real, ni el dato, ni el ítem sin cita.
    assert not resultados["cita real"].requiere_revision
    assert not resultados["liquidez"].requiere_revision
    assert not resultados["sin cita"].requiere_revision


async def test_ninguna_cita_no_verificada_sostiene_un_cumple(
    sesion: AsyncSession, analisis: Analisis
) -> None:
    await citas.ejecutar(analisis.estado)

    for resultado in (await _resultados(sesion)).values():
        no_verificada = any(e.get("verificada") is False for e in resultado.evidencia)
        if no_verificada:
            assert resultado.requiere_revision, (
                "un cumple no puede apoyarse en una cita sin verificar"
            )


async def test_el_umbral_es_configurable(
    sesion: AsyncSession, analisis: Analisis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CITAS_UMBRAL_SIMILITUD", "20")
    get_settings.cache_clear()

    await citas.ejecutar(analisis.estado)

    assert not (await _resultados(sesion))["cita inventada"].requiere_revision


async def test_el_motivo_se_suma_al_que_ya_tuviera_el_item(
    sesion: AsyncSession, analisis: Analisis
) -> None:
    resultado = (await _resultados(sesion))["cita inventada"]
    resultado.requiere_revision = True
    resultado.motivo_revision = "Motivo anterior de la verificación."
    await sesion.commit()

    await citas.ejecutar(analisis.estado)

    resultado = (await _resultados(sesion))["cita inventada"]
    assert resultado.motivo_revision == (
        f"Motivo anterior de la verificación. | {citas.MOTIVO_CITA_NO_VERIFICADA}"
    )


# --- Criterio 3: el texto literal del requisito se compara con su sección -------------------


async def test_el_texto_literal_de_cada_requisito_se_compara_con_su_seccion(
    sesion: AsyncSession, analisis: Analisis
) -> None:
    await citas.ejecutar(analisis.estado)

    sesion.expire_all()
    requisitos = {r.numeral: r for r in await sesion.scalars(select(Requisito))}
    assert requisitos["literal"].texto_verificado is True
    # Posible reformulación del modelo: queda señalada.
    assert requisitos["reformulado"].texto_verificado is False


# --- Registro e idempotencia ----------------------------------------------------------------


async def test_el_trabajo_registra_cuantas_citas_y_literales_se_verificaron(
    sesion: AsyncSession, analisis: Analisis
) -> None:
    await citas.ejecutar(analisis.estado)

    trabajo = await sesion.get(TrabajoProcesamiento, analisis.trabajo_id)
    assert trabajo is not None
    assert trabajo.detalle == {
        "validacion": {
            "citas_verificadas": 1,
            "citas_no_verificadas": 1,
            "literales_verificados": 1,
            "literales_no_verificados": 1,
        }
    }


async def test_repetir_la_etapa_no_cambia_nada(sesion: AsyncSession, analisis: Analisis) -> None:
    await citas.ejecutar(analisis.estado)
    antes = {d: (r.evidencia, r.motivo_revision) for d, r in (await _resultados(sesion)).items()}

    await citas.ejecutar(analisis.estado)

    despues = {d: (r.evidencia, r.motivo_revision) for d, r in (await _resultados(sesion)).items()}
    assert despues == antes
