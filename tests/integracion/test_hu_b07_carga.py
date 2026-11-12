"""HU-B07: cargar un pliego para análisis (RF-08, S-08, S-09, S-10)."""

import hashlib
import uuid

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.core.config import get_settings
from analizador.models import Licitacion, TrabajoProcesamiento
from analizador.models.enums import EstadoLicitacion, EstadoTrabajo, Rol, TipoTrabajo
from tests.apoyo import AlmacenFalso, Identidad, PublicadorFalso, crear_organizacion

PDF = b"%PDF-1.7\n% pliego de prueba\n%%EOF\n"
OTRO_PDF = b"%PDF-1.7\n% otro pliego\n%%EOF\n"


@pytest.fixture
async def revisor(sesion: AsyncSession) -> Identidad:
    return await crear_organizacion(sesion, rol=Rol.REVISOR)


async def _subir(
    cliente: httpx.AsyncClient,
    quien: Identidad,
    contenido: bytes = PDF,
    nombre: str = "pliego.pdf",
    **datos: str,
) -> httpx.Response:
    return await cliente.post(
        "/api/licitaciones",
        headers=quien.cabeceras,
        files={"archivo": (nombre, contenido, "application/pdf")},
        data=datos,
    )


async def _contar(sesion: AsyncSession, modelo: type) -> int:
    return await sesion.scalar(select(func.count()).select_from(modelo)) or 0


# --- Criterio 1 -----------------------------------------------------------------------------


async def test_pdf_valido_se_guarda_se_registra_y_se_encola(
    cliente: httpx.AsyncClient,
    sesion: AsyncSession,
    revisor: Identidad,
    almacen: AlmacenFalso,
    publicador: PublicadorFalso,
) -> None:
    respuesta = await _subir(cliente, revisor, titulo="Licitación de aseo 2026")

    assert respuesta.status_code == 202
    cuerpo = respuesta.json()
    assert cuerpo["duplicada"] is False
    assert cuerpo["licitacion"]["titulo"] == "Licitación de aseo 2026"
    assert cuerpo["licitacion"]["estado"] == "pendiente"
    assert cuerpo["trabajo"]["estado"] == "en_cola"
    assert cuerpo["trabajo"]["tipo"] == "analisis"

    licitacion = await sesion.get(Licitacion, uuid.UUID(cuerpo["licitacion"]["id"]))
    assert licitacion is not None
    assert licitacion.organizacion_id == revisor.organizacion_id
    assert licitacion.creado_por == revisor.usuario_id
    assert licitacion.hash_pdf == hashlib.sha256(PDF).hexdigest()
    assert almacen.objetos == {licitacion.storage_key: PDF}

    trabajo_id = uuid.UUID(cuerpo["trabajo"]["id"])
    trabajo = await sesion.get(TrabajoProcesamiento, trabajo_id)
    assert trabajo is not None and trabajo.licitacion_id == licitacion.id
    # El mensaje lleva solo el identificador del trabajo (ADR-015).
    assert publicador.publicados == [(TipoTrabajo.ANALISIS, trabajo_id)]


async def test_sin_titulo_se_usa_el_nombre_del_archivo(
    cliente: httpx.AsyncClient, revisor: Identidad
) -> None:
    respuesta = await _subir(cliente, revisor, nombre="LP-004-2026.pdf")
    assert respuesta.json()["licitacion"]["titulo"] == "LP-004-2026.pdf"


# --- Criterio 2: el tipo se verifica por el contenido ---------------------------------------


@pytest.mark.parametrize(
    "contenido",
    [
        pytest.param(b"MZ\x90\x00 ejecutable renombrado a .pdf", id="ejecutable"),
        pytest.param(b"<html><body>no soy un pdf</body></html>", id="html"),
        pytest.param(b"", id="vacio"),
        pytest.param(b"x" * 2000 + b"%PDF-1.7", id="cabecera-fuera-del-primer-kb"),
    ],
)
async def test_archivo_que_no_es_pdf_se_rechaza_sin_dejar_rastro(
    cliente: httpx.AsyncClient,
    sesion: AsyncSession,
    revisor: Identidad,
    almacen: AlmacenFalso,
    publicador: PublicadorFalso,
    contenido: bytes,
) -> None:
    respuesta = await _subir(cliente, revisor, contenido, nombre="pliego.pdf")

    assert respuesta.status_code == 415
    assert "PDF" in respuesta.json()["detail"]
    assert almacen.objetos == {}
    assert publicador.publicados == []
    assert await _contar(sesion, Licitacion) == 0


