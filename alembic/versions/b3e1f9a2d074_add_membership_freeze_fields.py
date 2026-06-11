"""add_membership_freeze_fields

Revision ID: b3e1f9a2d074
Revises: 5914efb3d04c
Create Date: 2026-06-09 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b3e1f9a2d074'
down_revision: Union[str, None] = '5914efb3d04c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('membership_plans', sa.Column('max_freeze_days', sa.Integer(), nullable=True))
    op.add_column('memberships', sa.Column('freeze_start', sa.DateTime(), nullable=True))
    op.add_column('memberships', sa.Column('freeze_end', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('memberships', 'freeze_end')
    op.drop_column('memberships', 'freeze_start')
    op.drop_column('membership_plans', 'max_freeze_days')
