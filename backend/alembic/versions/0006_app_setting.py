"""app_setting: runtime settings editable from the admin panel

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-14 18:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('app_setting',
    sa.Column('key', sa.Text(), nullable=False),
    sa.Column('value', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('updated_by', sa.BigInteger(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['updated_by'], ['admin_user.id'], name=op.f('fk_app_setting_updated_by_admin_user'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('key', name=op.f('pk_app_setting'))
    )
    op.create_index('ix_app_setting_updated_by', 'app_setting', ['updated_by'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_app_setting_updated_by', table_name='app_setting')
    op.drop_table('app_setting')
