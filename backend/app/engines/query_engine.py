import re
import time
from typing import Any, Optional
from sqlalchemy.orm import Session
from app.config import settings
from app.core.cache import cache_manager
from app.core.permissions import permission_resolver
from app.core.rls_cls import apply_cls_and_masking, build_data_security_context, DataSecurityContext
from app.core.sql_guard import validate_readonly_sql_ast
from app.engines.data_engine import FormulaCompiler
from app.engines.filter_engine import compile_filters_to_sql
from app.engines.warehouse import warehouse
from app.models.data import Dataset
from app.models.identity import User
from app.models.performance import QueryExecutionLog

IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PARAM_RE = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")


def _bind_named_params(sql: str, params: dict[str, Any]) -> tuple[str, list[Any]]:
    """Bind :param placeholders outside single-quoted string literals to positional '?'."""
    ordered_values: list[Any] = []
    out: list[str] = []
    i = 0
    n = len(sql)
    in_quote = False
    while i < n:
        ch = sql[i]
        if ch == "'":
            out.append(ch)
            if in_quote and i + 1 < n and sql[i + 1] == "'":
                out.append("'")
                i += 2
                continue
            in_quote = not in_quote
            i += 1
            continue
        if not in_quote and ch == ":" and (i == 0 or sql[i - 1] != ":"):
            m = re.match(r":([A-Za-z_][A-Za-z0-9_]*)", sql[i:])
            if m:
                pname = m.group(1)
                if pname not in params:
                    raise ValueError(f"Missing SQL parameter :{pname}")
                ordered_values.append(params[pname])
                out.append("?")
                i += len(m.group(0))
                continue
        out.append(ch)
        i += 1
    return "".join(out), ordered_values


def _combine_ctes(rls_ctes: list[str], sql: str) -> str:
    if not rls_ctes:
        return sql
    cte_block = ", ".join(rls_ctes)
    stripped = sql.lstrip()
    if re.match(r"^WITH\s+RECURSIVE\b", stripped, re.IGNORECASE):
        body = re.sub(r"^WITH\s+RECURSIVE\s+", "", stripped, count=1, flags=re.IGNORECASE)
        return f"WITH RECURSIVE {cte_block}, {body}"
    if re.match(r"^WITH\b", stripped, re.IGNORECASE):
        body = re.sub(r"^WITH\s+", "", stripped, count=1, flags=re.IGNORECASE)
        return f"WITH {cte_block}, {body}"
    return f"WITH {cte_block} {stripped}"


