"""tipo de fuente empresa

Añade `empresa` a los tipos de fuente de conocimiento (HU-B05). Las enumeraciones se guardan
como VARCHAR + CHECK y Alembic no detecta cambios en un CHECK: la restricción se reemplaza a
mano.

Revision ID: 24532171a473
Revises: 94f572e6ceaf
Create Date: 2026-11-12 11:23:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "24532171a473"
down_revision: str | Sequence[str] | None = "94f572e6ceaf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLA = "fuentes_conocimiento"
RESTRICCION = "ck_fuentes_conocimiento_tipo_fuente_conocimiento"
ANTES = ("contrato", "hoja_de_vida", "financiero", "certificacion", "capacidad")
DESPUES = ("empresa", *ANTES)


def _reemplazar_check(valores: tuple[str, ...]) -> None:
    lista = ", ".join(f"'{valor}'" for valor in valores)
    op.drop_constraint(op.f(RESTRICCION), TABLA, type_="check")
    op.create_check_constraint(op.f(RESTRICCION), TABLA, f"tipo IN ({lista})")


def upgrade() -> None:
    _reemplazar_check(DESPUES)


def downgrade() -> None:
    # Las fuentes de tipo empresa no caben en la restricción anterior.
    op.execute(f"DELETE FROM {TABLA} WHERE tipo = 'empresa'")  # noqa: S608  # constantes del módulo
    _reemplazar_check(ANTES)
