"""HU-B25: generar el borrador de una observación (RF-15, S-07, S-08)."""

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.contratos.observacion import BorradorGenerado
from analizador.core.config import get_settings
from analizador.integrations import anthropic_client
from analizador.integrations.anthropic_client import Uso
from analizador.models import EventoUso, Requisito
from analizador.models.enums import TipoUso
from tests.apoyo import crear_organizacion
from tests.apoyo_analisis import Analisis, agregar_requisito, crear_analisis

LITERAL = "El proponente deberá acreditar experiencia suficiente en proyectos similares."


@dataclass
class RedactorFalso:
    """Sustituye a Claude: devuelve un borrador fijo y guarda lo que recibe."""

    llamadas: list[dict[str, Any]] = field(default_factory=list)
    error: Exception | None = None

    async def __call__(self, **kwargs: Any) -> tuple[BorradorGenerado, Uso]:
        self.llamadas.append(kwargs)
        if self.error is not None:
            raise self.error
        return BorradorGenerado(
            asunto="Solicitud de aclaración sobre la experiencia exigida",
            cuerpo="Solicitamos precisar qué se entiende por experiencia suficiente.",
        ), Uso(entrada=10, salida=20)


@pytest.fixture
def redactor(base_de_datos: None, monkeypatch: pytest.MonkeyPatch) -> RedactorFalso:
    monkeypatch.setenv("ANTHROPIC_MODEL_REDACCION", "modelo-de-redaccion-de-prueba")
    get_settings.cache_clear()
    falso = RedactorFalso()
    monkeypatch.setattr(anthropic_client, "generar_estructurado", falso)
    return falso


async def _requisito(
    sesion: AsyncSession, *, ambiguo: bool = True, analisis: Analisis | None = None
) -> tuple[Analisis, Requisito]:
    analisis = analisis or await crear_analisis(sesion)
    requisito = await agregar_requisito(
        sesion,
        analisis,
        numeral="5.3.1",
        texto=LITERAL,
        items=["Experiencia suficiente"],
        ambiguo=ambiguo,
        motivo_ambiguedad="No define qué es experiencia suficiente.",
    )
    return analisis, requisito


async def _pedir(
    cliente: httpx.AsyncClient, analisis: Analisis, requisito_id: uuid.UUID
) -> httpx.Response:
    return await cliente.post(
        f"/api/licitaciones/{analisis.licitacion_id}/observaciones",
        headers=analisis.quien.cabeceras,
        json={"requisito_id": str(requisito_id)},
    )


# --- Criterio 1: borrador con numeral, asunto y texto formal para la entidad ---------------


async def test_un_requisito_ambiguo_produce_un_borrador_con_numeral_asunto_y_texto(
    cliente: httpx.AsyncClient, sesion: AsyncSession, redactor: RedactorFalso
) -> None:
    analisis, requisito = await _requisito(sesion)

    respuesta = await _pedir(cliente, analisis, requisito.id)

    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert datos["requisito_id"] == str(requisito.id)
    assert datos["numeral"] == "5.3.1"
    assert datos["asunto"] == "Solicitud de aclaración sobre la experiencia exigida"
    texto = datos["texto"]
    assert texto.startswith("Asunto: Solicitud de aclaración")
    assert "Respetada entidad:" in texto
    assert "numeral 5.3.1 del pliego" in texto
    assert "Solicitamos precisar" in texto
    assert texto.rstrip().endswith("[Nombre y firma del representante legal del proponente]")
    [llamada] = redactor.llamadas
    assert llamada["model"] == "modelo-de-redaccion-de-prueba"


async def test_solo_se_generan_para_requisitos_ambiguos(
    cliente: httpx.AsyncClient, sesion: AsyncSession, redactor: RedactorFalso
) -> None:
    analisis, requisito = await _requisito(sesion, ambiguo=False)

    respuesta = await _pedir(cliente, analisis, requisito.id)

    assert respuesta.status_code == 409
    assert redactor.llamadas == []


