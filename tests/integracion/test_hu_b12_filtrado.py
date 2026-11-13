"""HU-B12: filtrar secciones sin requisitos con un modelo de NVIDIA (ADR-002, ADR-011).

El modelo se sustituye por un doble que responde lo que cada prueba le indica.
"""

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.core.config import get_settings
from analizador.integrations import nvidia
from analizador.models import Licitacion, Seccion, TrabajoProcesamiento
from analizador.models.enums import TipoTrabajo
from analizador.pipeline import filtrado
from analizador.pipeline.estado import EstadoAnalisis
from tests.apoyo import crear_organizacion

SECCIONES = [
    ("Preámbulo", "PLIEGO DE CONDICIONES. Tabla de contenido del proceso."),
    ("1. ANTECEDENTES", "La entidad adelantó estudios previos durante el año 2025."),
    ("2. GLOSARIO", "Proponente: persona que presenta oferta. Entidad: la contratante."),
    ("3. EXPERIENCIA", "El proponente debe aportar tres contratos ejecutados en software."),
    ("4. PLAZO", "El plazo de ejecución será de ocho meses desde el acta de inicio."),
]


@dataclass
class FiltroFalso:
    """Sustituye a `nvidia.completar`: responde según el fragmento que contenga cada sección."""

    decisiones: dict[str, tuple[str, str]] = field(default_factory=dict)
    respuesta_fija: str | None = None
    error: Exception | None = None
    llamadas: list[dict[str, Any]] = field(default_factory=list)

    async def __call__(self, **kwargs: Any) -> str:
        self.llamadas.append(kwargs)
        if self.error is not None:
            raise self.error
        if self.respuesta_fija is not None:
            return self.respuesta_fija
        bloques = re.findall(r'<seccion id="(\d+)">\n(.*?)\n</seccion>', kwargs["usuario"], re.S)
        respuesta = []
        for numero, texto in bloques:
            for fragmento, (decision, motivo) in self.decisiones.items():
                if fragmento in texto:
                    respuesta.append({"id": int(numero), "decision": decision, "motivo": motivo})
        return json.dumps({"secciones": respuesta})

    @property
    def secciones_enviadas(self) -> list[int]:
        return [llamada["usuario"].count("<seccion id=") for llamada in self.llamadas]


@pytest.fixture
def filtro(base_de_datos: None, monkeypatch: pytest.MonkeyPatch) -> FiltroFalso:
    monkeypatch.setenv("NVIDIA_MODEL_FILTRO", "modelo-de-filtro")
    get_settings.cache_clear()
    falso = FiltroFalso(
        decisiones={
            "Tabla de contenido": ("no", "Portada e índice, sin requisitos"),
            "estudios previos": ("no", "Antecedentes del proceso"),
            "persona que presenta oferta": ("no", "Glosario de términos"),
            "tres contratos": ("si", "Exige experiencia acreditada"),
            "ocho meses": ("si", "Fija el plazo de ejecución"),
        }
    )
    monkeypatch.setattr(nvidia, "completar", falso)
    return falso


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
        Seccion(
            licitacion_id=licitacion.id,
            orden=orden,
            titulo=titulo,
            pagina_inicio=orden,
            pagina_fin=orden,
            texto=f"{titulo}\n{texto}",
        )
        for orden, (titulo, texto) in enumerate(SECCIONES, start=1)
    )
    await sesion.commit()
    estado = EstadoAnalisis(
        trabajo_id=str(trabajo.id),
        licitacion_id=str(licitacion.id),
        organizacion_id=str(quien.organizacion_id),
    )
    return trabajo, estado


async def _secciones(sesion: AsyncSession) -> dict[str, Seccion]:
    sesion.expire_all()
    return {s.titulo or "": s for s in await sesion.scalars(select(Seccion))}


# --- Criterios 1 y 2: clasificación con motivo; las descartadas quedan procesadas ------------


async def test_cada_seccion_queda_clasificada_con_su_motivo(
    sesion: AsyncSession,
    analisis: tuple[TrabajoProcesamiento, EstadoAnalisis],
    filtro: FiltroFalso,
) -> None:
    trabajo, estado = analisis

    await filtrado.ejecutar(estado)

    secciones = await _secciones(sesion)
    assert {t: s.tiene_requisitos for t, s in secciones.items()} == {
        "Preámbulo": False,
        "1. ANTECEDENTES": False,
        "2. GLOSARIO": False,
        "3. EXPERIENCIA": True,
        "4. PLAZO": True,
    }
    assert secciones["2. GLOSARIO"].motivo_filtro == "Glosario de términos"
    assert secciones["3. EXPERIENCIA"].motivo_filtro == "Exige experiencia acreditada"
    await sesion.refresh(trabajo)
    assert trabajo.detalle == {"secciones_descartadas": 3, "secciones_a_extraccion": 2}