async def test_pdf_con_bytes_previos_a_la_cabecera_se_acepta(
    cliente: httpx.AsyncClient, revisor: Identidad
) -> None:
    respuesta = await _subir(cliente, revisor, b"\xef\xbb\xbf\n" + PDF)
    assert respuesta.status_code == 202


# --- Criterio 3: límite de tamaño con mensaje que indica el límite --------------------------


async def test_pdf_demasiado_grande_indica_el_limite(
    cliente: httpx.AsyncClient, sesion: AsyncSession, revisor: Identidad, almacen: AlmacenFalso
) -> None:
    limite = get_settings().max_pdf_mb  # 1 MB en el entorno de pruebas
    respuesta = await _subir(cliente, revisor, b"%PDF-" + b"0" * (limite * 1024 * 1024))

    assert respuesta.status_code == 413
    assert f"{limite} MB" in respuesta.json()["detail"]
    assert almacen.objetos == {}
    assert await _contar(sesion, Licitacion) == 0


async def test_pdf_justo_en_el_limite_se_acepta(
    cliente: httpx.AsyncClient, revisor: Identidad
) -> None:
    limite = get_settings().max_pdf_bytes
    respuesta = await _subir(cliente, revisor, b"%PDF-" + b"0" * (limite - 5))
    assert respuesta.status_code == 202


# --- Criterio 4: el mismo PDF no se analiza dos veces ---------------------------------------


async def test_el_mismo_pdf_devuelve_el_analisis_existente_sin_gastar_creditos(
    cliente: httpx.AsyncClient,
    sesion: AsyncSession,
    revisor: Identidad,
    almacen: AlmacenFalso,
    publicador: PublicadorFalso,
) -> None:
    primera = await _subir(cliente, revisor, titulo="Original")
    segunda = await _subir(cliente, revisor, nombre="copia.pdf", titulo="Copia")

    assert primera.status_code == 202
    assert segunda.status_code == 200
    assert segunda.json()["duplicada"] is True
    assert segunda.json()["licitacion"] == primera.json()["licitacion"]
    assert segunda.json()["trabajo"]["id"] == primera.json()["trabajo"]["id"]
    assert await _contar(sesion, Licitacion) == 1
    assert await _contar(sesion, TrabajoProcesamiento) == 1
    assert len(almacen.objetos) == 1
    assert len(publicador.publicados) == 1


async def test_el_mismo_pdf_en_otra_organizacion_es_un_analisis_distinto(
    cliente: httpx.AsyncClient, sesion: AsyncSession, revisor: Identidad
) -> None:
    otra = await crear_organizacion(sesion, "Otra organización")

    propia = await _subir(cliente, revisor)
    ajena = await _subir(cliente, otra)

    assert propia.status_code == ajena.status_code == 202
    assert ajena.json()["duplicada"] is False
    assert ajena.json()["licitacion"]["id"] != propia.json()["licitacion"]["id"]


async def test_un_pdf_distinto_inicia_otro_analisis(
    cliente: httpx.AsyncClient, revisor: Identidad, publicador: PublicadorFalso
) -> None:
    assert (await _subir(cliente, revisor)).status_code == 202
    assert (await _subir(cliente, revisor, OTRO_PDF)).status_code == 202
    assert len(publicador.publicados) == 2


# --- Criterio 5: prefijo de la organización, nombre aleatorio -------------------------------


async def test_el_archivo_queda_bajo_el_prefijo_de_la_organizacion(
    cliente: httpx.AsyncClient, revisor: Identidad, almacen: AlmacenFalso
) -> None:
    await _subir(cliente, revisor, nombre="Pliego Secreto.pdf")

    [clave] = almacen.objetos
    assert clave.startswith(f"organizaciones/{revisor.organizacion_id}/pliegos/")
    assert "Secreto" not in clave


