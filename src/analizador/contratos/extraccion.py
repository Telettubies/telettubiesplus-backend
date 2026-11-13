"""Contrato de la extracción de requisitos de una sección del pliego (RF-01, RF-02, RF-07, P-18).

Es la forma que debe tener la respuesta del modelo de extracción (HU-B13). Se mantiene sin
restricciones de longitud ni de rango para que sea válido como esquema de salida estructurada;
las reglas de negocio se aplican después, en código.

Taxonomía: la del reto (RF-01). Q-16 sigue abierta en el DAS.
"""

from enum import StrEnum

from pydantic import BaseModel, Field

from analizador.models.enums import TipoExigibilidad, TipoRequisito, TipoVerificacion

__all__ = [
    "CampoComparable",
    "ExtraccionSeccion",
    "ItemExtraido",
    "Operador",
    "ParametrosNumericos",
    "RequisitoExtraido",
    "TipoExigibilidad",
    "TipoVerificacion",
    "Unidad",
]


class CampoComparable(StrEnum):
    """Datos de la base de conocimiento que se pueden comparar por código (P-03).

    Conjunto cerrado: lo que no encaje aquí se verifica como cualitativo.
    """

    INDICE_LIQUIDEZ = "indice_liquidez"
    INDICE_ENDEUDAMIENTO = "indice_endeudamiento"
    RAZON_COBERTURA_INTERESES = "razon_cobertura_intereses"
    CAPITAL_TRABAJO = "capital_trabajo"
    PATRIMONIO = "patrimonio"
    RENTABILIDAD_PATRIMONIO = "rentabilidad_patrimonio"
    RENTABILIDAD_ACTIVO = "rentabilidad_activo"
    # Suma del valor de los contratos ejecutados que acreditan experiencia.
    EXPERIENCIA_VALOR = "experiencia_valor"
    # Número de contratos ejecutados que acreditan experiencia.
    EXPERIENCIA_CONTRATOS = "experiencia_contratos"
    # Experiencia clasificada en un código UNSPSC (va en `valor_texto`).
    CODIGO_UNSPSC = "codigo_unspsc"


class Operador(StrEnum):
    MAYOR_O_IGUAL = ">="
    MENOR_O_IGUAL = "<="
    MAYOR = ">"
    MENOR = "<"
    IGUAL = "=="


class Unidad(StrEnum):
    RAZON = "razon"  # veces: 1.5
    PORCENTAJE = "porcentaje"  # 70 significa 70 %
    COP = "cop"  # pesos colombianos
    SMMLV = "smmlv"
    CONTRATOS = "contratos"
    CODIGO = "codigo"


class ParametrosNumericos(BaseModel):
    """Lo que hay que comparar: `campo operador valor`, p. ej. `indice_liquidez >= 1.5`."""

    campo: CampoComparable
    operador: Operador
    valor: float | None = Field(
        default=None, description="Valor numérico exigido, sin separadores de miles."
    )
    valor_texto: str | None = Field(
        default=None, description="Solo para codigo_unspsc: el código exigido, con sus dígitos."
    )
    unidad: Unidad


class ItemExtraido(BaseModel):
    descripcion: str = Field(
        description="Una sola condición verificable, redactada de forma que se entienda sola."
    )
    # Sin valor se evalúa como antes: contra la base (la opción que no oculta incumplimientos).
    tipo_exigibilidad: TipoExigibilidad = Field(
        default=TipoExigibilidad.HISTORICO_EMPRESA,
        description="Momento en que se exige: solo historico_empresa se verifica contra la "
        "base de conocimiento de la empresa.",
    )
    tipo_verificacion: TipoVerificacion
    parametros: ParametrosNumericos | None = Field(
        default=None, description="Obligatorio si tipo_verificacion es numerico; nulo si no."
    )


class RequisitoExtraido(BaseModel):
    numeral: str | None = Field(
        default=None, description="Numeral del pliego donde está el requisito, p. ej. 3.4.2."
    )
    tipo: TipoRequisito
    texto_literal: str = Field(
        description="Texto del requisito copiado tal cual aparece en la sección, sin reformular."
    )
    ambiguo: bool = Field(
        description="True si el requisito admite más de una lectura o le falta un dato para "
        "poder verificarlo."
    )
    motivo_ambiguedad: str | None = Field(
        default=None, description="Por qué es ambiguo. Nulo si no lo es."
    )
    items: list[ItemExtraido] = Field(description="Un ítem por cada condición del requisito.")


class ExtraccionSeccion(BaseModel):
    requisitos: list[RequisitoExtraido] = Field(
        description="Requisitos de la sección. Lista vacía si la sección no contiene ninguno."
    )
