"""Verificación de ítems numéricos en código, contra los datos estructurados (P-03, HU-B17).

Indicadores financieros, valores y número de contratos y códigos UNSPSC se comparan aquí, sin
modelo: la misma entrada da siempre el mismo estado y la evidencia es el dato exacto usado.

Ante la duda, revisión y nunca un estado favorable por defecto:
- una unidad que no corresponde al campo, o un valor en SMMLV sin SMMLV configurado (Q-12);
- una razón de cobertura de intereses indeterminada (la empresa no tiene gastos de intereses);
- experiencia que alcanza lo exigido sumando todos los contratos, cuando el requisito no
  indica un código UNSPSC con el que saber cuáles son pertinentes.
"""

import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from analizador.contratos.extraccion import CampoComparable, Operador, Unidad
from analizador.models.enums import EstadoCumplimiento


@dataclass(frozen=True)
class Registro:
    """Un registro de la base de conocimiento (un año financiero, un contrato)."""

    fuente_id: uuid.UUID
    titulo: str
    datos: dict[str, Any]


@dataclass(frozen=True)
class DatosEmpresa:
    financiero: Sequence[Registro] = ()
    contratos: Sequence[Registro] = ()


@dataclass(frozen=True)
class Veredicto:
    estado: EstadoCumplimiento
    regla: str
    evidencia: list[dict[str, Any]] = field(default_factory=list)
    faltante: str | None = None
    confianza: float | None = None
    requiere_revision: bool = False
    motivo_revision: str | None = None


# campo → (clave en los datos del año, etiqueta, clase de magnitud)
_FINANCIEROS: dict[CampoComparable, tuple[str, str, str]] = {
    CampoComparable.INDICE_LIQUIDEZ: ("indice_liquidez", "índice de liquidez", "razon"),
    CampoComparable.RAZON_COBERTURA_INTERESES: (
        "razon_cobertura_intereses",
        "razón de cobertura de intereses",
        "razon",
    ),
    CampoComparable.INDICE_ENDEUDAMIENTO: (
        "indice_endeudamiento",
        "índice de endeudamiento",
        "proporcion",
    ),
    CampoComparable.RENTABILIDAD_PATRIMONIO: (
        "rentabilidad_patrimonio",
        "rentabilidad del patrimonio",
        "proporcion",
    ),
    CampoComparable.RENTABILIDAD_ACTIVO: (
        "rentabilidad_activo",
        "rentabilidad del activo",
        "proporcion",
    ),
    CampoComparable.CAPITAL_TRABAJO: ("capital_trabajo_cop", "capital de trabajo", "dinero"),
    CampoComparable.PATRIMONIO: ("patrimonio_cop", "patrimonio", "dinero"),
}
TOLERANCIA = 1e-12

_SIMBOLOS = {
    Operador.MAYOR_O_IGUAL: "≥",
    Operador.MENOR_O_IGUAL: "≤",
    Operador.MAYOR: ">",
    Operador.MENOR: "<",
    Operador.IGUAL: "=",
}


def numero(valor: float, decimales: int = 2) -> str:
    """Número con formato colombiano, sin decimales sobrantes: 2112.07 → `2.112,07`."""
    texto = f"{valor:,.{decimales}f}".replace(",", "@").replace(".", ",").replace("@", ".")
    return texto.rstrip("0").rstrip(",") if "," in texto else texto


def comparar(real: float, operador: Operador, exigido: float) -> bool:
    """`real operador exigido`, tratando como iguales dos valores que solo difieren por redondeo.

    La tolerancia cubre únicamente el ruido de la coma flotante (0.1 + 0.2 frente a 0.3): con
    cifras de miles de millones de pesos, un peso de diferencia sigue siendo una diferencia.
    """
    iguales = math.isclose(real, exigido, rel_tol=TOLERANCIA, abs_tol=TOLERANCIA)
    match operador:
        case Operador.MAYOR_O_IGUAL:
            return real > exigido or iguales
        case Operador.MENOR_O_IGUAL:
            return real < exigido or iguales
        case Operador.MAYOR:
            return real > exigido and not iguales
        case Operador.MENOR:
            return real < exigido and not iguales
        case Operador.IGUAL:
            return iguales


def _dato(registro: Registro, texto: str) -> dict[str, Any]:
    """Evidencia de un dato estructurado: no es una cita de un modelo, es el dato usado."""
    return {
        "tipo": "dato",
        "fuente_id": str(registro.fuente_id),
        "fuente": registro.titulo,
        "texto": texto,
        "verificada": True,
    }


