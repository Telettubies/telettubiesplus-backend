"""Borrador de observación al pliego (HU-B25, RF-15)."""

from pydantic import BaseModel, Field


class BorradorGenerado(BaseModel):
    """Lo que redacta el modelo. El numeral y el texto literal los pone el código."""

    asunto: str = Field(min_length=1, max_length=200)
    cuerpo: str = Field(min_length=1, max_length=3000)
