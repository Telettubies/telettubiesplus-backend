"""Contrato de la base de conocimiento de la organización (RF-03, RF-10, Q-05, HU-B05).

Un único archivo JSON con la información que el oferente usa para acreditar requisitos:
indicadores financieros, contratos ejecutados, personas, certificaciones y capacidades.
Los datos son sintéticos o anonimizados (RC-07). Ejemplo: docs/ejemplos/base_conocimiento.json.

Reglas generales:
- No se aceptan campos desconocidos: un nombre mal escrito es un error, no un dato perdido.
- Fechas en formato ISO (AAAA-MM-DD). Valores monetarios en pesos colombianos, enteros.
- Códigos UNSPSC de ocho dígitos (segmento, familia, clase y producto).
"""

from collections import Counter
from datetime import date
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Texto = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
TextoCorto = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
Identificador = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)
]
CodigoUnspsc = Annotated[str, StringConstraints(pattern=r"^\d{8}$")]
Nit = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\d{6,10}(-\d)?$")]
Anio = Annotated[int, Field(ge=1990, le=2100)]
Pesos = Annotated[int, Field(strict=True)]


class _Registro(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Empresa(_Registro):
    razon_social: TextoCorto
    nit: Nit
    ciudad: TextoCorto | None = None
    representante_legal: TextoCorto | None = None


class IndicadoresFinancieros(_Registro):
    """Indicadores de capacidad financiera y organizacional de un año fiscal (RUP)."""

    anio: Anio
    # Activo corriente / pasivo corriente.
    indice_liquidez: Annotated[float, Field(ge=0)]
    # Pasivo total / activo total, como razón entre 0 y 1 (0.55 = 55 %).
    indice_endeudamiento: Annotated[float, Field(ge=0, le=1)]
    # Utilidad operacional / gastos de intereses. Nulo si no hay gastos de intereses.
    razon_cobertura_intereses: Annotated[float, Field(ge=0)] | None = None
    capital_trabajo_cop: Pesos
    patrimonio_cop: Pesos
    # Utilidad operacional / patrimonio y / activo total, como razón (0.12 = 12 %).
    rentabilidad_patrimonio: float
    rentabilidad_activo: float


class ContratoEjecutado(_Registro):
    id: Identificador
    objeto: Texto
    entidad_contratante: TextoCorto
    fecha_inicio: date
    fecha_fin: date
    valor_cop: Annotated[Pesos, Field(gt=0)]
    # Valor certificado en SMMLV; si falta, se calcula con el SMMLV configurado (Q-12).
    valor_smmlv: Annotated[float, Field(gt=0)] | None = None
    codigos_unspsc: list[CodigoUnspsc] = Field(default_factory=list)
    # Participación del oferente si lo ejecutó en consorcio o unión temporal.
    porcentaje_participacion: Annotated[float, Field(gt=0, le=100)] = 100.0
    descripcion: Texto | None = None

    @model_validator(mode="after")
    def _fechas_en_orden(self) -> Self:
        if self.fecha_fin < self.fecha_inicio:
            raise ValueError("fecha_fin no puede ser anterior a fecha_inicio")
        return self


class Titulo(_Registro):
    nivel: TextoCorto  # pregrado, especialización, maestría…
    titulo: TextoCorto
    institucion: TextoCorto
    anio: Anio


class Experiencia(_Registro):
    cargo: TextoCorto
    entidad: TextoCorto
    fecha_inicio: date
    # Nulo si es el cargo actual.
    fecha_fin: date | None = None
    descripcion: Texto | None = None

    @model_validator(mode="after")
    def _fechas_en_orden(self) -> Self:
        if self.fecha_fin is not None and self.fecha_fin < self.fecha_inicio:
            raise ValueError("fecha_fin no puede ser anterior a fecha_inicio")
        return self


class Persona(_Registro):
    """Hoja de vida resumida de una persona del equipo (datos sintéticos, RC-07)."""

    id: Identificador
    nombre: TextoCorto
    profesion: TextoCorto
    anios_experiencia_general: Annotated[float, Field(ge=0, le=70)]
    titulos: list[Titulo] = Field(default_factory=list)
    certificaciones: list[TextoCorto] = Field(default_factory=list)
    experiencias: list[Experiencia] = Field(default_factory=list)


class Certificacion(_Registro):
    nombre: TextoCorto
    entidad_emisora: TextoCorto
    fecha_expedicion: date
    # Nulo si no vence.
    fecha_vencimiento: date | None = None
    alcance: Texto | None = None

    @model_validator(mode="after")
    def _fechas_en_orden(self) -> Self:
        if self.fecha_vencimiento is not None and self.fecha_vencimiento < self.fecha_expedicion:
            raise ValueError("fecha_vencimiento no puede ser anterior a fecha_expedicion")
        return self


class Capacidad(_Registro):
    nombre: TextoCorto
    descripcion: Texto


def _sin_repetidos(valores: list[str], que: str) -> None:
    repetidos = sorted(valor for valor, veces in Counter(valores).items() if veces > 1)
    if repetidos:
        raise ValueError(f"{que} repetido: {', '.join(repetidos)}")


class BaseConocimiento(_Registro):
    empresa: Empresa
    financiero: list[IndicadoresFinancieros] = Field(default_factory=list)
    contratos: list[ContratoEjecutado] = Field(default_factory=list)
    personas: list[Persona] = Field(default_factory=list)
    certificaciones: list[Certificacion] = Field(default_factory=list)
    capacidades: list[Capacidad] = Field(default_factory=list)

    @field_validator("financiero")
    @classmethod
    def _anios_unicos(cls, valor: list[IndicadoresFinancieros]) -> list[IndicadoresFinancieros]:
        _sin_repetidos([str(f.anio) for f in valor], "año")
        return valor

    @field_validator("contratos")
    @classmethod
    def _contratos_unicos(cls, valor: list[ContratoEjecutado]) -> list[ContratoEjecutado]:
        _sin_repetidos([c.id for c in valor], "id de contrato")
        return valor

    @field_validator("personas")
    @classmethod
    def _personas_unicas(cls, valor: list[Persona]) -> list[Persona]:
        _sin_repetidos([p.id for p in valor], "id de persona")
        return valor
