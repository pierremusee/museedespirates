"""Billet unique par personne : table ticket_accesses, retrait des champs d'accès des tickets

Revision ID: a1c9e4f7b2d3
Revises: e8a2f50b1c39
Create Date: 2026-10-05 12:00:00.000000

Un billet = une personne ; il porte N droits d'accès (`ticket_accesses`)
consommés indépendamment au scan — le même QR passe au musée et au
théâtre. Reset des données de test assumé : pas de backfill.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "a1c9e4f7b2d3"
down_revision: Union[str, Sequence[str], None] = "e8a2f50b1c39"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    ticket_type = postgresql.ENUM(
        "open_ticket", "session_standard", "session_dining",
        name="ticket_type", create_type=False,
    )
    op.create_table(
        "ticket_accesses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("access_type", ticket_type, nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("event_id", sa.Uuid(), nullable=True),
        sa.Column("valid_date", sa.Date(), nullable=True),
        sa.Column("is_scanned", sa.Boolean(), nullable=False),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_ticket_accesses_ticket_id"),
        "ticket_accesses",
        ["ticket_id"],
    )
    op.create_index(
        op.f("ix_ticket_accesses_session_id"),
        "ticket_accesses",
        ["session_id"],
    )
    op.create_index(
        op.f("ix_ticket_accesses_event_id"),
        "ticket_accesses",
        ["event_id"],
    )

    # Les champs d'accès quittent tickets : ils vivent désormais par droit.
    op.drop_index(op.f("ix_tickets_event_id"), table_name="tickets")
    op.drop_index(op.f("ix_tickets_session_id"), table_name="tickets")
    op.drop_column("tickets", "ticket_type")
    op.drop_column("tickets", "session_id")
    op.drop_column("tickets", "event_id")
    op.drop_column("tickets", "valid_date")
    op.drop_column("tickets", "is_scanned")
    op.drop_column("tickets", "scanned_at")


def downgrade() -> None:
    ticket_type = postgresql.ENUM(
        "open_ticket", "session_standard", "session_dining",
        name="ticket_type", create_type=False,
    )
    op.add_column(
        "tickets",
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tickets",
        sa.Column(
            "is_scanned", sa.Boolean(), server_default="false", nullable=False
        ),
    )
    op.add_column("tickets", sa.Column("valid_date", sa.Date(), nullable=True))
    op.add_column("tickets", sa.Column("event_id", sa.Uuid(), nullable=True))
    op.add_column("tickets", sa.Column("session_id", sa.Uuid(), nullable=True))
    op.add_column(
        "tickets",
        sa.Column(
            "ticket_type", ticket_type,
            server_default="session_standard", nullable=False,
        ),
    )
    op.create_index(op.f("ix_tickets_session_id"), "tickets", ["session_id"])
    op.create_index(op.f("ix_tickets_event_id"), "tickets", ["event_id"])
    op.create_foreign_key(None, "tickets", "events", ["event_id"], ["id"])
    op.create_foreign_key(None, "tickets", "sessions", ["session_id"], ["id"])

    op.drop_table("ticket_accesses")
