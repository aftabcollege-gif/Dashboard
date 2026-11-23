import logging
import os
import secrets
import signal
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import inspect as sa_inspect, text
from app.api import (
    ai,
    auth,
    dashboards,
    data_sources,
    datasets,
    descriptive_builder,
    etl,
    exports,
    monitoring,
    queries,
    themes,
    users_rbac,
)
from app.api.deps import is_https_request
from app.config import settings
from app.database import Base, db_session_scope, engine
from app.engines.scheduler import start_scheduler, stop_scheduler
from app.engines.warehouse import warehouse
from app.seed.bootstrap import seed_database

logger = logging.getLogger(__name__)


def _verify_production_schema_at_head() -> None:
    inspector = sa_inspect(engine)
    if not inspector.has_table("organizations") or not inspector.has_table("alembic_version"):
        raise RuntimeError(
            "Database schema is not initialized for production startup. "
            "Run 'alembic upgrade head' before starting the server."
        )
    backend_root = Path(__file__).resolve().parents[1]
    alembic_cfg = AlembicConfig(str(backend_root / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(backend_root / "alembic"))
    expected_heads = set(ScriptDirectory.from_config(alembic_cfg).get_heads())
    with engine.connect() as conn:
        current_heads = set(MigrationContext.configure(conn).get_current_heads())
    if not current_heads or current_heads != expected_heads:
        raise RuntimeError(
            f"Database schema revision mismatch (current={sorted(current_heads)}, "
            f"expected={sorted(expected_heads)}). Run 'alembic upgrade head' before starting the server."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Enforce secret & data directory checks before booting
    settings.validate_runtime_security()
    # In non-production (or explicit BI_AUTO_CREATE_SCHEMA=1), ensure Layer 1 tables exist.
    # Production deployments must run `alembic upgrade head` prior to booting workers.
    if settings.AUTO_CREATE_SCHEMA:
        Base.metadata.create_all(bind=engine)
    else:
        _verify_production_schema_at_head()

    # Verify analytical DuckDB warehouse security lock & readiness before accepting traffic
    warehouse.check_readiness()

    # Seed enterprise roles, themes & (in dev/demo mode) demo accounts + dashboards
    with db_session_scope() as db:
        seed_database(db)
    # Start background ETL scheduler (single-leader guarded)
    start_scheduler()
    try:
        yield
    finally:
        stop_scheduler()
        warehouse.close()


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    description="Enterprise BI & Dynamic Dashboard Builder Platform (Data Engine, RBAC, Active Directory, Automated ETL, Analytical Warehouse & Smart Cache)",
    docs_url=None if settings.ENVIRONMENT == "production" else "/docs",
    redoc_url=None if settings.ENVIRONMENT == "production" else "/redoc",
    openapi_url=None if settings.ENVIRONMENT == "production" else "/openapi.json",
    lifespan=lifespan,
)


@app.exception_handler(Exception)
async def sanitized_unhandled_exception_handler(request: Request, exc: Exception):
    error_id = uuid.uuid4().hex[:12]
    logger.exception(
        "Unhandled server error [%s] on %s %s",
        error_id,
        request.method,
        request.url.path,
        exc_info=exc,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "error_id": error_id},
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-CSRF-Token",
        "X-Setup-Token",
        "X-Desktop-Control-Token",
        "X-Auth-Mode",
    ],
)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path or ""

    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")

    # Allow embedding only for public embed routes; block clickjacking everywhere else
    if f"{settings.API_PREFIX}/dashboards/public/" in path:
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    else:
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
        )

    if is_https_request(request):
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")

    if any(
        path.startswith(f"{settings.API_PREFIX}/{seg}")
        for seg in ("auth", "rbac", "queries", "data-sources", "monitoring", "descriptive-builder")
    ):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
        response.headers["Pragma"] = "no-cache"

    return response


@app.get("/health")
def health_check():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        warehouse.check_readiness()
    except Exception:
        return JSONResponse(status_code=503, content={"status": "unhealthy", "service": settings.APP_NAME})
    return {"status": "healthy", "service": settings.APP_NAME}


@app.get(f"{settings.API_PREFIX}/health")
def api_health_check():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        warehouse.check_readiness()
    except Exception:
        return JSONResponse(status_code=503, content={"status": "unhealthy", "api_version": "1.0.0"})
    return {"status": "healthy", "api_version": "1.0.0"}


@app.post("/_internal/shutdown", include_in_schema=False)
def desktop_graceful_shutdown(x_desktop_control_token: str | None = Header(default=None)):
    expected = os.environ.get("BI_DESKTOP_CONTROL_TOKEN")
    if not expected:
        raise HTTPException(status_code=404, detail="Not found")
    if not x_desktop_control_token or not secrets.compare_digest(x_desktop_control_token, expected):
        raise HTTPException(status_code=403, detail="Forbidden")
    threading.Timer(0.15, lambda: signal.raise_signal(signal.SIGINT)).start()
    return {"status": "shutting_down"}


app.include_router(auth.router, prefix=settings.API_PREFIX)
app.include_router(users_rbac.router, prefix=settings.API_PREFIX)
app.include_router(data_sources.router, prefix=settings.API_PREFIX)
app.include_router(datasets.router, prefix=settings.API_PREFIX)
app.include_router(queries.router, prefix=settings.API_PREFIX)
app.include_router(dashboards.router, prefix=settings.API_PREFIX)
app.include_router(descriptive_builder.router, prefix=settings.API_PREFIX)
app.include_router(themes.router, prefix=settings.API_PREFIX)
app.include_router(etl.router, prefix=settings.API_PREFIX)
app.include_router(ai.router, prefix=settings.API_PREFIX)
app.include_router(exports.router, prefix=settings.API_PREFIX)
app.include_router(monitoring.router, prefix=settings.API_PREFIX)
