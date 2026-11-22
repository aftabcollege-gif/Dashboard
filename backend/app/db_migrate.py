"""Idempotent Alembic database migration helper for server & standalone desktop startup."""
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect as sa_inspect

from app.config import settings
from app.database import engine

BASELINE_REVISION = "0001_initial_baseline"


def run_migrations() -> None:
    settings.validate_runtime_security()
    backend_dir = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))

    insp = sa_inspect(engine)
    has_orgs = insp.has_table("organizations")
    has_alembic = insp.has_table("alembic_version")

    # Upgrade path for pre-Alembic v1.1.0 databases created via create_all:
    # stamp baseline revision first so Stage 3+ migrations run cleanly without table collisions.
    if has_orgs and not has_alembic:
        command.stamp(cfg, BASELINE_REVISION)

    command.upgrade(cfg, "head")


if __name__ == "__main__":
    run_migrations()
