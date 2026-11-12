"""HU-B06: indexar los documentos de la empresa (RF-03, RF-10, ADR-013, ADR-002).

La base se carga por la API (HU-B05) y el trabajo de indexación se procesa como lo haría el
worker. El modelo de embeddings de NVIDIA se sustituye por vectores deterministas.
"""

import copy
import json
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.core.config import get_settings
from analizador.models import EmbeddingCache, Fragmento, FuenteConocimiento, TrabajoProcesamiento
from analizador.models.enums import EstadoTrabajo, OrigenFragmento, TipoTrabajo
from analizador.worker import handlers
from tests.apoyo import (
    EmbeddingsFalsos,
    Identidad,
    MensajeFalso,
    crear_organizacion,
    mensaje_de,
    vector_de,
)

EJEMPLO = json.loads(
    (Path(__file__).resolve().parents[2] / "docs/ejemplos/base_conocimiento.json").read_text(
        encoding="utf-8"
    )
)
# empresa 1 + financiero 2 + contratos 3 + personas (2 perfiles + 3 experiencias) +
# certificaciones 2 + capacidades 2
FUENTES, FRAGMENTOS = 12, 15
MODELO = "modelo-de-embeddings-de-prueba"


@pytest.fixture(autouse=True)
def modelo_configurado(base_de_datos: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_MODEL_EMBEDDINGS", MODELO)
    get_settings.cache_clear()


@pytest.fixture
def embeddings(monkeypatch: pytest.MonkeyPatch) -> EmbeddingsFalsos:
    falsos = EmbeddingsFalsos()
    falsos.instalar(monkeypatch)
    return falsos


@pytest.fixture
async def admin(sesion: AsyncSession) -> Identidad:
    return await crear_organizacion(sesion)


async def _cargar(
    cliente: httpx.AsyncClient, quien: Identidad, base: dict[str, Any] = EJEMPLO
) -> uuid.UUID:
    respuesta = await cliente.post(
        "/api/conocimiento",
        headers=quien.cabeceras,
        files={"archivo": ("base.json", json.dumps(base).encode(), "application/json")},
    )
    assert respuesta.status_code == 202, respuesta.text
    return uuid.UUID(respuesta.json()["trabajo"]["id"])


async def _reindexar(cliente: httpx.AsyncClient, quien: Identidad) -> uuid.UUID:
    respuesta = await cliente.post("/api/conocimiento/indexacion", headers=quien.cabeceras)
    assert respuesta.status_code == 202, respuesta.text
    return uuid.UUID(respuesta.json()["id"])


async def _procesar(trabajo_id: uuid.UUID) -> MensajeFalso:
    mensaje = mensaje_de(trabajo_id)
    await handlers.procesar(mensaje, TipoTrabajo.INDEXACION, None)  # type: ignore[arg-type]
    return mensaje


async def _trabajo(sesion: AsyncSession, trabajo_id: uuid.UUID) -> TrabajoProcesamiento:
    sesion.expire_all()
    trabajo = await sesion.get(TrabajoProcesamiento, trabajo_id)
    assert trabajo is not None
    return trabajo


async def _fragmentos(sesion: AsyncSession, quien: Identidad) -> list[Fragmento]:
    sesion.expire_all()
    resultado = await sesion.scalars(
        select(Fragmento).where(Fragmento.organizacion_id == quien.organizacion_id)
    )
    return list(resultado)


# --- Criterios 1 y 2: fragmentos con significado propio, vectorizados y guardados ------------


async def test_indexa_todos_los_registros_y_el_trabajo_informa_cuantos(
    cliente: httpx.AsyncClient,
    sesion: AsyncSession,
    admin: Identidad,
    embeddings: EmbeddingsFalsos,
) -> None:
    trabajo_id = await _cargar(cliente, admin)

    mensaje = await _procesar(trabajo_id)

    trabajo = await _trabajo(sesion, trabajo_id)
    assert mensaje.confirmado
    assert trabajo.estado == EstadoTrabajo.COMPLETADO and trabajo.progreso == 100
    assert trabajo.detalle == {
        "fuentes": FUENTES,
        "fragmentos": FRAGMENTOS,
        "vectorizados": FRAGMENTOS,
        "reutilizados": 0,
        "modelo": MODELO,
    }
    fragmentos = await _fragmentos(sesion, admin)
    assert len(fragmentos) == FRAGMENTOS
    # Completitud: toda fuente tiene al menos un fragmento.
    fuentes = set(
        await sesion.scalars(
            select(FuenteConocimiento.id).where(
                FuenteConocimiento.organizacion_id == admin.organizacion_id
            )
        )
    )
    assert {f.fuente_conocimiento_id for f in fragmentos} == fuentes and len(fuentes) == FUENTES


async def test_cada_vector_se_guarda_con_su_organizacion_su_fuente_y_su_modelo(
    cliente: httpx.AsyncClient,
    sesion: AsyncSession,
    admin: Identidad,
    embeddings: EmbeddingsFalsos,
) -> None:
    await _procesar(await _cargar(cliente, admin))

    for fragmento in await _fragmentos(sesion, admin):
        assert fragmento.organizacion_id == admin.organizacion_id
        assert fragmento.origen == OrigenFragmento.EMPRESA
        assert fragmento.fuente_conocimiento_id is not None
        assert fragmento.modelo_embedding == MODELO
        assert fragmento.embedding is not None
        assert list(fragmento.embedding) == pytest.approx(vector_de(fragmento.texto))
    assert set(embeddings.tipos) == {"passage"}


async def test_cada_fragmento_se_entiende_por_si_solo(
    cliente: httpx.AsyncClient,
    sesion: AsyncSession,
    admin: Identidad,
    embeddings: EmbeddingsFalsos,
) -> None:
    await _procesar(await _cargar(cliente, admin))
    textos = [f.texto for f in await _fragmentos(sesion, admin)]

    [contrato] = [t for t in textos if t.startswith("Contrato C-2023-014")]
    assert "Secretaría Distrital de Hacienda" in contrato
    assert "$2.450.000.000 COP (2112.07 SMMLV)" in contrato
    assert "81111500, 81112200, 43232300" in contrato

    # Una experiencia es un fragmento aparte y nombra a la persona: no depende del perfil.
    [experiencia] = [t for t in textos if "Líder técnico" in t]
    assert "Camilo Restrepo Vargas" in experiencia
    assert "desde 2012-03-01 hasta 2017-12-20" in experiencia
    [vigente] = [t for t in textos if "Gerente de proyecto" in t]
    assert "hasta la fecha" in vigente

    [union_temporal] = [t for t in textos if t.startswith("Contrato C-2022-031")]
    assert "Participación del oferente: 60 %" in union_temporal
    assert any("ISO 9001:2015 emitida por ICONTEC" in t for t in textos)
    assert any(t.startswith("Indicadores financieros del año 2025") for t in textos)


async def test_los_fragmentos_se_envian_por_lotes(
    cliente: httpx.AsyncClient,
    admin: Identidad,
    embeddings: EmbeddingsFalsos,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMBEDDINGS_LOTE", "4")
    get_settings.cache_clear()

    await _procesar(await _cargar(cliente, admin))

    assert [len(lote) for lote in embeddings.lotes] == [4, 4, 4, 3]


# --- Criterio 3: reindexar reemplaza los fragmentos, sin duplicados --------------------------


async def test_reindexar_reemplaza_los_fragmentos_sin_duplicarlos(
    cliente: httpx.AsyncClient,
    sesion: AsyncSession,
    admin: Identidad,
    embeddings: EmbeddingsFalsos,
) -> None:
    await _procesar(await _cargar(cliente, admin))
    antes = {f.id for f in await _fragmentos(sesion, admin)}

    segundo = await _reindexar(cliente, admin)
    await _procesar(segundo)

    despues = await _fragmentos(sesion, admin)
    assert len(despues) == FRAGMENTOS
    assert len({f.hash_texto for f in despues}) == FRAGMENTOS
    assert antes.isdisjoint({f.id for f in despues})


async def test_un_texto_ya_vectorizado_no_se_vuelve_a_enviar(
    cliente: httpx.AsyncClient,
    sesion: AsyncSession,
    admin: Identidad,
    embeddings: EmbeddingsFalsos,
) -> None:
    await _procesar(await _cargar(cliente, admin))
    assert len(embeddings.textos_enviados) == FRAGMENTOS

    segundo = await _reindexar(cliente, admin)
    await _procesar(segundo)

    assert len(embeddings.textos_enviados) == FRAGMENTOS  # nada nuevo
    assert (await _trabajo(sesion, segundo)).detalle == {
        "fuentes": FUENTES,
        "fragmentos": FRAGMENTOS,
        "vectorizados": 0,
        "reutilizados": FRAGMENTOS,
        "modelo": MODELO,
    }


async def test_al_reemplazar_la_base_solo_se_envian_los_textos_que_cambiaron(
    cliente: httpx.AsyncClient,
    sesion: AsyncSession,
    admin: Identidad,
    embeddings: EmbeddingsFalsos,
) -> None:
    await _procesar(await _cargar(cliente, admin))
    nueva = copy.deepcopy(EJEMPLO)
    nueva["capacidades"][0]["descripcion"] = "Equipo ampliado a 60 desarrolladores."
    nueva["capacidades"].append({"nombre": "Laboratorio de pruebas", "descripcion": "Propio."})

    await _procesar(await _cargar(cliente, admin, nueva))

    assert len(embeddings.textos_enviados) == FRAGMENTOS + 2
    assert "60 desarrolladores" in embeddings.textos_enviados[-2]
    assert len(await _fragmentos(sesion, admin)) == FRAGMENTOS + 1


# --- Criterio 4: si NVIDIA no responde, error visible y nada a medias -------------------------


async def test_si_nvidia_no_responde_el_trabajo_queda_en_error_sin_indexar_a_medias(
    cliente: httpx.AsyncClient,
    sesion: AsyncSession,
    admin: Identidad,
    embeddings: EmbeddingsFalsos,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMBEDDINGS_LOTE", "4")
    get_settings.cache_clear()
    embeddings.fallar_desde_el_lote = 2  # los dos primeros lotes responden, el tercero no
    trabajo_id = await _cargar(cliente, admin)

    mensaje = await _procesar(trabajo_id)

    trabajo = await _trabajo(sesion, trabajo_id)
    assert trabajo.estado == EstadoTrabajo.FALLIDO
    assert trabajo.error is not None and "embeddings no respondió" in trabajo.error
    assert "NVIDIA no respondió tras" not in trabajo.error  # sin el texto interno
    assert mensaje.rechazado and mensaje.reencolado is False
    assert await _fragmentos(sesion, admin) == []  # ni un fragmento: no hay índice a medias

    resumen = (await cliente.get("/api/conocimiento", headers=admin.cabeceras)).json()
    assert resumen["indexacion"]["estado"] == "fallido"
    assert resumen["fragmentos_indexados"] == 0


async def test_el_reintento_no_vuelve_a_pagar_los_lotes_que_si_respondieron(
    cliente: httpx.AsyncClient,
    sesion: AsyncSession,
    admin: Identidad,
    embeddings: EmbeddingsFalsos,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMBEDDINGS_LOTE", "4")
    get_settings.cache_clear()
    embeddings.fallar_desde_el_lote = 2
    await _procesar(await _cargar(cliente, admin))
    assert len(embeddings.textos_enviados) == 8

    embeddings.fallar_desde_el_lote = None
    reintento = await _reindexar(cliente, admin)
    await _procesar(reintento)

    assert len(embeddings.textos_enviados) == FRAGMENTOS  # solo los 7 que faltaban
    assert (await _trabajo(sesion, reintento)).estado == EstadoTrabajo.COMPLETADO
    assert len(await _fragmentos(sesion, admin)) == FRAGMENTOS


async def test_si_falla_un_reindexado_se_conserva_el_indice_anterior(
    cliente: httpx.AsyncClient,
    sesion: AsyncSession,
    admin: Identidad,
    embeddings: EmbeddingsFalsos,
) -> None:
    await _procesar(await _cargar(cliente, admin))
    antes = {f.id for f in await _fragmentos(sesion, admin)}
    # Sin caché, todo se tendría que volver a pedir, y NVIDIA no responde.
    await sesion.execute(delete(EmbeddingCache))
    await sesion.commit()
    embeddings.fallar_desde_el_lote = 0

    fallido = await _reindexar(cliente, admin)
    await _procesar(fallido)

    assert (await _trabajo(sesion, fallido)).estado == EstadoTrabajo.FALLIDO
    assert {f.id for f in await _fragmentos(sesion, admin)} == antes


async def test_sin_modelo_de_embeddings_el_error_lo_dice(
    cliente: httpx.AsyncClient,
    sesion: AsyncSession,
    admin: Identidad,
    embeddings: EmbeddingsFalsos,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NVIDIA_MODEL_EMBEDDINGS", "")
    get_settings.cache_clear()
    trabajo_id = await _cargar(cliente, admin)

    await _procesar(trabajo_id)

    trabajo = await _trabajo(sesion, trabajo_id)
    assert trabajo.estado == EstadoTrabajo.FALLIDO
    assert trabajo.error is not None and "modelo de embeddings" in trabajo.error
    assert embeddings.lotes == []


async def test_si_la_base_cambia_durante_la_indexacion_no_se_escribe_un_indice_viejo(
    cliente: httpx.AsyncClient,
    sesion: AsyncSession,
    admin: Identidad,
    embeddings: EmbeddingsFalsos,
) -> None:
    trabajo_id = await _cargar(cliente, admin)
    nueva = copy.deepcopy(EJEMPLO)
    nueva["contratos"] = nueva["contratos"][:1]
    cargas: list[uuid.UUID] = []

    async def reemplazar_la_base() -> None:
        if not cargas:
            cargas.append(await _cargar(cliente, admin, nueva))

    embeddings.antes_de_responder = reemplazar_la_base
    await _procesar(trabajo_id)

    trabajo = await _trabajo(sesion, trabajo_id)
    assert trabajo.estado == EstadoTrabajo.FALLIDO
    assert trabajo.error is not None and "cambió durante la indexación" in trabajo.error
    assert await _fragmentos(sesion, admin) == []

    # El trabajo de la carga nueva indexa la versión nueva.
    embeddings.antes_de_responder = None
    await _procesar(cargas[0])
    assert len(await _fragmentos(sesion, admin)) == FRAGMENTOS - 2


# --- Aislamiento ----------------------------------------------------------------------------


async def test_ni_el_indice_ni_la_cache_se_comparten_entre_organizaciones(
    cliente: httpx.AsyncClient,
    sesion: AsyncSession,
    admin: Identidad,
    embeddings: EmbeddingsFalsos,
) -> None:
    otra = await crear_organizacion(sesion, "Otra organización")
    await _procesar(await _cargar(cliente, admin))

    await _procesar(await _cargar(cliente, otra))  # los mismos textos, otra organización

    assert len(embeddings.textos_enviados) == 2 * FRAGMENTOS
    assert len(await _fragmentos(sesion, admin)) == FRAGMENTOS
    assert len(await _fragmentos(sesion, otra)) == FRAGMENTOS
    por_organizacion = dict(
        (
            await sesion.execute(
                select(EmbeddingCache.organizacion_id, func.count()).group_by(
                    EmbeddingCache.organizacion_id
                )
            )
        ).all()
    )
    assert por_organizacion == {
        admin.organizacion_id: FRAGMENTOS,
        otra.organizacion_id: FRAGMENTOS,
    }
