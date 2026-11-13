"""documentos empresariales y cambios pendientes

Revision ID: 362f8aee173c
Revises: 8a41f6c2d9b7
Create Date: 2026-11-13 09:26:36.524717

"""

from collections.abc import Sequence

import pgvector.sqlalchemy  # noqa: F401  # tipo Vector en migraciones autogeneradas
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "362f8aee173c"
down_revision: str | Sequence[str] | None = "8a41f6c2d9b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "documentos_empresa",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "organizacion_id",
            sa.Uuid(),
            sa.ForeignKey("organizaciones.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("nombre", sa.String(300), nullable=False),
        sa.Column("hash_pdf", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(1024), nullable=False),
        sa.Column("estado", sa.String(40), nullable=False),
        sa.Column("paginas", postgresql.JSONB(), nullable=False),
        sa.Column("candidatos", postgresql.JSONB(), nullable=False),
        sa.Column("aprobado_por", sa.Uuid(), nullable=True),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "actualizado_en",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("organizacion_id", "hash_pdf"),
    )
    op.create_index(
        "ix_documentos_empresa_organizacion_id", "documentos_empresa", ["organizacion_id"]
    )
    op.create_table(
        "cambios_conocimiento",
        sa.Column("secuencia", sa.BigInteger(), sa.Identity(), nullable=False, unique=True),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "organizacion_id",
            sa.Uuid(),
            sa.ForeignKey("organizaciones.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("trabajo_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("datos", postgresql.JSONB(), nullable=False),
        sa.Column("fuentes_anteriores", postgresql.JSONB(), nullable=False),
        sa.Column("documento_id", sa.Uuid(), sa.ForeignKey("documentos_empresa.id")),
        sa.Column("procedencia", postgresql.JSONB(), nullable=False),
        sa.Column("estado", sa.String(40), nullable=False),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "actualizado_en",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_cambios_conocimiento_organizacion_id", "cambios_conocimiento", ["organizacion_id"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("cambios_conocimiento")
    op.drop_table("documentos_empresa")
