"""Flujo documental real con almacenamiento e IA simulados, sobre PostgreSQL."""

import copy
import uuid

import httpx
import pymupdf
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.core.config import get_settings
from analizador.db.conocimiento import filtro_version, version_activa
from analizador.integrations import anthropic_client, nvidia
from analizador.models import CambioConocimiento, FuenteConocimiento
from analizador.models.enums import TipoTrabajo
from analizador.pipeline.documentos import Candidato, ExtraccionDocumento
from analizador.worker import handlers
from tests.apoyo import EmbeddingsFalsos, crear_organizacion, mensaje_de
from tests.integracion.test_hu_b06_indexacion import EJEMPLO, _cargar, _procesar


@pytest.mark.parametrize("escaneada", [False, True])
async def test_pdf_extraccion_revision_aprobacion_indexacion_y_aislamiento(
    cliente: httpx.AsyncClient,
    sesion: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    escaneada: bool,
) -> None:
    admin = await crear_organizacion(sesion)
    otra = await crear_organizacion(sesion, "Otra")
    monkeypatch.setenv("ANTHROPIC_MODEL_EXTRACCION", "modelo-prueba")
    monkeypatch.setenv("NVIDIA_MODEL_EMBEDDINGS", "modelo-prueba")
    monkeypatch.setenv("MAX_PDF_MB", "20")
    get_settings.cache_clear()
    EmbeddingsFalsos().instalar(monkeypatch)
    empresa = {"razon_social": "Empresa Ejemplo", "nit": "900123456-7"}
    cita = "Empresa Ejemplo NIT 900123456-7"
    import json

    async def extraer(**_kwargs: object) -> tuple[ExtraccionDocumento, anthropic_client.Uso]:
        return ExtraccionDocumento(
            candidatos=[
                Candidato(tipo="empresa", pagina=1, cita=cita, datos_json=json.dumps(empresa))
            ]
        ), anthropic_client.Uso()

    monkeypatch.setattr(anthropic_client, "generar_estructurado", extraer)
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((72, 72), cita)
        contenido = pdf.tobytes()
    if escaneada:
        from analizador.pipeline import lectura
        from tests.apoyo_pdf import PaginaDePrueba, crear_pdf

        contenido = crear_pdf([PaginaDePrueba([cita], escaneada=True)])
        monkeypatch.setattr(lectura, "_ocr", lambda _imagen, _settings: (cita, [], 99.0))
    carga = await cliente.post(
        "/api/conocimiento/documentos",
        headers=admin.cabeceras,
        files={"archivo": ("empresa.pdf", contenido, "application/pdf")},
    )
    assert carga.status_code == 202, carga.text
    documento_id = carga.json()["id"]
    from analizador.models import TrabajoProcesamiento

    trabajo = await sesion.scalar(
        select(TrabajoProcesamiento).where(
            TrabajoProcesamiento.organizacion_id == admin.organizacion_id
        )
    )
    assert trabajo
    await handlers.procesar(mensaje_de(trabajo.id), TipoTrabajo.INDEXACION, None)
    docs = (await cliente.get("/api/conocimiento/documentos", headers=admin.cabeceras)).json()
    assert len(docs) == 1 and "paginas" not in docs[0]
    docs = [
        (
            await cliente.get(
                f"/api/conocimiento/documentos/{documento_id}", headers=admin.cabeceras
            )
        ).json()
    ]
    assert docs[0]["estado"] == "revision"
    assert docs[0]["paginas"][0]["origen"] == ("ocr" if escaneada else "digital")
    assert docs[0]["candidatos"][0]["pagina"] == 1
    assert await sesion.scalar(select(FuenteConocimiento.id)) is None
    assert (
        await cliente.get(
            f"/api/conocimiento/documentos/{documento_id}/pdf-url", headers=otra.cabeceras
        )
    ).status_code == 404
    assert (await cliente.get("/api/conocimiento/documentos", headers=otra.cabeceras)).json() == []
    propuesta = {"base": {"empresa": empresa}, "procedencia": docs[0]["candidatos"]}
    aprobacion = await cliente.post(
        f"/api/conocimiento/documentos/{documento_id}/aprobar",
        headers=admin.cabeceras,
        json=propuesta,
    )
    assert aprobacion.status_code == 202, aprobacion.text
    assert await sesion.scalar(select(FuenteConocimiento.id)) is None
    await _procesar(uuid.UUID(aprobacion.json()["id"]))
    resumen = (await cliente.get("/api/conocimiento", headers=admin.cabeceras)).json()
    assert resumen["cargada"] and resumen["fragmentos_indexados"] > 0
    docs = (await cliente.get("/api/conocimiento/documentos", headers=admin.cabeceras)).json()
    assert docs[0]["estado"] == "aprobado"
    repetida = await cliente.post(
        "/api/conocimiento/documentos",
        headers=admin.cabeceras,
        files={"archivo": ("igual.pdf", contenido, "application/pdf")},
    )
    assert repetida.json()["id"] == documento_id


