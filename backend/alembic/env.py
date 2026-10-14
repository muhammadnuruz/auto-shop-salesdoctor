import asyncio

from alembic import context
from sqlalchemy.engine import Connection

import app.models  # noqa: F401  (register tables on Base.metadata)
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import create_engine

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    return get_settings().DATABASE_URL


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_engine(_url())
    async with engine.connect() as connection:
        await connection.run_sync(_do_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
