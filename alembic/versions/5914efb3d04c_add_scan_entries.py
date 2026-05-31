"""add_scan_entries

Revision ID: 5914efb3d04c
Revises: 55d5fc347b44
Create Date: 2026-05-31 14:29:49.553725

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5914efb3d04c'
down_revision: Union[str, None] = '55d5fc347b44'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'scan_entries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=100), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('qr_card_id', sa.Integer(), nullable=True),
        sa.Column('scanned_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['qr_card_id'], ['qr_cards.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_scan_entries_id', 'scan_entries', ['id'], unique=False)
    op.create_index('ix_scan_entries_code', 'scan_entries', ['code'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_scan_entries_code', table_name='scan_entries')
    op.drop_index('ix_scan_entries_id', table_name='scan_entries')
    op.drop_table('scan_entries')
