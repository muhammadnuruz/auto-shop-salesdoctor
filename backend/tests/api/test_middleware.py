import pytest
from sqlalchemy import select

from app.models import AuditLog
from tests.api.conftest import ORIGIN, Api, ApiFactory, make_settings
from tests.api.helpers import Seed
from tests.conftest import STRONG_SECRET


async def test_request_id_generated_and_propagated(api: Api) -> None:
    r = await api.get("/health")
    assert len(r.headers["x-request-id"]) == 32
    r = await api.get("/health", headers={"X-Request-ID": "client-req-12345"})
    assert r.headers["x-request-id"] == "client-req-12345"


async def test_invalid_request_id_is_replaced(api: Api) -> None:
    r = await api.get("/health", headers={"X-Request-ID": "bad id\twith junk"})
    assert r.headers["x-request-id"] != "bad id\twith junk" and len(r.headers["x-request-id"]) == 32


async def test_request_id_bound_to_logs_and_audit(
    api: Api, seed: Seed, caplog: pytest.LogCaptureFixture
) -> None:
    admin = await seed.admin("root")
    await seed.link(admin)
    caplog.set_level("INFO")
    r = await api.shop_login("shop-link", "998902996625")
    assert r.status_code == 401
    rid = r.headers["x-request-id"]
    audit_rows = (await seed.s.scalars(select(AuditLog))).all()
    assert any(a.request_id == rid for a in audit_rows)
    assert f"'request_id': '{rid}'" in caplog.text  # bound into structlog
    assert "shop_login_denied" in caplog.text
    # phone numbers are never logged in full
    assert "902996625" not in caplog.text and "90*****25" in caplog.text


async def test_security_headers(api: Api) -> None:
    r = await api.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert "cache-control" not in r.headers  # only /api/ responses are no-store
    r = await api.get("/api/shop/me")
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-frame-options"] == "DENY"


async def test_security_headers_on_errors_and_docs(api: Api) -> None:
    r = await api.get("/nope")
    assert r.status_code == 404 and r.headers["x-content-type-options"] == "nosniff"
    docs = await api.get("/docs")
    assert docs.status_code == 200 and "content-security-policy" not in docs.headers


async def test_cors_allows_configured_origin_with_credentials(api: Api) -> None:
    r = await api.http.options(
        "/api/admin/auth/login",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-csrf-token",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == ORIGIN
    assert r.headers["access-control-allow-credentials"] == "true"
    assert "x-csrf-token" in r.headers["access-control-allow-headers"].lower()


async def test_cors_rejects_other_origins(app_factory: ApiFactory) -> None:
    api = await app_factory(make_settings(ALLOWED_ORIGINS="https://shop.example.uz"))
    r = await api.http.options(
        "/api/admin/auth/login",
        headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in r.headers
    r = await api.get("/health", headers={"Origin": "https://shop.example.uz"})
    assert r.headers["access-control-allow-origin"] == "https://shop.example.uz"


async def test_hsts_only_when_cookies_are_secure(app_factory: ApiFactory) -> None:
    plain = await app_factory()
    assert "strict-transport-security" not in (await plain.get("/health")).headers
    secure = await app_factory(make_settings(COOKIE_SECURE=True))
    h = (await secure.get("/health")).headers["strict-transport-security"]
    assert "max-age=31536000" in h and "includeSubDomains" in h


async def test_docs_and_openapi_disabled_outside_dev(app_factory: ApiFactory) -> None:
    prod = make_settings(
        ENV="production",
        SD_LOGIN="u",
        SD_PASSWORD="p",
        APP_SECRET_KEY=STRONG_SECRET,
        ALLOWED_ORIGINS="https://shop.example.uz",
    )
    api = await app_factory(prod)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert (await api.get(path)).status_code == 404, path
    assert (await api.get("/health")).status_code == 200
    dev = await app_factory()
    assert (await dev.get("/openapi.json")).status_code == 200
