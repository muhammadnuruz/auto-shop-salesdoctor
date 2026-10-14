"""shop orders, lines, events, product settings; drop admin_user lockout columns

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-14 15:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORDER_STATES = ('draft', 'submitting', 'sent', 'failed', 'outcome_unknown', 'cancelled', 'deleted')
ORDER_EVENTS = (
    'created', 'updated', 'submitted', 'sd_ok', 'sd_error', 'outcome_unknown', 'reconciled',
    'cancelled', 'deleted', 'admin_edit', 'status_changed',
)


def upgrade() -> None:
    op.drop_constraint(op.f('ck_admin_user_failed_logins_nonneg'), 'admin_user', type_='check')
    op.drop_column('admin_user', 'failed_logins')
    op.drop_column('admin_user', 'locked_until')

    op.create_table('shop_order',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('public_id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('code_1c', sa.Text(), nullable=False),
    sa.Column('client_id', sa.BigInteger(), nullable=False),
    sa.Column('link_id', sa.BigInteger(), nullable=False),
    sa.Column('created_by_type', sa.Enum('shop', 'admin', name='order_creator'), nullable=False),
    sa.Column('created_by_admin_id', sa.BigInteger(), nullable=True),
    sa.Column('agent_sd_id', sa.Text(), nullable=True),
    sa.Column('warehouse_sd_id', sa.Text(), nullable=True),
    sa.Column('price_type_sd_id', sa.Text(), nullable=True),
    sa.Column('trade_sd_id', sa.Text(), nullable=True),
    sa.Column('shipment_date', sa.Date(), nullable=True),
    sa.Column('comment', sa.Text(), server_default='', nullable=False),
    sa.Column('state', sa.Enum(*ORDER_STATES, name='order_state'), nullable=False),
    sa.Column('sd_status', sa.SmallInteger(), nullable=True),
    sa.Column('sd_id', sa.Text(), nullable=True),
    sa.Column('total_sum', sa.Numeric(precision=18, scale=2), server_default='0', nullable=False),
    sa.Column('total_qty', sa.Numeric(precision=18, scale=3), server_default='0', nullable=False),
    sa.Column('lines_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('last_error', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('sd_synced_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('lines_count >= 0', name=op.f('ck_shop_order_lines_count_nonneg')),
    sa.CheckConstraint('sd_status IS NULL OR sd_status BETWEEN 1 AND 5', name=op.f('ck_shop_order_sd_status_range')),
    sa.CheckConstraint('version >= 1', name=op.f('ck_shop_order_version_positive')),
    sa.ForeignKeyConstraint(['client_id'], ['sd_client.id'], name=op.f('fk_shop_order_client_id_sd_client'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['created_by_admin_id'], ['admin_user.id'], name=op.f('fk_shop_order_created_by_admin_id_admin_user'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['link_id'], ['access_link.id'], name=op.f('fk_shop_order_link_id_access_link'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_shop_order')),
    sa.UniqueConstraint('code_1c', name='uq_shop_order_code_1c'),
    sa.UniqueConstraint('public_id', name='uq_shop_order_public_id')
    )
    op.create_index('ix_shop_order_client_id_created_at', 'shop_order', ['client_id', sa.literal_column('created_at DESC')], unique=False)
    op.create_index('ix_shop_order_created_at', 'shop_order', [sa.literal_column('created_at DESC')], unique=False)
    op.create_index('ix_shop_order_created_by_admin_id', 'shop_order', ['created_by_admin_id'], unique=False)
    op.create_index('ix_shop_order_link_id_created_at', 'shop_order', ['link_id', sa.literal_column('created_at DESC')], unique=False)
    op.create_index('ix_shop_order_sd_id', 'shop_order', ['sd_id'], unique=False)
    op.create_index('ix_shop_order_state', 'shop_order', ['state'], unique=False)

    op.create_table('shop_order_line',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('order_id', sa.BigInteger(), nullable=False),
    sa.Column('product_id', sa.BigInteger(), nullable=False),
    sa.Column('blocks', sa.Integer(), server_default='0', nullable=False),
    sa.Column('pieces', sa.Numeric(precision=18, scale=3), server_default='0', nullable=False),
    sa.Column('quantity', sa.Numeric(precision=18, scale=3), nullable=False),
    sa.Column('price', sa.Numeric(precision=18, scale=4), server_default='0', nullable=False),
    sa.Column('line_sum', sa.Numeric(precision=18, scale=2), server_default='0', nullable=False),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('position', sa.Integer(), server_default='0', nullable=False),
    sa.CheckConstraint('blocks >= 0', name=op.f('ck_shop_order_line_blocks_nonneg')),
    sa.CheckConstraint('pieces >= 0', name=op.f('ck_shop_order_line_pieces_nonneg')),
    sa.CheckConstraint('quantity > 0', name=op.f('ck_shop_order_line_quantity_positive')),
    sa.ForeignKeyConstraint(['order_id'], ['shop_order.id'], name=op.f('fk_shop_order_line_order_id_shop_order'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['product_id'], ['sd_product.id'], name=op.f('fk_shop_order_line_product_id_sd_product'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_shop_order_line')),
    sa.UniqueConstraint('order_id', 'product_id', name='uq_shop_order_line_order_id_product_id')
    )
    op.create_index('ix_shop_order_line_product_id', 'shop_order_line', ['product_id'], unique=False)

    op.create_table('shop_order_event',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('order_id', sa.BigInteger(), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('actor_type', sa.Enum('admin', 'shop', 'system', name='order_event_actor'), nullable=False),
    sa.Column('actor_id', sa.BigInteger(), nullable=True),
    sa.Column('event', sa.Enum(*ORDER_EVENTS, name='order_event_type'), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.ForeignKeyConstraint(['order_id'], ['shop_order.id'], name=op.f('fk_shop_order_event_order_id_shop_order'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_shop_order_event'))
    )
    op.create_index('ix_shop_order_event_order_id_at', 'shop_order_event', ['order_id', 'at'], unique=False)

    op.create_table('product_setting',
    sa.Column('product_id', sa.BigInteger(), nullable=False),
    sa.Column('hidden', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('updated_by', sa.BigInteger(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['product_id'], ['sd_product.id'], name=op.f('fk_product_setting_product_id_sd_product'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['updated_by'], ['admin_user.id'], name=op.f('fk_product_setting_updated_by_admin_user'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('product_id', name=op.f('pk_product_setting'))
    )
    op.create_index('ix_product_setting_updated_by', 'product_setting', ['updated_by'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_product_setting_updated_by', table_name='product_setting')
    op.drop_table('product_setting')
    op.drop_index('ix_shop_order_event_order_id_at', table_name='shop_order_event')
    op.drop_table('shop_order_event')
    op.drop_index('ix_shop_order_line_product_id', table_name='shop_order_line')
    op.drop_table('shop_order_line')
    for ix in ('state', 'sd_id', 'link_id_created_at', 'created_by_admin_id', 'created_at', 'client_id_created_at'):
        op.drop_index(f'ix_shop_order_{ix}', table_name='shop_order')
    op.drop_table('shop_order')
    for enum in ('order_event_type', 'order_event_actor', 'order_state', 'order_creator'):
        op.execute(f'DROP TYPE {enum}')

    op.add_column('admin_user', sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True))
    op.add_column('admin_user', sa.Column('failed_logins', sa.Integer(), server_default='0', nullable=False))
    op.alter_column('admin_user', 'failed_logins', server_default=None)
    op.create_check_constraint(op.f('ck_admin_user_failed_logins_nonneg'), 'admin_user', 'failed_logins >= 0')
