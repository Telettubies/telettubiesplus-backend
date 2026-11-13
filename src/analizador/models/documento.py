"""Documentos empresariales y cambios pendientes de publicación (RF-03, S-03)."""

import uuid
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Identity, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from analizador.db.base import Base, Timestamps, UUIDPk


class DocumentoEmpresa(UUIDPk, Timestamps, Base):
    __tablename__ = "documentos_empresa"
    __table_args__ = (UniqueConstraint("organizacion_id", "hash_pdf"),)

    organizacion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizaciones.id", ondelete="CASCADE"), index=True
    )
    nombre: Mapped[str] = mapped_column(String(300))
    hash_pdf: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(1024))
    estado: Mapped[str] = mapped_column(String(40), default="en_cola")
    paginas: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    candidatos: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    aprobado_por: Mapped[uuid.UUID | None]


class CambioConocimiento(UUIDPk, Timestamps, Base):
    """La propuesta no sustituye a la base activa hasta terminar sus embeddings."""

    __tablename__ = "cambios_conocimiento"
    secuencia: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    organizacion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizaciones.id", ondelete="CASCADE"), index=True
    )
    trabajo_id: Mapped[uuid.UUID] = mapped_column(unique=True)
    datos: Mapped[dict[str, Any]] = mapped_column(JSONB)
    fuentes_anteriores: Mapped[list[str]] = mapped_column(JSONB)
    documento_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documentos_empresa.id"))
    procedencia: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    estado: Mapped[str] = mapped_column(String(40), default="pendiente")