# --- Criterio 2: el borrador cita el texto literal del requisito ---------------------------


async def test_el_borrador_cita_el_texto_literal_y_el_numeral_los_pone_el_codigo(
    cliente: httpx.AsyncClient, sesion: AsyncSession, redactor: RedactorFalso
) -> None:
    analisis, requisito = await _requisito(sesion)

    texto = (await _pedir(cliente, analisis, requisito.id)).json()["texto"]

    assert f"«{LITERAL}»" in texto
    # El requisito llega al modelo como datos delimitados, separado de las instrucciones (S-07).
    [llamada] = redactor.llamadas
    assert LITERAL not in llamada["system"]
    contenido = llamada["messages"][0]["content"]
    assert contenido.startswith("<datos>")
    datos = json.loads(contenido.removeprefix("<datos>\n").removesuffix("\n</datos>"))
    assert datos["numeral"] == "5.3.1"
    assert datos["requisito"] == LITERAL


# --- Criterio 3: el sistema no envía nada a la entidad -------------------------------------


async def test_el_sistema_solo_entrega_el_texto(
    cliente: httpx.AsyncClient, sesion: AsyncSession, redactor: RedactorFalso
) -> None:
    analisis, requisito = await _requisito(sesion)

    respuesta = await _pedir(cliente, analisis, requisito.id)

    assert set(respuesta.json()) == {"requisito_id", "numeral", "asunto", "texto"}
    [evento] = list(await sesion.scalars(select(EventoUso)))
    assert evento.tipo == TipoUso.OBSERVACION


# --- Aislamiento, cuota y fallos del proveedor ---------------------------------------------


async def test_un_requisito_de_otra_licitacion_u_organizacion_responde_404(
    cliente: httpx.AsyncClient, sesion: AsyncSession, redactor: RedactorFalso
) -> None:
    analisis, _ = await _requisito(sesion)
    otra_licitacion = await crear_analisis(sesion, analisis.quien)
    _, requisito_ajeno = await _requisito(sesion, analisis=otra_licitacion)
    otra_organizacion, requisito_de_otra = await _requisito(
        sesion, analisis=await crear_analisis(sesion, await crear_organizacion(sesion, "Otra"))
    )

    assert (await _pedir(cliente, analisis, requisito_ajeno.id)).status_code == 404
    cruzada = await cliente.post(
        f"/api/licitaciones/{otra_organizacion.licitacion_id}/observaciones",
        headers=analisis.quien.cabeceras,
        json={"requisito_id": str(requisito_de_otra.id)},
    )
    assert cruzada.status_code == 404
    assert redactor.llamadas == []


async def test_al_superar_la_cuota_responde_429_sin_llamar_al_modelo(
    cliente: httpx.AsyncClient,
    sesion: AsyncSession,
    redactor: RedactorFalso,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CUOTA_OBSERVACIONES_DIARIA", "1")
    get_settings.cache_clear()
    analisis, requisito = await _requisito(sesion)

    assert (await _pedir(cliente, analisis, requisito.id)).status_code == 200
    segunda = await _pedir(cliente, analisis, requisito.id)

    assert segunda.status_code == 429
    assert "1 borradores de observación" in segunda.json()["detail"]
    assert len(redactor.llamadas) == 1


@pytest.mark.parametrize(
    ("error", "codigo"),
    [
        (anthropic_client.ModeloNoConfiguradoError("falta"), 503),
        (anthropic_client.RespuestaRechazadaError("rechazo"), 502),
    ],
)
async def test_un_fallo_del_proveedor_no_cuenta_para_la_cuota(
    cliente: httpx.AsyncClient,
    sesion: AsyncSession,
    redactor: RedactorFalso,
    error: Exception,
    codigo: int,
) -> None:
    analisis, requisito = await _requisito(sesion)
    redactor.error = error

    respuesta = await _pedir(cliente, analisis, requisito.id)

    assert respuesta.status_code == codigo
    assert str(error) not in respuesta.text
    assert await sesion.scalar(select(func.count()).select_from(EventoUso)) == 0
