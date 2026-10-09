"""idempotence_paiements

Idempotence des encaissements (phase paiements n°1) :
- payment_transactions.idempotency_key : UUID nullable, unique quand
  renseignée — identifie l'opération logique fournie par le client.
  NULL pour les transactions historiques (non destructif, pas de
  backfill nécessaire).
- payment_transactions.response : snapshot JSONB nullable du
  PaymentResult renvoyé à l'opération initiale — le rejeu d'une même
  clé restitue cette réponse verbatim.

Revision ID: f5a8c1d93e04
Revises: e6b3f1a84c2d
Create Date: 2026-10-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f5a8c1d93e04"
down_revision: Union[str, None] = "e6b3f1a84c2d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "payment_transactions",
        sa.Column("idempotency_key", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "payment_transactions",
        sa.Column(
            "response", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
    )
    op.create_unique_constraint(
        "uq_payment_transactions_idempotency_key",
        "payment_transactions",
        ["idempotency_key"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_payment_transactions_idempotency_key",
        "payment_transactions",
        type_="unique",
    )
    op.drop_column("payment_transactions", "response")
    op.drop_column("payment_transactions", "idempotency_key")
