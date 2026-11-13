"""Registro de uso por organización para aplicar cuotas (S-08, P-23, HU-B26)."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column

from analizador.db.base import Base, UUIDPk
from analizador.models.enums import TipoUso, str_enum


class EventoUso(UUIDPk, Base):
    """Una acción que consume créditos y cuenta para la cuota de su organización.

    Los análisis no se registran aquí: se cuentan por las licitaciones creadas.
    """

    __tablename__ = "eventos_uso"
    __table_args__ = (Index("ix_eventos_uso_cuota", "organizacion_id", "tipo", "creado_en"),)

    organizacion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizaciones.id", ondelete="CASCADE")
    )
    tipo: Mapped[TipoUso] = mapped_column(str_enum(TipoUso, "tipo_uso"))
    usuario_id: Mapped[uuid.UUID]
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
