"""Phone normalization and fake-number detection (see PLAN.md 1.2 / 2.4)."""

import re
from collections import Counter

KEY_LEN = 9
_SPLIT = re.compile(r"[,;/\n]+")


def phone_key(raw: str | None) -> str | None:
    """Return the 9-digit key of a phone number, or None if it is not a valid UZ number.

    Accepts 9 digits (``905146655``) or 12 digits starting with 998. Formatting characters are
    ignored (``+998 (91) 921-67-22``). Anything else (e.g. ``"123"``, ``"+99890"``) is invalid.
    """
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) == KEY_LEN:
        return digits
    if len(digits) == 12 and digits.startswith("998"):
        return digits[3:]
    return None


def phone_keys(raw: str | None) -> list[str]:
    """All distinct valid keys in a free-text field (several numbers may be comma-separated)."""
    if not raw:
        return []
    keys: list[str] = []
    for part in _SPLIT.split(raw):
        key = phone_key(part)
        if key and key not in keys:
            keys.append(key)
    return keys


def has_repeated_digits(key: str, min_repeat: int = 7) -> bool:
    """True for numbers like 999999999, 899999999, 989999999 (>= 7 of the same digit)."""
    return max(Counter(key).values()) >= min_repeat


def evaluate_phone(key: str, client_count: int, max_clients: int) -> tuple[str, str | None]:
    """Automatic status for a phone key: ``("ok"|"suspicious", reason)``."""
    reasons: list[str] = []
    if has_repeated_digits(key):
        reasons.append("repeated_digits")
    if client_count > max_clients:
        reasons.append(f"shared_by_{client_count}_clients")
    if reasons:
        return "suspicious", ",".join(reasons)
    return "ok", None
