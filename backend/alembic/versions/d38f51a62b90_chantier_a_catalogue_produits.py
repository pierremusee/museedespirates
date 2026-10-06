"""Chantier A — catalogue products, lignes de commande, saisonnalité

Revision ID: d38f51a62b90
Revises: b7e2a1f09c34
Create Date: 2026-10-05 08:05:00.000000

DFC n°5 : catalogue de produits vendables (billets simples, pass,
forfaits famille), lignes de commande reservation_items portant le prix
calculé et le modificateur saison, et table des périodes haute saison.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd38f51a62b90'
down_revision: Union[str, Sequence[str], None] = 'b7e2a1f09c34'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    product_kind = postgresql.ENUM('simple', 'pass', 'family', name='product_kind')
    product_kind.create(op.get_bind())
    component_type = postgresql.ENUM(
        'museum_day', 'theater_session', 'dining_session', name='component_type'
    )
    component_type.create(op.get_bind())
    # Dans les create_table ci-dessous, les colonnes référencent les types
    # déjà créés : create_type=False évite un second CREATE TYPE.
    kind_col = postgresql.ENUM(name='product_kind', create_type=False)
    comp_col = postgresql.ENUM(name='component_type', create_type=False)

    op.create_table(
        'products',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('code', sa.String(length=50), nullable=False),
        sa.Column('label', sa.String(length=255), nullable=False),
        sa.Column('kind', kind_col, nullable=False),
        sa.Column('price_adult', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('price_child', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('price_reduced', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('family_base_price', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('extra_child_price', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code'),
    )

    op.create_table(
        'product_components',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('product_id', sa.Uuid(), nullable=False),
        sa.Column('component_type', comp_col, nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('event_id', sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], ),
        sa.ForeignKeyConstraint(['event_id'], ['events.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_product_components_product_id'), 'product_components', ['product_id'], unique=False)

    op.create_table(
        'seasonal_periods',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('end_date', sa.Date(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    # L'enum ticket_category existe déjà : réutilisé sans recréation.
    op.create_table(
        'reservation_items',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('reservation_id', sa.Uuid(), nullable=False),
        sa.Column('product_id', sa.Uuid(), nullable=False),
        sa.Column(
            'category',
            postgresql.ENUM(
                'adult', 'child', 'reduced', 'group', 'school',
                name='ticket_category', create_type=False,
            ),
            nullable=True,
        ),
        sa.Column('extra_children', sa.Integer(), nullable=False),
        sa.Column('computed_price', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('season_modifier', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.ForeignKeyConstraint(['reservation_id'], ['reservations.id'], ),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_reservation_items_reservation_id'), 'reservation_items', ['reservation_id'], unique=False)
    op.create_index(op.f('ix_reservation_items_product_id'), 'reservation_items', ['product_id'], unique=False)

    # Lien ticket -> ligne de commande (le « bundle »).
    op.add_column('tickets', sa.Column('item_id', sa.Uuid(), nullable=True))
    op.create_index(op.f('ix_tickets_item_id'), 'tickets', ['item_id'], unique=False)
    op.create_foreign_key(None, 'tickets', 'reservation_items', ['item_id'], ['id'])


def downgrade() -> None:
    op.drop_index(op.f('ix_tickets_item_id'), table_name='tickets')
    op.drop_column('tickets', 'item_id')

    op.drop_index(op.f('ix_reservation_items_product_id'), table_name='reservation_items')
    op.drop_index(op.f('ix_reservation_items_reservation_id'), table_name='reservation_items')
    op.drop_table('reservation_items')

    op.drop_table('seasonal_periods')

    op.drop_index(op.f('ix_product_components_product_id'), table_name='product_components')
    op.drop_table('product_components')

    op.drop_table('products')

    postgresql.ENUM(name='component_type').drop(op.get_bind())
    postgresql.ENUM(name='product_kind').drop(op.get_bind())
