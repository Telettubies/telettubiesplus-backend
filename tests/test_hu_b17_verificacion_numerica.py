"""HU-B17: verificación numérica en código, con casos límite (P-03, Q-12)."""

import uuid
from typing import Any

import pytest

from analizador.contratos.extraccion import Operador
from analizador.models.enums import EstadoCumplimiento
from analizador.pipeline.verificacion_numerica import (
    DatosEmpresa,
    Registro,
    comparar,
    numero,
    prefijo_unspsc,
    verificar,
)

SMMLV = 1_300_000


def _financiero(anio: int = 2025, **cambios: Any) -> Registro:
    datos = {
        "anio": anio,
        "indice_liquidez": 1.5,
        "indice_endeudamiento": 0.7,
        "razon_cobertura_intereses": 3.0,
        "capital_trabajo_cop": 1_300_000_000,
        "patrimonio_cop": 4_000_000_000,
        "rentabilidad_patrimonio": 0.12,
        "rentabilidad_activo": 0.08,
    }
    datos.update(cambios)
    return Registro(uuid.uuid4(), f"Indicadores financieros {anio}", datos)


def _contrato(
    identificador: str,
    valor_cop: int,
    codigos: list[str],
    valor_smmlv: float | None = None,
    participacion: float = 100,
) -> Registro:
    return Registro(
        uuid.uuid4(),
        f"{identificador} · contrato",
        {
            "id": identificador,
            "objeto": f"Objeto del contrato {identificador}",
            "valor_cop": valor_cop,
            "valor_smmlv": valor_smmlv,
            "codigos_unspsc": codigos,
            "porcentaje_participacion": participacion,
        },
    )


EMPRESA = DatosEmpresa(
    financiero=[_financiero(2024, indice_liquidez=0.9), _financiero(2025)],
    contratos=[
        _contrato("C-1", 1_300_000_000, ["81111500", "81112200"], valor_smmlv=1000),
        _contrato("C-2", 1_300_000_000, ["81111800"], valor_smmlv=1000, participacion=50),
        _contrato("C-3", 650_000_000, ["80101500"]),  # sin valor en SMMLV: 500 con el SMMLV
    ],
)


def _item(
    campo: str, operador: str, valor: float | None, unidad: str, **extra: Any
) -> dict[str, Any]:
    return {"campo": campo, "operador": operador, "valor": valor, "unidad": unidad, **extra}


# --- Comparaciones: igual al mínimo, justo por debajo, justo por encima ---------------------


@pytest.mark.parametrize(
    ("real", "operador", "exigido", "esperado"),
    [
        (1.5, Operador.MAYOR_O_IGUAL, 1.5, True),  # igual al mínimo
        (1.4999, Operador.MAYOR_O_IGUAL, 1.5, False),  # justo por debajo
        (1.5001, Operador.MAYOR_O_IGUAL, 1.5, True),
        (0.7, Operador.MENOR_O_IGUAL, 0.7, True),  # igual al máximo
        (0.7001, Operador.MENOR_O_IGUAL, 0.7, False),  # justo por encima
        (1.5, Operador.MAYOR, 1.5, False),  # estricto: igual no basta
        (1.5001, Operador.MAYOR, 1.5, True),
        (0.7, Operador.MENOR, 0.7, False),
        (0.6999, Operador.MENOR, 0.7, True),
        (3.0, Operador.IGUAL, 3.0, True),
        (3.0001, Operador.IGUAL, 3.0, False),
        (0.1 + 0.2, Operador.MAYOR_O_IGUAL, 0.3, True),  # ruido de coma flotante
        (0.1 + 0.2, Operador.MAYOR, 0.3, False),
        (0.1 + 0.2, Operador.IGUAL, 0.3, True),
    ],
)
def test_comparar_con_casos_limite(
    real: float, operador: Operador, exigido: float, esperado: bool
) -> None:
    assert comparar(real, operador, exigido) is esperado


