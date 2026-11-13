"""HU-B24: reglas del chat que no necesitan base de datos (fragmentos y validación)."""

import json

import pytest

from analizador.api.chat import RespuestaInvalidaError, interpretar
from analizador.pipeline.indexacion_pliego import trozos


def test_los_trozos_respetan_el_maximo_y_no_pierden_texto() -> None:
    parrafos = ["Primer párrafo corto.", "x " * 400, "Último párrafo."]
    resultado = trozos("\n\n".join(parrafos), 300)

    assert all(len(trozo) <= 300 for trozo in resultado)
    assert "".join(resultado).replace("\n", "").replace(" ", "") == "".join(parrafos).replace(
        " ", ""
    )
    assert resultado[0] == "Primer párrafo corto."
    assert resultado[-1].endswith("Último párrafo.")


def test_un_texto_vacio_no_produce_trozos() -> None:
    assert trozos("  \n\n ", 300) == []


def _respuesta(**datos: object) -> str:
    return "Aquí está: " + json.dumps(datos, ensure_ascii=False)


def test_una_respuesta_valida_conserva_sus_numerales_en_orden_sin_repetir() -> None:
    datos = interpretar(
        _respuesta(encontrado=True, respuesta="Exige póliza.", numerales=["3.2", "1.4", "3.2"]),
        {"1.4", "3.2"},
    )
    assert datos.respuesta == "Exige póliza."
    assert datos.numerales == ["3.2", "1.4"]


@pytest.mark.parametrize(
    "texto",
    [
        "sin json",
        _respuesta(encontrado=True, respuesta="Exige póliza.", numerales=[]),
        _respuesta(encontrado=True, respuesta="Exige póliza.", numerales=["9.9"]),
        _respuesta(encontrado=True, respuesta="  ", numerales=["3.2"]),
        _respuesta(respuesta="Falta el campo encontrado", numerales=["3.2"]),
    ],
)
def test_una_respuesta_sin_citas_verificables_se_rechaza(texto: str) -> None:
    with pytest.raises(RespuestaInvalidaError):
        interpretar(texto, {"3.2"})


def test_sin_informacion_no_exige_numerales() -> None:
    datos = interpretar(_respuesta(encontrado=False, respuesta="", numerales=[]), {"3.2"})
    assert not datos.encontrado
