"""Esquemas de entrada y salida de la API.

No son los contratos de datos de los modelos de IA (P-18): esos se añaden cuando el equipo
los apruebe.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from analizador.contratos.decision import EstadoMatriz, Recomendacion
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
    # Cifras del trabajo al terminar; p. ej. fuentes y fragmentos indexados (HU-B06).
    detalle: dict[str, Any] | None
    creado_en: datetime
    iniciado_en: datetime | None
    finalizado_en: datetime | None


class EtapaOut(BaseModel):
    clave: str
    nombre: str
    porcentaje: int


class SeccionesProgreso(BaseModel):
    procesadas: int
    total: int


class ProgresoOut(BaseModel):
    """Avance del análisis de una licitación, leído de PostgreSQL (HU-B08, ADR-015)."""

    licitacion_id: uuid.UUID
    estado_licitacion: EstadoLicitacion
    trabajo_id: uuid.UUID
    estado: EstadoTrabajo
    etapa_actual: str | None
    progreso: int
    # Secciones del pliego ya procesadas sobre el total; nulo hasta que el pliego se divide.
    secciones: SeccionesProgreso | None
    # Mensaje para el usuario cuando el análisis falló; nunca contiene detalles internos.
    error: str | None
    intentos: int
    iniciado_en: datetime | None
    finalizado_en: datetime | None
    actualizado_en: datetime
    # Todas las etapas en orden, para dibujar la línea de avance.
    etapas: list[EtapaOut]


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


class UrlPdfOut(BaseModel):
    pdf_url_temporal: str


class ZonaMatrizOut(BaseModel):
    pagina: int
    x: float
    y: float
    width: float
    height: float


class UbicacionMatrizOut(BaseModel):
    pagina: int
    precision: PrecisionUbicacion
    zonas: list[ZonaMatrizOut]


class EvidenciaMatrizOut(BaseModel):
    fuente_id: str | None
    fuente_nombre: str | None
    cita_literal: str
    cita_verificada: bool
    similitud: float | None


class ItemMatrizOut(BaseModel):
    id: uuid.UUID
    requisito_id: uuid.UUID
    resultado_id: uuid.UUID | None
    codigo_req: str
    numeral: str
    categoria: TipoRequisito
    texto_requisito_literal: str
    texto_requisito_verificado: bool | None
    descripcion_item: str
    estado: EstadoMatriz
    estado_propuesto: EstadoCumplimiento | None
    motivo_revision: str | None
    es_ambiguo: bool
    evidencia: EvidenciaMatrizOut | None
    evidencias: list[EvidenciaMatrizOut]
    faltante: str | None
    confianza: float | None
    ubicacion: UbicacionMatrizOut
    revisiones: list[RevisionOut]


class CoberturaMatrizOut(BaseModel):
    secciones_procesadas: int
    secciones_total: int
    numerales_no_procesados: list[str]


class ResultadoMatrizOut(BaseModel):
    licitacion_id: uuid.UUID
    nombre: str
    fecha_carga: datetime
    pdf_nombre: str
    pdf_paginas: int | None
    pdf_url_temporal: str
    recomendacion: Recomendacion
    justificacion: str
    cobertura: CoberturaMatrizOut
    resumen_estados: dict[EstadoMatriz, int]
    categorias: dict[TipoRequisito, dict[EstadoMatriz, int]]
    items: list[ItemMatrizOut]
    brechas: list[ItemMatrizOut]


class ErrorResultadoOut(BaseModel):
    detail: str


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


class ResumenConocimiento(BaseModel):
    """Qué hay cargado en la base de conocimiento de la organización (HU-B05, HU-F04)."""

    cargada: bool
    empresa: str | None
    cargada_en: datetime | None
    anios_financieros: int
    contratos: int
    personas: int
    certificaciones: int
    capacidades: int
    fragmentos_indexados: int
    # Último trabajo de indexación (HU-B06); nulo si nunca se cargó la base.
    indexacion: TrabajoOut | None


class ConocimientoCargado(BaseModel):
    resumen: ResumenConocimiento
    trabajo: TrabajoOut


# --- Límites de uso (HU-B26) ------------------------------------------------------


class UsoOut(BaseModel):
    usados: int
    cuota: int


class LimitesOut(BaseModel):
    max_pdf_mb: int
    max_pdf_paginas: int
    chat_max_caracteres: int
    # Las cuotas se cuentan en una ventana móvil de estas horas.
    ventana_horas: int
    analisis: UsoOut
    chat: UsoOut


# --- Chat (RF-13) -----------------------------------------------------------------


class PreguntaIn(BaseModel):
    pregunta: str = Field(min_length=1)
