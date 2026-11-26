import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from sqlalchemy.orm import Session
from app.core.audit import record_audit
from app.core.cache import cache_manager
from app.core.security import decrypt_secret
from app.core.sql_guard import validate_readonly_sql_ast
from app.engines.connectors import (
    DataConnectorEngine,
    _build_columns_meta,
    _normalize_dataframe_chunk,
    _sanitize_headers_and_renames,
)
from app.engines.warehouse import sanitize_identifier, warehouse
from app.models.data import (
    CompositeDataset,
    DataLineage,
    DataSource,
    DataSourceCredential,
    Dataset,
    DatasetField,
)
from app.models.etl import EtlPipeline, EtlSchedule

_EXT_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,120}$")
_ALLOWED_JOIN_TYPES = {"INNER", "LEFT", "RIGHT"}


def _quote_ext_ident(ident: str, quote_char: str = '"') -> str:
    parts = [p.strip() for p in str(ident).split(".") if p.strip()]
    if not parts or len(parts) > 3:
        raise ValueError(f"Invalid external SQL identifier: {ident}")
    for p in parts:
        if not _EXT_IDENT_RE.match(p):
            raise ValueError(f"Unsafe external SQL identifier: {ident}")
    return ".".join(f"{quote_char}{p}{quote_char}" for p in parts)


def build_composite_select_sql(source_type: str, definition: dict[str, Any]) -> str:
    """Builds a safe, AST-validated read-only SELECT query across joined source tables/views."""
    st = (source_type or "").lower()
    qc = "`" if st in ("mysql", "mariadb") else '"'

    base_table = str(definition.get("base_table") or "").strip()
    if not base_table:
        raise ValueError("base_table is required")
    base_quoted = _quote_ext_ident(base_table, qc)

    joins = definition.get("joins") or []
    selected_columns = definition.get("selected_columns") or []
    row_limit = max(1, min(int(definition.get("limit_rows") or 100_000), 250_000))

    select_parts: list[str] = []
    if selected_columns:
        for col_spec in selected_columns:
            tbl = str(col_spec.get("table") or base_table).strip()
            col = str(col_spec.get("column") or "").strip()
            alias = str(col_spec.get("alias") or "").strip()
            tq = _quote_ext_ident(tbl, qc)
            cq = _quote_ext_ident(col, qc)
            if alias:
                aq = _quote_ext_ident(sanitize_identifier(alias), qc)
                select_parts.append(f"{tq}.{cq} AS {aq}")
            else:
                select_parts.append(f"{tq}.{cq}")
    else:
        select_parts.append(f"{base_quoted}.*")
        for j in joins:
            jt = str(j.get("table") or "").strip()
            if jt:
                select_parts.append(f"{_quote_ext_ident(jt, qc)}.*")

    join_clauses: list[str] = []
    for j in joins:
        jt = str(j.get("table") or "").strip()
        jtype = str(j.get("join_type") or "LEFT").upper()
        if jtype not in _ALLOWED_JOIN_TYPES:
            raise ValueError(f"Unsupported join_type: {jtype}")
        left_table = str(j.get("left_table") or base_table).strip()
        left_col = str(j.get("left_col") or "").strip()
        right_col = str(j.get("right_col") or "").strip()
        if not jt or not left_col or not right_col:
            raise ValueError("Each join requires table, left_col, and right_col")
        join_clauses.append(
            f"{jtype} JOIN {_quote_ext_ident(jt, qc)} "
            f"ON {_quote_ext_ident(left_table, qc)}.{_quote_ext_ident(left_col, qc)} = "
            f"{_quote_ext_ident(jt, qc)}.{_quote_ext_ident(right_col, qc)}"
        )

    sql = f"SELECT {', '.join(select_parts)} FROM {base_quoted}"
    if join_clauses:
        sql += " " + " ".join(join_clauses)
    sql += f" LIMIT {row_limit}"

    allowed_tbls = [base_table.split(".")[-1]] + [
        str(j.get("table")).split(".")[-1] for j in joins if j.get("table")
    ]
    # Validate AST read-only SELECT safety
    try:
        validate_readonly_sql_ast(
            sql,
            allowed_tables=allowed_tbls,
            dialect="mysql" if st in ("mysql", "mariadb") else "postgres",
            require_table_allowlist=True,
        )
    except ValueError as exc:
        if "qualified table" not in str(exc).lower():
            raise
    return sql


