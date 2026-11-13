"""Enumeraciones del dominio.

Se guardan como VARCHAR + CHECK (no como ENUM nativo de PostgreSQL) para que cambiar
la taxonomía (Q-16) o los estados (Q-17) sea una migración sencilla.
"""

from enum import StrEnum

from sqlalchemy import Enum as SAEnum


def str_enum(enum_cls: type[StrEnum], name: str) -> SAEnum:
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=40,
        values_callable=lambda cls: [m.value for m in cls],
        validate_strings=True,
    )


class Rol(StrEnum):
    """Rol de un miembro dentro de su organización (RF-09, ADR-014)."""

    ADMINISTRADOR = "administrador"
    REVISOR = "revisor"


class AccionMiembro(StrEnum):
    """Cambios de membresía que quedan auditados (HU-B02, HU-B03, S-05)."""

    INVITACION = "invitacion"
    CAMBIO_ROL = "cambio_rol"
    RETIRO = "retiro"


class TipoRequisito(StrEnum):
    """Taxonomía del reto (RF-01). Pendiente Q-16 / P-24."""

    HABILITANTE = "habilitante"
    TECNICO = "tecnico"
    EXPERIENCIA = "experiencia"
    PLAZO = "plazo"


class EstadoCumplimiento(StrEnum):
    """Estados del reto (RF-04). Pendiente Q-17 / P-25."""

    CUMPLE = "cumple"
    CUMPLE_PARCIALMENTE = "cumple_parcialmente"
    NO_CUMPLE = "no_cumple"


class PrecisionUbicacion(StrEnum):
    """Precisión de la ubicación del requisito en el PDF (RF-26, P-21)."""

    EXACTA = "exacta"
    APROXIMADA = "aproximada"
    SOLO_PAGINA = "solo_pagina"


class EstadoLicitacion(StrEnum):
    PENDIENTE = "pendiente"
    PROCESANDO = "procesando"
    COMPLETADA = "completada"
    FALLIDA = "fallida"


class TipoTrabajo(StrEnum):
    """Tipos de trabajo asíncrono; cada uno tiene su cola (P-15)."""

    ANALISIS = "analisis"  # F-03
    INDEXACION = "indexacion"  # F-02
    REEVALUACION = "reevaluacion"  # F-05


class EstadoTrabajo(StrEnum):
    EN_COLA = "en_cola"
    EN_EJECUCION = "en_ejecucion"
    COMPLETADO = "completado"
    FALLIDO = "fallido"


class TipoFuenteConocimiento(StrEnum):
    """Tipos de fuente de la base de conocimiento (sección 6.3).

    `empresa` no está en el DAS: guarda los datos generales del oferente (razón social, NIT).
    """

    EMPRESA = "empresa"
    CONTRATO = "contrato"
    HOJA_DE_VIDA = "hoja_de_vida"
    FINANCIERO = "financiero"
    CERTIFICACION = "certificacion"
    CAPACIDAD = "capacidad"


class OrigenFragmento(StrEnum):
    """Separación pliego / empresa en el almacén vectorial (C-06)."""

    EMPRESA = "empresa"
    PLIEGO = "pliego"


class TipoUso(StrEnum):
    """Acciones que cuentan para las cuotas por organización (S-08, HU-B26)."""

    PREGUNTA_CHAT = "pregunta_chat"


class OrigenPagina(StrEnum):
    """Cómo se obtuvo el texto de una página del pliego (HU-B10)."""

    DIGITAL = "digital"  # texto extraíble del PDF
    OCR = "ocr"  # página escaneada leída con Tesseract; conserva las cajas de las palabras
    VISION = "vision"  # transcrita por el modelo con visión; sin cajas
    NO_LEIDA = "no_leida"  # ni el OCR ni la visión lograron texto legible


class TipoVerificacion(StrEnum):
    """Cómo se verifica un ítem (P-03, HU-B13, HU-B17)."""

    NUMERICO = "numerico"  # por código, contra un dato estructurado de la empresa
    CUALITATIVO = "cualitativo"  # lo valora el modelo contra la evidencia recuperada
