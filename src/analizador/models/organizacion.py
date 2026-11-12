"""Organización y Miembro (RF-09, ADR-014).

Supabase solo autentica; la organización y el rol de cada usuario viven aquí.
"""

import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from analizador.db.base import Base, Timestamps, UUIDPk
from analizador.models.enums import Rol, str_enum


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

    organizacion: Mapped[Organizacion] = relationship(back_populates="miembros")
