"""Alembic: empty DB -> upgrade head -> downgrade base -> upgrade head, then no metadata drift."""

import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401
from app.core.config import get_settings
from app.db.base import Base
from tests.conftest import TEST_DATABASE_URL

DB_NAME = "autoshop_migration_test"
AUTH_TABLES = {
    "admin_user",
    "access_link",
    "link_client",
    "client_setting",
    "shop_session",
    "admin_session",
    "audit_log",
}


ORDER_TABLES = {"shop_order", "shop_order_line", "shop_order_event", "product_setting"}
ORDER_ENUMS = {"order_state", "order_creator", "order_event_actor", "order_event_type"}


async def _recreate_empty_db(url: str) -> str:
    target = make_url(url).set(database=DB_NAME)
    admin = create_async_engine(
        make_url(url).set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)'))
        await conn.execute(text(f'CREATE DATABASE "{DB_NAME}"'))
    await admin.dispose()
    return target.render_as_string(hide_password=False)


async def _inspect(url: str) -> dict[str, object]:
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.connect() as conn:

        def work(sync_conn):  # type: ignore[no-untyped-def]
            insp = inspect(sync_conn)
            tables = set(insp.get_table_names())
            diff = compare_metadata(
                MigrationContext.configure(sync_conn, opts={"compare_type": True}),
                Base.metadata,
            )
            idx = {
                t: {i["name"] for i in insp.get_indexes(t)}
                for t in (
                    "product_barcode",
                    "sd_price",
                    "sd_stock",
                    "client_phone",
                    "sync_run",
                    "audit_log",
                    "shop_session",
                    "admin_session",
                    "link_client",
                    "shop_order",
                    "shop_order_line",
                    "shop_order_event",
                )
                if t in tables
            }
            fks = {
                (t, fk["constrained_columns"][0]): fk["options"].get("ondelete")
                for t in (
                    "link_client",
                    "shop_session",
                    "client_setting",
                    "shop_order",
                    "shop_order_line",
                )
                if t in tables
                for fk in insp.get_foreign_keys(t)
            }
            uniques = {
                t: {tuple(u["column_names"]) for u in insp.get_unique_constraints(t)}
                for t in ("shop_order", "shop_order_line")
                if t in tables
            }
            admin_cols = (
                {c["name"] for c in insp.get_columns("admin_user")}
                if "admin_user" in tables
                else set()
            )
            return {
                "tables": tables,
                "diff": diff,
                "indexes": idx,
                "fks": fks,
                "uniques": uniques,
                "admin_cols": admin_cols,
            }

        result = await conn.run_sync(work)
        enums = (
            (await conn.execute(text("select typname from pg_type where typtype = 'e'")))
            .scalars()
            .all()
        )
    await engine.dispose()
    return {**result, "enums": set(enums)}


def test_migrations_roundtrip_and_no_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    url = asyncio.run(_recreate_empty_db(TEST_DATABASE_URL))
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    try:
        root = Path(__file__).resolve().parents[1]
        cfg = Config(str(root / "alembic.ini"))
        cfg.set_main_option("script_location", str(root / "alembic"))

        command.upgrade(cfg, "head")
        up = asyncio.run(_inspect(url))
        assert up["diff"] == [], f"migrations drift from models: {up['diff']}"
        idx = up["indexes"]
        assert "ix_product_barcode_barcode" not in idx["product_barcode"]  # type: ignore[index]
        assert "ix_sd_price_product_id" in idx["sd_price"]  # type: ignore[index]
        assert "ix_sd_stock_product_id" in idx["sd_stock"]  # type: ignore[index]
        assert "ix_client_phone_phone_key_client_id" in idx["client_phone"]  # type: ignore[index]
        assert {"ix_sync_run_running", "ix_sync_run_started_at"} <= idx["sync_run"]  # type: ignore[index]

        assert up["tables"] >= AUTH_TABLES  # type: ignore[operator]
        assert {"ix_audit_log_at", "ix_audit_log_entity", "ix_audit_log_actor"} <= idx["audit_log"]  # type: ignore[index]
        assert {"ix_shop_session_client_id", "ix_shop_session_link_id"} <= idx["shop_session"]  # type: ignore[index]
        assert "ix_admin_session_admin_user_id" in idx["admin_session"]  # type: ignore[index]
        assert "ix_link_client_client_id" in idx["link_client"]  # type: ignore[index]
        fks = up["fks"]
        assert fks[("link_client", "client_id")] == "RESTRICT"  # type: ignore[index]
        assert fks[("shop_session", "client_id")] == "RESTRICT"  # type: ignore[index]
        assert fks[("client_setting", "client_id")] == "RESTRICT"  # type: ignore[index]
        assert fks[("link_client", "link_id")] == "CASCADE"  # type: ignore[index]

        # Phase 4: order tables, indexes, RESTRICT/CASCADE FKs, uniqueness, dropped lockout columns
        assert up["tables"] >= ORDER_TABLES  # type: ignore[operator]
        assert up["enums"] >= ORDER_ENUMS  # type: ignore[operator]
        assert {
            "ix_shop_order_client_id_created_at",
            "ix_shop_order_state",
            "ix_shop_order_link_id_created_at",
            "ix_shop_order_created_at",
            "ix_shop_order_sd_id",
        } <= idx["shop_order"]  # type: ignore[index]
        assert "ix_shop_order_line_product_id" in idx["shop_order_line"]  # type: ignore[index]
        assert fks[("shop_order", "client_id")] == "RESTRICT"  # type: ignore[index]
        assert fks[("shop_order", "link_id")] == "RESTRICT"  # type: ignore[index]
        assert fks[("shop_order_line", "order_id")] == "CASCADE"  # type: ignore[index]
        assert fks[("shop_order_line", "product_id")] == "RESTRICT"  # type: ignore[index]
        uq = up["uniques"]
        assert ("code_1c",) in uq["shop_order"]  # type: ignore[index]
        assert ("order_id", "product_id") in uq["shop_order_line"]  # type: ignore[index]
        assert not {"failed_logins", "locked_until"} & up["admin_cols"]  # type: ignore[operator]

        # 0005 alone is reversible (back to the Phase 3 schema) and re-appliable
        command.downgrade(cfg, "0004")
        prev = asyncio.run(_inspect(url))
        assert not ORDER_TABLES & prev["tables"]  # type: ignore[operator]
        assert not ORDER_ENUMS & prev["enums"]  # type: ignore[operator]
        assert {"failed_logins", "locked_until"} <= prev["admin_cols"]  # type: ignore[operator]
        command.upgrade(cfg, "head")

        # step back to 0003 and forward again: 0004 alone is reversible
        command.downgrade(cfg, "0003")
        mid = asyncio.run(_inspect(url))
        assert not AUTH_TABLES & mid["tables"]  # type: ignore[operator]
        assert not {"admin_role", "link_client_scope", "audit_actor"} & mid["enums"]  # type: ignore[operator]
        command.upgrade(cfg, "head")

        command.downgrade(cfg, "base")
        down = asyncio.run(_inspect(url))
        assert down["tables"] == {"alembic_version"}
        assert down["enums"] == set()

        command.upgrade(cfg, "head")
        again = asyncio.run(_inspect(url))
        assert again["diff"] == []
    finally:
        get_settings.cache_clear()
