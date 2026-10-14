import ipaddress
import time
from typing import Any

import pytest
from starlette.requests import Request

from app.core import security
from app.core.config import Settings
from app.core.net import client_ip
from app.core.ratelimit import Limit, hit
from tests.api.conftest import make_settings
from tests.conftest import STRONG_SECRET


def test_token_hash_is_sha256_hex_and_tokens_are_random() -> None:
    a, b = security.new_token(), security.new_token()
    assert a != b and len(a) >= 43
    h = security.hash_token(a)
    assert len(h) == 64 and h != a and h == security.hash_token(a)


def test_password_hash_roundtrip_argon2id() -> None:
    h = security.hash_password("a long passphrase")
    assert h.startswith("$argon2id$")
    assert security.verify_password(h, "a long passphrase")
    assert not security.verify_password(h, "other passphrase")
    assert not security.verify_password("not-a-hash", "x")


@pytest.mark.parametrize(
    ("pw", "ok"),
    [("short", False), ("x" * 129, False), ("root-user-name", False), ("a-long-enough-pass", True)],
)
def test_password_policy(pw: str, ok: bool) -> None:
    assert (security.password_problem(pw, "root-user-name") is None) is ok


def test_csrf_token_is_deterministic_and_secret_dependent() -> None:
    s1, s2 = make_settings(), make_settings(APP_SECRET_KEY="y" * 40)
    th = security.hash_token("abc")
    assert security.csrf_token(s1, th) == security.csrf_token(s1, th)
    assert security.csrf_token(s1, th) != security.csrf_token(s2, th)
    assert security.csrf_token(s1, th) != security.csrf_token(s1, security.hash_token("abd"))


def test_totp_secret_encryption_roundtrip_and_dedicated_key() -> None:
    from cryptography.fernet import Fernet, InvalidToken

    s = make_settings()
    enc = security.encrypt_secret(s, "JBSWY3DPEHPK3PXP")
    assert "JBSWY3DPEHPK3PXP" not in enc and security.decrypt_secret(s, enc) == "JBSWY3DPEHPK3PXP"
    with pytest.raises(InvalidToken):
        security.decrypt_secret(make_settings(APP_SECRET_KEY="z" * 40), enc)
    dedicated = make_settings(TOTP_ENC_KEY=Fernet.generate_key().decode())
    enc2 = security.encrypt_secret(dedicated, "ABC")
    assert security.decrypt_secret(dedicated, enc2) == "ABC"
    with pytest.raises(InvalidToken):
        security.decrypt_secret(s, enc2)


def test_client_ref_roundtrip_binding_and_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    s = make_settings()
    ref = security.sign_client_ref(s, phone_key="902996625", slug="abc-def", client_id=5)
    assert security.read_client_ref(s, ref, "abc-def") == ("902996625", 5)
    assert security.read_client_ref(s, ref, "other") is None
    assert security.read_client_ref(s, ref + "x", "abc-def") is None
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 301)
    assert security.read_client_ref(s, ref, "abc-def") is None


def test_mask_phone_and_display() -> None:
    assert security.mask_phone("902996625") == "90*****25"
    assert security.mask_phone("+998 (90) 299-66-25") == "99" + "*" * 8 + "25"
    assert security.mask_phone("123") == "***" and security.mask_phone(None) == ""
    assert security.phone_display("902996625") == "+998 90 *** ** 25"
    assert "2996" not in security.mask_phone("902996625")


# --------------------------------------------------------------------------------- net


def _req(peer: str, xff: str | None) -> Request:
    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    return Request({"type": "http", "headers": headers, "client": (peer, 1234), "method": "GET"})


def test_client_ip_ignores_xff_without_trusted_proxy() -> None:
    assert client_ip(_req("9.9.9.9", "1.1.1.1"), []) == "9.9.9.9"
    assert client_ip(_req("9.9.9.9", "1.1.1.1"), ["10.0.0.0/8"]) == "9.9.9.9"


