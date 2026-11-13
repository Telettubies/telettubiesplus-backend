"""HU-B15: localizar cada requisito en el PDF (RF-26, P-21, R-17).

Las zonas calculadas se comparan con la posición real del texto en un PDF generado en la
prueba, obtenida de forma independiente con la búsqueda de PyMuPDF.
"""

from typing import Any

import pymupdf
import pytest

from analizador.models.enums import PrecisionUbicacion
from analizador.pipeline.localizacion import (
    Palabra,
    localizar,
    palabras_de_ocr,
    palabras_digitales,
)
from tests.apoyo_pdf import PaginaDePrueba, Tabla, crear_pdf

LIQUIDEZ = "El proponente deberá acreditar un índice de liquidez mayor o igual a 1,5."
EXPERIENCIA_L1 = "El proponente deberá acreditar experiencia en mínimo tres contratos de"
EXPERIENCIA_L2 = "desarrollo de software ejecutados en los últimos cinco años."
PDF = crear_pdf(
    [
        ["1. OBJETO", "Contratar el desarrollo de un sistema de gestión documental."],
        PaginaDePrueba(
            [
                "2. REQUISITOS HABILITANTES",
                LIQUIDEZ,
                "Texto intermedio que no es un requisito.",
                EXPERIENCIA_L1,
                EXPERIENCIA_L2,
                Tabla([["Perfil", "Experiencia mínima"], ["Director de proyecto", "Ocho años"]]),
                "El plazo de ejecución será de ocho meses contados desde",
            ]
        ),
        ["el acta de inicio suscrita por las partes.", "3. GARANTÍAS", "Póliza de cumplimiento."],
    ]
)


@pytest.fixture(scope="module")
def palabras() -> dict[int, list[Palabra]]:
    return palabras_digitales(PDF, {1, 2, 3})


def _de(palabras: dict[int, list[Palabra]], *paginas: int) -> list[Palabra]:
    return [p for numero in paginas for p in palabras[numero]]


def _caja_real(pagina: int, texto: str) -> tuple[float, float, float, float]:
    """Caja relativa del texto según la búsqueda de PyMuPDF (referencia independiente)."""
    documento: Any = pymupdf.open(stream=PDF, filetype="pdf")  # type: ignore[no-untyped-call]
    hoja = documento[pagina - 1]
    [caja] = hoja.search_for(texto)
    ancho, alto = hoja.rect.width, hoja.rect.height
    return caja.x0 / ancho, caja.y0 / alto, caja.x1 / ancho, caja.y1 / alto


def _coincide(zona: dict[str, float | int], real: tuple[float, float, float, float]) -> bool:
    tolerancia = 0.01  # un 1 % de la página
    return all(
        abs(float(zona[clave]) - valor) < tolerancia
        for clave, valor in zip(("x0", "y0", "x1", "y1"), real, strict=True)
    )


# --- Criterio 1: texto literal encontrado → página y zonas en coordenadas relativas ----------


def test_un_texto_literal_se_localiza_en_su_pagina_con_la_zona_exacta(
    palabras: dict[int, list[Palabra]],
) -> None:
    ubicacion = localizar(LIQUIDEZ, _de(palabras, 2), pagina_seccion=2)

    assert ubicacion.precision == PrecisionUbicacion.EXACTA
    assert ubicacion.pagina == 2
    [zona] = ubicacion.zonas
    assert zona["pagina"] == 2
    assert _coincide(zona, _caja_real(2, LIQUIDEZ))


def test_las_coordenadas_son_relativas_a_la_pagina(palabras: dict[int, list[Palabra]]) -> None:
    ubicacion = localizar(LIQUIDEZ, _de(palabras, 2), pagina_seccion=2)

    for zona in ubicacion.zonas:
        assert 0 <= zona["x0"] < zona["x1"] <= 1
        assert 0 <= zona["y0"] < zona["y1"] <= 1


def test_un_texto_de_varias_lineas_da_una_zona_por_linea(
    palabras: dict[int, list[Palabra]],
) -> None:
    ubicacion = localizar(f"{EXPERIENCIA_L1} {EXPERIENCIA_L2}", _de(palabras, 2), pagina_seccion=2)

    assert ubicacion.precision == PrecisionUbicacion.EXACTA
    primera, segunda = ubicacion.zonas
    assert _coincide(primera, _caja_real(2, EXPERIENCIA_L1))
    assert _coincide(segunda, _caja_real(2, EXPERIENCIA_L2))
    assert segunda["y0"] > primera["y0"]


def test_un_texto_que_cruza_de_pagina_lleva_zonas_en_las_dos(
    palabras: dict[int, list[Palabra]],
) -> None:
    literal = (
        "El plazo de ejecución será de ocho meses contados desde el acta de inicio suscrita "
        "por las partes."
    )

    ubicacion = localizar(literal, _de(palabras, 2, 3), pagina_seccion=2)

    assert ubicacion.precision == PrecisionUbicacion.EXACTA
    assert ubicacion.pagina == 2  # donde empieza
    assert [zona["pagina"] for zona in ubicacion.zonas] == [2, 3]
    assert _coincide(
        ubicacion.zonas[1], _caja_real(3, "el acta de inicio suscrita por las partes.")
    )


