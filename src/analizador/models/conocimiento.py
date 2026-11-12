"""Base de conocimiento de la organización (RF-03, RF-10) y fragmentos indexados (C-06)."""

import uuid
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from analizador.db.base import Base, Timestamps, UUIDPk
from analizador.models.enums import OrigenFragmento, TipoFuenteConocimiento, str_enum


class FuenteConocimiento(UUIDPk, Timestamps, Base):
    """Documento o registro de la base de conocimiento.

    Q-05 (formato de carga) sigue abierta: se admite un archivo en S3, datos
    estructurados, o ambos. Ajustar cuando se decida.
    """

    __tablename__ = "fuentes_conocimiento"

    organizacion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizaciones.id", ondelete="CASCADE"), index=True
    )
    tipo: Mapped[TipoFuenteConocimiento] = mapped_column(
        str_enum(TipoFuenteConocimiento, "tipo_fuente_conocimiento")
    )
    titulo: Mapped[str] = mapped_column(String(300))
    s3_key: Mapped[str | None] = mapped_column(String(1024))
    datos: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class Fragmento(UUIDPk, Timestamps, Base):
    """Texto con su representación vectorial (ADR-013).

    La dimensión del vector depende del modelo de embeddings (P-10), aún sin decidir:
    la columna se declara sin dimensión fija y el índice (HNSW/IVFFlat) se crea en una
    migración posterior, cuando se fije el modelo.
    """

    __tablename__ = "fragmentos"

    organizacion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizaciones.id", ondelete="CASCADE"), index=True
    )
    origen: Mapped[OrigenFragmento] = mapped_column(str_enum(OrigenFragmento, "origen_fragmento"))
    fuente_conocimiento_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fuentes_conocimiento.id", ondelete="CASCADE"), index=True
    )
    licitacion_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("licitaciones.id", ondelete="CASCADE"), index=True
    )
    texto: Mapped[str] = mapped_column(Text)
    pagina: Mapped[int | None] = mapped_column(Integer)
    embedding: Mapped[list[float] | None] = mapped_column(Vector())
    metadatos: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
