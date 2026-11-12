"""HU-B18: comparación de una cita con su fuente, tolerando diferencias menores (P-04)."""

import pytest

from analizador.pipeline.citas import normalizar, similitud, validar_evidencia

FRAGMENTO = (
    "Contrato C-2023-014 ejecutado para la Secretaría Distrital de Hacienda. Objeto: "
    "Desarrollo, implementación y soporte de un sistema de información para la gestión "
    "documental. Valor: $2.450.000.000 COP."
)
UMBRAL = 90


@pytest.mark.parametrize(
    "cita",
    [
        pytest.param(
            "soporte de un sistema de información para la gestión documental", id="literal"
        ),
        pytest.param(
            "SOPORTE DE UN SISTEMA DE INFORMACION PARA LA GESTION DOCUMENTAL", id="sin-tildes"
        ),
        pytest.param(
            "soporte  de un sistema\nde información   para la gestión documental", id="espacios"
        ),
        pytest.param("Desarrollo implementación y soporte de un sistema", id="puntuacion"),
        pytest.param("«Valor: $2.450.000.000 COP»", id="comillas-y-simbolos"),
        pytest.param("soporte de un sistema de informacion para la gestion documentl", id="errata"),
    ],
)
def test_diferencias_menores_no_impiden_verificar_la_cita(cita: str) -> None:
    assert similitud(cita, FRAGMENTO) >= UMBRAL


@pytest.mark.parametrize(
    "cita",
    [
        pytest.param("La empresa cuenta con certificación PMP vigente hasta 2028", id="inventada"),
        pytest.param("desarrollo de una plataforma de analítica de datos", id="reformulada"),
        pytest.param("Valor: $9.990.000.000 COP", id="cifra-cambiada"),
        pytest.param("", id="vacia"),
        pytest.param("   ", id="en-blanco"),
    ],
)
def test_una_cita_que_no_esta_en_la_fuente_no_se_verifica(cita: str) -> None:
    assert similitud(cita, FRAGMENTO) < UMBRAL


def test_anadir_texto_inventado_a_un_fragmento_real_no_cuenta_como_coincidencia() -> None:
    """La cita contiene el fragmento entero y además algo que el fragmento no dice."""
    cita = FRAGMENTO + " Además, la empresa acredita veinte años de experiencia certificada."
    assert similitud(cita, FRAGMENTO) < UMBRAL


def test_sin_fuente_la_similitud_es_cero() -> None:
    assert similitud("cualquier cita", "") == 0.0


def test_normalizar() -> None:
    assert normalizar("  Índice de   LIQUIDEZ: 1,5 %\n") == "indice de liquidez 1 5"
    assert normalizar("«Año»—ñandú") == "ano nandu"


def test_validar_evidencia_marca_cada_cita_y_no_toca_lo_ya_verificado() -> None:
    evidencia = [
        {"tipo": "cita", "texto": FRAGMENTO, "cita": "gestión documental", "verificada": None},
        {
            "tipo": "cita",
            "texto": FRAGMENTO,
            "cita": "certificación PMP vigente",
            "verificada": None,
        },
        {"tipo": "dato", "texto": "Índice de liquidez de 2025: 2,35", "verificada": True},
        {"tipo": "cita", "texto": "otro", "cita": "no coincide", "verificada": True},
    ]

    validada, alguna_falla = validar_evidencia(evidencia, UMBRAL)

    assert [e["verificada"] for e in validada] == [True, False, True, True]
    assert alguna_falla is True
    assert validada[0]["similitud"] == 100.0 and validada[1]["similitud"] < UMBRAL
    assert "similitud" not in validada[2] and "similitud" not in validada[3]
    assert evidencia[0]["verificada"] is None  # no modifica la lista recibida


def test_el_umbral_es_el_que_decide() -> None:
    evidencia = [
        {
            "tipo": "cita",
            "texto": FRAGMENTO,
            "cita": "una plataforma de información para la gestión",
            "verificada": None,
        }
    ]

    estricta, falla_estricta = validar_evidencia(evidencia, 95)
    laxa, falla_laxa = validar_evidencia(evidencia, 60)

    assert falla_estricta and estricta[0]["verificada"] is False
    assert not falla_laxa and laxa[0]["verificada"] is True
