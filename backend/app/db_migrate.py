"""Idempotent Alembic database migration helper with strict legacy schema validation."""
import importlib.util
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect as sa_inspect, text

from app.config import settings
from app.database import engine

BASELINE_REVISION = "0001_initial_baseline"


def _extract_schema_contract(insp) -> dict[str, dict[str, Any]]:
    contract: dict[str, dict[str, Any]] = {}
    for tbl in sorted(insp.get_table_names()):
        if tbl == "alembic_version":
            continue
        cols = {c["name"] for c in insp.get_columns(tbl)}
        raw_indexes = insp.get_indexes(tbl)
        all_indexed_col_tuples = {
            tuple(i["column_names"]) for i in raw_indexes if i.get("column_names")
        }
        non_unique_idxs = {
            tuple(i["column_names"])
            for i in raw_indexes
            if i.get("column_names") and not i.get("unique")
        }
        unique_idxs = {
            tuple(sorted(i["column_names"]))
            for i in raw_indexes
            if i.get("column_names") and i.get("unique")
        }
        uqs = {
            tuple(sorted(u.get("column_names") or []))
            for u in insp.get_unique_constraints(tbl)
            if u.get("column_names")
        }
        fks = {
            (
                tuple(fk["constrained_columns"]),
                fk["referred_table"],
                tuple(fk["referred_columns"]),
            )
            for fk in insp.get_foreign_keys(tbl)
            if fk.get("constrained_columns") and fk.get("referred_table")
        }
        contract[tbl] = {
            "cols": cols,
            "all_indexed_cols": all_indexed_col_tuples,
            "idxs": non_unique_idxs,
            "uqs": uqs | unique_idxs,
            "fks": fks,
        }
    return contract


def _load_baseline_contract(backend_dir: Path) -> dict[str, dict[str, Any]]:
    baseline_path = backend_dir / "alembic" / "versions" / f"{BASELINE_REVISION}.py"
    spec = importlib.util.spec_from_file_location("alembic_baseline_0001", str(baseline_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load baseline migration file: {baseline_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    mem_engine = create_engine("sqlite:///:memory:", future=True)
    try:
        with mem_engine.begin() as conn:
            mc = MigrationContext.configure(conn)
            with Operations.context(mc):
                mod.upgrade()
        return _extract_schema_contract(sa_inspect(mem_engine))
    finally:
        mem_engine.dispose()


def validate_legacy_baseline_schema(target_engine, backend_dir: Path | None = None) -> list[str]:
    """
    Validates that an existing pre-Alembic database matches the full 0001_initial_baseline schema
    (tables, columns, indexes, unique constraints, and foreign keys) before stamping.
    Returns list of mismatch descriptions (empty list == valid).
    """
    bdir = backend_dir or Path(__file__).resolve().parents[1]
    expected = _load_baseline_contract(bdir)
    actual = _extract_schema_contract(sa_inspect(target_engine))

    errors: list[str] = []
    expected_tables = set(expected.keys())
    actual_tables = set(actual.keys())
    missing_tables = sorted(expected_tables - actual_tables)
    if missing_tables:
        errors.append(f"Missing baseline tables: {', '.join(missing_tables)}")

    for tbl in sorted(expected_tables & actual_tables):
        exp_t = expected[tbl]
        act_t = actual[tbl]

        missing_cols = sorted(exp_t["cols"] - act_t["cols"])
        if missing_cols:
            errors.append(f"Table '{tbl}' missing columns: {', '.join(missing_cols)}")

        # Check indexes (unique constraints/unique indexes also satisfy index presence on Postgres/SQLite)
        act_covered_idx = act_t["all_indexed_cols"] | act_t["uqs"]
        for idx_cols in sorted(exp_t["idxs"]):
            if idx_cols not in act_covered_idx and tuple(sorted(idx_cols)) not in act_covered_idx:
                errors.append(f"Table '{tbl}' missing index on columns {list(idx_cols)}")

        for uq_cols in sorted(exp_t["uqs"]):
            if uq_cols not in act_t["uqs"]:
                errors.append(f"Table '{tbl}' missing unique constraint on columns {list(uq_cols)}")

        for fk in sorted(exp_t["fks"]):
            if fk not in act_t["fks"]:
                errors.append(
                    f"Table '{tbl}' missing foreign key {list(fk[0])} -> {fk[1]}({list(fk[2])})"
                )

    return errors


def get_current_alembic_revisions(target_engine) -> list[str]:
    insp = sa_inspect(target_engine)
    if not insp.has_table("alembic_version"):
        return []
    with target_engine.connect() as conn:
        rows = conn.execute(text("SELECT version_num FROM alembic_version")).fetchall()
    return [str(r[0]) for r in rows if r and r[0]]


def run_migrations() -> None:
    settings.validate_runtime_security()
    backend_dir = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    script_dir = ScriptDirectory.from_config(cfg)
    known_revs = {rev.revision for rev in script_dir.walk_revisions()}

    insp = sa_inspect(engine)
    existing_tables = [t for t in insp.get_table_names() if t != "alembic_version"]
    has_alembic = insp.has_table("alembic_version")

    if has_alembic:
        current_revs = get_current_alembic_revisions(engine)
        unknown = [r for r in current_revs if r not in known_revs]
        if unknown:
            raise RuntimeError(
                f"Unknown Alembic revision(s) in database: {unknown}. "
                "Refusing unsafe automatic migration; restore from backup or inspect schema manually."
            )
    elif existing_tables:
        # Legacy pre-Alembic database detected: validate FULL schema contract before stamping.
        mismatches = validate_legacy_baseline_schema(engine, backend_dir=backend_dir)
        if mismatches:
            preview = "; ".join(mismatches[:8])
            raise RuntimeError(
                f"Legacy database failed full baseline schema validation ({len(mismatches)} mismatches: {preview}). "
                "Refusing automatic baseline stamp/migration. Back up the database and resolve schema drift first."
            )
        command.stamp(cfg, BASELINE_REVISION)

    command.upgrade(cfg, "head")


if __name__ == "__main__":
    run_migrations()