def test_client_ip_uses_rightmost_untrusted_hop() -> None:
    trusted = ["10.0.0.0/8", "127.0.0.1"]
    assert client_ip(_req("10.0.0.1", "1.1.1.1"), trusted) == "1.1.1.1"
    # client-supplied junk on the left cannot override what the trusted proxy appended
    assert client_ip(_req("10.0.0.1", "6.6.6.6, 1.1.1.1"), trusted) == "1.1.1.1"
    assert client_ip(_req("10.0.0.1", "1.1.1.1, 10.2.2.2"), trusted) == "1.1.1.1"
    assert client_ip(_req("10.0.0.1", "garbage"), trusted) == "10.0.0.1"
    assert client_ip(_req("10.0.0.1", None), trusted) == "10.0.0.1"
    assert ipaddress.ip_address(client_ip(_req("::1", "2001:db8::1"), ["::1"])).version == 6


# --------------------------------------------------------------------------- ratelimit


async def test_rate_limit_counts_windows_and_reports_retry_after(redis) -> None:  # type: ignore[no-untyped-def]
    lim = [Limit("k", 2, 60)]
    assert await hit(redis, lim) is None
    assert await hit(redis, lim) is None
    retry = await hit(redis, lim)
    assert retry is not None and 1 <= retry <= 60
    assert await hit(redis, [Limit("other", 2, 60)]) is None


async def test_rate_limit_max_retry_across_limits(redis) -> None:  # type: ignore[no-untyped-def]
    limits = [Limit("a", 1, 60), Limit("a", 1, 3600)]
    assert await hit(redis, limits) is None
    retry = await hit(redis, limits)
    assert retry is not None and retry > 60  # the hourly window dominates


async def test_rate_limit_window_resets_after_ttl(redis) -> None:  # type: ignore[no-untyped-def]
    lim = [Limit("r", 1, 60)]
    await hit(redis, lim)
    assert await hit(redis, lim) is not None
    await redis.delete("rl:r:60")
    assert await hit(redis, lim) is None


async def test_rate_limit_key_without_ttl_is_repaired(redis) -> None:  # type: ignore[no-untyped-def]
    await redis.set("rl:z:60", 5)  # e.g. EXPIRE lost after a crash
    assert await hit(redis, [Limit("z", 10, 60)]) is None
    assert 0 < await redis.ttl("rl:z:60") <= 60


# ---------------------------------------------------------------------------- config


def test_cookie_secure_defaults() -> None:
    assert Settings(ENV="local").cookie_secure is False
    assert Settings(ENV="test").cookie_secure is False
    prod = _prod()
    assert Settings(**prod).cookie_secure is True
    assert Settings(ENV="local", COOKIE_SECURE=True).cookie_secure is True


def test_secure_off_only_allowed_for_local_and_test() -> None:
    prod = _prod()
    with pytest.raises(ValueError, match="COOKIE_SECURE"):
        Settings(**prod, COOKIE_SECURE=False)
    assert Settings(ENV="local", COOKIE_SECURE=False).cookie_secure is False


def test_app_secret_required_outside_local() -> None:
    base: dict[str, Any] = {"ENV": "production", "SD_LOGIN": "u", "SD_PASSWORD": "p"}
    with pytest.raises(ValueError, match="APP_SECRET_KEY"):
        Settings(**base)
    with pytest.raises(ValueError, match="APP_SECRET_KEY"):
        Settings(**base, APP_SECRET_KEY="short")
    assert Settings(ENV="local", APP_SECRET_KEY="").APP_SECRET_KEY.get_secret_value()
    assert STRONG_SECRET not in repr(Settings(ENV="local", APP_SECRET_KEY=STRONG_SECRET))


def test_origin_and_proxy_lists_parse() -> None:
    s = Settings(
        ENV="local",
        ALLOWED_ORIGINS="http://a.test/, https://b.test ,",
        TRUSTED_PROXIES="10.0.0.0/8, 127.0.0.1",
    )
    assert s.allowed_origins == ["http://a.test", "https://b.test"]
    assert s.trusted_proxies == ["10.0.0.0/8", "127.0.0.1"]


