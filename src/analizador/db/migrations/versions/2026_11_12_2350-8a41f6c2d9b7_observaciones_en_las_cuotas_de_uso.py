"""observaciones en las cuotas de uso

Revision ID: 8a41f6c2d9b7
Revises: 5d2c8e1a7f30
Create Date: 2026-11-12 23:50:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8a41f6c2d9b7"
down_revision: str | Sequence[str] | None = "5d2c8e1a7f30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLA = "eventos_uso"
RESTRICCION = "ck_eventos_uso_tipo_uso"
ANTES = ("pregunta_chat",)
DESPUES = (*ANTES, "observacion")


def _reemplazar_check(valores: tuple[str, ...]) -> None:
    lista = ", ".join(f"'{valor}'" for valor in valores)
    op.drop_constraint(op.f(RESTRICCION), TABLA, type_="check")
    op.create_check_constraint(op.f(RESTRICCION), TABLA, f"tipo IN ({lista})")


def upgrade() -> None:
    _reemplazar_check(DESPUES)


def downgrade() -> None:
    # Los eventos de observación no caben en la restricción anterior.
    op.execute(f"DELETE FROM {TABLA} WHERE tipo = 'observacion'")  # noqa: S608  # constantes del módulo
    _reemplazar_check(ANTES)