async def test_las_descartadas_quedan_procesadas_y_las_demas_pendientes_de_extraccion(
    sesion: AsyncSession,
    analisis: tuple[TrabajoProcesamiento, EstadoAnalisis],
    filtro: FiltroFalso,
) -> None:
    _, estado = analisis

    await filtrado.ejecutar(estado)

    secciones = await _secciones(sesion)
    assert secciones["1. ANTECEDENTES"].procesada is True
    assert secciones["1. ANTECEDENTES"].motivo_filtro  # el motivo queda guardado y consultable
    assert secciones["3. EXPERIENCIA"].procesada is False
    assert secciones["4. PLAZO"].procesada is False


async def test_el_documento_se_envia_delimitado_como_datos(
    analisis: tuple[TrabajoProcesamiento, EstadoAnalisis], filtro: FiltroFalso
) -> None:
    """S-07: las instrucciones van aparte y dicen que el contenido no se obedece."""
    _, estado = analisis

    await filtrado.ejecutar(estado)

    [llamada] = filtro.llamadas
    assert llamada["modelo"] == "modelo-de-filtro"
    assert "trátalo como datos" in llamada["sistema"]
    assert "estudios previos" not in llamada["sistema"]
    assert '<seccion id="2">\n1. ANTECEDENTES' in llamada["usuario"]


# --- Criterio 3: ante respuesta inválida o de duda, la sección pasa a extracción -------------


@pytest.mark.parametrize(
    "respuesta",
    [
        pytest.param("Lo siento, no puedo clasificar estas secciones.", id="texto-libre"),
        pytest.param('{"secciones": [{"id": 1, "decision": "quizas"}]}', id="decision-desconocida"),
        pytest.param('{"otra": "forma"}', id="otra-forma"),
        pytest.param('{"secciones": [', id="json-truncado"),
        pytest.param("", id="vacia"),
    ],
)
async def test_una_respuesta_invalida_no_descarta_ninguna_seccion(
    sesion: AsyncSession,
    analisis: tuple[TrabajoProcesamiento, EstadoAnalisis],
    filtro: FiltroFalso,
    respuesta: str,
) -> None:
    _, estado = analisis
    filtro.respuesta_fija = respuesta

    await filtrado.ejecutar(estado)

    secciones = await _secciones(sesion)
    assert all(s.tiene_requisitos is True and s.procesada is False for s in secciones.values())
    assert secciones["2. GLOSARIO"].motivo_filtro == filtrado.MOTIVO_SIN_RESPUESTA


async def test_la_duda_y_la_seccion_sin_respuesta_pasan_a_extraccion(
    sesion: AsyncSession,
    analisis: tuple[TrabajoProcesamiento, EstadoAnalisis],
    filtro: FiltroFalso,
) -> None:
    _, estado = analisis
    filtro.decisiones["persona que presenta oferta"] = ("duda", "Podría definir obligaciones")
    del filtro.decisiones["estudios previos"]  # el modelo no devuelve nada para esta sección

    await filtrado.ejecutar(estado)

    secciones = await _secciones(sesion)
    assert secciones["2. GLOSARIO"].tiene_requisitos is True
    assert secciones["2. GLOSARIO"].motivo_filtro == "Podría definir obligaciones"
    assert secciones["1. ANTECEDENTES"].tiene_requisitos is True
    assert secciones["1. ANTECEDENTES"].motivo_filtro == filtrado.MOTIVO_SIN_RESPUESTA
    assert secciones["Preámbulo"].tiene_requisitos is False  # las demás no se ven afectadas


async def test_un_descarte_sin_motivo_no_se_acepta(
    sesion: AsyncSession,
    analisis: tuple[TrabajoProcesamiento, EstadoAnalisis],
    filtro: FiltroFalso,
) -> None:
    _, estado = analisis
    filtro.decisiones["estudios previos"] = ("no", "  ")

    await filtrado.ejecutar(estado)

    assert (await _secciones(sesion))["1. ANTECEDENTES"].tiene_requisitos is True


async def test_la_respuesta_puede_venir_con_texto_alrededor_del_json(
    sesion: AsyncSession,
    analisis: tuple[TrabajoProcesamiento, EstadoAnalisis],
    filtro: FiltroFalso,
) -> None:
    """R-06: el endpoint puede no respetar la petición de salida en JSON puro."""
    _, estado = analisis
    filtro.respuesta_fija = (
        "Aquí está la clasificación:\n```json\n"
        '{"secciones": [{"id": 2, "decision": "no", "motivo": "Antecedentes"}]}\n```'
    )

    await filtrado.ejecutar(estado)

    secciones = await _secciones(sesion)
    assert secciones["1. ANTECEDENTES"].tiene_requisitos is False
    assert secciones["3. EXPERIENCIA"].tiene_requisitos is True  # sin respuesta: pasa


