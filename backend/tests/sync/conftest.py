import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.integrations.salesdoctor import SalesDoctorClient, SDCredentials, TokenStore
from app.sync.context import SyncContext
from tests.conftest import BASE_URL, ok


class FakeSD:
    """Canned SD server: ``responses[method]`` is a JSON payload or ``f(body) -> payload``."""

    def __init__(self) -> None:
        self.responses: dict[str, Any] = {}
        self.calls: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls.append(body)
        resp = self.responses[body["method"]]
        return httpx.Response(200, json=resp(body) if callable(resp) else resp)

    def calls_of(self, method: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["method"] == method]

    def set_list(
        self, method: str, key: str, rows: list[dict[str, Any]], paged: bool = False
    ) -> None:
        extra = {"pagination": {"limit": 500, "total": len(rows), "page": 1}} if paged else {}
        self.responses[method] = ok({key: rows}, **extra)


@pytest.fixture
def fake_sd(mock_sd: respx.MockRouter) -> FakeSD:
    server = FakeSD()
    mock_sd.post(BASE_URL).mock(side_effect=server.handler)
    return server


@pytest.fixture
async def sync_ctx(
    fake_sd: FakeSD,
    sessionmaker: async_sessionmaker[AsyncSession],
    store: TokenStore,
    redis: Any,
) -> Any:
    await store.set(SDCredentials("d0_1", "tok"))
    async with httpx.AsyncClient() as http:
        sd = SalesDoctorClient(
            http, store, base_url=BASE_URL, login="u", password=SecretStr("p"), max_retries=0
        )
        yield SyncContext(sessionmaker, sd, redis, Settings(ENV="test", PHONE_MAX_CLIENTS=3))


def ent(sd_id: str) -> dict[str, str]:
    return {"CS_id": sd_id, "SD_id": sd_id, "code_1C": ""}


def product(sd_id: str, name: str = "P", barcode: str | None = None, **kw: Any) -> dict[str, Any]:
    return {
        "CS_id": sd_id,
        "SD_id": sd_id,
        "code_1C": None,
        "name": name,
        "active": "Y",
        "sort": 5,
        "packQuantity": 12,
        "volume": 0.2,
        "weight": 0,
        "barCode": barcode,
        "imageUrl": "http://i/1.jpg",
        "thumbUrl": "http://i/1t.jpg",
        "imageID": "7",
        "category": ent("c1"),
        "subCategory": ent(""),
        "unit": ent("u1"),
        "brand": {"CS_id": None, "SD_id": None, "code_1C": None},
        "trade": ent("1"),
        **kw,
    }


def client(sd_id: str, tel: str | None, name: str = "Shop", **kw: Any) -> dict[str, Any]:
    return {
        "CS_id": sd_id,
        "SD_id": sd_id,
        "code_1C": None,
        "name": name,
        "firmName": None,
        "address": "addr",
        "waymark": None,
        "tel": tel,
        "lon": 0,
        "lat": 0,
        "active": "Y",
        "updatedAt": "2026-05-30 17:02:31",
        "createdAt": None,
        "category": ent("k1"),
        "type": ent("1"),
        "city": ent("c"),
        "expeditor": ent("e"),
        "agents": [{"id": "d0_11", "code": None, "days": [3]}],
        **kw,
    }


def stock_resp(wh: dict[str, list[tuple[str, float]]]) -> dict[str, Any]:
    return ok(
        {
            "warehouse": [
                {
                    "SD_id": w,
                    "name": w,
                    "active": "Y",
                    "products": [{"SD_id": p, "quantity": q} for p, q in prods],
                }
                for w, prods in wh.items()
            ]
        }
    )


Responder = Callable[[dict[str, Any]], Any]
