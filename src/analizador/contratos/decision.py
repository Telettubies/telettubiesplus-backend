"""Estados del dashboard y datos de la decisión para HU-B19 a HU-B21."""

from enum import StrEnum

from pydantic import BaseModel, Field


class EstadoMatriz(StrEnum):
    CUMPLE = "cumple"
    CUMPLE_PARCIALMENTE = "cumple_parcialmente"
    NO_CUMPLE = "no_cumple"
    REVISION = "revision"


class Recomendacion(StrEnum):
    PRESENTARSE = "presentarse"
    PRESENTARSE_CON_RIESGO = "presentarse_con_riesgo"
    NO_PRESENTARSE = "no_presentarse"
    CONDICIONADA = "condicionada"


class DecisionAnalisis(BaseModel):
    recomendacion: Recomendacion
    motivos: tuple[str, ...]
    numerales: tuple[str, ...]


class JustificacionGenerada(BaseModel):
    texto: str = Field(min_length=1, max_length=600)
    referencias: list[int] = Field(default_factory=list, max_length=8)