# --- Indicadores financieros ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("exigido", "estado"),
    [
        (1.5, EstadoCumplimiento.CUMPLE),  # igual al mínimo
        (1.51, EstadoCumplimiento.NO_CUMPLE),  # justo por debajo de lo exigido
        (1.2, EstadoCumplimiento.CUMPLE),
    ],
)
def test_indice_de_liquidez(exigido: float, estado: EstadoCumplimiento) -> None:
    veredicto = verificar(_item("indice_liquidez", ">=", exigido, "razon"), EMPRESA)

    assert veredicto.estado == estado
    assert not veredicto.requiere_revision
    # La evidencia es el dato exacto usado, del último año cargado (2025, no 2024).
    [dato] = veredicto.evidencia
    assert dato["texto"] == "Índice de liquidez de 2025: 1,5"
    assert dato["fuente"] == "Indicadores financieros 2025" and dato["verificada"] is True
    assert veredicto.regla == f"numerico: indice_liquidez >= {exigido} (razon)"


def test_un_no_cumple_dice_que_falta_con_las_dos_cifras() -> None:
    veredicto = verificar(_item("indice_liquidez", ">=", 2.0, "razon"), EMPRESA)

    assert veredicto.estado == EstadoCumplimiento.NO_CUMPLE
    assert veredicto.faltante == "El índice de liquidez de 2025 es 1,5 y se exige ≥ 2."


@pytest.mark.parametrize(
    ("valor", "unidad", "estado"),
    [
        (70, "porcentaje", EstadoCumplimiento.CUMPLE),  # 70 % exigido, 0,70 real: igual al máximo
        (69.9, "porcentaje", EstadoCumplimiento.NO_CUMPLE),
        (0.7, "razon", EstadoCumplimiento.CUMPLE),  # el pliego lo expresa como razón
        (0.69, "razon", EstadoCumplimiento.NO_CUMPLE),
    ],
)
def test_el_endeudamiento_se_compara_en_porcentaje_o_en_razon(
    valor: float, unidad: str, estado: EstadoCumplimiento
) -> None:
    veredicto = verificar(_item("indice_endeudamiento", "<=", valor, unidad), EMPRESA)

    assert veredicto.estado == estado
    assert veredicto.evidencia[0]["texto"] == "Índice de endeudamiento de 2025: 70 %"


@pytest.mark.parametrize(
    ("valor", "unidad", "estado"),
    [
        (1_300_000_000, "cop", EstadoCumplimiento.CUMPLE),  # igual al mínimo, en pesos
        (1_300_000_001, "cop", EstadoCumplimiento.NO_CUMPLE),
        (1000, "smmlv", EstadoCumplimiento.CUMPLE),  # 1000 SMMLV = 1.300 millones
        (1000.01, "smmlv", EstadoCumplimiento.NO_CUMPLE),
    ],
)
def test_los_valores_en_smmlv_se_convierten_con_el_valor_configurado(
    valor: float, unidad: str, estado: EstadoCumplimiento
) -> None:
    veredicto = verificar(_item("capital_trabajo", ">=", valor, unidad), EMPRESA, SMMLV)

    assert veredicto.estado == estado
    assert veredicto.evidencia[0]["texto"] == "Capital de trabajo de 2025: $1.300.000.000 COP"


def test_sin_smmlv_configurado_un_valor_en_smmlv_pasa_a_revision() -> None:
    veredicto = verificar(_item("patrimonio", ">=", 1000, "smmlv"), EMPRESA, smmlv_cop=None)

    assert veredicto.requiere_revision
    assert veredicto.estado != EstadoCumplimiento.CUMPLE  # nunca favorable por defecto
    assert "SMMLV" in (veredicto.motivo_revision or "")


def test_una_unidad_que_no_corresponde_al_campo_pasa_a_revision() -> None:
    veredicto = verificar(_item("indice_liquidez", ">=", 150, "porcentaje"), EMPRESA)

    assert veredicto.requiere_revision and veredicto.estado == EstadoCumplimiento.NO_CUMPLE
    assert "no corresponde" in (veredicto.motivo_revision or "")


def test_la_cobertura_de_intereses_indeterminada_pasa_a_revision() -> None:
    empresa = DatosEmpresa(financiero=[_financiero(razon_cobertura_intereses=None)])

    veredicto = verificar(_item("razon_cobertura_intereses", ">=", 2, "razon"), empresa)

    assert veredicto.requiere_revision
    assert "indeterminada" in (veredicto.motivo_revision or "")


