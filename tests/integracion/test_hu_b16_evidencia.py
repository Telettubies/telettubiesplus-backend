"""HU-B16: recuperar evidencia de la base de conocimiento (RF-03, ADR-013, P-11).

pgvector es real; los embeddings de consulta y el reranker de NVIDIA se sustituyen por dobles.
Los vectores son de tres dimensiones para controlar las distancias a mano.
"""

from dataclasses import dataclass, field

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.core.config import get_settings
from analizador.integrations import nvidia
from analizador.models import Item, TrabajoProcesamiento
from analizador.pipeline import evidencia
from analizador.pipeline.errores import AnalisisError
from tests.apoyo import crear_organizacion
from tests.apoyo_analisis import (
    MODELO_EMBEDDINGS,
    Analisis,
    agregar_fragmento,
    agregar_requisito,
    crear_analisis,
)

ITEM_SOFTWARE = "Experiencia en desarrollo de software para entidades públicas"
ITEM_CALIDAD = "Certificación vigente del sistema de gestión de calidad"
ITEM_LIQUIDEZ = "Índice de liquidez mayor o igual a 1,5"

# Consulta de software → [1, 0, 0]; de calidad → [0, 1, 0].
CONSULTAS = {ITEM_SOFTWARE: [1.0, 0.0, 0.0], ITEM_CALIDAD: [0.0, 1.0, 0.0]}


@dataclass
class ConsultasFalsas:
    """Sustituye a `nvidia.embeddings` para las consultas."""

    lotes: list[list[str]] = field(default_factory=list)
    tipos: list[str] = field(default_factory=list)
    fallar_desde_el_lote: int | None = None

    async def __call__(self, textos: list[str], tipo: str) -> list[list[float]]:
        if self.fallar_desde_el_lote is not None and len(self.lotes) >= self.fallar_desde_el_lote:
            raise nvidia.NvidiaNoDisponibleError("sin respuesta")
        self.lotes.append(list(textos))
        self.tipos.append(tipo)
        return [CONSULTAS.get(texto, [0.0, 0.0, 1.0]) for texto in textos]


@dataclass
class RerankerFalso:
    """Sustituye a `nvidia.reordenar`: prefiere los pasajes que contienen `preferido`."""

    preferido: str = ""
    error: Exception | None = None
    llamadas: list[tuple[str, list[str]]] = field(default_factory=list)

    async def __call__(self, consulta: str, pasajes: list[str]) -> list[tuple[int, float]]:
        self.llamadas.append((consulta, list(pasajes)))
        if self.error is not None:
            raise self.error
        puntajes = [(i, 5.0 if self.preferido in p else -float(i)) for i, p in enumerate(pasajes)]
        return sorted(puntajes, key=lambda par: par[1], reverse=True)


@pytest.fixture
def consultas(base_de_datos: None, monkeypatch: pytest.MonkeyPatch) -> ConsultasFalsas:
    monkeypatch.setenv("NVIDIA_MODEL_EMBEDDINGS", MODELO_EMBEDDINGS)
    monkeypatch.setenv("EVIDENCIA_MAX", "3")
    get_settings.cache_clear()
    falsas = ConsultasFalsas()
    monkeypatch.setattr(nvidia, "embeddings", falsas)
    return falsas


@pytest.fixture
def reranker(monkeypatch: pytest.MonkeyPatch) -> RerankerFalso:
    falso = RerankerFalso()
    monkeypatch.setattr(nvidia, "reordenar", falso)
    return falso


