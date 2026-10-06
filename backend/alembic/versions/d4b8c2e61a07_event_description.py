"""event_description

Description éditoriale optionnelle sur les événements — utilisée pour les
productions du Théâtre du Kraken (affiche des pièces côté billetterie).

Revision ID: d4b8c2e61a07
Revises: a1c9e4f7b2d3
Create Date: 2026-10-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4b8c2e61a07"
down_revision: Union[str, None] = "a1c9e4f7b2d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "events", sa.Column("description", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("events", "description")
