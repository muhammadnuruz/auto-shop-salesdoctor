import time

import pytest

from app.integrations.salesdoctor import SDTransportError
from tests.api.conftest import Api, ApiFactory, make_settings
from tests.api.helpers import Seed
from tests.conftest import STRONG_SECRET


class FakeSD:
    def __init__(self, exc: Exception | None = None) -> None:
        self.exc = exc
        self.calls = 0

    async def call(self, *_a: object, **_k: object) -> dict[str, object]:
        self.calls += 1
        if self.exc:
            raise self.exc
        return {}


def install(api: Api, sd: FakeSD) -> None:
    api.http._transport.app.state.sd = sd  # type: ignore[attr-defined]


async def test_public_probe_removed_and_new_one_requires_auth(api: Api) -> None:
    assert (await api.get("/health/sd")).status_code == 404
    assert (await api.get("/api/admin/health/sd")).status_code == 401
    assert (await api.get("/health")).status_code == 200  # public probes stay
    assert (await api.get("/health/ready")).status_code in (200, 503)


async def test_requires_sync_read(app_factory: ApiFactory, seed: Seed) -> None:
    await seed.admin("op", role="operator")  # no sync.read
    op = await app_factory()
    install(op, FakeSD())
    await op.admin_login("op")
    assert (await op.get("/api/admin/health/sd")).status_code == 403
    await seed.admin("v", role="viewer")
    v = await app_factory()
    install(v, FakeSD())
    await v.admin_login("v")
    assert (await v.get("/api/admin/health/sd")).status_code == 200


async def test_ok_cached_30s_and_cache_expiry(api: Api, seed: Seed) -> None:
    await seed.admin("root")
    await api.admin_login("root")
    sd = FakeSD()
    install(api, sd)
    assert (await api.get("/api/admin/health/sd")).json()["status"] == "ok"
    await api.get("/api/admin/health/sd")
    assert sd.calls == 1
    state = api.http._transport.app.state  # type: ignore[attr-defined]
    cached = state.sd_health
    state.sd_health = (time.monotonic() - 1, cached[1], cached[2])
    await api.get("/api/admin/health/sd")
    assert sd.calls == 2


async def test_errors_do_not_leak_detail_in_production(
    app_factory: ApiFactory, seed: Seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed.admin("root")
    prod = make_settings(
        ENV="production",
        SD_LOGIN="u",
        SD_PASSWORD="p",
        APP_SECRET_KEY=STRONG_SECRET,
        ALLOWED_ORIGINS="https://shop.example.uz",
    )
    api = await app_factory(prod)
    install(api, FakeSD(SDTransportError("boom detail")))
    assert (await api.admin_login("root")).status_code == 200
    r = await api.get("/api/admin/health/sd")
    assert r.status_code == 503 and r.json()["error"] == "SDTransportError"
    assert "message" not in r.json() and "boom" not in r.text


async def test_dev_shows_sd_message_but_not_foreign_exceptions(api: Api, seed: Seed) -> None:
    await seed.admin("root")
    await api.admin_login("root")
    install(api, FakeSD(RuntimeError("secret-detail")))
    r = await api.get("/api/admin/health/sd")
    assert r.status_code == 503 and "secret-detail" not in r.text
