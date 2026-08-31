"""add_promo_codes

Percentage-off discount codes for online checkout.

  * promo_codes       — the code, its discount, and who it is for
  * promo_code_users  — the named members, when audience = 'named'
  * promo_redemptions — one row per checkout that used a code, keyed by the
                        Netopia order_id so the IPN handler can find it without
                        the orderID string having to carry the discount

`audience` states the intended reach explicitly ('named' or 'everyone') rather
than inferring it from whether promo_code_users has rows. Those two are
different intentions and must not look alike in the data.

Nothing on `memberships` changes: it keeps storing the plan's list price, so the
stats join in admin.py that matches on (key, amount) is unaffected. A
membership's discount is recovered by joining payment_session_id to
promo_redemptions.order_id.

Revision ID: c1f7a92b40d3
Revises: d7a4c81b96e3
Create Date: 2026-08-31 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c1f7a92b40d3'
down_revision: Union[str, None] = 'd7a4c81b96e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'promo_codes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=50), nullable=False),
        sa.Column('discount_percent', sa.Integer(), nullable=False),
        sa.Column('audience', sa.String(length=20), nullable=False, server_default='named'),
        sa.Column('max_uses', sa.Integer(), nullable=True),
        sa.Column('max_uses_per_user', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('valid_from', sa.DateTime(), nullable=True),
        sa.Column('valid_until', sa.DateTime(), nullable=True),
        sa.Column('plan_key', sa.String(length=50), nullable=True),
        sa.Column('plan_type', sa.String(length=20), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_promo_codes_id'), 'promo_codes', ['id'])
    op.create_index(op.f('ix_promo_codes_code'), 'promo_codes', ['code'], unique=True)

    op.create_table(
        'promo_code_users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('promo_code_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['promo_code_id'], ['promo_codes.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('promo_code_id', 'user_id', name='uq_promo_code_user'),
    )
    op.create_index(op.f('ix_promo_code_users_id'), 'promo_code_users', ['id'])
    op.create_index(op.f('ix_promo_code_users_promo_code_id'), 'promo_code_users', ['promo_code_id'])
    op.create_index(op.f('ix_promo_code_users_user_id'), 'promo_code_users', ['user_id'])

    op.create_table(
        'promo_redemptions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.String(length=255), nullable=False),
        sa.Column('promo_code_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('discount_percent', sa.Integer(), nullable=False),
        sa.Column('original_amount', sa.Integer(), nullable=False),
        sa.Column('discount_amount', sa.Integer(), nullable=False),
        sa.Column('final_amount', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='pending'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('confirmed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['promo_code_id'], ['promo_codes.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_promo_redemptions_id'), 'promo_redemptions', ['id'])
    op.create_index(op.f('ix_promo_redemptions_order_id'), 'promo_redemptions', ['order_id'], unique=True)
    op.create_index(op.f('ix_promo_redemptions_promo_code_id'), 'promo_redemptions', ['promo_code_id'])
    op.create_index(op.f('ix_promo_redemptions_user_id'), 'promo_redemptions', ['user_id'])


def downgrade() -> None:
    op.drop_table('promo_redemptions')
    op.drop_table('promo_code_users')
    op.drop_table('promo_codes')
