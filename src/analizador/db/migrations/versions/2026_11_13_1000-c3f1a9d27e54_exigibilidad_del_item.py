"""exigibilidad del item

Revision ID: c3f1a9d27e54
Revises: 362f8aee173c
Create Date: 2026-11-13 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3f1a9d27e54"
down_revision: str | Sequence[str] | None = "362f8aee173c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VALORES = ("historico_empresa", "documento_oferta", "compromiso_ejecucion", "hito_cronograma")


def upgrade() -> None:
    """Upgrade schema."""
    # Los ítems ya extraídos se tratan como históricos: así se evaluaban hasta ahora.
    op.add_column(
        "items",
        sa.Column(
            "exigibilidad",
            sa.Enum(
                *VALORES,
                name="tipo_exigibilidad",
                native_enum=False,
                create_constraint=True,
                length=40,
            ),
            server_default="historico_empresa",
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    # El CHECK del enum se elimina con la columna.
    op.drop_column("items", "exigibilidad")
