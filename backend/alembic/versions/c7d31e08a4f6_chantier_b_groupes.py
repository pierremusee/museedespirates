"""chantier_b_groupes

Règle métier groupes : seuil minimum passé à 8 personnes.
- nouveau kind `group` sur products
- reservation_items.group_size : taille du groupe (validée >= 8 côté
  service — la contrainte vit dans le code métier, comme les Pass)

Revision ID: c7d31e08a4f6
Revises: f2c4a8e91b07
Create Date: 2026-10-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7d31e08a4f6"
down_revision: Union[str, None] = "f2c4a8e91b07"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE product_kind ADD VALUE IF NOT EXISTS 'group'")
    op.add_column(
        "reservation_items",
        sa.Column("group_size", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    # PostgreSQL ne permet pas de retirer une valeur d'un enum :
    # le downgrade ne supprime que la colonne.
    op.drop_column("reservation_items", "group_size")
