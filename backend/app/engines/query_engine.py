import re
import time
from typing import Any, Optional
import sqlglot
from sqlglot import exp
from sqlalchemy.orm import Session
from app.config import settings
from app.core.cache import cache_manager
from app.core.permissions import permission_resolver
from app.core.rls_cls import (
    DataSecurityContext,
    apply_cls_and_masking,
    build_data_security_context,
    build_public_data_security_context,
    build_security_cte_sql,
    build_security_projection_and_where,
)
from app.core.sql_guard import extract_ast_column_references, validate_formula_ast, validate_readonly_sql_ast
from app.engines.data_engine import FormulaCompiler
from app.engines.filter_engine import compile_filters_to_sql
from app.engines.formula_engine import expand_jalali_sql_calls
from app.engines.warehouse import warehouse
from app.models.data import Dataset, DatasetField, SemanticMetric
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


def _assert_expr_no_hidden_cols(compiled_expr: str, hidden_cols: set[str], avail_cols: set[str]) -> None:
    if not hidden_cols:
        return
    try:
        tree = sqlglot.parse_one(f"SELECT {compiled_expr} AS _m", read="duckdb")
        for col in tree.find_all(exp.Column):
            cname = (col.name or "").strip()
            if cname and cname in hidden_cols and cname not in avail_cols:
                raise PermissionError(f"Access denied to hidden column '{cname}'")
    except PermissionError:
        raise
    except Exception:
        pass


