"""Errores de validación de un contrato, en español y con la ubicación de cada campo.

Se devuelven todos a la vez para que el usuario corrija el archivo de una sola pasada
(HU-B05: protección contra errores del usuario).
"""

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ValidationError

FORMATOS: dict[str, str] = {
    r"^\d{8}$": "Debe ser un código UNSPSC de 8 dígitos",
    r"^\d{6,10}(-\d)?$": "Debe ser un NIT de 6 a 10 dígitos, con dígito de verificación opcional",
}

FECHA = "Debe ser una fecha con formato AAAA-MM-DD"
ENTERO = "Debe ser un número entero"
NUMERO = "Debe ser un número"

MENSAJES: dict[str, str] = {
    "missing": "Campo obligatorio",
    "extra_forbidden": "Campo no reconocido",
    "string_type": "Debe ser texto",
    "int_type": ENTERO,
    "int_parsing": ENTERO,
    "int_from_float": ENTERO,
    "float_type": NUMERO,
    "float_parsing": NUMERO,
    "date_type": FECHA,
    "date_parsing": FECHA,
    "date_from_datetime_parsing": FECHA,
    "date_from_datetime_inexact": FECHA,
    "list_type": "Debe ser una lista",
    "model_type": "Debe ser un objeto",
    "dict_type": "Debe ser un objeto",
    "greater_than": "Debe ser mayor que {gt}",
    "greater_than_equal": "Debe ser mayor o igual que {ge}",
    "less_than": "Debe ser menor que {lt}",
    "less_than_equal": "Debe ser menor o igual que {le}",
    "string_too_long": "No puede superar {max_length} caracteres",
}


class ErrorDeCampo(BaseModel):
    # Ruta legible del campo, p. ej. `contratos[2].fecha_fin`.
    campo: str
    # La misma ruta por partes, para que el frontend la recorra.
    ubicacion: list[str | int]
    mensaje: str


def ruta(ubicacion: Sequence[str | int]) -> str:
    partes: list[str] = []
    for parte in ubicacion:
        if isinstance(parte, int):
            partes.append(f"[{parte}]")
        else:
            partes.append(f".{parte}" if partes else parte)
    return "".join(partes) or "(archivo)"


def _mensaje(error: dict[str, Any]) -> str:
    tipo, contexto = error["type"], error.get("ctx") or {}
    if tipo == "value_error":
        return str(contexto.get("error", error["msg"]))
    if tipo == "string_pattern_mismatch":
        return FORMATOS.get(contexto.get("pattern", ""), "Formato inválido")
    if tipo == "string_too_short":
        minimo = contexto.get("min_length", 1)
        return "No puede estar vacío" if minimo <= 1 else f"Debe tener al menos {minimo} caracteres"
    plantilla = MENSAJES.get(tipo)
    if plantilla is None:
        return str(error["msg"])
    return plantilla.format(**contexto)


def errores_de_validacion(exc: ValidationError) -> list[ErrorDeCampo]:
    """Todos los errores de la validación, uno por campo inválido."""
    return [
        ErrorDeCampo(
            campo=ruta(error["loc"]), ubicacion=list(error["loc"]), mensaje=_mensaje(dict(error))
        )
        for error in exc.errors(include_url=False)
    ]
