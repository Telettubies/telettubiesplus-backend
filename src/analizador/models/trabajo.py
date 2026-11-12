"""Trabajo de procesamiento: fuente única del estado y del progreso (ADR-015).

RabbitMQ solo transporta el identificador del trabajo; el dashboard consulta esta tabla.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from analizador.db.base import Base, Timestamps, UUIDPk
from analizador.models.enums import EstadoTrabajo, TipoTrabajo, str_enum


class TrabajoProcesamiento(UUIDPk, Timestamps, Base):
    __tablename__ = "trabajos_procesamiento"
    __table_args__ = (CheckConstraint("progreso >= 0 AND progreso <= 100", name="progreso"),)

    organizacion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizaciones.id", ondelete="CASCADE"), index=True
    )
    tipo: Mapped[TipoTrabajo] = mapped_column(str_enum(TipoTrabajo, "tipo_trabajo"))
    # Según el tipo: análisis/reevaluación → licitación; indexación → fuente de conocimiento.
    licitacion_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("licitaciones.id", ondelete="CASCADE"), index=True
    )
    fuente_conocimiento_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fuentes_conocimiento.id", ondelete="CASCADE"), index=True
    )
    estado: Mapped[EstadoTrabajo] = mapped_column(
        str_enum(EstadoTrabajo, "estado_trabajo"), default=EstadoTrabajo.EN_COLA
    )
    etapa_actual: Mapped[str | None] = mapped_column(String(60))
    progreso: Mapped[int] = mapped_column(Integer, default=0)
    intentos: Mapped[int] = mapped_column(Integer, default=0)
    # Mensaje para el usuario; sin datos sensibles (S-14).
    error: Mapped[str | None] = mapped_column(Text)
    iniciado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalizado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
