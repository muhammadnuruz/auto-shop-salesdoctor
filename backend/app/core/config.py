import base64
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_SECRET = "dev-only-insecure-secret-key-change-me-0000"  # noqa: S105


def weak_secret(secret: str) -> bool:
    """Too short or visibly low-entropy (repeated characters, few distinct symbols)."""
    return len(secret) < 32 or len(set(secret)) < 12


def _check_fernet_key(key: str, *, strict: bool) -> None:
    try:
        raw = base64.urlsafe_b64decode(key.encode())
    except ValueError as exc:
        raise ValueError("TOTP_ENC_KEY must be a urlsafe-base64 Fernet key") from exc
    if len(raw) != 32:
        raise ValueError("TOTP_ENC_KEY must be a urlsafe-base64 Fernet key (32 bytes)")
    if strict and len(set(raw)) < 12:
        raise ValueError(
            "TOTP_ENC_KEY has too little entropy; generate it with Fernet.generate_key()"
        )


def _check_origin(origin: str, *, https_only: bool) -> None:
    if "*" in origin:
        raise ValueError("ALLOWED_ORIGINS must not contain wildcards")
    parts = urlsplit(origin)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.path not in ("", "/"):
        raise ValueError(f"ALLOWED_ORIGINS entry must look like scheme://host[:port]: {origin!r}")
    if https_only and parts.scheme != "https":
        raise ValueError("ALLOWED_ORIGINS must use https outside local/test")


