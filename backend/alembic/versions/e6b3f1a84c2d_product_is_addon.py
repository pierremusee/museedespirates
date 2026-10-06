"""product_is_addon

Produits « add-on » : droits complémentaires à tarif réduit (séance
supplémentaire) qui ne peuvent être vendus qu'en complément d'un produit
de base couvrant les mêmes droits dans la même commande. Sans cette
contrainte, `extra_show` (5 €) offrirait une séance à moitié prix du
billet théâtre et « musée + séance supp. » (17 €) court-circuiterait le
Pass 1 Spectacle (20 €).

Revision ID: e6b3f1a84c2d
Revises: d4b8c2e61a07
Create Date: 2026-10-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e6b3f1a84c2d"
down_revision: Union[str, None] = "d4b8c2e61a07"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column(
            "is_addon",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("products", "is_addon")
