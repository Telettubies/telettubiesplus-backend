"""Verificación de ítems numéricos en código, contra los datos estructurados (P-03, HU-B17).

Indicadores financieros, valores y número de contratos y códigos UNSPSC se comparan aquí, sin
modelo: la misma entrada da siempre el mismo estado y la evidencia es el dato exacto usado.

Ante la duda, revisión y nunca un estado favorable por defecto:
- una unidad que no corresponde al campo, o un valor en SMMLV sin SMMLV configurado (Q-12);
- una razón de cobertura de intereses indeterminada (la empresa no tiene gastos de intereses);
- experiencia que alcanza lo exigido, cuando el requisito no indica un código UNSPSC con el
  que saber cuáles contratos son pertinentes.

La base de conocimiento es el repositorio histórico de la empresa: puede tener muchos más
contratos de los que el pliego deja presentar. Cuando el requisito fija un tope («máximo dos
contratos»), se eligen los de mayor valor en vez de penalizar a la empresa por tener más.
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


def _aporte_cop(contrato: Registro) -> float:
    """Valor que el contrato aporta al oferente, para ordenar cuáles conviene presentar."""
    return float(contrato.datos.get("valor_cop") or 0) * _participacion(contrato)


def _seleccionar(contratos: Sequence[Registro], maximo: int | None) -> list[Registro]:
    """Los contratos que la empresa presentaría: los de mayor valor, hasta el tope del pliego.

    La base de conocimiento es el repositorio histórico de la empresa, no la oferta: tener más
    contratos de los que el pliego admite no es un incumplimiento, solo obliga a elegir.
    """
    ordenados = sorted(contratos, key=_aporte_cop, reverse=True)
    return ordenados if maximo is None else ordenados[: max(maximo, 0)]


def limite_de_contratos(parametros: Sequence[dict[str, Any]]) -> int | None:
    """Máximo de contratos que el requisito deja presentar, según sus ítems de número de
    contratos con operador <=, < o ==. None si el requisito no fija un tope."""
    topes: list[int] = []
    for p in parametros:
        valor = p.get("valor")
        if p.get("campo") != CampoComparable.EXPERIENCIA_CONTRATOS.value or not isinstance(
            valor, (int, float)
        ):
            continue
        match p.get("operador"):
            case Operador.MENOR_O_IGUAL.value | Operador.IGUAL.value:
                topes.append(math.floor(valor))
            case Operador.MENOR.value:
                topes.append(math.ceil(valor) - 1)
    return min(topes) if topes else None


def _experiencia(
    campo: CampoComparable,
    operador: Operador,
    valor: float,
    unidad: Unidad,
    datos: DatosEmpresa,
    smmlv_cop: int | None,
    codigos: Sequence[str],
    regla: str,
    max_contratos: int | None = None,
) -> Veredicto:
    if not datos.contratos:
        return _no_cumple(regla, "La base de conocimiento no tiene contratos ejecutados.")
    filtrado = bool(codigos)
    pertinentes = _con_codigo(datos.contratos, codigos) if filtrado else list(datos.contratos)
    alcance = (
        f"contratos clasificados en UNSPSC {', '.join(codigos)}"
        if filtrado
        else "todos los contratos de la empresa"
    )

    if campo == CampoComparable.EXPERIENCIA_CONTRATOS:
        es_tope = operador in (Operador.MENOR_O_IGUAL, Operador.MENOR)
        if operador == Operador.IGUAL:
            # Se exigen exactamente N: basta con tener N y presentar esos.
            tope: int | None = math.floor(valor)
        elif es_tope:
            tope = limite_de_contratos(
                [{"campo": campo.value, "operador": operador.value, "valor": valor}]
            )
        else:
            tope = max_contratos
        contratos = _seleccionar(pertinentes, tope)
        evidencia = [
            _dato(c, f"Contrato {c.datos.get('id', '')}: {c.datos.get('objeto', '')}")
            for c in contratos
        ]
        resumen = f"{len(contratos)} contratos ({alcance}; {len(pertinentes)} disponibles)"
        regla = f"{regla}; seleccionados: {resumen}"
        if es_tope:
            # Un tope solo limita lo que se presenta; lo que falla es no tener ninguno.
            if not contratos:
                return _no_cumple(
                    regla,
                    "La empresa no tiene contratos que acrediten la experiencia exigida"
                    + (f" ({alcance})." if filtrado else "."),
                )
            return Veredicto(EstadoCumplimiento.CUMPLE, regla, evidencia)
        if operador == Operador.IGUAL:
            cumple = len(pertinentes) >= valor
        else:
            cumple = comparar(float(len(contratos)), operador, valor)
        if not cumple:
            return _no_cumple(
                regla,
                f"Se pueden acreditar {resumen} y se exige {_SIMBOLOS[operador]} "
                f"{numero(valor)} contratos.",
                evidencia,
            )
        return _favorable(regla, evidencia, filtrado)

    aportes: list[tuple[Registro, float]] = []
    for contrato in pertinentes:
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
    # Con un tope de contratos se presentan los de mayor aporte.
    aportes.sort(key=lambda par: par[1], reverse=True)
    if max_contratos is not None:
        aportes = aportes[: max(max_contratos, 0)]
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
    seleccion = (
        f"; los de mayor valor entre {len(pertinentes)}, el pliego admite máximo {max_contratos}"
        if max_contratos is not None and len(pertinentes) > len(aportes)
        else ""
    )
    resumen = f"{numero(acreditado)} {sufijo} en {len(aportes)} contratos ({alcance}{seleccion})"
    exigido_texto = f"{numero(valor)} {sufijo}"

    regla = f"{regla}; acreditado: {resumen}"
    if not comparar(acreditado, operador, valor):
        # Ni con los mejores contratos se alcanza: el incumplimiento es seguro.
        return _no_cumple(
            regla,
            f"Se acreditan {resumen} y se exige {_SIMBOLOS[operador]} {exigido_texto}.",
            evidencia,
        )
    return _favorable(regla, evidencia, filtrado)


def _favorable(regla: str, evidencia: list[dict[str, Any]], filtrado: bool) -> Veredicto:
    if filtrado:
        return Veredicto(EstadoCumplimiento.CUMPLE, regla, evidencia)
    motivo = (
        "El requisito no indica un código UNSPSC: la experiencia se calculó con los contratos "
        "de la empresa sin filtrar por objeto. Confirme que los seleccionados son pertinentes."
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
    max_contratos: int | None = None,
) -> Veredicto:
    """Estado de un ítem numérico.

    `codigos_del_requisito`: UNSPSC exigidos en su requisito; acotan qué contratos cuentan.
    `max_contratos`: cuántos contratos admite presentar el requisito; se eligen los mejores.
    """
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
        campo,
        operador,
        float(valor),
        unidad,
        datos,
        smmlv_cop,
        codigos_del_requisito,
        regla,
        max_contratos,
    )