class Settings(BaseSettings):
    """Application settings, loaded from environment variables (see .env.example)."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    # Fail safe: unset ENV means production (strict validation), never a permissive dev mode.
    ENV: Literal["local", "test", "staging", "production"] = "production"
    LOG_LEVEL: str = "INFO"

    DATABASE_URL: str = "postgresql+asyncpg://autoshop:autoshop@localhost:5432/autoshop"
    REDIS_URL: str = "redis://localhost:6379/0"
    # per process; total = processes (WEB_CONCURRENCY api workers + 1 arq worker) x (size+overflow)
    DB_POOL_SIZE: int = Field(10, ge=1, le=100)
    DB_MAX_OVERFLOW: int = Field(10, ge=0, le=100)

    SD_BASE_URL: str = "https://example.salesdoc.io/api/v2/"
    SD_LOGIN: str = ""
    SD_PASSWORD: SecretStr = SecretStr("")
    SD_FILIAL_ID: int | None = None
    SD_TIMEOUT: float = Field(30.0, gt=0)
    SD_MAX_RETRIES: int = Field(3, ge=0, le=10)
    SD_USER_AGENT: str = "Mozilla/5.0 (compatible; AutoShop/1.0)"
    SD_LOGIN_TIMEOUT: float = Field(15.0, gt=0)
    SD_LOCK_TIMEOUT: float = Field(30.0, gt=0)

    # --- sync (seconds) ---
    SYNC_ENABLED: bool = True
    SYNC_STOCK_INTERVAL: int = Field(60, ge=10)
    SYNC_BALANCES_INTERVAL: int = Field(300, ge=60)
    SYNC_PRODUCTS_INTERVAL: int = Field(600, ge=60)
    SYNC_PRICES_INTERVAL: int = Field(600, ge=60)
    SYNC_CLIENTS_INTERVAL: int = Field(1800, ge=60)
    SYNC_REFERENCES_INTERVAL: int = Field(1800, ge=60)
    SYNC_MAX_DEACTIVATE_RATIO: float = Field(0.10, gt=0, le=1)
    SYNC_RUN_RETENTION_DAYS: int = Field(30, ge=1)
    PHONE_MAX_CLIENTS: int = Field(3, ge=1)

    # --- security / auth (Phase 3) ---
    # Signs client_ref tokens and derives CSRF tokens (and the TOTP encryption key unless
    # TOTP_ENC_KEY is set). Required (>= 32 chars) outside local/test.
    APP_SECRET_KEY: SecretStr = SecretStr(DEV_SECRET)
    TOTP_ENC_KEY: SecretStr | None = None  # urlsafe-base64 Fernet key; default derived from secret
    ALLOWED_ORIGINS: str = "http://localhost:5173"  # comma-separated; CORS + Origin/Referer check
    TRUSTED_PROXIES: str = ""  # comma-separated IPs/CIDRs allowed to set X-Forwarded-For
    # None -> Secure cookies everywhere except ENV local/test. False is rejected elsewhere.
    COOKIE_SECURE: bool | None = None

    SHOP_SESSION_DAYS: int = Field(30, ge=1)
    ADMIN_SESSION_HOURS: int = Field(12, ge=1)  # absolute lifetime
    ADMIN_IDLE_MINUTES: int = Field(60, ge=1)
    SESSION_CACHE_TTL: int = Field(60, ge=1)  # Redis cache of valid sessions (seconds)
    SHOP_REVALIDATE_TTL: int = Field(60, ge=1)  # client/link/phone re-check interval (seconds)
    CLIENT_REF_TTL: int = Field(300, ge=30)
    ADMIN_MAX_FAILED_LOGINS: int = Field(5, ge=1)
    ADMIN_LOCKOUT_MINUTES: int = Field(15, ge=1)

    # rate limits (fixed windows): shop login
    RL_SHOP_IP_PER_MIN: int = Field(10, ge=1)
    RL_SHOP_IP_PER_HOUR: int = Field(50, ge=1)
    RL_SHOP_PHONE_PER_MIN: int = Field(5, ge=1)
    RL_SHOP_PHONE_PER_HOUR: int = Field(20, ge=1)
    RL_SHOP_SLUG_PER_MIN: int = Field(300, ge=1)
    # rate limits: admin login
    RL_ADMIN_IP_PER_MIN: int = Field(10, ge=1)
    RL_ADMIN_IP_PER_HOUR: int = Field(60, ge=1)
    RL_ADMIN_USER_PER_MIN: int = Field(5, ge=1)
    RL_ADMIN_USER_PER_HOUR: int = Field(30, ge=1)
    # secondary tier: IPv4 /24 (IPv6 /48), looser than the per-address limits
    RL_SHOP_SUBNET_PER_MIN: int = Field(60, ge=1)
    RL_SHOP_SUBNET_PER_HOUR: int = Field(300, ge=1)
    RL_ADMIN_SUBNET_PER_MIN: int = Field(30, ge=1)
    RL_ADMIN_SUBNET_PER_HOUR: int = Field(200, ge=1)
    RL_SHOP_LINKINFO_IP_PER_MIN: int = Field(60, ge=1)
    # failed shop logins on one link inside the window -> further failures answer 429 + alert
    RL_SHOP_LINK_FAIL_THRESHOLD: int = Field(100, ge=1)
    RL_SHOP_LINK_FAIL_WINDOW: int = Field(600, ge=10)
    # shop ordering (per client, per minute)
    RL_SHOP_FRESH_STOCK_PER_MIN: int = Field(12, ge=1)
    RL_SHOP_IMPORT_PER_MIN: int = Field(10, ge=1)
    RL_SHOP_SUBMIT_PER_MIN: int = Field(10, ge=1)
    # admin: password-verifying endpoints (change password, TOTP setup/enable/disable)
    RL_ADMIN_PASSWORD_PER_MIN: int = Field(5, ge=1)
    RL_ADMIN_PASSWORD_PER_HOUR: int = Field(20, ge=1)
    # admin lockout is per (username, ip); per-username failures only add a growing delay
    ADMIN_USER_DELAY_THRESHOLD: int = Field(10, ge=1)
    ADMIN_USER_DELAY_BASE: float = Field(0.5, ge=0)
    ADMIN_USER_DELAY_MAX: float = Field(8.0, ge=0)

    # retention (daily arq job)
    SESSION_PURGE_AFTER_DAYS: int = Field(7, ge=1)
    AUDIT_RETENTION_DAYS: int = Field(365, ge=30)

    @property
    def docs_enabled(self) -> bool:
        return self.ENV in ("local", "test")

    @property
    def allowed_origins(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def trusted_proxies(self) -> list[str]:
        return [p.strip() for p in self.TRUSTED_PROXIES.split(",") if p.strip()]

    @property
    def cookie_secure(self) -> bool:
        if self.COOKIE_SECURE is not None:
            return self.COOKIE_SECURE
        return self.ENV not in ("local", "test")

    @model_validator(mode="after")
    def _validate(self) -> "Settings":
        if self.SD_LOCK_TIMEOUT < self.SD_LOGIN_TIMEOUT + 5:
            raise ValueError("SD_LOCK_TIMEOUT must be >= SD_LOGIN_TIMEOUT + 5 seconds")
        if self.ENV not in ("local", "test") and not (
            self.SD_LOGIN and self.SD_PASSWORD.get_secret_value()
        ):
            raise ValueError("SD_LOGIN and SD_PASSWORD are required outside local/test")
        dev = self.ENV in ("local", "test")
        if dev and not self.APP_SECRET_KEY.get_secret_value():
            self.APP_SECRET_KEY = SecretStr(DEV_SECRET)
        if not dev:
            secret = self.APP_SECRET_KEY.get_secret_value()
            if secret == DEV_SECRET or weak_secret(secret):
                raise ValueError(
                    "APP_SECRET_KEY must be a random value of >= 32 chars (not the dev default)"
                )
            if self.COOKIE_SECURE is False:
                raise ValueError("COOKIE_SECURE=false is only allowed when ENV is local/test")
        if self.TOTP_ENC_KEY is not None:
            _check_fernet_key(self.TOTP_ENC_KEY.get_secret_value(), strict=not dev)
        for origin in self.allowed_origins:
            _check_origin(origin, https_only=not dev)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