def test_sin_datos_financieros_no_cumple_y_dice_por_que() -> None:
    veredicto = verificar(_item("indice_liquidez", ">=", 1.5, "razon"), DatosEmpresa())

    assert veredicto.estado == EstadoCumplimiento.NO_CUMPLE and not veredicto.requiere_revision
    assert veredicto.faltante == "La base de conocimiento no tiene indicadores financieros."


# --- Experiencia ----------------------------------------------------------------------------


def test_la_experiencia_en_smmlv_suma_los_contratos_del_codigo_exigido_por_participacion() -> None:
    # C-1 (1000 SMMLV) y C-2 (1000 × 50 %) están en la familia 8111; C-3 no.
    veredicto = verificar(
        _item("experiencia_valor", ">=", 1500, "smmlv"),
        EMPRESA,
        SMMLV,
        codigos_del_requisito=["81110000"],
    )

    assert veredicto.estado == EstadoCumplimiento.CUMPLE and not veredicto.requiere_revision
    assert [d["texto"] for d in veredicto.evidencia] == [
        "Contrato C-1: 1.000 SMMLV (participación 100 %)",
        "Contrato C-2: 500 SMMLV (participación 50 %)",
    ]
    assert "acreditado: 1.500 SMMLV en 2 contratos" in veredicto.regla


def test_la_experiencia_justo_por_debajo_no_cumple() -> None:
    veredicto = verificar(
        _item("experiencia_valor", ">=", 1500.01, "smmlv"),
        EMPRESA,
        SMMLV,
        codigos_del_requisito=["81110000"],
    )

    assert veredicto.estado == EstadoCumplimiento.NO_CUMPLE
    assert veredicto.faltante is not None and "se exige ≥ 1.500,01 SMMLV" in veredicto.faltante


def test_un_contrato_sin_valor_en_smmlv_se_convierte_con_el_smmlv_configurado() -> None:
    veredicto = verificar(
        _item("experiencia_valor", ">=", 500, "smmlv"),
        EMPRESA,
        SMMLV,
        codigos_del_requisito=["80101500"],
    )

    assert veredicto.estado == EstadoCumplimiento.CUMPLE
    assert veredicto.evidencia[0]["texto"] == "Contrato C-3: 500 SMMLV (participación 100 %)"


def test_sin_codigo_unspsc_en_el_requisito_la_experiencia_que_alcanza_pasa_a_revision() -> None:
    """Sumar todos los contratos es un máximo: no se da por cumplido sin confirmarlo."""
    veredicto = verificar(_item("experiencia_valor", ">=", 1500, "smmlv"), EMPRESA, SMMLV)

    assert veredicto.estado == EstadoCumplimiento.CUMPLE
    assert veredicto.requiere_revision
    assert "pertinentes" in (veredicto.motivo_revision or "")
    assert len(veredicto.evidencia) == 3


def test_si_ni_sumando_todos_los_contratos_se_alcanza_el_no_cumple_es_seguro() -> None:
    veredicto = verificar(_item("experiencia_valor", ">=", 5000, "smmlv"), EMPRESA, SMMLV)

    assert veredicto.estado == EstadoCumplimiento.NO_CUMPLE and not veredicto.requiere_revision
    assert veredicto.faltante is not None and "2.000 SMMLV en 3 contratos" in veredicto.faltante


def test_la_experiencia_en_pesos_usa_el_valor_del_contrato() -> None:
    veredicto = verificar(
        _item("experiencia_valor", ">=", 1_950_000_000, "cop"),
        EMPRESA,
        codigos_del_requisito=["8111"],
    )

    assert veredicto.estado == EstadoCumplimiento.CUMPLE  # 1.300 + 650 millones: igual al mínimo


@pytest.mark.parametrize(
    ("exigidos", "estado"), [(2, EstadoCumplimiento.CUMPLE), (3, EstadoCumplimiento.NO_CUMPLE)]
)
def test_numero_de_contratos_con_el_codigo_exigido(
    exigidos: int, estado: EstadoCumplimiento
) -> None:
    veredicto = verificar(
        _item("experiencia_contratos", ">=", exigidos, "contratos"),
        EMPRESA,
        codigos_del_requisito=["81110000"],
    )

    assert veredicto.estado == estado
    assert len(veredicto.evidencia) == 2


