"""reservation_expiree

Purge des paniers abandonnés : nouveau statut `expired` pour les
réservations `pending` dont le TTL de 15 minutes est dépassé — la jauge
qu'elles bloquaient est libérée.

Revision ID: e8a2f50b1c39
Revises: c7d31e08a4f6
Create Date: 2026-10-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e8a2f50b1c39"
down_revision: Union[str, None] = "c7d31e08a4f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE reservation_status ADD VALUE IF NOT EXISTS 'expired'")


def downgrade() -> None:
    # PostgreSQL ne permet pas de retirer une valeur d'un enum.
    pass
