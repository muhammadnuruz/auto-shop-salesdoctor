import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from typing import Any

import fakeredis
import httpx
import pytest
import respx
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

os.environ.setdefault("ENV", "test")

from app.integrations.salesdoctor import SalesDoctorClient, TokenStore

# high-entropy value accepted by the production-grade APP_SECRET_KEY validation
STRONG_SECRET = "Qm7vX2pLr9TzK4bNc8WdHs1YfJg6AeUo3iPx5RtMyBn"
BASE_URL = "https://sd.test/api/v2/"
UA = "Mozilla/5.0 (compatible; AutoShop-Test/1.0)"


def ok(result: Any, **extra: Any) -> dict[str, Any]:
    return {"status": True, "result": result, **extra}


def sd_error(code: int, message: str, data: Any = None) -> dict[str, Any]:
    return {
        "status": False,
        "result": [],
        "error": {"code": code, "message": message, "data": data},
    }


def login_ok(token: str, user_id: str = "d0_92") -> dict[str, Any]:
    return ok({"userId": user_id, "token": token})


@pytest.fixture
async def redis() -> AsyncIterator[fakeredis.FakeAsyncRedis]:
    r = fakeredis.FakeAsyncRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest.fixture
def mock_sd() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
async def store(redis: fakeredis.FakeAsyncRedis) -> TokenStore:
    return TokenStore(redis, lock_timeout=5)


@pytest.fixture
async def sd(
    store: TokenStore, sleeps: list[float], mock_sd: respx.MockRouter
) -> AsyncIterator[SalesDoctorClient]:
    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    async with httpx.AsyncClient() as http:
        yield SalesDoctorClient(
            http,
            store,
            base_url=BASE_URL,
            login="user",
            password=SecretStr("s3cret-pass"),
            user_agent=UA,
            max_retries=3,
            sleep=fake_sleep,
        )


# --------------------------------------------------------------------------- Postgres fixtures

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


async def _prepare_database(url: str) -> None:
    from sqlalchemy import text
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    import app.models  # noqa: F401
    from app.db.base import Base

    target = make_url(url)
    admin = create_async_engine(
        target.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    async with admin.connect() as conn:
        exists = await conn.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": target.database}
        )
        if not exists:
            await conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    await admin.dispose()

    engine = create_async_engine(target, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


@pytest.fixture(scope="session")
def _db_ready() -> str:
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set (run via `make test`)")
    asyncio.run(_prepare_database(TEST_DATABASE_URL))
    return TEST_DATABASE_URL


@pytest.fixture
async def sessionmaker(_db_ready: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Sessions bound to one connection inside an outer transaction that is rolled back.

    ``session.commit()`` only releases a SAVEPOINT, so tests never leak data.
    """
    engine = create_async_engine(_db_ready, poolclass=NullPool)
    async with engine.connect() as conn:
        outer = await conn.begin()
        yield async_sessionmaker(
            bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        await outer.rollback()
    await engine.dispose()


@pytest.fixture
async def session(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with sessionmaker() as s:
        yield s