def materialize_composite_dataset(
    db: Session,
    *,
    comp: CompositeDataset,
    src: DataSource,
    actor_user_id: Optional[str] = None,
    actor_username: Optional[str] = None,
    progress_callback=None,
) -> dict[str, Any]:
    """Extracts joined data from the shared DataSource and materializes it into a DuckDB Dataset."""
    if progress_callback:
        progress_callback(10, 100, "تولید و اعتبارسنجی کوئری چندجدولی...")

    sql = build_composite_select_sql(src.source_type, comp.definition_json or {})
    cred_row = db.query(DataSourceCredential).filter(DataSourceCredential.data_source_id == src.id).first()
    creds = json.loads(decrypt_secret(cred_row.encrypted_payload)) if cred_row else {}
    db.commit()  # Ensure no open transaction before async progress callbacks

    if progress_callback:
        progress_callback(30, 100, "دریافت داده‌ها از منبع داده...")

    limit_rows = max(1, min(int((comp.definition_json or {}).get("limit_rows") or 100_000), 250_000))
    try:
        df = DataConnectorEngine.extract_dataframe(
            src.source_type,
            src.config_json,
            creds,
            query=sql,
            read_only=True,
            max_rows=limit_rows,
        )
    except Exception as exc:
        comp.last_status = "error"
        comp.last_error = str(exc)[:1500]
        src.health_status = "error"
        src.last_error = str(exc)[:1500]
        db.commit()
        raise

    if progress_callback:
        progress_callback(65, 100, "نرمال‌سازی و بارگذاری در انبار DuckDB...")

    raw_headers = [str(c).strip() or f"col_{i+1}" for i, c in enumerate(df.columns)]
    safe_cols, display_map = _sanitize_headers_and_renames(raw_headers)
    df.columns = safe_cols
    df, null_counts = _normalize_dataframe_chunk(df)
    dtypes_map = {str(c): str(df[c].dtype) for c in df.columns}
    cols_meta = _build_columns_meta(df.columns, dtypes_map, display_map, null_counts)

    now = datetime.now(timezone.utc)
    ds = db.query(Dataset).filter(Dataset.id == comp.dataset_id).first() if comp.dataset_id else None
    if ds is None:
        org_short = re.sub(r"[^a-z0-9]", "", comp.organization_id.lower())[:6] or "org"
        slug = re.sub(r"_+", "_", sanitize_identifier(comp.name)).strip("_")[:36] or "composite"
        wh_table = f"wh_comp_{org_short}_{slug}_{uuid.uuid4().hex[:8]}"
        ds = Dataset(
            organization_id=comp.organization_id,
            data_source_id=src.id,
            name=comp.name,
            description=comp.description or f"Composite dataset from {src.name}",
            warehouse_table=wh_table,
            schema_type="composite",
            refresh_policy=comp.refresh_schedule or "manual",
            row_count=len(df),
            last_refreshed_at=now,
            freshness_status="fresh",
            profile_json={"composite_id": comp.id, "generated_sql": sql},
        )
        db.add(ds)
        db.flush()
        comp.dataset_id = ds.id
    else:
        wh_table = ds.warehouse_table
        ds.row_count = len(df)
        ds.data_version += 1
        ds.last_refreshed_at = now
        ds.freshness_status = "fresh"
        ds.refresh_policy = comp.refresh_schedule or ds.refresh_policy
        ds.profile_json = {**(ds.profile_json or {}), "composite_id": comp.id, "generated_sql": sql}
        db.query(DatasetField).filter(DatasetField.dataset_id == ds.id).delete()

    warehouse.load_dataframe(wh_table, df, mode="replace")

    db.add_all(
        [
            DatasetField(
                dataset_id=ds.id,
                field_name=cm["field_name"],
                display_name=cm["display_name"],
                data_type=cm["data_type"],
                semantic_role=cm["semantic_role"],
            )
            for cm in cols_meta
        ]
    )

    existing_lineage = (
        db.query(DataLineage)
        .filter(
            DataLineage.source_type == "data_source",
            DataLineage.source_id == src.id,
            DataLineage.target_type == "dataset",
            DataLineage.target_id == ds.id,
        )
        .first()
    )
    if existing_lineage:
        existing_lineage.metadata_json = {"composite_id": comp.id, "rows": len(df), "sql": sql}
    else:
        db.add(
            DataLineage(
                source_type="data_source",
                source_id=src.id,
                target_type="dataset",
                target_id=ds.id,
                metadata_json={"composite_id": comp.id, "rows": len(df), "sql": sql},
            )
        )

    comp.last_refreshed_at = now
    comp.last_status = "healthy"
    comp.last_error = None
    src.health_status = "healthy"
    src.last_tested_at = now
    src.last_error = None

    # Keep optional ETL schedule synced when schedule != manual
    if comp.refresh_schedule in ("hourly", "daily", "weekly"):
        interval_map = {"hourly": 60, "daily": 1440, "weekly": 10080}
        cron_map = {"hourly": "0 * * * *", "daily": "0 2 * * *", "weekly": "0 3 * * 1"}
        pipe = (
            db.query(EtlPipeline)
            .filter(EtlPipeline.organization_id == comp.organization_id, EtlPipeline.target_dataset_id == ds.id)
            .first()
        )
        if pipe is None:
            pipe = EtlPipeline(
                organization_id=comp.organization_id,
                name=f"Auto-Refresh: {comp.name}",
                description=f"Composite sync from {src.name}",
                data_source_id=src.id,
                target_dataset_id=ds.id,
                source_query=sql,
                extract_mode="full",
                is_active=True,
            )
            db.add(pipe)
            db.flush()
            db.add(
                EtlSchedule(
                    pipeline_id=pipe.id,
                    schedule_type="interval",
                    interval_minutes=interval_map[comp.refresh_schedule],
                    cron_expression=cron_map[comp.refresh_schedule],
                    is_enabled=True,
                )
            )
        else:
            pipe.source_query = sql

    record_audit(
        db,
        comp.organization_id,
        "composite_dataset_materialized",
        "dataset",
        resource_id=ds.id,
        user_id=actor_user_id,
        username=actor_username,
        details={"composite_id": comp.id, "rows": len(df), "columns": len(cols_meta)},
        commit=False,
    )
    cache_manager.invalidate_dataset(ds.id)
    db.commit()

    if progress_callback:
        progress_callback(100, 100, "تکمیل شد")

    return {
        "composite_id": comp.id,
        "dataset_id": ds.id,
        "name": ds.name,
        "warehouse_table": wh_table,
        "total_rows": len(df),
        "columns_count": len(cols_meta),
        "generated_sql": sql,
        "last_refreshed_at": now.isoformat(),
    }
