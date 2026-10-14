"""Crypto helpers: opaque tokens, password hashing, TOTP secret encryption, signed client refs."""

import base64
import hashlib
import hmac
import re
import secrets
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.core.config import Settings

_hasher = PasswordHasher()  # argon2id with library defaults
# Verified against when the username is unknown, so response time does not reveal existence.
DUMMY_HASH = _hasher.hash("dummy-password-for-timing-equalisation")

PASSWORD_MIN_LEN = 10
PASSWORD_MAX_LEN = 128


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def password_problem(password: str, username: str | None = None) -> str | None:
    if len(password) < PASSWORD_MIN_LEN:
        return f"password must be at least {PASSWORD_MIN_LEN} characters"
    if len(password) > PASSWORD_MAX_LEN:
        return f"password must be at most {PASSWORD_MAX_LEN} characters"
    if username and password.lower() == username.lower():
        return "password must differ from the username"
    return None


def generate_password() -> str:
    return secrets.token_urlsafe(15)


def _secret(settings: Settings) -> bytes:
    return settings.APP_SECRET_KEY.get_secret_value().encode()


def csrf_token(settings: Settings, token_hash: str) -> str:
    """Per-session CSRF token, derived from the server secret and the session token hash."""
    return hmac.new(_secret(settings), f"csrf:{token_hash}".encode(), hashlib.sha256).hexdigest()


def _fernet(settings: Settings) -> Fernet:
    if settings.TOTP_ENC_KEY is not None:
        return Fernet(settings.TOTP_ENC_KEY.get_secret_value().encode())
    digest = hashlib.sha256(b"totp-enc:" + _secret(settings)).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(settings: Settings, plaintext: str) -> str:
    return _fernet(settings).encrypt(plaintext.encode()).decode()


def decrypt_secret(settings: Settings, ciphertext: str) -> str:
    return _fernet(settings).decrypt(ciphertext.encode()).decode()


def _ref_serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_secret(settings), salt="shop-client-ref")


def sign_client_ref(settings: Settings, *, phone_key: str, slug: str, client_id: int) -> str:
    return _ref_serializer(settings).dumps({"p": phone_key, "s": slug, "c": client_id})


def read_client_ref(settings: Settings, ref: str, slug: str) -> tuple[str, int] | None:
    """Return ``(phone_key, client_id)`` for a valid, unexpired ref bound to ``slug``."""
    try:
        data: Any = _ref_serializer(settings).loads(ref, max_age=settings.CLIENT_REF_TTL)
    except BadSignature:  # includes SignatureExpired
        return None
    if not isinstance(data, dict) or data.get("s") != slug:
        return None
    phone, client_id = data.get("p"), data.get("c")
    if not isinstance(phone, str) or not isinstance(client_id, int):
        return None
    return phone, client_id


_DIGITS = re.compile(r"\D")


def mask_phone(value: str | None) -> str:
    """Mask the middle digits of a phone key / raw phone for logs, audit and display."""
    digits = _DIGITS.sub("", value or "")
    if len(digits) <= 4:
        return "*" * len(digits)
    return digits[:2] + "*" * (len(digits) - 4) + digits[-2:]


def phone_display(phone_key: str) -> str:
    return f"+998 {phone_key[:2]} *** ** {phone_key[-2:]}"
