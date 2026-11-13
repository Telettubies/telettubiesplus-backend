"""HU-B23: registrar la revisión humana y recalcular (RF-14, N-01)."""

import json
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.contratos.decision import JustificacionGenerada
from analizador.core.config import get_settings
from analizador.integrations import anthropic_client
from analizador.integrations.anthropic_client import Uso
from analizador.models import Licitacion, Miembro, Resultado, Revision, TrabajoProcesamiento
from analizador.models.enums import (
    EstadoCumplimiento,
    EstadoLicitacion,
    EstadoTrabajo,
    Rol,
    TipoRequisito,
    TipoTrabajo,
)
from analizador.pipeline import etapas
from analizador.worker import handlers
from tests.apoyo import PublicadorFalso, agregar_miembro, crear_organizacion
from tests.apoyo_analisis import Analisis, agregar_requisito, crear_analisis


async def _analisis_terminado(
    sesion: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> tuple[Analisis, Resultado, list[str]]:
    """Análisis cerrado con una brecha habilitante: la recomendación es no presentarse."""
    analisis = await crear_analisis(sesion)
    requisito = await agregar_requisito(
        sesion,
        analisis,
        numeral="3.2",
        texto="Índice de liquidez mayor o igual a 1,5",
        items=["Índice de liquidez"],
        tipo=TipoRequisito.HABILITANTE,
    )
    resultado = Resultado(
        item_id=requisito.items[0].id,
        estado=EstadoCumplimiento.NO_CUMPLE,
        faltante="Falta liquidez",
    )
    sesion.add(resultado)
    await sesion.commit()

    redacciones: list[str] = []
    monkeypatch.setattr(get_settings(), "anthropic_model_redaccion", "modelo-doble")

    async def generar(**kwargs: Any) -> tuple[JustificacionGenerada, Uso]:
        mensaje = kwargs["messages"][0]["content"]
        redacciones.append(mensaje)
        referencias = [1] if json.loads(mensaje)["referencias"] else []
        return JustificacionGenerada(
            texto="Explicación de la decisión.", referencias=referencias
        ), Uso()

    monkeypatch.setattr(anthropic_client, "generar_estructurado", generar)
    for etapa in (etapas.decidir, etapas.justificar, etapas.cerrar):
        await etapa(analisis.estado)
    licitacion = await sesion.get(Licitacion, analisis.licitacion_id)
    assert licitacion is not None and licitacion.recomendacion == "no_presentarse"
    return analisis, resultado, redacciones


async def _revisar(
    cliente: httpx.AsyncClient, cabeceras: dict[str, str], resultado: Resultado, **cuerpo: Any
) -> httpx.Response:
    datos = {"estado_nuevo": "cumple", "comentario": "Soporte verificado en el RUP 2025."}
    return await cliente.post(
        f"/api/resultados/{resultado.id}/revisiones", headers=cabeceras, json=datos | cuerpo
    )


# --- Criterio 1: nuevo estado y comentario obligatorio -------------------------------------


@pytest.mark.parametrize(
    "cuerpo",
    [
        {"comentario": ""},
        {"comentario": "   "},
        {"estado_nuevo": "revision"},
        {"estado_nuevo": "aprobado"},
    ],
)
async def test_el_estado_debe_ser_valido_y_el_comentario_obligatorio(
    cliente: httpx.AsyncClient,
    sesion: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    cuerpo: dict[str, str],
) -> None:
    analisis, resultado, _ = await _analisis_terminado(sesion, monkeypatch)

    respuesta = await _revisar(cliente, analisis.quien.cabeceras, resultado, **cuerpo)

    assert respuesta.status_code == 422
    assert list(await sesion.scalars(select(Revision))) == []


# --- Criterios 2 y 4: se guarda quién, cuándo, antes, después y por qué --------------------


async def test_la_revision_guarda_autor_fecha_estados_y_comentario(
    cliente: httpx.AsyncClient, sesion: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    analisis, resultado, _ = await _analisis_terminado(sesion, monkeypatch)
    revisor = await agregar_miembro(sesion, analisis.quien.organizacion_id, Rol.REVISOR)

    respuesta = await _revisar(
        cliente, revisor.cabeceras, resultado, comentario="  Soporte verificado.  "
    )

    assert respuesta.status_code == 201
    revision = respuesta.json()["revision"]
    miembro = await sesion.get(Miembro, revisor.miembro_id)
    assert miembro is not None
    assert revision["usuario_id"] == str(revisor.usuario_id)
    assert revision["autor_email"] == miembro.email
    assert revision["estado_anterior"] == "no_cumple"
    assert revision["estado_nuevo"] == "cumple"
    assert revision["comentario"] == "Soporte verificado."
    assert revision["creado_en"]
    sesion.expire_all()
    [guardada] = list(await sesion.scalars(select(Revision)))
    assert guardada.usuario_id == revisor.usuario_id


# --- Criterio 3: solo se recalculan brechas, recomendación y justificación -----------------


async def test_tras_la_revision_se_recalcula_la_recomendacion_sin_repetir_el_analisis(
    cliente: httpx.AsyncClient,
    sesion: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    publicador: PublicadorFalso,
) -> None:
    analisis, resultado, redacciones = await _analisis_terminado(sesion, monkeypatch)

    respuesta = await _revisar(cliente, analisis.quien.cabeceras, resultado)
    assert respuesta.status_code == 201
    [(tipo, trabajo_id)] = publicador.publicados
    assert tipo == TipoTrabajo.REEVALUACION

    trabajo = await sesion.get(TrabajoProcesamiento, trabajo_id)
    assert trabajo is not None

    async def no_debe_ejecutarse(*_: Any, **__: Any) -> None:
        raise AssertionError("El recálculo no debe repetir extracción ni verificación")

    for etapa in ("leer_pdf", "extraer", "verificar", "recuperar_evidencia", "validar_citas"):
        monkeypatch.setattr(etapas, etapa, no_debe_ejecutarse)
    await handlers.HANDLERS[TipoTrabajo.REEVALUACION](trabajo, None)

    sesion.expire_all()
    licitacion = await sesion.get(Licitacion, analisis.licitacion_id)
    assert licitacion is not None
    assert licitacion.recomendacion == "presentarse"
    assert licitacion.estado == EstadoLicitacion.COMPLETADA
    assert len(redacciones) == 2  # la justificación original y la del recálculo


async def test_un_recalculo_fallido_se_puede_reintentar(
    cliente: httpx.AsyncClient,
    sesion: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    publicador: PublicadorFalso,
) -> None:
    analisis, resultado, _ = await _analisis_terminado(sesion, monkeypatch)
    await _revisar(cliente, analisis.quien.cabeceras, resultado)
    [(_, trabajo_id)] = publicador.publicados
    trabajo = await sesion.get(TrabajoProcesamiento, trabajo_id)
    assert trabajo is not None
    trabajo.estado = EstadoTrabajo.FALLIDO
    await sesion.commit()

    respuesta = await cliente.post(
        f"/api/licitaciones/{analisis.licitacion_id}/reintentar",
        headers=analisis.quien.cabeceras,
    )

    assert respuesta.status_code == 202
    assert respuesta.json()["tipo"] == "reevaluacion"
    assert publicador.publicados[-1] == (TipoTrabajo.REEVALUACION, trabajo_id)


async def test_no_se_revisa_un_analisis_que_no_ha_terminado(
    cliente: httpx.AsyncClient, sesion: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    analisis, resultado, _ = await _analisis_terminado(sesion, monkeypatch)
    licitacion = await sesion.get(Licitacion, analisis.licitacion_id)
    assert licitacion is not None
    licitacion.estado = EstadoLicitacion.PROCESANDO
    await sesion.commit()

    respuesta = await _revisar(cliente, analisis.quien.cabeceras, resultado)

    assert respuesta.status_code == 409


# --- Criterio 4: el ítem revisado muestra la persona que lo cambió -------------------------


async def test_la_matriz_muestra_quien_reviso_cada_item(
    cliente: httpx.AsyncClient,
    sesion: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    publicador: PublicadorFalso,
) -> None:
    analisis, resultado, _ = await _analisis_terminado(sesion, monkeypatch)
    await _revisar(cliente, analisis.quien.cabeceras, resultado)
    [(_, trabajo_id)] = publicador.publicados
    trabajo = await sesion.get(TrabajoProcesamiento, trabajo_id)
    assert trabajo is not None
    await handlers.HANDLERS[TipoTrabajo.REEVALUACION](trabajo, None)
    trabajo.estado = EstadoTrabajo.COMPLETADO
    await sesion.commit()

    matriz = await cliente.get(
        f"/api/licitaciones/{analisis.licitacion_id}/resultado",
        headers=analisis.quien.cabeceras,
    )

    assert matriz.status_code == 200
    [item] = matriz.json()["items"]
    [revision] = item["revisiones"]
    miembro = await sesion.get(Miembro, analisis.quien.miembro_id)
    assert miembro is not None
    assert revision["autor_email"] == miembro.email
    assert item["estado"] == "cumple"


# --- Criterio 5: solo miembros de la organización de la licitación -------------------------


async def test_solo_revisan_miembros_de_la_organizacion(
    cliente: httpx.AsyncClient, sesion: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, resultado, _ = await _analisis_terminado(sesion, monkeypatch)
    ajeno = await crear_organizacion(sesion, "Otra")

    respuesta = await _revisar(cliente, ajeno.cabeceras, resultado)

    assert respuesta.status_code == 404
    assert list(await sesion.scalars(select(Revision))) == []