def build_visual_query_sql(
    db: Session,
    dataset: Dataset,
    visual_spec: dict[str, Any],
    filters: list[dict[str, Any]],
    rls_clauses: list[str],
    rls_params: list[Any],
    override_dimension: Optional[str] = None,
    raw_mode: bool = False,
    user: Optional[User] = None,
    public_mode: bool = False,
    enforce_join_rbac: bool = True,
    security_bundle_out: Optional[dict[str, Any]] = None,
) -> tuple[str, list[Any], str]:
    """
    Compiles a Visual Query Builder / Widget Visual Spec into safe parameterized DuckDB SQL.
    - Enforces organization ownership + dataset:view RBAC on primary and all joined datasets.
    - Applies per-dataset pre-SQL RLS, CLS (hidden-column exclusion), and masking inside
      isolated subqueries BEFORE joins, WHERE, GROUP BY, ORDER BY, or aggregates execute.
    """
    base_table = dataset.warehouse_table
    if not IDENT_RE.match(base_table):
        raise ValueError("Invalid warehouse table name")

    if user is not None:
        primary_sc = build_data_security_context(db, user, dataset.id)
    elif public_mode:
        primary_sc = build_public_data_security_context(db, dataset.organization_id, dataset.id)
    else:
        primary_sc = DataSecurityContext(
            user_id="system",
            rls_where_clauses=list(rls_clauses),
            rls_params=list(rls_params),
            hidden_columns=set(),
            masked_columns={},
            context_hash="legacy",
        )

    base_cols_meta = warehouse.describe_table(base_table)
    base_sub_sql, base_sub_params, visible_base_cols = build_security_projection_and_where(
        base_table, base_cols_meta, primary_sc
    )

    alias_base = "t0"
    table_expr = f"({base_sub_sql}) AS {alias_base}"
    subquery_params: list[Any] = list(base_sub_params)
    avail_cols = set(visible_base_cols)
    ds_alias_map: dict[str, str] = {dataset.id: alias_base}
    ds_visible_map: dict[str, set[str]] = {dataset.id: set(visible_base_cols)}
    ds_hidden_map: dict[str, set[str]] = {dataset.id: set(primary_sc.hidden_columns)}
    ds_objects: list[Dataset] = [dataset]

    base_proj_parts: list[str] = [f'{alias_base}."{c}" AS "{c}"' for c in visible_base_cols]

    merged_hidden: set[str] = set(primary_sc.hidden_columns)
    merged_masks: dict[str, str] = dict(primary_sc.masked_columns)
    sec_hashes: list[str] = [primary_sc.context_hash]
    joined_meta: list[dict[str, Any]] = []

    join_clauses = ""
    joins = visual_spec.get("joins", [])
    if joins:
        for idx, j in enumerate(joins, start=1):
            target_ds_id = j.get("dataset_id")
            target_ds = (
                db.query(Dataset)
                .filter(Dataset.id == target_ds_id, Dataset.organization_id == dataset.organization_id)
                .first()
            )
            if not target_ds or not IDENT_RE.match(target_ds.warehouse_table):
                raise PermissionError("Joined dataset not found or outside organization scope")

            if user is not None and enforce_join_rbac:
                dec = permission_resolver.resolve(db, user, "dataset", target_ds.id, "view")
                if not dec.allowed:
                    raise PermissionError(
                        f"Permission denied for joined dataset '{target_ds.name}' ({dec.reason})"
                    )

            if user is not None:
                join_sc = build_data_security_context(db, user, target_ds.id)
            elif public_mode:
                join_sc = build_public_data_security_context(db, dataset.organization_id, target_ds.id)
            else:
                join_sc = DataSecurityContext("system", [], [], set(), {}, "legacy")

            j_cols_meta = warehouse.describe_table(target_ds.warehouse_table)
            j_sub_sql, j_sub_params, j_visible_cols = build_security_projection_and_where(
                target_ds.warehouse_table, j_cols_meta, join_sc
            )

            jtype = str(j.get("join_type", "LEFT")).upper()
            if jtype == "FULL OUTER":
                jtype = "FULL"
            if jtype not in ("INNER", "LEFT", "RIGHT", "FULL"):
                jtype = "LEFT"
            lf = str(j.get("left_field", ""))
            rf = str(j.get("right_field", ""))
            if not (IDENT_RE.match(lf) and IDENT_RE.match(rf)):
                raise ValueError("Invalid join column identifier")

            left_ds_id = j.get("left_dataset_id") or dataset.id
            left_alias = ds_alias_map.get(left_ds_id)
            if not left_alias:
                raise ValueError(f"Join source dataset '{left_ds_id}' is not part of the active join chain")
            left_visible = ds_visible_map.get(left_ds_id, visible_base_cols)
            left_hidden = ds_hidden_map.get(left_ds_id, primary_sc.hidden_columns)

            if lf in left_hidden or lf not in left_visible:
                raise PermissionError(f"Access denied to hidden or unavailable join column '{lf}'")
            if rf in join_sc.hidden_columns or rf not in j_visible_cols:
                raise PermissionError(f"Access denied to hidden or unavailable join column '{rf}'")

            alias_j = f"t{idx}"
            join_clauses += (
                f' {jtype} JOIN ({j_sub_sql}) AS {alias_j} '
                f'ON {left_alias}."{lf}" = {alias_j}."{rf}"'
            )
            subquery_params.extend(j_sub_params)
            for jc in j_visible_cols:
                if jc not in avail_cols:
                    base_proj_parts.append(f'{alias_j}."{jc}" AS "{jc}"')
                    avail_cols.add(jc)
                else:
                    disambig = f"{alias_j}__{jc}"
                    base_proj_parts.append(f'{alias_j}."{jc}" AS "{disambig}"')
                    avail_cols.add(disambig)

            ds_alias_map[target_ds.id] = alias_j
            ds_visible_map[target_ds.id] = set(j_visible_cols)
            ds_hidden_map[target_ds.id] = set(join_sc.hidden_columns)
            ds_objects.append(target_ds)
            merged_hidden.update(join_sc.hidden_columns)
            merged_masks.update(join_sc.masked_columns)
            sec_hashes.append(join_sc.context_hash)
            joined_meta.append(
                {
                    "id": target_ds.id,
                    "data_version": target_ds.data_version,
                    "context_hash": join_sc.context_hash,
                }
            )

    # Project active row-level Calculated Columns into _base_raw after verifying strict AST + CLS safety
    calc_col_defs: list[dict[str, Any]] = []
    seen_calc_names: set[str] = set(avail_cols)
    for d_obj in ds_objects:
        db_calc_fields = (
            db.query(DatasetField)
            .filter(DatasetField.dataset_id == d_obj.id, DatasetField.is_calculated == True)
            .all()
        )
        for cf in db_calc_fields:
            if getattr(cf, "calculation_type", "calculated_column") == "calculated_measure":
                continue
            if cf.field_name in seen_calc_names or not cf.expression or not IDENT_RE.match(cf.field_name):
                continue
            calc_col_defs.append({"field_name": cf.field_name, "expression": cf.expression, "explicit": False})

    for inline_cc in (visual_spec.get("calculated_columns") or []):
        cname = str(inline_cc.get("field_name") or "").strip()
        cexpr = str(inline_cc.get("expression") or "").strip()
        if cname and cexpr and IDENT_RE.match(cname) and cname not in seen_calc_names:
            calc_col_defs.append({"field_name": cname, "expression": cexpr, "explicit": True})

    # Topologically project active row-level Calculated Columns into _base_raw (supports chained calc columns)
    remaining_cc = list(calc_col_defs)
    for _pass in range(len(remaining_cc) + 1):
        if not remaining_cc:
            break
        progressed = False
        next_remaining: list[dict[str, Any]] = []
        for cc in remaining_cc:
            try:
                comp_cc = expand_jalali_sql_calls(cc["expression"])
                validate_formula_ast(comp_cc, allowed_columns=avail_cols)
                _assert_expr_no_hidden_cols(comp_cc, merged_hidden, avail_cols)
                base_proj_parts.append(f'({comp_cc}) AS "{cc["field_name"]}"')
                avail_cols.add(cc["field_name"])
                seen_calc_names.add(cc["field_name"])
                progressed = True
            except PermissionError:
                if cc["explicit"]:
                    raise
            except Exception as exc:
                next_remaining.append({**cc, "_last_err": exc})
        remaining_cc = next_remaining
        if not progressed:
            for cc in remaining_cc:
                if cc["explicit"] and cc.get("_last_err"):
                    raise cc["_last_err"]
            break

    x_field = override_dimension or visual_spec.get("x_field") or visual_spec.get("dimension")

    # Optional custom dimension formula (e.g. JALALI_DATE(order_date) or DATE_TRUNC('month', order_date))
    x_formula = visual_spec.get("x_formula")
    if x_formula and x_field and IDENT_RE.match(x_field) and x_field not in avail_cols:
        comp_xf = expand_jalali_sql_calls(str(x_formula))
        validate_formula_ast(comp_xf, allowed_columns=avail_cols)
        _assert_expr_no_hidden_cols(comp_xf, merged_hidden, avail_cols)
        base_proj_parts.append(f'({comp_xf}) AS "{x_field}"')
        avail_cols.add(x_field)

    # Load saved calculated measures & semantic metrics for involved datasets so widgets can reference them by name/slug
    saved_measure_exprs: dict[str, str] = {}
    for d_obj in ds_objects:
        for cf in db.query(DatasetField).filter(
            DatasetField.dataset_id == d_obj.id,
            DatasetField.is_calculated == True,
        ).all():
            if getattr(cf, "calculation_type", "calculated_column") == "calculated_measure" and cf.expression:
                saved_measure_exprs[cf.field_name.lower()] = cf.expression
        for sm in db.query(SemanticMetric).filter(
            SemanticMetric.organization_id == dataset.organization_id,
            SemanticMetric.dataset_id == d_obj.id,
        ).all():
            if sm.formula:
                saved_measure_exprs[sm.slug.lower()] = sm.formula

    if security_bundle_out is not None:
        security_bundle_out["merged_sec_ctx"] = DataSecurityContext(
            user_id=user.id if user else ("public" if public_mode else "system"),
            rls_where_clauses=[],
            rls_params=[],
            hidden_columns=merged_hidden,
            masked_columns=merged_masks,
            context_hash=":".join(sec_hashes),
        )
        security_bundle_out["joined_meta"] = joined_meta
        security_bundle_out["combined_context_hash"] = ":".join(sec_hashes)
        security_bundle_out["joined_versions"] = [
            f"{jm['id']}:{jm['data_version']}" for jm in joined_meta
        ]

    # Reject any filter referencing hidden columns before compiling SQL
    for flt in filters or []:
        if flt.get("is_active") is False:
            continue
        ffield = str(flt.get("field_name") or flt.get("field") or "").strip()
        if ffield and ffield in merged_hidden and ffield not in avail_cols:
            raise PermissionError(f"Access denied to hidden column '{ffield}' in filter")

    compiled_filters = compile_filters_to_sql(filters, available_columns=avail_cols)
    where_parts = list(compiled_filters["where_clauses"])
    params = list(subquery_params) + list(compiled_filters["params"])
    where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

    proj_sql = ", ".join(base_proj_parts) if base_proj_parts else "*"
    base_ctes = f"_base_raw AS (SELECT {proj_sql} FROM {table_expr}{join_clauses}), _base AS (SELECT * FROM _base_raw {where_sql})"

    if raw_mode:
        sql = f"WITH {base_ctes} SELECT * FROM _base LIMIT 1000"
        return sql, params, "raw"

    series_field = visual_spec.get("series_field") or visual_spec.get("legend_field")
    y_field = visual_spec.get("y_field") or visual_spec.get("measure")
    agg = str(visual_spec.get("aggregation", "SUM")).upper()
    time_col = visual_spec.get("time_field") or (x_field if x_field and "date" in x_field.lower() else None)
    measures: list[dict[str, Any]] = visual_spec.get("measures") or []
    explicit_cols: list[str] = visual_spec.get("columns") or []

    # Enforce CLS on every visual_spec field reference
    for candidate in (
        x_field,
        series_field,
        visual_spec.get("secondary_y_field"),
        visual_spec.get("size_field"),
        visual_spec.get("order_by"),
        visual_spec.get("sort_by"),
        time_col,
        *(explicit_cols or []),
    ):
        if candidate and str(candidate) in merged_hidden and str(candidate) not in avail_cols:
            raise PermissionError(f"Access denied to hidden column '{candidate}'")

    if explicit_cols and not x_field and not y_field and not measures:
        safe_cols = [f'"{c}"' for c in explicit_cols if IDENT_RE.match(c) and c in avail_cols]
        sel_sql = ", ".join(safe_cols) if safe_cols else "*"
        lim = max(1, min(int(visual_spec.get("limit", 1000)), settings.MAX_QUERY_ROWS))
        order_col = visual_spec.get("order_by")
        order_dir = "DESC" if str(visual_spec.get("order_dir", "ASC")).upper() == "DESC" else "ASC"
        order_sql = f' ORDER BY "{order_col}" {order_dir}' if order_col and IDENT_RE.match(order_col) else ""
        sql = f"WITH {base_ctes} SELECT {sel_sql} FROM _base{order_sql} LIMIT {lim}"
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
            m_field = str(m.get("field") or "").strip()
            m_expr = m.get("formula") or (
                saved_measure_exprs.get(m_field.lower())
                if m_field and m_field.lower() in saved_measure_exprs
                else f"{m.get('aggregation', 'SUM')}({m_field or '*'})"
            )
            m_alias = str(m.get("alias") or f"value_{idx+1}")
            if not IDENT_RE.match(m_alias):
                m_alias = f"value_{idx+1}"
            compiled_expr = FormulaCompiler.compile_measure(m_expr, time_col=time_col, order_col=x_field)
            _assert_expr_no_hidden_cols(compiled_expr, merged_hidden, avail_cols)
            select_parts.append(f'{compiled_expr} AS "{m_alias}"')
    elif visual_spec.get("formula"):
        compiled_expr = FormulaCompiler.compile_measure(str(visual_spec["formula"]), time_col=time_col, order_col=x_field)
        _assert_expr_no_hidden_cols(compiled_expr, merged_hidden, avail_cols)
        alias_name = visual_spec.get("y_alias") or "value"
        if not IDENT_RE.match(alias_name):
            alias_name = "value"
        select_parts.append(f'{compiled_expr} AS "{alias_name}"')
    elif y_field:
        if agg in ("RAW", "NONE"):
            if IDENT_RE.match(y_field):
                if y_field in merged_hidden and y_field not in avail_cols:
                    raise PermissionError(f"Access denied to hidden column '{y_field}'")
                select_parts.append(f'"{y_field}"')
        else:
            if y_field.lower() in saved_measure_exprs and y_field not in avail_cols:
                formula = saved_measure_exprs[y_field.lower()]
            else:
                formula = f"{agg}({y_field})" if "(" not in y_field else y_field
            compiled_expr = FormulaCompiler.compile_measure(formula, time_col=time_col, order_col=x_field)
            _assert_expr_no_hidden_cols(compiled_expr, merged_hidden, avail_cols)
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
            _assert_expr_no_hidden_cols(sec_expr, merged_hidden, avail_cols)
            select_parts.append(f'{sec_expr} AS "secondary_value"')

    if size_field := visual_spec.get("size_field"):
        if IDENT_RE.match(size_field):
            size_expr = FormulaCompiler.compile_measure(f"SUM({size_field})", time_col=time_col, order_col=x_field)
            _assert_expr_no_hidden_cols(size_expr, merged_hidden, avail_cols)
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
        f"WITH {base_ctes} "
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
    - AST-based read-only SQL guard + strict organization-owned registered Dataset allowlist
      (NO unregistered DuckDB tables, main.*, or super_admin arbitrary-table bypasses)
    - Per-table dataset:view authorization
    - Pre-SQL RLS + CLS (hidden column omission) + Column Masking CTE wrapping across all
      referenced datasets BEFORE user SQL / WHERE / GROUP BY / ORDER BY / aggregates execute
    - Bounded row limit (1..MAX_QUERY_ROWS) and active timeout guard
    - Layer 3 Smart Cache keyed by org/dash/widget/dataset/version/query/filter/perm_hash
    """
    cleaned_sql = (sql_text or "").strip().rstrip(";")
    bound_sql, bound_params = _bind_named_params(cleaned_sql, named_params or {})

    if dataset and dataset.organization_id != user.organization_id:
        raise PermissionError("Dataset belongs to another organization")

    # Resolve ONLY datasets owned by the caller's organization; unregistered physical tables are strictly forbidden
    org_datasets = db.query(Dataset).filter(Dataset.organization_id == user.organization_id).all()
    by_table_org = {d.warehouse_table.lower(): d for d in org_datasets if d.warehouse_table}
    referenced_tables = validate_readonly_sql_ast(
        bound_sql,
        dialect="duckdb",
        protected_tables=by_table_org.keys(),
    )

    referenced_datasets: list[Dataset] = []
    seen_ds_ids: set[str] = set()
    if dataset:
        if enforce_dataset_rbac:
            dec = permission_resolver.resolve(db, user, "dataset", dataset.id, "view")
            if not dec.allowed:
                raise PermissionError(f"Permission denied for dataset '{dataset.name}' ({dec.reason})")
        referenced_datasets.append(dataset)
        seen_ds_ids.add(dataset.id)

    for tbl in referenced_tables:
        tlow = tbl.lower()
        ds_match = by_table_org.get(tlow)
        if ds_match is None:
            raise PermissionError(f"Access denied to unregistered or unauthorized table '{tbl}'")
        if enforce_dataset_rbac:
            dec = permission_resolver.resolve(db, user, "dataset", ds_match.id, "view")
            if not dec.allowed:
                raise PermissionError(f"Permission denied for dataset '{ds_match.name}' ({dec.reason})")
        if ds_match.id not in seen_ds_ids:
            referenced_datasets.append(ds_match)
            seen_ds_ids.add(ds_match.id)

    # Wrap every referenced dataset in a security CTE enforcing RLS, CLS (hidden col removal),
    # and SQL-level masking BEFORE any user SQL expressions run.
    merged_hidden: set[str] = set()
    merged_masks: dict[str, str] = {}
    rls_ctes: list[str] = []
    rls_cte_params: list[Any] = []
    sec_hashes: list[str] = []
    hidden_by_table: dict[str, set[str]] = {}
    visible_cols_union: set[str] = set()

    if referenced_datasets:
        for ds_item in referenced_datasets:
            sc = build_data_security_context(db, user, ds_item.id)
            merged_hidden.update(sc.hidden_columns)
            merged_masks.update(sc.masked_columns)
            sec_hashes.append(sc.context_hash)
            tname = ds_item.warehouse_table
            hidden_by_table[tname.lower()] = sc.hidden_columns
            cols_meta = warehouse.describe_table(tname)
            cte_sql, cte_params, vis_cols = build_security_cte_sql(tname, cols_meta, sc, source_schema="main")
            rls_ctes.append(cte_sql)
            rls_cte_params.extend(cte_params)
            visible_cols_union.update(vis_cols)
    else:
        sc = build_data_security_context(db, user, None)
        merged_hidden.update(sc.hidden_columns)
        merged_masks.update(sc.masked_columns)
        sec_hashes.append(sc.context_hash)

    # Reject explicit AST column references to hidden columns cleanly with PermissionError (403)
    if merged_hidden:
        for tqual, cname in extract_ast_column_references(bound_sql, dialect="duckdb"):
            if tqual and tqual.lower() in hidden_by_table:
                if cname in hidden_by_table[tqual.lower()]:
                    raise PermissionError(f"Access denied to hidden column '{cname}'")
            elif cname in merged_hidden and cname not in visible_cols_union:
                raise PermissionError(f"Access denied to hidden column '{cname}'")

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
        for flt in filters:
            if flt.get("is_active") is False:
                continue
            ffield = str(flt.get("field_name") or flt.get("field") or "").strip()
            if ffield and ffield in merged_hidden and ffield not in visible_cols_union:
                raise PermissionError(f"Access denied to hidden column '{ffield}' in filter")
        avail = visible_cols_union if visible_cols_union else (
            {c["column_name"] for c in warehouse.describe_table(dataset.warehouse_table)} - merged_hidden
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
    q_hash = cache_manager.hash_dict({
        "sql": cleaned_sql,
        "params": named_params or {},
        "limit": bounded_limit,
        "versions": [f"{d.id}:{d.data_version}" for d in referenced_datasets],
    })
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
        # Convert DuckDB Binder errors on hidden columns into PermissionError (403)
        msg = str(exc)
        if merged_hidden and "Binder Error" in msg and any(f'"{hc}"' in msg for hc in merged_hidden):
            raise PermissionError("Access denied to hidden column") from exc
        raise
