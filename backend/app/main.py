from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text

from app.api.admin import router as admin_router
from app.api.shop import router as shop_router
from app.api.shop_catalog import router as shop_catalog_router
from app.api.shop_orders import router as shop_orders_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestIDMiddleware, SecurityHeadersMiddleware
from app.db.session import create_engine, create_sessionmaker
from app.integrations.salesdoctor import SalesDoctorClient, TokenStore
from app.services.jobs import ArqJobQueue
from app.sync.context import SyncContext

log = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.LOG_LEVEL)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings.DATABASE_URL)
        redis: Redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        http = httpx.AsyncClient(
            timeout=settings.SD_TIMEOUT,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        app.state.settings = settings
        app.state.engine = engine
        app.state.sessionmaker = create_sessionmaker(engine)
        app.state.redis = redis
        app.state.http = http
        app.state.sd = SalesDoctorClient(
            http,
            TokenStore.for_account(
                redis,
                base_url=settings.SD_BASE_URL,
                login=settings.SD_LOGIN,
                lock_timeout=settings.SD_LOCK_TIMEOUT,
            ),
            base_url=settings.SD_BASE_URL,
            login=settings.SD_LOGIN,
            password=settings.SD_PASSWORD,
            user_agent=settings.SD_USER_AGENT,
            filial_id=settings.SD_FILIAL_ID,
            timeout=settings.SD_TIMEOUT,
            login_timeout=settings.SD_LOGIN_TIMEOUT,
            max_retries=settings.SD_MAX_RETRIES,
        )
        app.state.sync_ctx = SyncContext(app.state.sessionmaker, app.state.sd, redis, settings)
        app.state.jobs = ArqJobQueue(settings.REDIS_URL)
        log.info("startup", env=settings.ENV)
        try:
            yield
        finally:
            await app.state.jobs.close()
            await http.aclose()
            await redis.aclose()
            await engine.dispose()
            log.info("shutdown")

    docs = settings.docs_enabled
    app = FastAPI(
        title="Auto-Shop API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Retry-After"],
    )
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.cookie_secure)
    app.add_middleware(RequestIDMiddleware)  # outermost: every response carries X-Request-ID

    app.include_router(shop_router)
    app.include_router(shop_catalog_router)
    app.include_router(shop_orders_router)
    app.include_router(admin_router)

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    async def ready(request: Request) -> JSONResponse:
        checks: dict[str, str] = {}
        try:
            async with request.app.state.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            checks["db"] = "ok"
        except Exception as exc:
            checks["db"] = f"error: {type(exc).__name__}"
        try:
            await request.app.state.redis.ping()
            checks["redis"] = "ok"
        except Exception as exc:
            checks["redis"] = f"error: {type(exc).__name__}"
        ok = all(v == "ok" for v in checks.values())
        return JSONResponse(
            {"status": "ok" if ok else "unavailable", **checks}, status_code=200 if ok else 503
        )

    return app
