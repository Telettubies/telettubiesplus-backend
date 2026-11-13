"""almacenamiento en supabase storage

Los PDF pasan de AWS S3 a Supabase Storage (T-01): la columna con la clave del objeto deja
de llamarse `s3_key`. Solo se renombra; los datos se conservan.

Revision ID: 11c3c7b12f41
Revises: 40e3cf4b401c
Create Date: 2026-11-12 10:56:17.089416

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "11c3c7b12f41"
down_revision: str | Sequence[str] | None = "40e3cf4b401c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLAS = ("licitaciones", "fuentes_conocimiento")


def upgrade() -> None:
    for tabla in TABLAS:
        op.alter_column(tabla, "s3_key", new_column_name="storage_key")


def downgrade() -> None:
    for tabla in TABLAS:
        op.alter_column(tabla, "storage_key", new_column_name="s3_key")