def _revision(regla: str, motivo: str, evidencia: list[dict[str, Any]] | None = None) -> Veredicto:
    return Veredicto(
        estado=EstadoCumplimiento.NO_CUMPLE,
        regla=regla,
        evidencia=evidencia or [],
        faltante=motivo,
        requiere_revision=True,
        motivo_revision=motivo,
    )


def _no_cumple(
    regla: str, faltante: str, evidencia: list[dict[str, Any]] | None = None
) -> Veredicto:
    return Veredicto(EstadoCumplimiento.NO_CUMPLE, regla, evidencia or [], faltante)


def _mostrar(valor: float, clase: str) -> str:
    if clase == "proporcion":
        return f"{numero(valor * 100)} %"
    if clase == "dinero":
        return f"${numero(valor, 0)} COP"
    return numero(valor)


def _financiero(
    campo: CampoComparable,
    operador: Operador,
    valor: float,
    unidad: Unidad,
    datos: DatosEmpresa,
    smmlv_cop: int | None,
    regla: str,
) -> Veredicto:
    clave, etiqueta, clase = _FINANCIEROS[campo]
    if not datos.financiero:
        return _no_cumple(regla, "La base de conocimiento no tiene indicadores financieros.")

    if clase == "razon" and unidad == Unidad.RAZON:
        exigido = valor
    elif clase == "proporcion" and unidad == Unidad.PORCENTAJE:
        exigido = valor / 100
    elif (clase == "proporcion" and unidad == Unidad.RAZON) or (
        clase == "dinero" and unidad == Unidad.COP
    ):
        exigido = valor
    elif clase == "dinero" and unidad == Unidad.SMMLV:
        if smmlv_cop is None:
            return _revision(
                regla, "Falta configurar el valor del SMMLV para convertir lo exigido."
            )
        exigido = valor * smmlv_cop
    else:
        return _revision(
            regla, f"La unidad «{unidad.value}» no corresponde al {etiqueta}: no se pudo comparar."
        )

    # Se usa el último año fiscal cargado.
    registro = max(datos.financiero, key=lambda r: r.datos.get("anio") or 0)
    anio = registro.datos.get("anio")
    real = registro.datos.get(clave)
    if real is None:
        if campo == CampoComparable.RAZON_COBERTURA_INTERESES:
            return _revision(
                regla,
                f"La razón de cobertura de intereses de {anio} es indeterminada (la empresa no "
                "tiene gastos de intereses). Confirme si el pliego la da por cumplida.",
            )
        return _no_cumple(regla, f"La base de conocimiento no tiene el {etiqueta} de {anio}.")

    real = float(real)
    evidencia = [_dato(registro, f"{etiqueta.capitalize()} de {anio}: {_mostrar(real, clase)}")]
    if comparar(real, operador, exigido):
        return Veredicto(EstadoCumplimiento.CUMPLE, regla, evidencia)
    return _no_cumple(
        regla,
        f"El {etiqueta} de {anio} es {_mostrar(real, clase)} y se exige "
        f"{_SIMBOLOS[operador]} {_mostrar(exigido, clase)}.",
        evidencia,
    )


def prefijo_unspsc(codigo: str) -> str:
    """Código sin los pares de ceros finales: `81111500` (clase) → `811115`."""
    digitos = "".join(c for c in codigo if c.isdigit())
    while len(digitos) > 2 and digitos.endswith("00"):
        digitos = digitos[:-2]
    return digitos


def _con_codigo(contratos: Sequence[Registro], codigos: Sequence[str]) -> list[Registro]:
    prefijos = [p for p in (prefijo_unspsc(c) for c in codigos) if p]
    return [
        contrato
        for contrato in contratos
        if any(
            str(propio).startswith(prefijo)
            for propio in contrato.datos.get("codigos_unspsc") or []
            for prefijo in prefijos
        )
    ]


def _participacion(contrato: Registro) -> float:
    return float(contrato.datos.get("porcentaje_participacion") or 100) / 100


