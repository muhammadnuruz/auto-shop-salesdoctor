"""A stateful fake Sales Doctor API for Playwright E2E and load tests.

Speaks the same single-endpoint JSON protocol as SD (``POST /api/v2/`` with ``method``) for
everything the app calls outside of the mirror sync: ``login``, ``getWarehouse`` (health),
``getStock``, ``getOrder``, ``setOrder``, ``setStatus``, ``setDeletedOrder``. Stock is reserved
on ``setOrder`` and restored on status 5, like the real server. The mirror itself is seeded
directly into Postgres by ``e2e.seed`` (the e2e stack runs with ``SYNC_ENABLED=false``).

Test-only control endpoints (no auth; the service is only reachable inside the compose
network and from 127.0.0.1):

* ``GET  /__state``  -> orders, stock, call counts
* ``POST /__reset``  -> restore the initial stock and forget orders
* ``POST /__script`` -> ``{"set_order": ["validation" | "timeout" | "ok", ...]}`` outcomes
* ``POST /__latency`` -> ``{"ms": 150}`` artificial delay per call (load tests)
"""

from __future__ import annotations

import asyncio
import copy
import secrets
from collections import Counter
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from e2e.fixtures import INITIAL_STOCK, WAREHOUSE

app = FastAPI(title="fake-sd", docs_url=None, redoc_url=None, openapi_url=None)


class State:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.stock: dict[str, dict[str, float]] = {WAREHOUSE: dict(INITIAL_STOCK)}
        self.orders: dict[str, dict[str, Any]] = {}
        self.deleted: list[str] = []
        self.calls: Counter[str] = Counter()
        self.script: list[str] = []
        self.latency_ms = 0
        self.token = secrets.token_hex(8)
        self.seq = 70000

    def reserve(self, order: dict[str, Any], sign: int) -> None:
        wh = order["warehouse"]["SD_id"]
        for line in order["orderProducts"]:
            ref = line["product"]["SD_id"]
            self.stock.setdefault(wh, {})
            self.stock[wh][ref] = self.stock[wh].get(ref, 0) + sign * float(line["quantity"])


state = State()


def ok(result: Any, **extra: Any) -> JSONResponse:
    return JSONResponse({"status": True, "result": result, **extra})


def err(code: int, message: str, data: Any = None) -> JSONResponse:
    return JSONResponse(
        {"status": False, "result": [], "error": {"code": code, "message": message, "data": data}}
    )


@app.post("/api/v2/")
async def rpc(request: Request) -> JSONResponse:
    body = await request.json()
    method = body.get("method", "")
    state.calls[method] += 1
    if state.latency_ms:
        await asyncio.sleep(state.latency_ms / 1000)
    if method == "login":
        return ok({"userId": "d0_92", "token": state.token})
    if (body.get("auth") or {}).get("token") != state.token:
        return err(401, "Unauthorized")
    if method == "getWarehouse":
        return ok({"warehouse": [{"SD_id": WAREHOUSE, "name": "Основной склад", "active": True}]})
    if method == "getStock":
        rows = [
            {
                "SD_id": wh,
                "name": wh,
                "active": True,
                "products": [{"SD_id": ref, "quantity": qty} for ref, qty in products.items()],
            }
            for wh, products in state.stock.items()
        ]
        return ok({"warehouse": rows})
    if method == "getOrder":
        rows = [
            {
                "SD_id": o["SD_id"],
                "code_1C": code,
                "status": o["status"],
                "orderProducts": o["orderProducts"],
            }
            for code, o in state.orders.items()
        ]
        return ok({"order": rows}, pagination={"limit": 1000, "total": len(rows), "page": 1})
    if method == "setOrder":
        return set_order(body)
    if method == "setStatus":
        for change in body["data"]["order"]:
            order = state.orders.get(change["code_1C"])
            if order is None:
                return err(400, "not found")
            if order["status"] != 5 and change["status"] == 5:
                state.reserve(order, +1)
            order["status"] = change["status"]
        return ok({"completed": 1, "error": 0, "data": {}})
    if method == "setDeletedOrder":
        for ref in body["data"]["deletedOrder"]:
            code = ref["code_1C"]
            state.deleted.append(code)
            order = state.orders.get(code)
            if order is not None and order["status"] != 5:
                state.reserve(order, +1)
                order["status"] = 5
        return ok({"completed": 1, "error": 0, "data": {}})
    return err(400, f"fake-sd: method {method} not implemented")


def set_order(body: dict[str, Any]) -> JSONResponse:
    action = state.script.pop(0) if state.script else "ok"
    (order,) = body["data"]["order"]
    if action == "validation":
        return err(
            400,
            "Validation error",
            {"orderProduct": [{"index": 0, "errors": ["Product is blocked"]}]},
        )
    if action == "unavailable":
        return JSONResponse({"detail": "down"}, status_code=503)
    code = order["code_1C"]
    old = state.orders.get(code)
    if old is not None and old["status"] != 5:
        state.reserve(old, +1)
    sd_id = old["SD_id"] if old else f"d0_{state.seq}"
    if old is None:
        state.seq += 1
    stored = {**copy.deepcopy(order), "SD_id": sd_id}
    state.orders[code] = stored
    if stored["status"] != 5:
        state.reserve(stored, -1)
    data = {"order": [{"CS_id": "", "SD_id": sd_id, "code_1C": code}]}
    return ok({"completed": 1, "error": 0, "data": data})


# ----------------------------------------------------------------------------- test control


@app.get("/__state")
async def get_state() -> dict[str, Any]:
    return {
        "orders": state.orders,
        "stock": state.stock,
        "deleted": state.deleted,
        "calls": dict(state.calls),
    }


@app.post("/__reset")
async def reset() -> dict[str, str]:
    state.reset()
    return {"status": "ok"}


@app.post("/__script")
async def script(request: Request) -> dict[str, Any]:
    body = await request.json()
    state.script = [str(x) for x in body.get("set_order", [])]
    return {"set_order": state.script}


@app.post("/__latency")
async def latency(request: Request) -> dict[str, int]:
    body = await request.json()
    state.latency_ms = max(0, min(int(body.get("ms", 0)), 10_000))
    return {"ms": state.latency_ms}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
