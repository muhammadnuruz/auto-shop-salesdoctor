import pytest

from app.domain.phone import evaluate_phone, has_repeated_digits, phone_key, phone_keys


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+998 (91) 921-67-22", "919216722"),
        ("+998905146655", "905146655"),
        ("998902996625", "902996625"),
        ("905146655", "905146655"),
        ("90 514 66 55", "905146655"),
        ("", None),
        (None, None),
        ("123", None),
        ("+99890", None),
        ("8 905 146 655 0", None),  # 10 digits
        ("123456789012", None),  # 12 digits not starting with 998
        ("+998 90 514 66 5", None),  # 11 digits
        ("abc", None),
    ],
)
def test_phone_key(raw: str | None, expected: str | None) -> None:
    assert phone_key(raw) == expected


def test_phone_keys_multiple_and_dedupe() -> None:
    raw = "+998 90 514-66-55, 905146655; 998 91 921 67 22 / 12"
    assert phone_keys(raw) == ["905146655", "919216722"]
    assert phone_keys(None) == [] and phone_keys("n/a") == []


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("999999999", True),
        ("899999999", True),
        ("989999999", True),
        ("998999999", True),
        ("999999123", False),
        ("905146655", False),
        ("111111234", False),
        ("111111223", False),
        ("000000012", True),
    ],
)
def test_repeated_digits(key: str, expected: bool) -> None:
    assert has_repeated_digits(key) is expected


def test_evaluate_phone() -> None:
    assert evaluate_phone("905146655", 1, 3) == ("ok", None)
    assert evaluate_phone("905146655", 3, 3) == ("ok", None)
    assert evaluate_phone("905146655", 4, 3) == ("suspicious", "shared_by_4_clients")
    assert evaluate_phone("999999999", 1, 3) == ("suspicious", "repeated_digits")
    assert evaluate_phone("999999999", 12, 3) == (
        "suspicious",
        "repeated_digits,shared_by_12_clients",
    )