def _experiencia(
    campo: CampoComparable,
    operador: Operador,
    valor: float,
    unidad: Unidad,
    datos: DatosEmpresa,
    smmlv_cop: int | None,
    codigos: Sequence[str],
    regla: str,
) -> Veredicto:
    if not datos.contratos:
        return _no_cumple(regla, "La base de conocimiento no tiene contratos ejecutados.")
    filtrado = bool(codigos)
    contratos = _con_codigo(datos.contratos, codigos) if filtrado else list(datos.contratos)
    alcance = (
        f"contratos clasificados en UNSPSC {', '.join(codigos)}"
        if filtrado
        else "todos los contratos de la empresa"
    )

    if campo == CampoComparable.EXPERIENCIA_CONTRATOS:
        acreditado = float(len(contratos))
        evidencia = [
            _dato(c, f"Contrato {c.datos.get('id', '')}: {c.datos.get('objeto', '')}")
            for c in contratos
        ]
        resumen = f"{len(contratos)} contratos ({alcance})"
        exigido_texto = f"{numero(valor)} contratos"
    else:
        aportes: list[tuple[Registro, float]] = []
        for contrato in contratos:
            if unidad == Unidad.SMMLV:
                propio = contrato.datos.get("valor_smmlv")
                if propio is None:
                    if smmlv_cop is None:
                        return _revision(
                            regla,
                            f"El contrato {contrato.datos.get('id', '')} no tiene valor en SMMLV y "
                            "falta configurar el SMMLV para convertirlo.",
                        )
                    propio = float(contrato.datos["valor_cop"]) / smmlv_cop
            elif unidad == Unidad.COP:
                propio = contrato.datos["valor_cop"]
            else:
                return _revision(
                    regla,
                    f"La unidad «{unidad.value}» no corresponde al valor de la experiencia: "
                    "no se pudo comparar.",
                )
            aportes.append((contrato, float(propio) * _participacion(contrato)))
        sufijo = "SMMLV" if unidad == Unidad.SMMLV else "COP"
        acreditado = sum(aporte for _, aporte in aportes)
        evidencia = [
            _dato(
                c,
                f"Contrato {c.datos.get('id', '')}: {numero(aporte)} {sufijo} "
                f"(participación {numero(_participacion(c) * 100)} %)",
            )
            for c, aporte in aportes
        ]
        resumen = f"{numero(acreditado)} {sufijo} en {len(aportes)} contratos ({alcance})"
        exigido_texto = f"{numero(valor)} {sufijo}"

    regla = f"{regla}; acreditado: {resumen}"
    if not comparar(acreditado, operador, valor):
        # Ni sumando todo se alcanza: el incumplimiento es seguro aunque no haya filtro.
        return _no_cumple(
            regla,
            f"Se acreditan {resumen} y se exige {_SIMBOLOS[operador]} {exigido_texto}.",
            evidencia,
        )
    if filtrado:
        return Veredicto(EstadoCumplimiento.CUMPLE, regla, evidencia)
    motivo = (
        "El requisito no indica un código UNSPSC: la experiencia se calculó con todos los "
        "contratos de la empresa. Confirme que son pertinentes al objeto exigido."
    )
    return Veredicto(
        EstadoCumplimiento.CUMPLE, regla, evidencia, requiere_revision=True, motivo_revision=motivo
    )


def _unspsc(codigo: str, datos: DatosEmpresa, regla: str) -> Veredicto:
    if len(prefijo_unspsc(codigo)) < 2:
        return _revision(regla, f"El código UNSPSC «{codigo}» no es válido: no se pudo comparar.")
    coincidentes = _con_codigo(datos.contratos, [codigo])
    if not coincidentes:
        return _no_cumple(
            regla, f"Ningún contrato de la empresa está clasificado en el código UNSPSC {codigo}."
        )
    evidencia = [
        _dato(
            c,
            f"Contrato {c.datos.get('id', '')}: códigos UNSPSC "
            f"{', '.join(c.datos.get('codigos_unspsc') or [])}",
        )
        for c in coincidentes
    ]
    return Veredicto(EstadoCumplimiento.CUMPLE, regla, evidencia)


def verificar(
    parametros: dict[str, Any],
    datos: DatosEmpresa,
    smmlv_cop: int | None = None,
    codigos_del_requisito: Sequence[str] = (),
) -> Veredicto:
    """Estado de un ítem numérico. `codigos_del_requisito`: UNSPSC exigidos en su requisito."""
    try:
        campo = CampoComparable(parametros["campo"])
        operador = Operador(parametros["operador"])
        unidad = Unidad(parametros.get("unidad") or "razon")
    except (KeyError, ValueError):
        return _revision(
            "numerico: parámetros no reconocidos", "Los parámetros del ítem no son válidos."
        )

    if campo == CampoComparable.CODIGO_UNSPSC:
        codigo = str(parametros.get("valor_texto") or "").strip()
        return _unspsc(codigo, datos, f"numerico: codigo_unspsc == {codigo}")

    valor = parametros.get("valor")
    regla = f"numerico: {campo.value} {operador.value} {valor} ({unidad.value})"
    if not isinstance(valor, (int, float)) or isinstance(valor, bool):
        return _revision(regla, "El ítem no indica el valor exigido: no se pudo comparar.")
    if campo in _FINANCIEROS:
        return _financiero(campo, operador, float(valor), unidad, datos, smmlv_cop, regla)
    return _experiencia(
        campo, operador, float(valor), unidad, datos, smmlv_cop, codigos_del_requisito, regla
    )
