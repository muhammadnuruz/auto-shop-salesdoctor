"""db review fixes: indexes, phone_key text+check, price scale, sync_run indexes

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-14
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PHONE_CHECK = "phone_key ~ '^[0-9]{9}$'"


def upgrade() -> None:
    # redundant: UNIQUE(barcode, product_id) already serves barcode lookups
    op.drop_index("ix_product_barcode_barcode", table_name="product_barcode")
    op.create_index("ix_sd_price_product_id", "sd_price", ["product_id"])
    op.create_index("ix_sd_stock_product_id", "sd_stock", ["product_id"])

    op.alter_column("sd_price", "price", type_=sa.Numeric(18, 4), existing_type=sa.Numeric(18, 2),
                    existing_nullable=False)

    op.drop_index("ix_client_phone_phone_key", table_name="client_phone")
    op.alter_column("client_phone", "phone_key", type_=sa.Text(), existing_type=sa.CHAR(9),
                    existing_nullable=False)
    op.alter_column("phone_flag", "phone_key", type_=sa.Text(), existing_type=sa.CHAR(9),
                    existing_nullable=False)
    op.create_check_constraint("ck_client_phone_phone_key_format", "client_phone", PHONE_CHECK)
    op.create_check_constraint("ck_phone_flag_phone_key_format", "phone_flag", PHONE_CHECK)
    op.create_index("ix_client_phone_phone_key_client_id", "client_phone", ["phone_key", "client_id"])

    op.create_index("ix_sync_run_started_at", "sync_run", [sa.literal_column("started_at DESC")])
    op.create_index("ix_sync_run_running", "sync_run", ["started_at"],
                    postgresql_where=sa.text("status = 'running'"))


def downgrade() -> None:
    op.drop_index("ix_sync_run_running", table_name="sync_run")
    op.drop_index("ix_sync_run_started_at", table_name="sync_run")
    op.drop_index("ix_client_phone_phone_key_client_id", table_name="client_phone")
    op.drop_constraint("ck_phone_flag_phone_key_format", "phone_flag", type_="check")
    op.drop_constraint("ck_client_phone_phone_key_format", "client_phone", type_="check")
    op.alter_column("phone_flag", "phone_key", type_=sa.CHAR(9), existing_type=sa.Text(),
                    existing_nullable=False)
    op.alter_column("client_phone", "phone_key", type_=sa.CHAR(9), existing_type=sa.Text(),
                    existing_nullable=False)
    op.create_index("ix_client_phone_phone_key", "client_phone", ["phone_key"])
    op.alter_column("sd_price", "price", type_=sa.Numeric(18, 2), existing_type=sa.Numeric(18, 4),
                    existing_nullable=False)
    op.drop_index("ix_sd_stock_product_id", table_name="sd_stock")
    op.drop_index("ix_sd_price_product_id", table_name="sd_price")
    op.create_index("ix_product_barcode_barcode", "product_barcode", ["barcode"])
