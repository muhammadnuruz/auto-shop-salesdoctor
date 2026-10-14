"""SD connectivity probe (formerly the public /health/sd)."""

import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.deps import AdminContext, SettingsDep, require
from app.integrations.salesdoctor import SalesDoctorClient, SDError

router = APIRouter(prefix="/health", tags=["admin-health"])
ReadDep = Annotated[AdminContext, Depends(require("sync.read"))]

SD_HEALTH_TTL = 30.0


@router.get("/sd")
async def health_sd(request: Request, ctx: ReadDep, settings: SettingsDep) -> JSONResponse:
    state = request.app.state
    cached = getattr(state, "sd_health", None)
    now = time.monotonic()
    if cached is not None and cached[0] > now:
        return JSONResponse(cached[1], status_code=cached[2])

    sd: SalesDoctorClient = state.sd
    started = time.perf_counter()
    body: dict[str, Any]
    try:
        await sd.call("getWarehouse", params={"page": 1, "limit": 1})
        body, code = {"status": "ok"}, 200
    except Exception as exc:
        body, code = {"status": "error", "error": type(exc).__name__}, 503
        if settings.ENV in ("local", "test") and isinstance(exc, SDError):
            body["message"] = exc.message
    body["latency_ms"] = round((time.perf_counter() - started) * 1000)
    state.sd_health = (now + SD_HEALTH_TTL, body, code)
    return JSONResponse(body, status_code=code)