# --- Integridad: si falla un paso no queda nada a medias ------------------------------------


async def test_si_falla_el_almacen_no_se_crea_nada(
    cliente: httpx.AsyncClient,
    sesion: AsyncSession,
    revisor: Identidad,
    almacen: AlmacenFalso,
    publicador: PublicadorFalso,
) -> None:
    almacen.fallar_al_subir = True

    respuesta = await _subir(cliente, revisor)

    assert respuesta.status_code == 503
    assert await _contar(sesion, Licitacion) == 0
    assert await _contar(sesion, TrabajoProcesamiento) == 0
    assert publicador.publicados == []


async def test_si_falla_la_cola_se_deshace_la_carga(
    cliente: httpx.AsyncClient,
    sesion: AsyncSession,
    revisor: Identidad,
    almacen: AlmacenFalso,
    publicador: PublicadorFalso,
) -> None:
    publicador.fallar = True

    respuesta = await _subir(cliente, revisor)

    assert respuesta.status_code == 503
    assert "Intente de nuevo" in respuesta.json()["detail"]
    assert await _contar(sesion, Licitacion) == 0
    assert await _contar(sesion, TrabajoProcesamiento) == 0
    assert almacen.objetos == {}
    assert len(almacen.eliminadas) == 1

    # Tras el fallo, el mismo PDF se puede volver a cargar: no quedó marcado como duplicado.
    publicador.fallar = False
    reintento = await _subir(cliente, revisor)
    assert reintento.status_code == 202
    assert reintento.json()["duplicada"] is False


async def test_si_falla_la_base_de_datos_se_retira_el_archivo(
    cliente: httpx.AsyncClient,
    sesion: AsyncSession,
    revisor: Identidad,
    almacen: AlmacenFalso,
    publicador: PublicadorFalso,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession as Sesion

    async def commit_roto(self: Sesion) -> None:
        raise ConnectionError("base de datos caída")

    monkeypatch.setattr(Sesion, "commit", commit_roto)
    respuesta = await _subir(cliente, revisor)
    monkeypatch.undo()

    assert respuesta.status_code == 500
    assert "caída" not in respuesta.text
    assert almacen.objetos == {}
    assert publicador.publicados == []


async def test_reintentar_sin_cola_deja_el_trabajo_en_fallido(
    cliente: httpx.AsyncClient,
    sesion: AsyncSession,
    revisor: Identidad,
    publicador: PublicadorFalso,
) -> None:
    creada = (await _subir(cliente, revisor)).json()
    trabajo = await sesion.get(TrabajoProcesamiento, uuid.UUID(creada["trabajo"]["id"]))
    assert trabajo is not None
    trabajo.estado = EstadoTrabajo.FALLIDO
    trabajo.error = "El procesamiento falló. Puede reintentarse."
    await sesion.commit()

    publicador.fallar = True
    url = f"/api/licitaciones/{creada['licitacion']['id']}/reintentar"
    respuesta = await cliente.post(url, headers=revisor.cabeceras)

    assert respuesta.status_code == 503
    await sesion.refresh(trabajo)
    assert trabajo.estado == EstadoTrabajo.FALLIDO
    assert trabajo.error == "El procesamiento falló. Puede reintentarse."

    publicador.fallar = False
    respuesta = await cliente.post(url, headers=revisor.cabeceras)
    assert respuesta.status_code == 202
    assert respuesta.json()["estado"] == "en_cola"
    assert publicador.publicados[-1] == (TipoTrabajo.ANALISIS, trabajo.id)


async def test_la_respuesta_no_espera_al_analisis(
    cliente: httpx.AsyncClient, sesion: AsyncSession, revisor: Identidad
) -> None:
    """Responde de inmediato: la licitación queda pendiente y el trabajo en cola."""
    cuerpo = (await _subir(cliente, revisor)).json()
    licitacion = await sesion.get(Licitacion, uuid.UUID(cuerpo["licitacion"]["id"]))
    assert licitacion is not None and licitacion.estado == EstadoLicitacion.PENDIENTE
    assert cuerpo["trabajo"]["progreso"] == 0
    assert cuerpo["trabajo"]["iniciado_en"] is None