def build_visual_query_sql(
    db: Session,
    dataset: Dataset,
    visual_spec: dict[str, Any],
    filters: list[dict[str, Any]],
    rls_clauses: list[str],
    rls_params: list[Any],
    override_dimension: Optional[str] = None,
    raw_mode: bool = False,
) -> tuple[str, list[Any], str]:
    """
    Compiles a Visual Query Builder / Widget Visual Spec into safe parameterized DuckDB SQL.
    Only permits joins to datasets belonging to the same organization.
    """
    base_table = dataset.warehouse_table
    if not IDENT_RE.match(base_table):
        raise ValueError("Invalid warehouse table name")

    cols_meta = warehouse.describe_table(base_table)
    avail_cols = {c["column_name"] for c in cols_meta}

    join_clauses = ""
    joins = visual_spec.get("joins", [])
    alias_base = "t0"
    table_expr = f'"{base_table}" AS {alias_base}'
    if joins:
        for idx, j in enumerate(joins, start=1):
            target_ds_id = j.get("dataset_id")
            target_ds = (
                db.query(Dataset)
                .filter(Dataset.id == target_ds_id, Dataset.organization_id == dataset.organization_id)
                .first()
            )
            if not target_ds or not IDENT_RE.match(target_ds.warehouse_table):
                continue
            jtype = str(j.get("join_type", "LEFT")).upper()
            if jtype not in ("INNER", "LEFT", "RIGHT", "FULL"):
                jtype = "LEFT"
            lf = str(j.get("left_field", ""))
            rf = str(j.get("right_field", ""))
            if IDENT_RE.match(lf) and IDENT_RE.match(rf):
                alias_j = f"t{idx}"
                join_clauses += f' {jtype} JOIN "{target_ds.warehouse_table}" AS {alias_j} ON {alias_base}."{lf}" = {alias_j}."{rf}"'
                for jc in warehouse.describe_table(target_ds.warehouse_table):
                    avail_cols.add(jc["column_name"])

    compiled_filters = compile_filters_to_sql(filters, available_columns=avail_cols)
    where_parts = list(rls_clauses) + list(compiled_filters["where_clauses"])
    params = list(rls_params) + list(compiled_filters["params"])
    where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

    if raw_mode:
        sql = f"WITH _base AS (SELECT * FROM {table_expr}{join_clauses} {where_sql}) SELECT * FROM _base LIMIT 1000"
        return sql, params, "raw"

    x_field = override_dimension or visual_spec.get("x_field") or visual_spec.get("dimension")
    series_field = visual_spec.get("series_field") or visual_spec.get("legend_field")
    y_field = visual_spec.get("y_field") or visual_spec.get("measure")
    agg = str(visual_spec.get("aggregation", "SUM")).upper()
    time_col = visual_spec.get("time_field") or (x_field if x_field and "date" in x_field.lower() else None)
    measures: list[dict[str, Any]] = visual_spec.get("measures") or []
    explicit_cols: list[str] = visual_spec.get("columns") or []

    if explicit_cols and not x_field and not y_field and not measures:
        safe_cols = [f'"{c}"' for c in explicit_cols if IDENT_RE.match(c)]
        sel_sql = ", ".join(safe_cols) if safe_cols else "*"
        lim = max(1, min(int(visual_spec.get("limit", 1000)), settings.MAX_QUERY_ROWS))
        order_col = visual_spec.get("order_by")
        order_dir = "DESC" if str(visual_spec.get("order_dir", "ASC")).upper() == "DESC" else "ASC"
        order_sql = f' ORDER BY "{order_col}" {order_dir}' if order_col and IDENT_RE.match(order_col) else ""
        sql = f"WITH _base AS (SELECT * FROM {table_expr}{join_clauses} {where_sql}) SELECT {sel_sql} FROM _base{order_sql} LIMIT {lim}"
        return sql, params, "visual_select"

    group_fields: list[str] = []
    select_parts: list[str] = []

    if x_field and IDENT_RE.match(x_field):
        select_parts.append(f'"{x_field}"')
        group_fields.append(f'"{x_field}"')
    if series_field and IDENT_RE.match(series_field) and series_field != x_field:
        select_parts.append(f'"{series_field}"')
        group_fields.append(f'"{series_field}"')

    if measures:
        for idx, m in enumerate(measures):
            m_expr = m.get("formula") or f"{m.get('aggregation', 'SUM')}({m.get('field', '*')})"
            m_alias = str(m.get("alias") or f"value_{idx+1}")
            if not IDENT_RE.match(m_alias):
                m_alias = f"value_{idx+1}"
            compiled_expr = FormulaCompiler.compile_measure(m_expr, time_col=time_col, order_col=x_field)
            select_parts.append(f'{compiled_expr} AS "{m_alias}"')
    elif y_field:
        if agg in ("RAW", "NONE"):
            if IDENT_RE.match(y_field):
                select_parts.append(f'"{y_field}"')
        else:
            formula = f"{agg}({y_field})" if "(" not in y_field else y_field
            compiled_expr = FormulaCompiler.compile_measure(formula, time_col=time_col, order_col=x_field)
            alias_name = visual_spec.get("y_alias") or "value"
            if not IDENT_RE.match(alias_name):
                alias_name = "value"
            select_parts.append(f'{compiled_expr} AS "{alias_name}"')
    else:
        select_parts.append('COUNT(*) AS "value"')

    if sec_field := visual_spec.get("secondary_y_field"):
        if IDENT_RE.match(sec_field):
            sec_agg = str(visual_spec.get("secondary_aggregation", "AVG")).upper()
            sec_expr = FormulaCompiler.compile_measure(f"{sec_agg}({sec_field})", time_col=time_col, order_col=x_field)
            select_parts.append(f'{sec_expr} AS "secondary_value"')

    if size_field := visual_spec.get("size_field"):
        if IDENT_RE.match(size_field):
            size_expr = FormulaCompiler.compile_measure(f"SUM({size_field})", time_col=time_col, order_col=x_field)
            select_parts.append(f'{size_expr} AS "size_value"')

    group_sql = f"GROUP BY {', '.join(group_fields)}" if group_fields and agg not in ("RAW", "NONE") else ""

    top_n = compiled_filters.get("top_n")
    lim = max(1, min(int(visual_spec.get("limit", 1000)), settings.MAX_QUERY_ROWS))
    order_sql = ""
    if top_n:
        lim = min(top_n["limit"], settings.MAX_QUERY_ROWS)
        order_sql = f'ORDER BY "value" {top_n["direction"]}'
    elif visual_spec.get("sort_by"):
        sb = str(visual_spec["sort_by"])
        sdir = "DESC" if str(visual_spec.get("sort_dir", "DESC")).upper() == "DESC" else "ASC"
        if sb in ("value", "secondary_value") or IDENT_RE.match(sb):
            order_sql = f'ORDER BY "{sb}" {sdir}'
    elif x_field and any(k in x_field.lower() for k in ("year", "quarter", "month", "day", "date")):
        order_sql = f'ORDER BY "{x_field}" ASC'
    elif group_fields:
        order_sql = "ORDER BY 2 DESC"

    sel_sql = ", ".join(select_parts)
    sql = (
        f"WITH _base AS (SELECT * FROM {table_expr}{join_clauses} {where_sql}) "
        f"SELECT {sel_sql} FROM _base {group_sql} {order_sql} LIMIT {lim}"
    )
    return sql, params, "visual_agg"


