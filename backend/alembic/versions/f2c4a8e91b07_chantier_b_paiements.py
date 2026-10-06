"""chantier_b_paiements

DFC n°7 : canaux de vente et multi-paiement.
- table payment_transactions (nominal remis vs montant imputé)
- reservations.sales_channel (web / pos)
- reservation_items.visit_date + session_ids : paramètres d'émission
  figés à la commande (les tickets ne sont émis qu'à la confirmation)
- backfill : les réservations existantes ont déjà leurs billets →
  statut confirmed, canal web.

Revision ID: f2c4a8e91b07
Revises: e5a91c7f3d28
Create Date: 2026-10-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f2c4a8e91b07"
down_revision: Union[str, None] = "e5a91c7f3d28"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    payment_method = postgresql.ENUM(
        "cb", "cash", "ancv", "check", name="payment_method"
    )
    payment_status = postgresql.ENUM("completed", name="payment_status")
    sales_channel = postgresql.ENUM("web", "pos", name="sales_channel")
    payment_method.create(op.get_bind())
    payment_status.create(op.get_bind())
    sales_channel.create(op.get_bind())

    op.create_table(
        "payment_transactions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("reservation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "method",
            postgresql.ENUM(
                "cb",
                "cash",
                "ancv",
                "check",
                name="payment_method",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("applied_amount", sa.Numeric(10, 2), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "completed", name="payment_status", create_type=False
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["reservation_id"], ["reservations.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_payment_transactions_reservation_id",
        "payment_transactions",
        ["reservation_id"],
    )

    op.add_column(
        "reservations",
        sa.Column(
            "sales_channel",
            postgresql.ENUM(
                "web", "pos", name="sales_channel", create_type=False
            ),
            server_default="web",
            nullable=False,
        ),
    )

    op.add_column(
        "reservation_items",
        sa.Column("visit_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "reservation_items",
        sa.Column(
            "session_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
    )

    # Les réservations existantes ont déjà leurs billets émis :
    # elles sont considérées soldées.
    op.execute(
        "UPDATE reservations SET status = 'confirmed' "
        "WHERE status = 'pending'"
    )


def downgrade() -> None:
    op.drop_column("reservation_items", "session_ids")
    op.drop_column("reservation_items", "visit_date")
    op.drop_column("reservations", "sales_channel")
    op.drop_index(
        "ix_payment_transactions_reservation_id",
        table_name="payment_transactions",
    )
    op.drop_table("payment_transactions")
    for enum_name in ("payment_method", "payment_status", "sales_channel"):
        postgresql.ENUM(name=enum_name).drop(op.get_bind())
