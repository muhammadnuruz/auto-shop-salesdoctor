"""indexes for the Telegram notifier (sync_run.finished_at) and the audit browser (action)

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-14 20:00:00.000000
"""
from collections.abc import Sequence

from alembic import op

revision: str = '0007'
down_revision: str | None = '0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index('ix_sync_run_finished_at_id', 'sync_run', ['finished_at', 'id'], unique=False)
    op.create_index('ix_sync_run_entity_finished_at', 'sync_run', ['entity', 'finished_at'], unique=False)
    op.create_index('ix_audit_log_action', 'audit_log', ['action'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_audit_log_action', table_name='audit_log')
    op.drop_index('ix_sync_run_entity_finished_at', table_name='sync_run')
    op.drop_index('ix_sync_run_finished_at_id', table_name='sync_run')