@pytest.fixture
async def analisis(sesion: AsyncSession) -> Analisis:
    """Dos ítems cualitativos y uno numérico, con una base de seis fragmentos indexados."""
    analisis = await crear_analisis(sesion)
    await agregar_requisito(
        sesion,
        analisis,
        numeral="2.1",
        texto="Requisitos de experiencia, calidad y liquidez.",
        items=[
            ITEM_SOFTWARE,
            ITEM_CALIDAD,
            (ITEM_LIQUIDEZ, {"campo": "indice_liquidez", "operador": ">=", "valor": 1.5}),
        ],
    )
    quien = analisis.quien
    # Distancia coseno a la consulta de software [1, 0, 0]: 0, 0.2, 0.4, ~0.9 y 2.
    await agregar_fragmento(
        sesion, quien, "Contrato de software A", [1.0, 0.0, 0.0], titulo="C-001"
    )
    await agregar_fragmento(
        sesion, quien, "Contrato de software B", [0.8, 0.6, 0.0], titulo="C-002"
    )
    await agregar_fragmento(
        sesion, quien, "Contrato de software C", [0.6, 0.8, 0.0], titulo="C-003"
    )
    await agregar_fragmento(sesion, quien, "Mesa de ayuda", [0.1, 0.995, 0.0], titulo="C-004")
    await agregar_fragmento(sesion, quien, "Servicio de aseo", [-1.0, 0.0, 0.0], titulo="C-005")
    await agregar_fragmento(sesion, quien, "Certificación ISO 9001", [0.0, 1.0, 0.0], titulo="ISO")
    return analisis


async def _items(sesion: AsyncSession) -> dict[str, Item]:
    sesion.expire_all()
    return {item.descripcion: item for item in await sesion.scalars(select(Item))}


def _textos(item: Item) -> list[str]:
    return [candidato["texto"] for candidato in item.candidatos or []]


# --- Criterio 1: la descripción se vectoriza como consulta y se recuperan los más cercanos ----


async def test_cada_item_cualitativo_recibe_los_fragmentos_mas_cercanos(
    sesion: AsyncSession, analisis: Analisis, consultas: ConsultasFalsas
) -> None:
    await evidencia.ejecutar(analisis.estado)

    items = await _items(sesion)
    assert _textos(items[ITEM_SOFTWARE]) == [
        "Contrato de software A",
        "Contrato de software B",
        "Contrato de software C",
    ]
    assert _textos(items[ITEM_CALIDAD])[0] == "Certificación ISO 9001"
    primero = (items[ITEM_SOFTWARE].candidatos or [])[0]
    assert primero["fuente"] == "C-001" and primero["tipo_fuente"] == "contrato"
    assert primero["distancia"] == 0.0 and primero["puntaje"] is None
    assert primero["fragmento_id"] and primero["fuente_id"]
    assert [c["distancia"] for c in items[ITEM_SOFTWARE].candidatos or []] == [0.0, 0.2, 0.4]
    # Las descripciones se vectorizan como consulta, en un solo lote.
    assert consultas.lotes == [[ITEM_SOFTWARE, ITEM_CALIDAD]] and consultas.tipos == ["query"]


async def test_los_items_numericos_no_buscan_evidencia(
    sesion: AsyncSession, analisis: Analisis, consultas: ConsultasFalsas
) -> None:
    await evidencia.ejecutar(analisis.estado)

    assert (await _items(sesion))[ITEM_LIQUIDEZ].candidatos is None
    assert ITEM_LIQUIDEZ not in consultas.lotes[0]


