"""Chantier A — refonte billets (ticket_type, valid_date, event_id) + retrait prix events

Revision ID: b7e2a1f09c34
Revises: 5e59dc6519d3
Create Date: 2026-10-05 08:00:00.000000

DFC n°2/n°3/n°5 : les tickets deviennent typés (open_ticket / session_*),
peuvent viser une journée civile sans séance, et la tarification quitte
la table events pour le catalogue products.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b7e2a1f09c34'
down_revision: Union[str, Sequence[str], None] = '5e59dc6519d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nouvelle catégorie tarifaire « réduit » (justificatif).
    op.execute("ALTER TYPE ticket_category ADD VALUE 'reduced'")

    # Typage des billets : backfill des existants en session_standard.
    ticket_type = postgresql.ENUM(
        'open_ticket', 'session_standard', 'session_dining', name='ticket_type'
    )
    ticket_type.create(op.get_bind())
    op.add_column(
        'tickets',
        sa.Column(
            'ticket_type', ticket_type,
            server_default='session_standard', nullable=False,
        ),
    )

    # Choix de menu (dîner-spectacle) — toujours nullable (précommande facultative).
    menu_choice = postgresql.ENUM(
        'viande', 'poisson', 'vegetarien', 'enfant', name='menu_choice'
    )
    menu_choice.create(op.get_bind())
    op.add_column('tickets', sa.Column('menu_choice', menu_choice, nullable=True))

    # Un open_ticket n'a pas de séance : session_id devient optionnel.
    op.alter_column('tickets', 'session_id', nullable=True)

    # Ancrage événementiel + validité à la journée civile pour les open_tickets.
    op.add_column('tickets', sa.Column('event_id', sa.Uuid(), nullable=True))
    op.create_index(op.f('ix_tickets_event_id'), 'tickets', ['event_id'], unique=False)
    op.create_foreign_key(None, 'tickets', 'events', ['event_id'], ['id'])
    op.add_column('tickets', sa.Column('valid_date', sa.Date(), nullable=True))

    # L'argent quitte events : prix gérés par le catalogue products.
    op.drop_column('events', 'price_school')
    op.drop_column('events', 'price_group')
    op.drop_column('events', 'price_child')
    op.drop_column('events', 'price_adult')
    op.drop_column('events', 'base_price')


def downgrade() -> None:
    op.add_column('events', sa.Column('base_price', sa.Numeric(precision=10, scale=2), server_default='0', nullable=False))
    op.add_column('events', sa.Column('price_adult', sa.Numeric(precision=10, scale=2), server_default='0', nullable=False))
    op.add_column('events', sa.Column('price_child', sa.Numeric(precision=10, scale=2), server_default='0', nullable=False))
    op.add_column('events', sa.Column('price_group', sa.Numeric(precision=10, scale=2), server_default='0', nullable=False))
    op.add_column('events', sa.Column('price_school', sa.Numeric(precision=10, scale=2), server_default='0', nullable=False))

    op.drop_column('tickets', 'valid_date')
    op.drop_index(op.f('ix_tickets_event_id'), table_name='tickets')
    op.drop_column('tickets', 'event_id')
    op.alter_column('tickets', 'session_id', nullable=False)
    op.drop_column('tickets', 'menu_choice')
    op.drop_column('tickets', 'ticket_type')

    postgresql.ENUM(name='menu_choice').drop(op.get_bind())
    postgresql.ENUM(name='ticket_type').drop(op.get_bind())
    # NB : PostgreSQL ne sait pas retirer la valeur 'reduced' de l'enum
    # ticket_category — le downgrade la laisse en place (inerte).