def execute_dataset_sql(
    db: Session,
    user: User,
    sql_text: str,
    dataset: Optional[Dataset] = None,
    named_params: Optional[dict[str, Any]] = None,
    filters: Optional[list[dict[str, Any]]] = None,
    limit: int = 1000,
    include_explain: bool = False,
    dashboard_id: Optional[str] = None,
    widget_id: Optional[str] = None,
    use_cache: bool = True,
    enforce_dataset_rbac: bool = True,
) -> dict[str, Any]:
    """
    Central Query Execution Engine (Section 27F):
    - AST-based read-only SQL guard + cross-tenant table allowlist
    - Per-table dataset:view authorization + RLS predicate shadowing across all referenced datasets
    - Bounded row limit (1..MAX_QUERY_ROWS) and active timeout guard
    - Layer 3 Smart Cache keyed by org/dash/widget/dataset/version/query/filter/perm_hash
    - CLS & Data Masking
    - Performance log persistence
    """
    cleaned_sql = (sql_text or "").strip().rstrip(";")
    bound_sql, bound_params = _bind_named_params(cleaned_sql, named_params or {})
    referenced_tables = validate_readonly_sql_ast(bound_sql, dialect="duckdb")

    if dataset and dataset.organization_id != user.organization_id:
        raise PermissionError("Dataset belongs to another organization")

    # Resolve all registered datasets to verify tenant ownership and dataset RBAC
    all_registered = db.query(Dataset).all()
    by_table_all = {d.warehouse_table.lower(): d for d in all_registered if d.warehouse_table}
    eff_summary = permission_resolver.get_effective_permissions_summary(db, user)
    is_super_admin = bool(eff_summary.get("is_super_admin"))

    referenced_datasets: list[Dataset] = []
    seen_ds_ids: set[str] = set()
    if dataset:
        referenced_datasets.append(dataset)
        seen_ds_ids.add(dataset.id)

    for tbl in referenced_tables:
        tlow = tbl.lower()
        ds_match = by_table_all.get(tlow)
        if ds_match is not None:
            if ds_match.organization_id != user.organization_id:
                raise PermissionError(f"Access denied to table '{tbl}' outside your organization")
            if enforce_dataset_rbac:
                dec = permission_resolver.resolve(db, user, "dataset", ds_match.id, "view")
                if not dec.allowed:
                    raise PermissionError(f"Permission denied for dataset '{ds_match.name}' ({dec.reason})")
            if ds_match.id not in seen_ds_ids:
                referenced_datasets.append(ds_match)
                seen_ds_ids.add(ds_match.id)
        else:
            # Unregistered physical tables (e.g. system benchmark tables) may only be queried by super_admin
            if not is_super_admin:
                raise PermissionError(f"Access denied to unregistered table '{tbl}'")

    # Aggregate RLS and CLS policies across all referenced datasets (or explicit dataset)
    merged_hidden: set[str] = set()
    merged_masks: dict[str, str] = {}
    rls_ctes: list[str] = []
    rls_cte_params: list[Any] = []
    sec_hashes: list[str] = []

    if referenced_datasets:
        for ds_item in referenced_datasets:
            sc = build_data_security_context(db, user, ds_item.id)
            merged_hidden.update(sc.hidden_columns)
            merged_masks.update(sc.masked_columns)
            sec_hashes.append(sc.context_hash)
            if sc.rls_where_clauses:
                tname = ds_item.warehouse_table
                rls_where = " AND ".join(sc.rls_where_clauses)
                rls_ctes.append(f'"{tname}" AS (SELECT * FROM main."{tname}" WHERE {rls_where})')
                rls_cte_params.extend(sc.rls_params)
    else:
        sc = build_data_security_context(db, user, None)
        merged_hidden.update(sc.hidden_columns)
        merged_masks.update(sc.masked_columns)
        sec_hashes.append(sc.context_hash)

    merged_sec_ctx = DataSecurityContext(
        user_id=user.id,
        rls_where_clauses=[],
        rls_params=[],
        hidden_columns=merged_hidden,
        masked_columns=merged_masks,
        context_hash=":".join(sec_hashes),
    )

    perm_hash = permission_resolver.get_permission_context_hash(db, user) + ":" + merged_sec_ctx.context_hash

    final_sql = _combine_ctes(rls_ctes, bound_sql)
    final_params = list(rls_cte_params) + list(bound_params)

    if filters:
        avail = (
            {c["column_name"] for c in warehouse.describe_table(dataset.warehouse_table)}
            if dataset
            else None
        )
        fc = compile_filters_to_sql(filters, available_columns=avail)
        if fc["where_clauses"]:
            f_where = " AND ".join(fc["where_clauses"])
            final_sql = f"SELECT * FROM ({final_sql}) AS _qf WHERE {f_where}"
            final_params.extend(fc["params"])

    bounded_limit = max(1, min(int(limit), settings.MAX_QUERY_ROWS))
    primary_ds = dataset or (referenced_datasets[0] if referenced_datasets else None)
    data_ver = primary_ds.data_version if primary_ds else 1
    ds_id = primary_ds.id if primary_ds else "adhoc"
    q_hash = cache_manager.hash_dict({"sql": cleaned_sql, "params": named_params or {}, "limit": bounded_limit})
    f_hash = cache_manager.hash_dict(filters or [])
    cache_key = cache_manager.build_key(
        organization_id=user.organization_id,
        dataset_id=ds_id,
        data_version=data_ver,
        query_hash=q_hash,
        filter_hash=f_hash,
        permission_hash=perm_hash,
        dashboard_id=dashboard_id,
        widget_id=widget_id,
    )

    if use_cache:
        cached = cache_manager.get(cache_key, db=db)
        if cached is not None:
            db.add(
                QueryExecutionLog(
                    organization_id=user.organization_id,
                    user_id=user.id,
                    dataset_id=primary_ds.id if primary_ds else None,
                    widget_id=widget_id,
                    query_hash=q_hash,
                    execution_ms=0.2,
                    rows_returned=cached["row_count"],
                    cache_hit=True,
                    status="success",
                )
            )
            db.commit()
            return {**cached, "cache_hit": True}

    t0 = time.perf_counter()
    try:
        res = warehouse.execute_query(final_sql, final_params, limit=bounded_limit)
        vis_cols, masked_rows = apply_cls_and_masking(res["columns"], res["rows"], merged_sec_ctx)
        explain_plan = warehouse.explain_query(final_sql, final_params) if include_explain else None
        elapsed = round((time.perf_counter() - t0) * 1000, 3)

        payload = {
            "columns": vis_cols,
            "rows": masked_rows,
            "row_count": len(masked_rows),
            "execution_ms": elapsed,
            "cache_hit": False,
            "query_plan": explain_plan,
            "data_version": data_ver,
        }

        if use_cache:
            cache_manager.set(
                cache_key,
                payload,
                meta_info={
                    "organization_id": user.organization_id,
                    "dashboard_id": dashboard_id,
                    "widget_id": widget_id,
                    "dataset_id": ds_id,
                    "data_version": data_ver,
                    "permission_hash": perm_hash,
                    "filter_hash": f_hash,
                },
                db=db,
            )

        db.add(
            QueryExecutionLog(
                organization_id=user.organization_id,
                user_id=user.id,
                dataset_id=primary_ds.id if primary_ds else None,
                widget_id=widget_id,
                query_hash=q_hash,
                execution_ms=elapsed,
                rows_returned=len(masked_rows),
                cache_hit=False,
                status="success",
            )
        )
        db.commit()
        return payload
    except Exception as exc:
        elapsed = round((time.perf_counter() - t0) * 1000, 3)
        db.add(
            QueryExecutionLog(
                organization_id=user.organization_id,
                user_id=user.id,
                dataset_id=primary_ds.id if primary_ds else None,
                widget_id=widget_id,
                query_hash=q_hash,
                execution_ms=elapsed,
                rows_returned=0,
                cache_hit=False,
                status="error",
                error_message=str(exc),
            )
        )
        db.commit()
        raise