def test_se_busca_solo_en_las_paginas_de_la_seccion(palabras: dict[int, list[Palabra]]) -> None:
    """El mismo texto en otra página del pliego no cuenta."""
    ubicacion = localizar(LIQUIDEZ, _de(palabras, 1), pagina_seccion=1)
    assert ubicacion.precision == PrecisionUbicacion.SOLO_PAGINA


def test_las_diferencias_de_tildes_mayusculas_y_puntuacion_no_impiden_la_coincidencia(
    palabras: dict[int, list[Palabra]],
) -> None:
    literal = "el proponente DEBERA acreditar un indice de liquidez mayor o igual a 1.5"

    ubicacion = localizar(literal, _de(palabras, 2), pagina_seccion=2)

    assert ubicacion.precision == PrecisionUbicacion.EXACTA


def test_el_texto_de_una_celda_de_tabla_tambien_se_localiza(
    palabras: dict[int, list[Palabra]],
) -> None:
    ubicacion = localizar("Director de proyecto", _de(palabras, 2), pagina_seccion=2)

    assert ubicacion.precision == PrecisionUbicacion.EXACTA
    assert _coincide(ubicacion.zonas[0], _caja_real(2, "Director de proyecto"))


# --- Criterio 2: si no aparece completo, el comienzo → aproximada ----------------------------


def test_si_solo_coincide_el_comienzo_la_ubicacion_es_aproximada(
    palabras: dict[int, list[Palabra]],
) -> None:
    """El modelo reformuló el final del requisito."""
    reformulado = (
        "El proponente deberá acreditar un índice de liquidez superior o equivalente a "
        "uno coma cinco."
    )

    ubicacion = localizar(reformulado, _de(palabras, 2), pagina_seccion=2)

    assert ubicacion.precision == PrecisionUbicacion.APROXIMADA
    assert ubicacion.pagina == 2
    # Se resalta hasta donde coincide: el comienzo de la línea real.
    [zona] = ubicacion.zonas
    real = _caja_real(2, "El proponente deberá acreditar un índice de liquidez")
    assert _coincide(zona, real)


def test_entre_varios_comienzos_iguales_se_elige_el_que_mas_coincide(
    palabras: dict[int, list[Palabra]],
) -> None:
    """Dos requisitos empiezan igual ("El proponente deberá acreditar…")."""
    literal = "El proponente deberá acreditar experiencia en mínimo tres contratos de obra civil"

    ubicacion = localizar(literal, _de(palabras, 2), pagina_seccion=2)

    assert ubicacion.precision == PrecisionUbicacion.APROXIMADA
    assert abs(float(ubicacion.zonas[0]["y0"]) - _caja_real(2, EXPERIENCIA_L1)[1]) < 0.01


# --- Criterio 3: si tampoco, solo la página de la sección ------------------------------------


@pytest.mark.parametrize(
    "literal",
    [
        pytest.param("Un requisito que el modelo inventó y no está en el pliego.", id="inventado"),
        pytest.param("El proponente presentará su oferta en sobre cerrado.", id="comienzo-corto"),
        pytest.param("   ", id="vacio"),
    ],
)
def test_si_no_se_encuentra_se_guarda_solo_la_pagina_de_la_seccion(
    palabras: dict[int, list[Palabra]], literal: str
) -> None:
    ubicacion = localizar(literal, _de(palabras, 2, 3), pagina_seccion=2)

    assert ubicacion.precision == PrecisionUbicacion.SOLO_PAGINA
    assert ubicacion.pagina == 2 and ubicacion.zonas == []


def test_sin_palabras_en_la_pagina_solo_queda_la_pagina() -> None:
    """Página transcrita con visión o no leída: no hay cajas con las que resaltar."""
    ubicacion = localizar(LIQUIDEZ, [], pagina_seccion=7)
    assert (ubicacion.pagina, ubicacion.zonas, ubicacion.precision) == (
        7,
        [],
        PrecisionUbicacion.SOLO_PAGINA,
    )


# --- Criterio 4: en páginas escaneadas se usan las coordenadas del OCR -----------------------


def test_en_paginas_escaneadas_se_usan_las_cajas_del_ocr() -> None:
    cajas = [
        {"t": palabra, "x0": 0.10 + i * 0.08, "y0": 0.30, "x1": 0.17 + i * 0.08, "y1": 0.32}
        for i, palabra in enumerate(["Póliza", "de", "cumplimiento", "por", "el", "veinte"])
    ] + [
        {"t": palabra, "x0": 0.10 + i * 0.08, "y0": 0.34, "x1": 0.17 + i * 0.08, "y1": 0.36}
        for i, palabra in enumerate(["por", "ciento", "del", "valor"])
    ]

    ubicacion = localizar(
        "Póliza de cumplimiento por el veinte por ciento del valor",
        palabras_de_ocr(5, cajas),
        pagina_seccion=5,
    )

    assert ubicacion.precision == PrecisionUbicacion.EXACTA
    assert ubicacion.zonas == [
        {"pagina": 5, "x0": 0.1, "y0": 0.3, "x1": 0.57, "y1": 0.32},
        {"pagina": 5, "x0": 0.1, "y0": 0.34, "x1": 0.41, "y1": 0.36},
    ]


def test_las_palabras_de_una_pagina_inexistente_se_ignoran() -> None:
    assert palabras_digitales(PDF, {99}) == {}
    assert palabras_digitales(PDF, set()) == {}