async def test_reemplazo_con_fallo_conserva_fuentes_indice_y_version(
    cliente: httpx.AsyncClient, sesion: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin = await crear_organizacion(sesion)
    monkeypatch.setenv("NVIDIA_MODEL_EMBEDDINGS", "modelo-prueba")
    get_settings.cache_clear()
    falsos = EmbeddingsFalsos()
    falsos.instalar(monkeypatch)
    inicial = await _cargar(cliente, admin)
    await _procesar(inicial)
    version = await version_activa(sesion, admin.organizacion_id)
    fuentes = set(
        await sesion.scalars(select(FuenteConocimiento.id).where(filtro_version(version)))
    )
    nueva = copy.deepcopy(EJEMPLO)
    nueva["empresa"]["razon_social"] = "Nombre cambiado"

    async def fallar(*_args: object, **_kwargs: object) -> list[list[float]]:
        raise nvidia.NvidiaNoDisponibleError("prueba")

    monkeypatch.setattr(nvidia, "embeddings", fallar)
    pendiente = await _cargar(cliente, admin, nueva)
    await _procesar(pendiente)
    assert await version_activa(sesion, admin.organizacion_id) == version
    assert (
        set(await sesion.scalars(select(FuenteConocimiento.id).where(filtro_version(version))))
        == fuentes
    )
    cambio = await sesion.scalar(
        select(CambioConocimiento).where(CambioConocimiento.trabajo_id == pendiente)
    )
    assert cambio and cambio.estado == "pendiente"


async def test_retirar_documento_conserva_evidencia_historica(
    cliente: httpx.AsyncClient, sesion: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from analizador.models import DocumentoEmpresa

    admin = await crear_organizacion(sesion)
    monkeypatch.setenv("NVIDIA_MODEL_EMBEDDINGS", "modelo-prueba")
    get_settings.cache_clear()
    EmbeddingsFalsos().instalar(monkeypatch)
    await _procesar(await _cargar(cliente, admin))
    datos = {"nombre": "Capacidad adicional", "descripcion": "Soporte especializado"}
    evidencia = {"tipo": "capacidad", "pagina": 1, "cita": "Soporte especializado", "datos": datos}
    documento = DocumentoEmpresa(
        id=uuid.uuid4(),
        organizacion_id=admin.organizacion_id,
        nombre="capacidad.pdf",
        hash_pdf="a" * 64,
        storage_key=f"organizaciones/{admin.organizacion_id}/empresa/capacidad.pdf",
        estado="revision",
        paginas=[{"numero": 1, "texto": "Soporte especializado", "origen": "digital"}],
        candidatos=[evidencia],
    )
    sesion.add(documento)
    await sesion.commit()
    base = (
        await cliente.get("/api/conocimiento/documentos/base/actual", headers=admin.cabeceras)
    ).json()
    version = base.pop("_version")
    base["capacidades"].append(datos)
    aprobada = await cliente.post(
        f"/api/conocimiento/documentos/{documento.id}/aprobar",
        headers=admin.cabeceras,
        json={"base": base, "procedencia": [evidencia], "version_base": version},
    )
    assert aprobada.status_code == 202, aprobada.text
    await _procesar(uuid.UUID(aprobada.json()["id"]))
    fuentes = (await cliente.get("/api/conocimiento/fuentes", headers=admin.cabeceras)).json()
    fuente_id = next(f["id"] for f in fuentes if f["titulo"] == "Capacidad adicional")
    retiro = await cliente.post(
        f"/api/conocimiento/documentos/{documento.id}/retirar", headers=admin.cabeceras
    )
    assert retiro.status_code == 202, retiro.text
    await _procesar(uuid.UUID(retiro.json()["id"]))
    nuevas = (await cliente.get("/api/conocimiento/fuentes", headers=admin.cabeceras)).json()
    assert fuente_id not in {f["id"] for f in nuevas}
    assert (
        await cliente.get(
            f"/api/conocimiento/documentos/fuentes/{fuente_id}/pdf-url", headers=admin.cabeceras
        )
    ).status_code == 200


async def test_entregas_simultaneas_no_ejecutan_dos_veces_el_trabajo(
    sesion: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from analizador.models import TrabajoProcesamiento

    admin = await crear_organizacion(sesion)
    trabajo = TrabajoProcesamiento(
        organizacion_id=admin.organizacion_id, tipo=TipoTrabajo.INDEXACION
    )
    sesion.add(trabajo)
    await sesion.commit()
    iniciado, continuar = asyncio.Event(), asyncio.Event()
    llamadas = []

    async def procesar(_trabajo: object, _grafo: object) -> None:
        llamadas.append(True)
        iniciado.set()
        await continuar.wait()

    monkeypatch.setitem(handlers.HANDLERS, TipoTrabajo.INDEXACION, procesar)
    primero, duplicado = mensaje_de(trabajo.id), mensaje_de(trabajo.id)
    tarea = asyncio.create_task(handlers.procesar(primero, TipoTrabajo.INDEXACION, None))
    await asyncio.wait_for(iniciado.wait(), timeout=10)
    try:
        await handlers.procesar(duplicado, TipoTrabajo.INDEXACION, None)
        assert duplicado.confirmado and llamadas == [True]
    finally:
        continuar.set()
        await tarea
    assert primero.confirmado
