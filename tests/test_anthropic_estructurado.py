"""Llamada a Claude con salida estructurada: caché de prompt, consumo y respuestas no válidas."""

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from analizador.integrations import anthropic_client
from analizador.integrations.anthropic_client import RespuestaRechazadaError, Uso


class _Salida(BaseModel):
    valor: int


class _Mensajes:
    def __init__(self, respuesta: Any) -> None:
        self.respuesta = respuesta
        self.peticiones: list[dict[str, Any]] = []

    async def parse(self, **kwargs: Any) -> Any:
        self.peticiones.append(kwargs)
        return self.respuesta


def _respuesta(stop_reason: str = "end_turn", salida: Any = None) -> Any:
    uso = SimpleNamespace(
        input_tokens=120,
        output_tokens=40,
        cache_read_input_tokens=900,
        cache_creation_input_tokens=None,
    )
    return SimpleNamespace(stop_reason=stop_reason, parsed_output=salida, usage=uso)


def _instalar(monkeypatch: pytest.MonkeyPatch, respuesta: Any) -> _Mensajes:
    mensajes = _Mensajes(respuesta)
    monkeypatch.setattr(anthropic_client, "get_client", lambda: SimpleNamespace(messages=mensajes))
    return mensajes


async def _llamar() -> tuple[_Salida, Uso]:
    return await anthropic_client.generar_estructurado(
        model="modelo",
        system="instrucciones fijas",
        messages=[{"role": "user", "content": "<seccion>documento</seccion>"}],
        output_format=_Salida,
    )


async def test_devuelve_la_salida_validada_y_el_consumo(monkeypatch: pytest.MonkeyPatch) -> None:
    _instalar(monkeypatch, _respuesta(salida=_Salida(valor=7)))

    salida, uso = await _llamar()

    assert salida == _Salida(valor=7)
    assert uso == Uso(entrada=120, salida=40, cache_leidos=900, cache_escritos=0)


async def test_las_instrucciones_van_como_bloque_cacheable_y_el_documento_aparte(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mensajes = _instalar(monkeypatch, _respuesta(salida=_Salida(valor=1)))

    await _llamar()

    [peticion] = mensajes.peticiones
    assert peticion["system"] == [
        {"type": "text", "text": "instrucciones fijas", "cache_control": {"type": "ephemeral"}}
    ]
    assert peticion["messages"] == [{"role": "user", "content": "<seccion>documento</seccion>"}]
    assert peticion["output_format"] is _Salida


@pytest.mark.parametrize(
    "respuesta",
    [
        pytest.param(_respuesta("refusal", _Salida(valor=1)), id="rechazo"),
        pytest.param(_respuesta("max_tokens", _Salida(valor=1)), id="truncada"),
        pytest.param(_respuesta("end_turn", None), id="no-cumple-el-esquema"),
    ],
)
async def test_una_respuesta_no_utilizable_es_un_error(
    monkeypatch: pytest.MonkeyPatch, respuesta: Any
) -> None:
    _instalar(monkeypatch, respuesta)
    with pytest.raises(RespuestaRechazadaError):
        await _llamar()


def test_el_consumo_se_puede_acumular() -> None:
    total = Uso(entrada=10, salida=2) + Uso(entrada=5, salida=1, cache_leidos=30)
    assert total.como_dict() == {
        "entrada": 15,
        "salida": 3,
        "cache_leidos": 30,
        "cache_escritos": 0,
    }
