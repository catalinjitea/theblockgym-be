"""add duration_months to membership_plans

Revision ID: e0d0ff428621
Revises: c5c27aea6474
Create Date: 2026-05-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e0d0ff428621'
down_revision: Union[str, None] = 'c5c27aea6474'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('membership_plans', sa.Column('duration_months', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('membership_plans', 'duration_months')
