"""Esquemas de entrada y salida de la API.

No son los contratos de datos de los modelos de IA (P-18): esos se añaden cuando el equipo
los apruebe.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from analizador.models.enums import (
    EstadoCumplimiento,
    EstadoLicitacion,
    EstadoTrabajo,
    PrecisionUbicacion,
    Rol,
    TipoFuenteConocimiento,
    TipoRequisito,
    TipoTrabajo,
)


class _Orm(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Organización y miembros ---------------------------------------------------


class MiembroOut(_Orm):
    id: uuid.UUID
    organizacion_id: uuid.UUID
    usuario_id: uuid.UUID
    email: str
    rol: Rol
    creado_en: datetime


class YoOut(BaseModel):
    usuario_id: uuid.UUID
    email: str | None
    membresias: list[MiembroOut]


class InvitacionIn(BaseModel):
    email: EmailStr
    rol: Rol = Rol.REVISOR


class CambioRolIn(BaseModel):
    rol: Rol


# --- Trabajos ---------------------------------------------------------------


class TrabajoOut(_Orm):
    id: uuid.UUID
    tipo: TipoTrabajo
    estado: EstadoTrabajo
    etapa_actual: str | None
    progreso: int
    error: str | None
    creado_en: datetime
    iniciado_en: datetime | None
    finalizado_en: datetime | None


# --- Licitaciones y matriz ---------------------------------------------------


class LicitacionResumen(_Orm):
    id: uuid.UUID
    titulo: str
    nombre_archivo: str
    estado: EstadoLicitacion
    recomendacion: str | None
    creado_en: datetime


class LicitacionCreada(BaseModel):
    licitacion: LicitacionResumen
    trabajo: TrabajoOut
    # True si el mismo PDF ya había sido cargado en la organización (HU-B07, P-16).
    duplicada: bool = False


class UbicacionOut(_Orm):
    pagina: int
    zonas: list[dict[str, float]]
    precision: PrecisionUbicacion


class RevisionOut(_Orm):
    id: uuid.UUID
    usuario_id: uuid.UUID
    estado_anterior: EstadoCumplimiento
    estado_nuevo: EstadoCumplimiento
    comentario: str
    creado_en: datetime


class ResultadoOut(_Orm):
    id: uuid.UUID
    estado: EstadoCumplimiento
    evidencia: list[dict[str, Any]]
    faltante: str | None
    confianza: float | None
    requiere_revision: bool
    revisiones: list[RevisionOut]


class ItemOut(_Orm):
    id: uuid.UUID
    orden: int
    descripcion: str
    resultado: ResultadoOut | None


class RequisitoOut(_Orm):
    id: uuid.UUID
    numeral: str | None
    tipo: TipoRequisito
    texto_literal: str
    ambiguo: bool
    motivo_ambiguedad: str | None
    ubicacion: UbicacionOut | None
    items: list[ItemOut]


class LicitacionDetalle(LicitacionResumen):
    justificacion: str | None
    # URL prefirmada de corta duración para el visor (P-14, RF-26, F-09).
    url_pdf: str
    requisitos: list[RequisitoOut]


# --- Revisión humana (RF-14) ---------------------------------------------------


class RevisionIn(BaseModel):
    estado_nuevo: EstadoCumplimiento
    comentario: str = Field(min_length=1, max_length=4000)


class RevisionCreada(BaseModel):
    revision: RevisionOut
    trabajo: TrabajoOut


# --- Base de conocimiento -------------------------------------------------------


class FuenteConocimientoOut(_Orm):
    id: uuid.UUID
    tipo: TipoFuenteConocimiento
    titulo: str
    creado_en: datetime
