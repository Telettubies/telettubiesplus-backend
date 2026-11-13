"""Licitación y resultado del análisis (sección 6.3).

Licitacion 1─* Seccion 1─* Requisito 1─1 UbicacionPliego
                               Requisito 1─* Item 1─1 Resultado 1─* Revision

Solo Licitacion lleva organizacion_id; las entidades hijas se aíslan a través de ella (S-03).
Atributos, cardinalidades y reglas de integridad siguen en desarrollo [D].
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from analizador.db.base import Base, Timestamps, UUIDPk
from analizador.models.enums import (
    EstadoCumplimiento,
    EstadoLicitacion,
    OrigenPagina,
    PrecisionUbicacion,
    TipoRequisito,
    TipoVerificacion,
    str_enum,
)


class Licitacion(UUIDPk, Timestamps, Base):
    __tablename__ = "licitaciones"
    # Un mismo PDF se analiza una sola vez por organización (HU-B07, P-16).
    __table_args__ = (UniqueConstraint("organizacion_id", "hash_pdf"),)

    organizacion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizaciones.id", ondelete="CASCADE"), index=True
    )
    creado_por: Mapped[uuid.UUID]  # usuario_id de Supabase
    titulo: Mapped[str] = mapped_column(String(300))
    nombre_archivo: Mapped[str] = mapped_column(String(300))
    # Clave del PDF en el almacén: nombre aleatorio bajo el prefijo de la organización (S-09).
    storage_key: Mapped[str] = mapped_column(String(1024))
    # SHA-256 del PDF en hexadecimal. Nulo en licitaciones anteriores a esta columna.
    hash_pdf: Mapped[str | None] = mapped_column(String(64))
    estado: Mapped[EstadoLicitacion] = mapped_column(
        str_enum(EstadoLicitacion, "estado_licitacion"), default=EstadoLicitacion.PENDIENTE
    )
    # Q-04: valores de la recomendación sin decidir (P-05) → texto libre por ahora.
    recomendacion: Mapped[str | None] = mapped_column(String(60))
    justificacion: Mapped[str | None] = mapped_column(Text)

    secciones: Mapped[list["Seccion"]] = relationship(
        back_populates="licitacion", cascade="all, delete-orphan"
    )
    requisitos: Mapped[list["Requisito"]] = relationship(
        back_populates="licitacion", cascade="all, delete-orphan"
    )


class PaginaPliego(UUIDPk, Timestamps, Base):
    """Texto de una página del pliego tal como se leyó en la etapa 1 (HU-B10)."""

    __tablename__ = "paginas"
    __table_args__ = (UniqueConstraint("licitacion_id", "numero"),)

    licitacion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("licitaciones.id", ondelete="CASCADE"), index=True
    )
    numero: Mapped[int] = mapped_column(Integer)  # desde 1
    texto: Mapped[str] = mapped_column(Text)
    origen: Mapped[OrigenPagina] = mapped_column(str_enum(OrigenPagina, "origen_pagina"))
    # Cajas de las palabras del OCR en coordenadas relativas (0..1), para HU-B15.
    palabras: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)


class Seccion(UUIDPk, Timestamps, Base):
    """Parte del pliego asociada a un numeral y páginas (ADR-012)."""

    __tablename__ = "secciones"

    licitacion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("licitaciones.id", ondelete="CASCADE"), index=True
    )
    orden: Mapped[int] = mapped_column(Integer)
    numeral: Mapped[str | None] = mapped_column(String(50))
    titulo: Mapped[str | None] = mapped_column(String(500))
    pagina_inicio: Mapped[int] = mapped_column(Integer)
    pagina_fin: Mapped[int] = mapped_column(Integer)
    texto: Mapped[str] = mapped_column(Text)
    # Etapa 3 (filtrado): None = aún no evaluada.
    tiene_requisitos: Mapped[bool | None]
    # Por qué el filtro la descartó o la dejó pasar; consultable (HU-B12).
    motivo_filtro: Mapped[str | None] = mapped_column(Text)
    # Etapa 4 (extracción): por qué no se pudo procesar y cuántas veces se intentó (HU-B13).
    error: Mapped[str | None] = mapped_column(Text)
    intentos: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Etapa 5 (comprobación de cobertura, ADR-012).
    procesada: Mapped[bool] = mapped_column(default=False)

    licitacion: Mapped[Licitacion] = relationship(back_populates="secciones")


class Requisito(UUIDPk, Timestamps, Base):
    __tablename__ = "requisitos"

    licitacion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("licitaciones.id", ondelete="CASCADE"), index=True
    )
    seccion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("secciones.id", ondelete="CASCADE"), index=True
    )
    numeral: Mapped[str | None] = mapped_column(String(50))
    tipo: Mapped[TipoRequisito] = mapped_column(str_enum(TipoRequisito, "tipo_requisito"))
    texto_literal: Mapped[str] = mapped_column(Text)
    # RF-07: marcar para revisión humana en lugar de asumir.
    ambiguo: Mapped[bool] = mapped_column(default=False)
    motivo_ambiguedad: Mapped[str | None] = mapped_column(Text)
    # Si el texto literal aparece en su sección del pliego (HU-B18). False = posible
    # reformulación del modelo; None = aún no comprobado.
    texto_verificado: Mapped[bool | None]

    licitacion: Mapped[Licitacion] = relationship(back_populates="requisitos")
    ubicacion: Mapped["UbicacionPliego | None"] = relationship(
        back_populates="requisito", cascade="all, delete-orphan", uselist=False
    )
    items: Mapped[list["Item"]] = relationship(
        back_populates="requisito", cascade="all, delete-orphan", order_by="Item.orden"
    )


class UbicacionPliego(UUIDPk, Timestamps, Base):
    """Ubicación del texto literal en el PDF (RF-26). La calcula el código, no el modelo (P-21).

    `pagina`: donde empieza el texto. `zonas`: un rectángulo por línea resaltada, en
    coordenadas relativas a su página (0..1), independientes del zoom:
    [{"pagina": 12, "x0": .., "y0": .., "x1": .., "y1": ..}, ...]. Cada zona lleva su página
    porque un requisito puede continuar en la siguiente.
    """

    __tablename__ = "ubicaciones_pliego"

    requisito_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("requisitos.id", ondelete="CASCADE"), unique=True
    )
    pagina: Mapped[int] = mapped_column(Integer)
    zonas: Mapped[list[dict[str, float | int]]] = mapped_column(JSONB, default=list)
    precision: Mapped[PrecisionUbicacion] = mapped_column(
        str_enum(PrecisionUbicacion, "precision_ubicacion")
    )

    requisito: Mapped[Requisito] = relationship(back_populates="ubicacion")


class Item(UUIDPk, Timestamps, Base):
    """Unidad verificable de un requisito (RF-02)."""

    __tablename__ = "items"

    requisito_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("requisitos.id", ondelete="CASCADE"), index=True
    )
    orden: Mapped[int] = mapped_column(Integer, default=0)
    descripcion: Mapped[str] = mapped_column(Text)
    # Cómo se verifica: por código contra datos estructurados, o por el modelo (P-03).
    tipo_verificacion: Mapped[TipoVerificacion] = mapped_column(
        str_enum(TipoVerificacion, "tipo_verificacion"),
        default=TipoVerificacion.CUALITATIVO,
        server_default=TipoVerificacion.CUALITATIVO.value,
    )
    # Para ítems numéricos: {"campo", "operador", "valor", "valor_texto", "unidad"}.
    parametros: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Evidencia recuperada de la base de conocimiento para verificar el ítem (HU-B16):
    # [{"fragmento_id", "fuente_id", "fuente", "tipo_fuente", "texto", "distancia", "puntaje"}].
    # NULL = aún no se buscó; [] = se buscó y no hay evidencia relevante.
    candidatos: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)

    requisito: Mapped[Requisito] = relationship(back_populates="items")
    resultado: Mapped["Resultado | None"] = relationship(
        back_populates="item", cascade="all, delete-orphan", uselist=False
    )


class Resultado(UUIDPk, Timestamps, Base):
    """Estado, evidencia y faltante de un ítem (RF-04, RF-05)."""

    __tablename__ = "resultados"
    __table_args__ = (
        CheckConstraint(
            "confianza IS NULL OR (confianza >= 0 AND confianza <= 1)", name="confianza"
        ),
    )

    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("items.id", ondelete="CASCADE"), unique=True
    )
    estado: Mapped[EstadoCumplimiento] = mapped_column(
        str_enum(EstadoCumplimiento, "estado_cumplimiento")
    )
    # Soporte del estado (RF-05). Un dato estructurado: {"tipo": "dato", "fuente", "texto",
    # "verificada": true}. Una cita del modelo: {"tipo": "cita", "fragmento_id", "fuente",
    # "texto", "cita", "verificada"} (la validación de citas, P-04, fija `verificada`).
    evidencia: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    faltante: Mapped[str | None] = mapped_column(Text)
    # Q-14: umbrales sin decidir; se guarda el valor para poder aplicarlos después.
    confianza: Mapped[float | None] = mapped_column(Float)
    requiere_revision: Mapped[bool] = mapped_column(default=False)
    # Por qué el ítem pasa a revisión humana (RF-07, HU-B20). Nulo si no la requiere.
    motivo_revision: Mapped[str | None] = mapped_column(Text)
    # Qué regla o modelo produjo el estado: deja trazable el porqué (N-01).
    regla: Mapped[str | None] = mapped_column(String(500))

    item: Mapped[Item] = relationship(back_populates="resultado")
    revisiones: Mapped[list["Revision"]] = relationship(
        back_populates="resultado", cascade="all, delete-orphan", order_by="Revision.creado_en"
    )


class Revision(UUIDPk, Base):
    """Cambio hecho por una persona sobre un resultado (RF-14, N-01). Solo inserción."""

    __tablename__ = "revisiones"

    resultado_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resultados.id", ondelete="CASCADE"), index=True
    )
    usuario_id: Mapped[uuid.UUID]
    estado_anterior: Mapped[EstadoCumplimiento] = mapped_column(
        str_enum(EstadoCumplimiento, "estado_cumplimiento_anterior")
    )
    estado_nuevo: Mapped[EstadoCumplimiento] = mapped_column(
        str_enum(EstadoCumplimiento, "estado_cumplimiento_nuevo")
    )
    comentario: Mapped[str] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    resultado: Mapped[Resultado] = relationship(back_populates="revisiones")
