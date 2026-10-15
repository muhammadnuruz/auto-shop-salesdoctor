"""The E2E fake Sales Doctor behaves like the real one for the calls the app makes."""

from typing import Any

import httpx
import pytest

from e2e import fake_sd
from e2e.fixtures import INITIAL_STOCK, WAREHOUSE


@pytest.fixture
async def client() -> Any:
    fake_sd.state.reset()
    transport = httpx.ASGITransport(app=fake_sd.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://fake") as c:
        yield c


async def rpc(c: httpx.AsyncClient, method: str, token: str | None, **body: Any) -> Any:
    payload: dict[str, Any] = {"method": method, **body}
    if token is not None:
        payload["auth"] = {"userId": "d0_92", "token": token}
    return (await c.post("/api/v2/", json=payload)).json()


def order(code: str, qty: float, status: int = 1) -> dict[str, Any]:
    return {
        "code_1C": code,
        "status": status,
        "warehouse": {"SD_id": WAREHOUSE},
        "orderProducts": [{"product": {"SD_id": "d0_2784"}, "quantity": qty}],
    }


async def test_login_token_required(client: httpx.AsyncClient) -> None:
    assert (await rpc(client, "getWarehouse", "nope"))["error"]["code"] == 401
    token = (await rpc(client, "login", None, auth={"login": "e2e", "password": "e2e"}))["result"][
        "token"
    ]
    assert (await rpc(client, "getWarehouse", token))["status"] is True


async def test_set_order_reserves_and_is_idempotent(client: httpx.AsyncClient) -> None:
    token = fake_sd.state.token
    start = INITIAL_STOCK["d0_2784"]
    r = await rpc(client, "setOrder", token, data={"order": [order("WEB-000001", 2)]})
    sd_id = r["result"]["data"]["order"][0]["SD_id"]
    assert fake_sd.state.stock[WAREHOUSE]["d0_2784"] == start - 2
    r = await rpc(client, "setOrder", token, data={"order": [order("WEB-000001", 3)]})
    assert r["result"]["data"]["order"][0]["SD_id"] == sd_id  # same code_1C -> same order
    assert fake_sd.state.stock[WAREHOUSE]["d0_2784"] == start - 3
    await rpc(client, "setStatus", token, data={"order": [{"code_1C": "WEB-000001", "status": 5}]})
    assert fake_sd.state.stock[WAREHOUSE]["d0_2784"] == start
    rows = (await rpc(client, "getOrder", token, params={}))["result"]["order"]
    assert rows[0]["status"] == 5


async def test_scripted_validation_error_and_reset(client: httpx.AsyncClient) -> None:
    await client.post("/__script", json={"set_order": ["validation"]})
    r = await rpc(client, "setOrder", fake_sd.state.token, data={"order": [order("WEB-2", 1)]})
    assert r["status"] is False and r["error"]["code"] == 400
    assert (await client.get("/__state")).json()["orders"] == {}
    await client.post("/__reset")
    assert fake_sd.state.script == [] and fake_sd.state.calls == {}


async def test_seed_refuses_non_e2e_database(monkeypatch: pytest.MonkeyPatch) -> None:
    from e2e import seed

    monkeypatch.setattr(
        seed,
        "get_settings",
        lambda: type(
            "S", (), {"DATABASE_URL": "postgresql+asyncpg://u:p@db/autoshop", "ENV": "local"}
        ),
    )
    with pytest.raises(SystemExit, match="refusing to seed"):
        await seed.seed("pw", load=False)