# ---------------------------------------------------------------- fail-safe configuration


def _prod(**over: Any) -> dict[str, Any]:
    return {
        "ENV": "production",
        "SD_LOGIN": "u",
        "SD_PASSWORD": "p",
        "APP_SECRET_KEY": STRONG_SECRET,
        "ALLOWED_ORIGINS": "https://shop.example.uz",
        **over,
    }


def test_env_defaults_to_production_not_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENV", raising=False)
    with pytest.raises(ValueError, match="SD_LOGIN"):
        Settings()  # an unset ENV must not silently become a permissive dev mode
    assert Settings.model_fields["ENV"].default == "production"


@pytest.mark.parametrize(
    "secret",
    ["", "short", "x" * 64, "ab" * 30, "dev-only-insecure-secret-key-change-me-0000"],
)
def test_weak_or_default_secret_refused_outside_dev(secret: str) -> None:
    with pytest.raises(ValueError, match="APP_SECRET_KEY"):
        Settings(**_prod(APP_SECRET_KEY=secret))
    for env in ("staging",):
        with pytest.raises(ValueError, match="APP_SECRET_KEY"):
            Settings(**_prod(ENV=env, APP_SECRET_KEY=secret))


def test_dev_secret_allowed_only_in_local_and_test() -> None:
    assert Settings(ENV="local").APP_SECRET_KEY.get_secret_value()
    assert Settings(ENV="test").APP_SECRET_KEY.get_secret_value()
    assert Settings(**_prod()).ENV == "production"


def test_totp_enc_key_validated() -> None:
    from cryptography.fernet import Fernet

    good = Fernet.generate_key().decode()
    assert Settings(**_prod(TOTP_ENC_KEY=good)).TOTP_ENC_KEY is not None
    for bad in ("not-a-key", "AAAA", base64_zeros()):
        with pytest.raises(ValueError, match="TOTP_ENC_KEY"):
            Settings(**_prod(TOTP_ENC_KEY=bad))
    with pytest.raises(ValueError, match="TOTP_ENC_KEY"):
        Settings(ENV="local", TOTP_ENC_KEY="nope")


def base64_zeros() -> str:
    import base64

    return base64.urlsafe_b64encode(bytes(32)).decode()  # valid Fernet shape, zero entropy


@pytest.mark.parametrize(
    "origins", ["*", "https://*.example.uz", "https://a.uz, *", "ftp://a.uz", "https://a.uz/path"]
)
def test_allowed_origins_wildcards_and_garbage_rejected(origins: str) -> None:
    with pytest.raises(ValueError, match="ALLOWED_ORIGINS"):
        Settings(ENV="local", ALLOWED_ORIGINS=origins)


def test_allowed_origins_must_be_https_outside_dev() -> None:
    with pytest.raises(ValueError, match="https"):
        Settings(**_prod(ALLOWED_ORIGINS="http://shop.example.uz"))
    assert Settings(ENV="local", ALLOWED_ORIGINS="http://localhost:5173").allowed_origins


def test_docs_only_in_dev() -> None:
    assert Settings(ENV="local").docs_enabled and Settings(ENV="test").docs_enabled
    assert not Settings(**_prod()).docs_enabled


# ---------------------------------------------------------------------- rate buckets


def test_rate_buckets() -> None:
    from app.core.net import rate_buckets

    assert rate_buckets("203.0.113.77") == ("203.0.113.77", "203.0.113.0/24")
    assert rate_buckets("2001:db8:1:2:aaaa:bbbb:cccc:dddd") == (
        "2001:db8:1:2::/64",
        "2001:db8:1::/48",
    )
    assert rate_buckets("::ffff:203.0.113.5") == ("203.0.113.5", "203.0.113.0/24")
    assert rate_buckets(None) == ("unknown", "unknown")
