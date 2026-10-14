from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def _state(c: TestClient) -> Any:
    return c.app.state  # type: ignore[attr-defined]


class FakeConn:
    def __init__(self, fail: bool) -> None:
        self.fail = fail

    async def __aenter__(self) -> "FakeConn":
        return self

    async def __aexit__(self, *a: object) -> None:
        return None

    async def execute(self, *_: object) -> None:
        if self.fail:
            raise RuntimeError("db down")


class FakeEngine:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def connect(self) -> FakeConn:
        return FakeConn(self.fail)

    async def dispose(self) -> None:
        return None


class FakeRedis:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def ping(self) -> bool:
        if self.fail:
            raise ConnectionError("down")
        return True

    async def aclose(self) -> None:
        return None


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app(Settings(ENV="test"))) as c:
        yield c


def test_liveness(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_ready_ok(client: TestClient) -> None:
    _state(client).engine = FakeEngine()
    _state(client).redis = FakeRedis()
    r = client.get("/health/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": "ok", "redis": "ok"}


@pytest.mark.parametrize(("db_fail", "redis_fail"), [(True, False), (False, True)])
def test_ready_failing(client: TestClient, db_fail: bool, redis_fail: bool) -> None:
    _state(client).engine = FakeEngine(db_fail)
    _state(client).redis = FakeRedis(redis_fail)
    r = client.get("/health/ready")
    assert r.status_code == 503 and r.json()["status"] == "unavailable"


def test_public_sd_probe_is_gone(client: TestClient) -> None:
    assert client.get("/health/sd").status_code == 404