async def test_la_cantidad_de_fragmentos_es_configurable(
    sesion: AsyncSession,
    analisis: Analisis,
    consultas: ConsultasFalsas,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EVIDENCIA_MAX", "1")
    get_settings.cache_clear()

    await evidencia.ejecutar(analisis.estado)

    assert _textos((await _items(sesion))[ITEM_SOFTWARE]) == ["Contrato de software A"]


# --- Criterio 2: reranker activo reordena; inactivo, los primeros de la búsqueda -------------


async def test_con_el_reranker_activo_se_reordenan_los_candidatos_y_se_conservan_los_mejores(
    sesion: AsyncSession,
    analisis: Analisis,
    consultas: ConsultasFalsas,
    reranker: RerankerFalso,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RERANK_ACTIVO", "true")
    get_settings.cache_clear()
    reranker.preferido = "Mesa de ayuda"  # cuarto en la búsqueda: fuera sin el reranker

    await evidencia.ejecutar(analisis.estado)

    software = (await _items(sesion))[ITEM_SOFTWARE]
    assert _textos(software) == [
        "Mesa de ayuda",
        "Contrato de software A",
        "Contrato de software B",
    ]
    assert (software.candidatos or [])[0]["puntaje"] == 5.0
    # El reranker recibe la consulta y todos los candidatos relevantes de la búsqueda.
    consulta, pasajes = reranker.llamadas[0]
    assert consulta == ITEM_SOFTWARE and len(pasajes) == 5


async def test_con_el_reranker_inactivo_no_se_llama_y_se_usa_el_orden_de_la_busqueda(
    sesion: AsyncSession, analisis: Analisis, consultas: ConsultasFalsas, reranker: RerankerFalso
) -> None:
    await evidencia.ejecutar(analisis.estado)  # RERANK_ACTIVO es false por defecto

    assert reranker.llamadas == []
    assert _textos((await _items(sesion))[ITEM_SOFTWARE])[0] == "Contrato de software A"


async def test_si_el_reranker_falla_se_conserva_el_orden_de_la_busqueda(
    sesion: AsyncSession,
    analisis: Analisis,
    consultas: ConsultasFalsas,
    reranker: RerankerFalso,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RERANK_ACTIVO", "true")
    get_settings.cache_clear()
    reranker.error = nvidia.NvidiaNoDisponibleError("sin respuesta")

    await evidencia.ejecutar(analisis.estado)  # no falla la etapa

    assert _textos((await _items(sesion))[ITEM_SOFTWARE]) == [
        "Contrato de software A",
        "Contrato de software B",
        "Contrato de software C",
    ]


# --- Criterio 3: nunca fragmentos de otra organización ---------------------------------------


async def test_la_busqueda_nunca_devuelve_fragmentos_de_otra_organizacion(
    sesion: AsyncSession, analisis: Analisis, consultas: ConsultasFalsas
) -> None:
    otra = await crear_organizacion(sesion, "Otra organización")
    # Idéntico a la consulta: sería el primero si no se filtrara por organización.
    ajeno = await agregar_fragmento(sesion, otra, "Contrato AJENO", [1.0, 0.0, 0.0], titulo="X")
    ajeno_id = str(ajeno.id)

    await evidencia.ejecutar(analisis.estado)

    for item in (await _items(sesion)).values():
        for candidato in item.candidatos or []:
            assert candidato["fragmento_id"] != ajeno_id
            assert "AJENO" not in candidato["texto"]


async def test_cada_organizacion_recibe_solo_su_propia_evidencia(
    sesion: AsyncSession, analisis: Analisis, consultas: ConsultasFalsas
) -> None:
    otra = await crear_organizacion(sesion, "Otra organización")
    await agregar_fragmento(sesion, otra, "Contrato de la otra", [1.0, 0.0, 0.0], titulo="X")
    analisis_otra = await crear_analisis(sesion, otra)
    await agregar_requisito(
        sesion, analisis_otra, numeral="9.1", texto="Experiencia.", items=[ITEM_SOFTWARE]
    )

    await evidencia.ejecutar(analisis_otra.estado)

    resultado = await sesion.scalars(
        select(Item).where(Item.requisito.has(licitacion_id=analisis_otra.licitacion_id))
    )
    [item] = list(resultado)
    assert _textos(item) == ["Contrato de la otra"]


# --- Criterio 4: sin fragmentos relevantes el ítem sigue sin evidencia ------------------------


async def test_un_fragmento_demasiado_lejano_no_cuenta_como_evidencia(
    sesion: AsyncSession,
    analisis: Analisis,
    consultas: ConsultasFalsas,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EVIDENCIA_DISTANCIA_MAXIMA", "0.3")
    monkeypatch.setenv("EVIDENCIA_MAX", "5")
    get_settings.cache_clear()

    await evidencia.ejecutar(analisis.estado)

    assert _textos((await _items(sesion))[ITEM_SOFTWARE]) == [
        "Contrato de software A",
        "Contrato de software B",
    ]


async def test_sin_fragmentos_relevantes_el_item_queda_sin_evidencia_y_continua(
    sesion: AsyncSession, consultas: ConsultasFalsas
) -> None:
    analisis = await crear_analisis(sesion)
    await agregar_requisito(sesion, analisis, numeral="3", texto="Calidad.", items=[ITEM_CALIDAD])
    await agregar_fragmento(sesion, analisis.quien, "Servicio de aseo", [0.0, -1.0, 0.0])

    await evidencia.ejecutar(analisis.estado)

    [item] = (await _items(sesion)).values()
    assert item.candidatos == []  # se buscó y no hay nada: sigue a verificación sin evidencia
    trabajo = await sesion.get(TrabajoProcesamiento, analisis.trabajo_id)
    assert trabajo is not None and trabajo.detalle is not None
    assert trabajo.detalle["evidencia"]["sin_evidencia"] == 1


async def test_sin_base_de_conocimiento_indexada_no_se_gastan_embeddings(
    sesion: AsyncSession, consultas: ConsultasFalsas
) -> None:
    analisis = await crear_analisis(sesion)
    await agregar_requisito(sesion, analisis, numeral="3", texto="Calidad.", items=[ITEM_CALIDAD])

    with pytest.raises(AnalisisError, match="indice compatible"):
        await evidencia.ejecutar(analisis.estado)
    [item] = (await _items(sesion)).values()
    assert item.candidatos is None and consultas.lotes == []


async def test_los_fragmentos_de_otro_modelo_de_embeddings_se_ignoran(
    sesion: AsyncSession, analisis: Analisis, consultas: ConsultasFalsas
) -> None:
    """Vectores de otro modelo no son comparables y pueden tener otra dimensión (ADR-013)."""
    await agregar_fragmento(
        sesion,
        analisis.quien,
        "Fragmento de un modelo anterior",
        [1.0, 0.0, 0.0, 0.0, 0.0],
        modelo="modelo-anterior",
    )

    await evidencia.ejecutar(analisis.estado)

    assert "Fragmento de un modelo anterior" not in _textos((await _items(sesion))[ITEM_SOFTWARE])


# --- Reanudación, fallos y registro ---------------------------------------------------------


async def test_repetir_la_etapa_no_vuelve_a_vectorizar_los_items_ya_resueltos(
    sesion: AsyncSession, analisis: Analisis, consultas: ConsultasFalsas
) -> None:
    await evidencia.ejecutar(analisis.estado)
    antes = {d: i.candidatos for d, i in (await _items(sesion)).items()}

    await evidencia.ejecutar(analisis.estado)

    assert len(consultas.lotes) == 1
    assert {d: i.candidatos for d, i in (await _items(sesion)).items()} == antes


async def test_si_nvidia_no_responde_la_etapa_falla_y_conserva_lo_ya_resuelto(
    sesion: AsyncSession,
    analisis: Analisis,
    consultas: ConsultasFalsas,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMBEDDINGS_LOTE", "1")
    get_settings.cache_clear()
    consultas.fallar_desde_el_lote = 1  # responde el primer lote y falla el segundo

    with pytest.raises(AnalisisError) as exc:
        await evidencia.ejecutar(analisis.estado)

    assert exc.value.mensaje_usuario == evidencia.MENSAJE_NO_DISPONIBLE
    items = await _items(sesion)
    resueltos = [d for d, i in items.items() if i.candidatos is not None]
    assert len(resueltos) == 1

    consultas.fallar_desde_el_lote = None
    await evidencia.ejecutar(analisis.estado)  # el reintento solo pide el que faltaba
    assert sum(len(lote) for lote in consultas.lotes) == 2


async def test_sin_modelo_de_embeddings_el_error_lo_dice(
    analisis: Analisis, consultas: ConsultasFalsas, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NVIDIA_MODEL_EMBEDDINGS", "")
    get_settings.cache_clear()

    with pytest.raises(AnalisisError) as exc:
        await evidencia.ejecutar(analisis.estado)
    assert exc.value.mensaje_usuario == evidencia.MENSAJE_SIN_MODELO


async def test_el_trabajo_registra_cuantos_items_tienen_evidencia(
    sesion: AsyncSession, analisis: Analisis, consultas: ConsultasFalsas
) -> None:
    await evidencia.ejecutar(analisis.estado)

    trabajo = await sesion.get(TrabajoProcesamiento, analisis.trabajo_id)
    assert trabajo is not None
    assert trabajo.detalle == {
        "version_empresa": "legacy",
        "evidencia": {
            "items": 2,
            "con_evidencia": 2,
            "sin_evidencia": 0,
            "base_indexada": True,
            "rerank": False,
        },
    }
