"""add_freeze_allowance_tracking

Adds max_freezes to membership_plans, and snapshots the freeze entitlement
onto each membership (allowance + used, for both days and freeze count) so
that plan edits never retroactively change a membership already sold.

Columns are added nullable / defaulted only. Backfilling existing rows is a
separate manual step — see scripts/backfill_freeze_allowance.sql.

Revision ID: d7a4c81b96e3
Revises: a1b2c3d4e5f6
Create Date: 2026-07-25 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd7a4c81b96e3'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('membership_plans', sa.Column('max_freezes', sa.Integer(), nullable=True))

    op.add_column('memberships', sa.Column('freeze_days_allowance', sa.Integer(), nullable=True))
    op.add_column('memberships', sa.Column('freeze_days_used', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('memberships', sa.Column('freezes_allowance', sa.Integer(), nullable=True))
    op.add_column('memberships', sa.Column('freezes_used', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('memberships', 'freezes_used')
    op.drop_column('memberships', 'freezes_allowance')
    op.drop_column('memberships', 'freeze_days_used')
    op.drop_column('memberships', 'freeze_days_allowance')
    op.drop_column('membership_plans', 'max_freezes')
