"""Production tooling: healthcheck watchdog, Sentry scrubbing, browser error reports."""

import os
import signal
from pathlib import Path
from typing import Any

import pytest

from app import healthcheck
from app.core.observability import scrub_event


def test_healthcheck_restarts_only_after_consecutive_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kills: list[tuple[int, int]] = []
    monkeypatch.setattr(healthcheck, "STATE", tmp_path / "hc")
    monkeypatch.setattr(os, "kill", lambda pid, sig: kills.append((pid, sig)))
    assert healthcheck.record(False, 3) == 1
    assert healthcheck.record(False, 3) == 1
    assert healthcheck.record(True, 3) == 0  # success resets the streak
    assert healthcheck.record(False, 3) == 1
    assert healthcheck.record(False, 3) == 1
    assert kills == []
    assert healthcheck.record(False, 3) == 1
    assert kills == [(1, signal.SIGTERM)]
    assert not (tmp_path / "hc").exists()
    assert healthcheck.record(False, 1, kill=False) == 1 and len(kills) == 1


def test_healthcheck_http_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    assert healthcheck.probe_http("http://127.0.0.1:9/nothing", 0.5) is False


def test_sentry_scrubbing_drops_secrets_and_pii() -> None:
    event: dict[str, Any] = {
        "request": {
            "url": "https://shop.example/api/shop/auth/login",
            "query_string": "phone=998901234567",
            "cookies": {"shop_session": "abc"},
            "data": {"phone": "998901234567"},
            "headers": {
                "Cookie": "shop_session=abc",
                "X-CSRF-Token": "t",
                "Authorization": "Bearer x",
                "User-Agent": "UA",
            },
        },
        "user": {"ip_address": "1.2.3.4"},
        "extra": {"password": "hunter2", "note": "token=abcdef"},
    }
    out = scrub_event(event)
    req = out["request"]
    assert "cookies" not in req and "data" not in req and "query_string" not in req
    assert req["headers"] == {"User-Agent": "UA"}
    assert "user" not in out
    assert out["extra"]["password"] == "***" and "abcdef" not in out["extra"]["note"]
