"""Contrato de la verificación cualitativa de un ítem (RF-04, RF-05, P-18, HU-B17).

Es la forma de la respuesta del modelo de verificación. Sin restricciones de rango para que
sea válido como esquema de salida estructurada; las reglas se aplican después, en código.
"""

from pydantic import BaseModel, Field

from analizador.models.enums import EstadoCumplimiento


class VerificacionItem(BaseModel):
    estado: EstadoCumplimiento
    fragmento: int | None = Field(
        default=None,
        description="Número del fragmento de evidencia en que se apoya la conclusión. "
        "Nulo si ningún fragmento respalda la condición.",
    )
    cita: str | None = Field(
        default=None,
        description="Frase copiada literalmente de ese fragmento que respalda la conclusión.",
    )
    faltante: str | None = Field(
        default=None,
        description="Qué le falta a la empresa para cumplir. Obligatorio si no es cumple.",
    )
    confianza: float = Field(description="Seguridad en la conclusión, de 0 a 1.")
