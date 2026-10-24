"""add_push_notifications

  * push_devices      — Expo push tokens, one per app install
  * notification_log  — what was sent, so the scheduled jobs never repeat one
  * user_preferences  — class_reminders and expiry_alerts toggles (default on)

Both new tables may already exist if the new code started before this
migration ran (app.main calls create_all on startup); the new columns can't,
since create_all never alters an existing table.

Revision ID: b9c4f2a8e6d1
Revises: a7d3e9f1c2b4
Create Date: 2026-10-24 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b9c4f2a8e6d1'
down_revision: Union[str, None] = 'a7d3e9f1c2b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    if not inspector.has_table('push_devices'):
        op.create_table(
            'push_devices',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('expo_push_token', sa.String(length=255), nullable=False),
            sa.Column('platform', sa.String(length=10), nullable=False),
            sa.Column('locale', sa.String(length=20), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('last_seen_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(['user_id'], ['users.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(op.f('ix_push_devices_id'), 'push_devices', ['id'])
        op.create_index(op.f('ix_push_devices_user_id'), 'push_devices', ['user_id'])
        op.create_index(op.f('ix_push_devices_expo_push_token'), 'push_devices', ['expo_push_token'], unique=True)

    if not inspector.has_table('notification_log'):
        op.create_table(
            'notification_log',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('kind', sa.String(length=30), nullable=False),
            sa.Column('ref_id', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(['user_id'], ['users.id']),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('kind', 'ref_id', name='uq_notification_log_kind_ref'),
        )
        op.create_index(op.f('ix_notification_log_id'), 'notification_log', ['id'])
        op.create_index(op.f('ix_notification_log_user_id'), 'notification_log', ['user_id'])

    existing = {c['name'] for c in inspector.get_columns('user_preferences')}
    for column in ('class_reminders', 'expiry_alerts'):
        if column not in existing:
            op.add_column('user_preferences', sa.Column(column, sa.Boolean(), nullable=False, server_default='true'))


def downgrade() -> None:
    op.drop_column('user_preferences', 'expiry_alerts')
    op.drop_column('user_preferences', 'class_reminders')
    op.drop_table('notification_log')
    op.drop_table('push_devices')
