"""Browser error reports: logged with the path only, rate limited, Origin-checked."""

from typing import Any

import pytest

from tests.api.conftest import ORIGIN, Api, make_settings


async def test_client_error_report_is_logged_rate_limited_and_stripped(
    app_factory: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    api: Api = await app_factory(make_settings(RL_CLIENT_ERRORS_PER_MIN=2))
    body = {
        "message": "TypeError: x is undefined",
        "url": "https://shop.example/s/abc123/home?phone=998901234567#frag",
        "source": "boundary",
    }
    r = await api.post("/api/client-errors", body, csrf=False, headers={"Origin": ORIGIN})
    assert r.status_code == 204
    out = capsys.readouterr().out
    assert "client_error" in out and "/s/abc123/home" in out and "998901234567" not in out
    assert (await api.post("/api/client-errors", body, csrf=False)).status_code == 204
    assert (await api.post("/api/client-errors", body, csrf=False)).status_code == 429
    bad = await api.post(
        "/api/client-errors", body, csrf=False, headers={"Origin": "https://evil.example"}
    )
    assert bad.status_code == 403
    too_long = await api.post("/api/client-errors", {"message": "x" * 600}, csrf=False)
    assert too_long.status_code in (422, 429)
