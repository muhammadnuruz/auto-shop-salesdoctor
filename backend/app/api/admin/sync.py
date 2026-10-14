"""SD sync operations (formerly /internal/sync/*)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import AdminContext, DbDep, MetaDep, require
from app.services import audit
from app.sync.context import SyncContext
from app.sync.service import ENTITIES, latest_runs, run_sync

router = APIRouter(prefix="/sync", tags=["admin-sync"])
ReadDep = Annotated[AdminContext, Depends(require("sync.read"))]
RunDep = Annotated[AdminContext, Depends(require("sync.run"))]


def _ctx(request: Request) -> SyncContext:
    ctx: SyncContext = request.app.state.sync_ctx
    return ctx


@router.get("/status")
async def sync_status(request: Request, ctx: ReadDep) -> dict[str, Any]:
    return {"entities": sorted(ENTITIES), **await latest_runs(_ctx(request))}


@router.post("/{entity}")
async def sync_now(
    entity: str, request: Request, ctx: RunDep, db: DbDep, meta: MetaDep
) -> dict[str, Any]:
    if entity not in ENTITIES:
        raise HTTPException(404, f"unknown entity; choose one of {sorted(ENTITIES)}")
    result = (await run_sync(_ctx(request), entity)).as_dict()
    audit.record(
        db,
        actor_type="admin",
        actor_id=ctx.user.id,
        action="sync.run",
        entity_type="sync",
        entity_id=entity,
        after={k: result[k] for k in ("status", "items", "warnings", "run_id")},
        meta=meta,
    )
    await db.commit()
    return result
