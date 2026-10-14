"""auth admin links sessions audit

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-14 09:09:03.304013
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('admin_user',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('username', sa.Text(), nullable=False),
    sa.Column('full_name', sa.Text(), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=False),
    sa.Column('role', sa.Enum('superadmin', 'admin', 'operator', 'viewer', name='admin_role'), nullable=False),
    sa.Column('extra_permissions', sa.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False),
    sa.Column('totp_secret', sa.Text(), nullable=True),
    sa.Column('totp_enabled', sa.Boolean(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('must_change_password', sa.Boolean(), nullable=False),
    sa.Column('failed_logins', sa.Integer(), nullable=False),
    sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('failed_logins >= 0', name=op.f('ck_admin_user_failed_logins_nonneg')),
    sa.CheckConstraint('username = lower(username)', name=op.f('ck_admin_user_username_lower')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_admin_user')),
    sa.UniqueConstraint('username', name='uq_admin_user_username')
    )
    op.create_table('audit_log',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('actor_type', sa.Enum('admin', 'shop', 'system', name='audit_actor'), nullable=False),
    sa.Column('actor_id', sa.BigInteger(), nullable=True),
    sa.Column('action', sa.Text(), nullable=False),
    sa.Column('entity_type', sa.Text(), nullable=True),
    sa.Column('entity_id', sa.Text(), nullable=True),
    sa.Column('before', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('after', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('ip', sa.Text(), nullable=True),
    sa.Column('user_agent', sa.Text(), nullable=True),
    sa.Column('request_id', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_log'))
    )
    op.create_index('ix_audit_log_actor', 'audit_log', ['actor_type', 'actor_id'], unique=False)
    op.create_index('ix_audit_log_at', 'audit_log', [sa.literal_column('at DESC')], unique=False)
    op.create_index('ix_audit_log_entity', 'audit_log', ['entity_type', 'entity_id'], unique=False)
    op.create_table('access_link',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('slug', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('starts_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ends_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('max_logins', sa.Integer(), nullable=True),
    sa.Column('login_count', sa.Integer(), nullable=False),
    sa.Column('client_scope', sa.Enum('all', 'selected', 'territory', 'category', name='link_client_scope'), nullable=False),
    sa.Column('scope_values', sa.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False),
    sa.Column('settings', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_by', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("slug ~ '^[a-z0-9-]{3,64}$'", name=op.f('ck_access_link_slug_format')),
    sa.CheckConstraint('ends_at IS NULL OR starts_at IS NULL OR ends_at > starts_at', name=op.f('ck_access_link_period')),
    sa.CheckConstraint('login_count >= 0', name=op.f('ck_access_link_login_count_nonneg')),
    sa.CheckConstraint('max_logins IS NULL OR max_logins >= 1', name=op.f('ck_access_link_max_logins_positive')),
    sa.ForeignKeyConstraint(['created_by'], ['admin_user.id'], name=op.f('fk_access_link_created_by_admin_user'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_access_link')),
    sa.UniqueConstraint('slug', name='uq_access_link_slug')
    )
    op.create_index('ix_access_link_archived_at', 'access_link', ['archived_at'], unique=False)
    op.create_index('ix_access_link_created_by', 'access_link', ['created_by'], unique=False)
    op.create_table('admin_session',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('token_hash', sa.Text(), nullable=False),
    sa.Column('admin_user_id', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ip', sa.Text(), nullable=True),
    sa.Column('user_agent', sa.Text(), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['admin_user_id'], ['admin_user.id'], name=op.f('fk_admin_session_admin_user_id_admin_user'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_admin_session')),
    sa.UniqueConstraint('token_hash', name='uq_admin_session_token_hash')
    )
    op.create_index('ix_admin_session_admin_user_id', 'admin_session', ['admin_user_id'], unique=False)
    op.create_index('ix_admin_session_expires_at', 'admin_session', ['expires_at'], unique=False)
    op.create_table('client_setting',
    sa.Column('client_id', sa.BigInteger(), nullable=False),
    sa.Column('blocked', sa.Boolean(), nullable=False),
    sa.Column('blocked_reason', sa.Text(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('updated_by', sa.BigInteger(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['client_id'], ['sd_client.id'], name=op.f('fk_client_setting_client_id_sd_client'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['updated_by'], ['admin_user.id'], name=op.f('fk_client_setting_updated_by_admin_user'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('client_id', name=op.f('pk_client_setting'))
    )
    op.create_index('ix_client_setting_updated_by', 'client_setting', ['updated_by'], unique=False)
    op.create_table('link_client',
    sa.Column('link_id', sa.BigInteger(), nullable=False),
    sa.Column('client_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['client_id'], ['sd_client.id'], name=op.f('fk_link_client_client_id_sd_client'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['link_id'], ['access_link.id'], name=op.f('fk_link_client_link_id_access_link'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('link_id', 'client_id', name=op.f('pk_link_client'))
    )
    op.create_index('ix_link_client_client_id', 'link_client', ['client_id'], unique=False)
    op.create_table('shop_session',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('token_hash', sa.Text(), nullable=False),
    sa.Column('client_id', sa.BigInteger(), nullable=False),
    sa.Column('link_id', sa.BigInteger(), nullable=False),
    sa.Column('phone_key', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ip', sa.Text(), nullable=True),
    sa.Column('user_agent', sa.Text(), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("phone_key ~ '^[0-9]{9}$'", name=op.f('ck_shop_session_phone_key_format')),
    sa.ForeignKeyConstraint(['client_id'], ['sd_client.id'], name=op.f('fk_shop_session_client_id_sd_client'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['link_id'], ['access_link.id'], name=op.f('fk_shop_session_link_id_access_link'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_shop_session')),
    sa.UniqueConstraint('token_hash', name='uq_shop_session_token_hash')
    )
    op.create_index('ix_shop_session_client_id', 'shop_session', ['client_id'], unique=False)
    op.create_index('ix_shop_session_expires_at', 'shop_session', ['expires_at'], unique=False)
    op.create_index('ix_shop_session_link_id', 'shop_session', ['link_id'], unique=False)
    op.create_index('ix_shop_session_phone_key', 'shop_session', ['phone_key'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_shop_session_phone_key', table_name='shop_session')
    op.drop_index('ix_shop_session_link_id', table_name='shop_session')
    op.drop_index('ix_shop_session_expires_at', table_name='shop_session')
    op.drop_index('ix_shop_session_client_id', table_name='shop_session')
    op.drop_table('shop_session')
    op.drop_index('ix_link_client_client_id', table_name='link_client')
    op.drop_table('link_client')
    op.drop_index('ix_client_setting_updated_by', table_name='client_setting')
    op.drop_table('client_setting')
    op.drop_index('ix_admin_session_expires_at', table_name='admin_session')
    op.drop_index('ix_admin_session_admin_user_id', table_name='admin_session')
    op.drop_table('admin_session')
    op.drop_index('ix_access_link_created_by', table_name='access_link')
    op.drop_index('ix_access_link_archived_at', table_name='access_link')
    op.drop_table('access_link')
    op.drop_index('ix_audit_log_entity', table_name='audit_log')
    op.drop_index('ix_audit_log_at', table_name='audit_log')
    op.drop_index('ix_audit_log_actor', table_name='audit_log')
    op.drop_table('audit_log')
    op.drop_table('admin_user')
    for enum in ('audit_actor', 'link_client_scope', 'admin_role'):
        op.execute(f'DROP TYPE {enum}')
