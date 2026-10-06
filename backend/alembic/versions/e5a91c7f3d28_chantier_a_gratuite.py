"""Chantier A — profils de gratuité sur les lignes de commande

Revision ID: e5a91c7f3d28
Revises: d38f51a62b90
Create Date: 2026-10-05 09:00:00.000000

DFC n°6 : la gratuité ne dispense jamais de billet. Un item avec un
`free_profile` émet des tickets à 0 € (jauge conservée), et un panier à
0 € bypasse l'étape de paiement.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e5a91c7f3d28'
down_revision: Union[str, Sequence[str], None] = 'd38f51a62b90'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    free_profile = postgresql.ENUM(
        'under_4', 'disability', 'pmr_companion', name='free_profile'
    )
    free_profile.create(op.get_bind())
    op.add_column(
        'reservation_items',
        sa.Column('free_profile', free_profile, nullable=True),
    )


def downgrade() -> None:
    op.drop_column('reservation_items', 'free_profile')
    postgresql.ENUM(name='free_profile').drop(op.get_bind())
