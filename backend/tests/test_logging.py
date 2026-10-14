import json

from app.core.logging import REDACTED, configure_logging, get_logger, redact


def test_redact_keys_and_nested() -> None:
    out = redact({"token": "abc", "a": {"password": "p", "x": 1}, "l": [{"auth": {"token": "t"}}]})
    assert out == {
        "token": REDACTED,
        "a": {"password": REDACTED, "x": 1},
        "l": [{"auth": REDACTED}],
    }


def test_redact_inline_text() -> None:
    assert "deadbeef" not in redact("failed token=deadbeef host=x")
    assert "hunter2" not in redact('{"password": "hunter2"}')


def test_logger_emits_json_without_secrets(capsys) -> None:  # type: ignore[no-untyped-def]
    configure_logging("INFO")
    get_logger("t").info("hello", token="abc123", user="u")
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["event"] == "hello"
    assert line["token"] == REDACTED
    assert "abc123" not in json.dumps(line)
