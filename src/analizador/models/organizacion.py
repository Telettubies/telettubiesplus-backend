"""Organización y Miembro (RF-09, ADR-014).

Supabase solo autentica; la organización y el rol de cada usuario viven aquí.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from analizador.db.base import Base, Timestamps, UUIDPk
from analizador.models.enums import AccionMiembro, Rol, str_enum


class Organizacion(UUIDPk, Timestamps, Base):
    __tablename__ = "organizaciones"

    nombre: Mapped[str] = mapped_column(String(200))

    miembros: Mapped[list["Miembro"]] = relationship(back_populates="organizacion")


class Miembro(UUIDPk, Timestamps, Base):
    """Asociación usuario (id de Supabase) + organización + rol."""

    __tablename__ = "miembros"
    __table_args__ = (UniqueConstraint("organizacion_id", "usuario_id"),)

    organizacion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizaciones.id", ondelete="CASCADE"), index=True
    )
    # `sub` del JWT de Supabase.
    usuario_id: Mapped[uuid.UUID] = mapped_column(index=True)
    email: Mapped[str] = mapped_column(String(320))
    rol: Mapped[Rol] = mapped_column(str_enum(Rol, "rol"))
    # True desde la invitación hasta la primera petición autenticada del usuario (HU-B03).
    invitacion_pendiente: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )

    organizacion: Mapped[Organizacion] = relationship(back_populates="miembros")


class AuditoriaMiembro(UUIDPk, Base):
    """Registro inalterable de invitaciones, cambios de rol y retiros (HU-B02, HU-B03).

    Guarda el correo y los ids, no referencias: el registro sobrevive al retiro del miembro.
    """

    __tablename__ = "auditoria_miembros"

    organizacion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizaciones.id", ondelete="CASCADE"), index=True
    )
    accion: Mapped[AccionMiembro] = mapped_column(str_enum(AccionMiembro, "accion_miembro"))
    actor_usuario_id: Mapped[uuid.UUID]
    miembro_usuario_id: Mapped[uuid.UUID]
    email: Mapped[str] = mapped_column(String(320))
    rol_anterior: Mapped[Rol | None] = mapped_column(str_enum(Rol, "rol_anterior"))
    rol_nuevo: Mapped[Rol | None] = mapped_column(str_enum(Rol, "rol_nuevo"))
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
