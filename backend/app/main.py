import os
import secrets
import signal
import threading
from contextlib import asynccontextmanager
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from app.api import (
    ai,
    auth,
    dashboards,
    data_sources,
    datasets,
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Enforce secret & data directory checks before booting
    settings.validate_runtime_security()
    # Ensure all Layer 1 metadata tables exist
    Base.metadata.create_all(bind=engine)
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
    lifespan=lifespan,
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
        response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'; base-uri 'none'")

    if is_https_request(request):
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")

    if any(
        path.startswith(f"{settings.API_PREFIX}/{seg}")
        for seg in ("auth", "rbac", "queries", "data-sources", "monitoring")
    ):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
        response.headers["Pragma"] = "no-cache"

    return response


@app.get("/health")
def health_check():
    return {"status": "healthy", "service": settings.APP_NAME}


@app.get(f"{settings.API_PREFIX}/health")
def api_health_check():
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
app.include_router(themes.router, prefix=settings.API_PREFIX)
app.include_router(etl.router, prefix=settings.API_PREFIX)
app.include_router(ai.router, prefix=settings.API_PREFIX)
app.include_router(exports.router, prefix=settings.API_PREFIX)
app.include_router(monitoring.router, prefix=settings.API_PREFIX)