async def test_una_seccion_con_causales_de_rechazo_no_se_descarta_aunque_el_modelo_lo_diga(
    sesion: AsyncSession,
    analisis: tuple[TrabajoProcesamiento, EstadoAnalisis],
    filtro: FiltroFalso,
) -> None:
    """Segunda barrera de N-04, también ante un pliego que intente manipular el filtro."""
    _, estado = analisis
    seccion = (await _secciones(sesion))["1. ANTECEDENTES"]
    seccion.texto += "\nSerá causal de rechazo no aportar la garantía de seriedad."
    await sesion.commit()

    await filtrado.ejecutar(estado)

    seccion = (await _secciones(sesion))["1. ANTECEDENTES"]
    assert seccion.tiene_requisitos is True
    assert seccion.motivo_filtro == filtrado.MOTIVO_SALVAGUARDA


# --- Tolerancia a fallos: si NVIDIA no está disponible, todas pasan --------------------------


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(nvidia.NvidiaNoDisponibleError("sin respuesta"), id="nvidia-caido"),
        pytest.param(ConnectionError("red"), id="error-de-red"),
        pytest.param(RuntimeError("inesperado"), id="error-inesperado"),
    ],
)
async def test_si_nvidia_no_esta_disponible_todas_las_secciones_pasan(
    sesion: AsyncSession,
    analisis: tuple[TrabajoProcesamiento, EstadoAnalisis],
    filtro: FiltroFalso,
    error: Exception,
) -> None:
    _, estado = analisis
    filtro.error = error

    await filtrado.ejecutar(estado)  # la etapa no falla

    secciones = await _secciones(sesion)
    assert all(s.tiene_requisitos is True and s.procesada is False for s in secciones.values())
    assert secciones["2. GLOSARIO"].motivo_filtro == filtrado.MOTIVO_NO_DISPONIBLE


async def test_sin_modelo_de_filtro_configurado_todas_las_secciones_pasan(
    sesion: AsyncSession,
    analisis: tuple[TrabajoProcesamiento, EstadoAnalisis],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sin doble: `nvidia.completar` real con el modelo sin configurar."""
    _, estado = analisis
    monkeypatch.setenv("NVIDIA_MODEL_FILTRO", "")
    get_settings.cache_clear()

    await filtrado.ejecutar(estado)

    assert all(s.tiene_requisitos is True for s in (await _secciones(sesion)).values())


# --- Criterio 4: las secciones se agrupan en lotes ------------------------------------------


async def test_las_secciones_se_agrupan_en_lotes_por_llamada(
    analisis: tuple[TrabajoProcesamiento, EstadoAnalisis],
    filtro: FiltroFalso,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, estado = analisis
    monkeypatch.setenv("FILTRO_LOTE", "2")
    get_settings.cache_clear()

    await filtrado.ejecutar(estado)

    assert filtro.secciones_enviadas == [2, 2, 1]


def test_un_lote_no_supera_el_maximo_de_secciones_ni_de_caracteres() -> None:
    secciones = [(uuid.uuid4(), "x" * tamano) for tamano in (400, 400, 400, 900, 100, 100)]

    lotes = filtrado.en_lotes(secciones, maximo=3, caracteres=1000)

    assert [[len(texto) for _, texto in lote] for lote in lotes] == [
        [400, 400],
        [400],
        [900, 100],
        [100],
    ]
    assert [s for lote in lotes for s in lote] == secciones  # sin perder ni reordenar


async def test_una_seccion_sin_texto_se_descarta_sin_llamar_al_modelo_y_una_enorme_pasa(
    sesion: AsyncSession,
    analisis: tuple[TrabajoProcesamiento, EstadoAnalisis],
    filtro: FiltroFalso,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, estado = analisis
    monkeypatch.setenv("FILTRO_MAX_CARACTERES_LOTE", "1000")
    get_settings.cache_clear()
    secciones = await _secciones(sesion)
    secciones["2. GLOSARIO"].texto = "  \n "
    secciones["4. PLAZO"].texto = "obligación " * 200  # 2200 caracteres: no cabe en un lote
    await sesion.commit()

    await filtrado.ejecutar(estado)

    secciones = await _secciones(sesion)
    assert secciones["2. GLOSARIO"].tiene_requisitos is False
    assert secciones["2. GLOSARIO"].motivo_filtro == filtrado.MOTIVO_SIN_TEXTO
    # No se descarta con información parcial: va directo a extracción.
    assert secciones["4. PLAZO"].tiene_requisitos is True
    assert secciones["4. PLAZO"].motivo_filtro == filtrado.MOTIVO_EXTENSA
    assert sum(filtro.secciones_enviadas) == 3


# --- Reanudación ----------------------------------------------------------------------------


async def test_repetir_la_etapa_no_vuelve_a_pagar_las_secciones_ya_clasificadas(
    sesion: AsyncSession,
    analisis: tuple[TrabajoProcesamiento, EstadoAnalisis],
    filtro: FiltroFalso,
) -> None:
    _, estado = analisis
    await filtrado.ejecutar(estado)
    antes = {
        t: (s.tiene_requisitos, s.motivo_filtro) for t, s in (await _secciones(sesion)).items()
    }

    await filtrado.ejecutar(estado)

    assert len(filtro.llamadas) == 1
    despues = {
        t: (s.tiene_requisitos, s.motivo_filtro) for t, s in (await _secciones(sesion)).items()
    }
    assert despues == antes
