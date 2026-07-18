"""add_sessions_and_bookings

Revision ID: a1b2c3d4e5f6
Revises: 1c9cc3e95c39
Create Date: 2026-07-05 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '1c9cc3e95c39'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Startup create_all may have already created the new tables before this
    # migration runs (deploy boots the app first), so every step is guarded
    # to keep the migration re-runnable from any partial state.
    inspector = sa.inspect(op.get_bind())
    tables = inspector.get_table_names()

    # Add group_classes to the existing enum type
    op.execute("ALTER TYPE membershipplan_type ADD VALUE IF NOT EXISTS 'group_classes'")

    # Add is_trainer to users
    if not any(c['name'] == 'is_trainer' for c in inspector.get_columns('users')):
        op.add_column('users', sa.Column('is_trainer', sa.Boolean(), server_default='false', nullable=False))

    # Add sessions_count to membership_plans
    if not any(c['name'] == 'sessions_count' for c in inspector.get_columns('membership_plans')):
        op.add_column('membership_plans', sa.Column('sessions_count', sa.Integer(), nullable=True))

    # Create sessions table
    if 'sessions' not in tables:
        op.create_table(
            'sessions',
            sa.Column('id', sa.Integer(), primary_key=True, index=True),
            sa.Column('trainer_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False, index=True),
            sa.Column('title', sa.String(200), nullable=False),
            sa.Column('start_datetime', sa.DateTime(), nullable=False, index=True),
            sa.Column('duration_minutes', sa.Integer(), nullable=False),
            sa.Column('max_capacity', sa.Integer(), nullable=False),
        )

    # Create bookings table
    if 'bookings' not in tables:
        op.create_table(
            'bookings',
            sa.Column('id', sa.Integer(), primary_key=True, index=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False, index=True),
            sa.Column('session_id', sa.Integer(), sa.ForeignKey('sessions.id'), nullable=False, index=True),
            sa.Column('status', sa.String(20), nullable=False, server_default='confirmed'),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint('user_id', 'session_id', name='uq_booking_user_session'),
        )


def downgrade() -> None:
    op.drop_table('bookings')
    op.drop_table('sessions')
    op.drop_column('users', 'is_trainer')
    op.drop_column('membership_plans', 'sessions_count')
    # PostgreSQL does not support removing enum values — downgrade leaves group_classes in the type