def test_sin_contratos_no_cumple() -> None:
    veredicto = verificar(_item("experiencia_contratos", ">=", 1, "contratos"), DatosEmpresa())

    assert veredicto.estado == EstadoCumplimiento.NO_CUMPLE
    assert veredicto.faltante == "La base de conocimiento no tiene contratos ejecutados."


# --- Códigos UNSPSC -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("codigo", "estado", "contratos"),
    [
        ("81111500", EstadoCumplimiento.CUMPLE, ["C-1"]),  # clase exacta
        ("81110000", EstadoCumplimiento.CUMPLE, ["C-1", "C-2"]),  # familia
        ("81000000", EstadoCumplimiento.CUMPLE, ["C-1", "C-2"]),  # segmento
        ("43230000", EstadoCumplimiento.NO_CUMPLE, []),
    ],
)
def test_el_codigo_unspsc_se_compara_por_nivel(
    codigo: str, estado: EstadoCumplimiento, contratos: list[str]
) -> None:
    veredicto = verificar(_item("codigo_unspsc", "==", None, "codigo", valor_texto=codigo), EMPRESA)

    assert veredicto.estado == estado
    assert [d["texto"].split(":")[0] for d in veredicto.evidencia] == [
        f"Contrato {c}" for c in contratos
    ]
    if estado == EstadoCumplimiento.NO_CUMPLE:
        assert veredicto.faltante is not None and codigo in veredicto.faltante


def test_prefijo_unspsc() -> None:
    assert prefijo_unspsc("81111500") == "811115"
    assert prefijo_unspsc("81110000") == "8111"
    assert prefijo_unspsc("81000000") == "81"
    assert prefijo_unspsc("81111503") == "81111503"
    assert prefijo_unspsc("8111-1500") == "811115"


# --- Entradas que no se pueden comparar: revisión, nunca un estado favorable ----------------


@pytest.mark.parametrize(
    "parametros",
    [
        pytest.param(
            {"campo": "utilidad_neta", "operador": ">=", "valor": 1}, id="campo-desconocido"
        ),
        pytest.param({"campo": "indice_liquidez", "operador": "aprox", "valor": 1}, id="operador"),
        pytest.param({"operador": ">=", "valor": 1}, id="sin-campo"),
        pytest.param(_item("indice_liquidez", ">=", None, "razon"), id="sin-valor"),
        pytest.param(_item("codigo_unspsc", "==", None, "codigo", valor_texto=""), id="sin-codigo"),
    ],
)
def test_parametros_inutilizables_pasan_a_revision(parametros: dict[str, Any]) -> None:
    veredicto = verificar(parametros, EMPRESA, SMMLV)

    assert veredicto.requiere_revision and veredicto.motivo_revision
    assert veredicto.estado == EstadoCumplimiento.NO_CUMPLE


def test_todo_estado_distinto_de_cumple_lleva_faltante_y_todo_cumple_lleva_evidencia() -> None:
    casos = [
        _item("indice_liquidez", ">=", 1.5, "razon"),
        _item("indice_liquidez", ">=", 9, "razon"),
        _item("indice_endeudamiento", "<=", 10, "porcentaje"),
        _item("experiencia_valor", ">=", 100, "smmlv"),
        _item("experiencia_valor", ">=", 99999, "smmlv"),
        _item("experiencia_contratos", ">=", 1, "contratos"),
        _item("codigo_unspsc", "==", None, "codigo", valor_texto="81111500"),
        _item("codigo_unspsc", "==", None, "codigo", valor_texto="10000000"),
        _item("patrimonio", ">=", 1, "razon"),
    ]
    for parametros in casos:
        veredicto = verificar(parametros, EMPRESA, SMMLV, ["81110000"])
        if veredicto.estado == EstadoCumplimiento.CUMPLE:
            assert veredicto.evidencia, parametros
        else:
            assert veredicto.faltante, parametros
        assert veredicto.regla.startswith("numerico:")


def test_formato_de_numeros() -> None:
    assert numero(2112.07) == "2.112,07"
    assert numero(1500.0) == "1.500"
    assert numero(1.5) == "1,5"
    assert numero(1300000000, 0) == "1.300.000.000"
    assert numero(70.0) == "70"
