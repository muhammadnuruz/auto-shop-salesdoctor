"""API test harness: the real app, wired to the rolled-back test DB and fakeredis (no lifespan)."""

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import fakeredis
import httpx
import pytest
from argon2 import PasswordHasher
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import security
from app.core.config import Settings
from app.main import create_app
from app.services import admin_auth
from tests.api.helpers import Seed

ORIGIN = "http://localhost:5173"
PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def _fast_argon2(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cheap argon2 parameters keep the suite fast; production uses library defaults."""
    fast = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    monkeypatch.setattr(security, "_hasher", fast)
    dummy = fast.hash("dummy")
    monkeypatch.setattr(security, "DUMMY_HASH", dummy)
    monkeypatch.setattr(admin_auth, "DUMMY_HASH", dummy)


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "ENV": "test",
        "APP_SECRET_KEY": "test-secret-key-0123456789abcdef0123456789",
        "ALLOWED_ORIGINS": ORIGIN,
        # generous by default; rate-limit tests lower them explicitly
        "RL_SHOP_IP_PER_MIN": 1000,
        "RL_SHOP_IP_PER_HOUR": 5000,
        "RL_SHOP_PHONE_PER_MIN": 1000,
        "RL_SHOP_PHONE_PER_HOUR": 5000,
        "RL_SHOP_SLUG_PER_MIN": 1000,
        "RL_ADMIN_IP_PER_MIN": 1000,
        "RL_ADMIN_IP_PER_HOUR": 5000,
        "RL_ADMIN_USER_PER_MIN": 1000,
        "RL_ADMIN_USER_PER_HOUR": 5000,
    }
    return Settings(**{**base, **overrides})


class Api:
    """httpx client with its own cookie jar and automatic CSRF header once ``csrf`` is set."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self.http = http
        self.csrf: str | None = None

    def _h(self, headers: dict[str, str] | None, csrf: bool) -> dict[str, str]:
        out = dict(headers or {})
        if csrf and self.csrf and "X-CSRF-Token" not in out:
            out["X-CSRF-Token"] = self.csrf
        return out

    async def get(self, url: str, **kw: Any) -> httpx.Response:
        return await self.http.get(url, **kw)

    async def post(
        self,
        url: str,
        json: Any = None,
        *,
        csrf: bool = True,
        headers: dict[str, str] | None = None,
        **kw: Any,
    ) -> httpx.Response:
        return await self.http.post(url, json=json, headers=self._h(headers, csrf), **kw)

    async def put(
        self, url: str, json: Any = None, *, csrf: bool = True, **kw: Any
    ) -> httpx.Response:
        return await self.http.put(url, json=json, headers=self._h(None, csrf), **kw)

    async def patch(
        self, url: str, json: Any = None, *, csrf: bool = True, **kw: Any
    ) -> httpx.Response:
        return await self.http.patch(url, json=json, headers=self._h(None, csrf), **kw)

    async def delete(self, url: str, *, csrf: bool = True, **kw: Any) -> httpx.Response:
        return await self.http.delete(url, headers=self._h(None, csrf), **kw)

    async def admin_login(
        self, username: str, password: str = PASSWORD, totp: str | None = None
    ) -> httpx.Response:
        body = {"username": username, "password": password, "totp": totp}
        r = await self.post("/api/admin/auth/login", body, csrf=False)
        if r.status_code == 200:
            self.csrf = r.json()["csrf_token"]
        return r

    async def shop_login(self, slug: str, phone: str) -> httpx.Response:
        r = await self.post("/api/shop/auth/login", {"slug": slug, "phone": phone}, csrf=False)
        if r.status_code == 200 and r.json().get("csrf_token"):
            self.csrf = r.json()["csrf_token"]
        return r


ApiFactory = Callable[..., Awaitable[Api]]


@pytest.fixture
async def app_factory(
    sessionmaker: async_sessionmaker[AsyncSession], redis: fakeredis.FakeAsyncRedis
) -> AsyncIterator[Callable[..., Awaitable[Api]]]:
    clients: list[httpx.AsyncClient] = []

    async def factory(
        settings: Settings | None = None,
        client_host: str = "127.0.0.1",
        *,
        sd: Any = None,
        jobs: Any = None,
    ) -> Api:
        settings = settings or make_settings()
        app = create_app(settings)
        app.state.settings = settings
        app.state.sessionmaker = sessionmaker
        app.state.redis = redis
        if sd is not None:
            app.state.sd = sd
        if jobs is not None:
            app.state.jobs = jobs
        transport = httpx.ASGITransport(app=app, client=(client_host, 5000))
        scheme = "https" if settings.cookie_secure else "http"
        http = httpx.AsyncClient(transport=transport, base_url=f"{scheme}://test")
        clients.append(http)
        return Api(http)

    yield factory
    for c in clients:
        await c.aclose()


@pytest.fixture
async def api(app_factory: ApiFactory) -> Api:
    return await app_factory()


@pytest.fixture
async def seed(session: AsyncSession) -> Seed:
    s = Seed(session)
    await s.reference_data()
    return s
