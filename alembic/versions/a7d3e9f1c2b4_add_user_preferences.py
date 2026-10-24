"""add_user_preferences

One row per member with app settings (for now the UI language), created on
first save. A separate table rather than a users column so that new code
starting before this migration can't break user queries.

Revision ID: a7d3e9f1c2b4
Revises: f3a8b2c1d9e7
Create Date: 2026-10-24 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7d3e9f1c2b4'
down_revision: Union[str, None] = 'f3a8b2c1d9e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # app.main runs Base.metadata.create_all on startup, so the table may
    # already exist if the new code started first; only the defaults differ.
    if sa.inspect(op.get_bind()).has_table('user_preferences'):
        op.alter_column('user_preferences', 'language', server_default='ro')
        op.alter_column('user_preferences', 'updated_at', server_default=sa.func.now())
        return

    op.create_table(
        'user_preferences',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('language', sa.String(length=5), nullable=False, server_default='ro'),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('user_id'),
    )


def downgrade() -> None:
    op.drop_table('user_preferences')
