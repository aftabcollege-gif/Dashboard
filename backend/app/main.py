from contextlib import asynccontextmanager
from fastapi import FastAPI
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
from app.config import settings
from app.database import Base, db_session_scope, engine
from app.engines.scheduler import start_scheduler, stop_scheduler
from app.seed.bootstrap import seed_database


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure all Layer 1 metadata tables exist
    Base.metadata.create_all(bind=engine)
    # Seed enterprise roles, users, sample ERP DB, DuckDB warehouse, themes, dashboards & ETL
    with db_session_scope() as db:
        seed_database(db)
    # Start background ETL scheduler
    start_scheduler()
    try:
        yield
    finally:
        stop_scheduler()


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    description="Enterprise BI & Dynamic Dashboard Builder Platform (Data Engine, RBAC, Active Directory, Automated ETL, Analytical Warehouse & Smart Cache)",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check():
    return {"status": "healthy", "service": settings.APP_NAME}


@app.get(f"{settings.API_PREFIX}/health")
def api_health_check():
    return {"status": "healthy", "api_version": "1.0.0"}


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
