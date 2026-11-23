import re
from datetime import date, timedelta
from typing import Any, Optional
from app.core.rls_cls import DataSecurityContext, build_security_projection_and_where
from app.core.sql_guard import validate_filter_expression_ast
from app.engines.formula_engine import gregorian_to_jalali, jalali_to_gregorian, parse_jalali_or_gregorian_date
from app.engines.warehouse import warehouse

IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _maybe_convert_jalali_bound(val: Any, calendar_hint: Optional[str] = None) -> Any:
    if not isinstance(val, str):
        return val
    txt = val.strip()
    if not txt:
        return val
    if calendar_hint == "jalali" or re.match(r"^(13|14|۱۳|۱۴)\d{2}[-/]", txt):
        parsed = parse_jalali_or_gregorian_date(txt)
        if parsed is not None:
            return parsed.isoformat()
    return val


def compile_filters_to_sql(
    filters: list[dict[str, Any]],
    available_columns: Optional[set[str]] = None,
) -> dict[str, Any]:
    """
    Advanced Filtering Engine Compiler (Section 7).
    Compiles Global, Dashboard, Widget, and Cross filters into parameterized DuckDB SQL.
    """
    where_clauses: list[str] = []
    params: list[Any] = []
    top_n_spec: Optional[dict[str, Any]] = None

    for f in filters:
        if f.get("is_active") is False:
            continue
        field = str(f.get("field_name") or f.get("field") or "").strip()
        if not field or not IDENT_RE.match(field):
            continue
        if available_columns is not None and field not in available_columns:
            continue

        op = str(f.get("operator", "eq")).lower()
        val = f.get("value")
        if val is None and "default_value_json" in f and f["default_value_json"] is not None:
            dv = f["default_value_json"]
            val = dv.get("value") if isinstance(dv, dict) and "value" in dv else dv
        if val is None and "value_json" in f and f["value_json"] is not None:
            vj = f["value_json"]
            val = vj.get("value") if isinstance(vj, dict) and "value" in vj else vj

        col_sql = f'"{field}"'

        if op == "is_null":
            where_clauses.append(f"{col_sql} IS NULL")
            continue
        if op == "not_null":
            where_clauses.append(f"{col_sql} IS NOT NULL")
            continue

        if val is None or val == "" or (isinstance(val, list) and len(val) == 0):
            continue

        if op in ("eq", "equals", "radio"):
            where_clauses.append(f"{col_sql} = ?")
            params.append(val)
        elif op in ("neq", "not_equals"):
            where_clauses.append(f"{col_sql} != ?")
            params.append(val)
        elif op in ("in", "include", "multi_select", "checkbox"):
            vals = val if isinstance(val, list) else [val]
            placeholders = ", ".join(["?"] * len(vals))
            where_clauses.append(f"{col_sql} IN ({placeholders})")
            params.extend(vals)
        elif op in ("not_in", "exclude"):
            vals = val if isinstance(val, list) else [val]
            placeholders = ", ".join(["?"] * len(vals))
            where_clauses.append(f"{col_sql} NOT IN ({placeholders})")
            params.extend(vals)
        elif op in ("gt", "greater_than"):
            where_clauses.append(f"{col_sql} > ?")
            params.append(val)
        elif op in ("gte", "greater_than_or_equal"):
            where_clauses.append(f"{col_sql} >= ?")
            params.append(val)
        elif op in ("lt", "less_than"):
            where_clauses.append(f"{col_sql} < ?")
            params.append(val)
        elif op in ("lte", "less_than_or_equal"):
            where_clauses.append(f"{col_sql} <= ?")
            params.append(val)
        elif op in ("between", "numeric_range", "date_range"):
            cal_hint = str(f.get("calendar") or "").lower() or None
            if isinstance(val, dict):
                low, high = val.get("min") or val.get("start"), val.get("max") or val.get("end")
            elif isinstance(val, (list, tuple)) and len(val) >= 2:
                low, high = val[0], val[1]
            else:
                continue
            if low is not None and low != "":
                where_clauses.append(f"{col_sql} >= ?")
                params.append(_maybe_convert_jalali_bound(low, cal_hint))
            if high is not None and high != "":
                where_clauses.append(f"{col_sql} <= ?")
                params.append(_maybe_convert_jalali_bound(high, cal_hint))
        elif op == "relative_date":
            today = date.today()
            mode = str(val).lower()
            start_d: Optional[date] = None
            if mode == "last_7_days":
                start_d = today - timedelta(days=7)
            elif mode == "last_30_days":
                start_d = today - timedelta(days=30)
            elif mode == "last_90_days":
                start_d = today - timedelta(days=90)
            elif mode == "last_365_days":
                start_d = today - timedelta(days=365)
            elif mode == "ytd":
                start_d = date(today.year, 1, 1)
            elif mode == "mtd":
                start_d = date(today.year, today.month, 1)
            elif mode == "jalali_ytd":
                jy, _, _ = gregorian_to_jalali(today.year, today.month, today.day)
                gy, gm, gd = jalali_to_gregorian(jy, 1, 1)
                start_d = date(gy, gm, gd)
            elif mode == "jalali_mtd":
                jy, jm, _ = gregorian_to_jalali(today.year, today.month, today.day)
                gy, gm, gd = jalali_to_gregorian(jy, jm, 1)
                start_d = date(gy, gm, gd)
            if start_d is not None:
                where_clauses.append(f"CAST({col_sql} AS DATE) >= ?")
                params.append(start_d.isoformat())
        elif op in ("contains", "text_search"):
            where_clauses.append(f"LOWER(CAST({col_sql} AS VARCHAR)) LIKE ?")
            params.append(f"%{str(val).lower()}%")
        elif op == "starts_with":
            where_clauses.append(f"LOWER(CAST({col_sql} AS VARCHAR)) LIKE ?")
            params.append(f"{str(val).lower()}%")
        elif op == "ends_with":
            where_clauses.append(f"LOWER(CAST({col_sql} AS VARCHAR)) LIKE ?")
            params.append(f"%{str(val).lower()}")
        elif op in ("top_n", "bottom_n", "ranking"):
            n_val = int(val.get("n", 10) if isinstance(val, dict) else val)
            direction = "ASC" if op == "bottom_n" else "DESC"
            order_metric = val.get("by") if isinstance(val, dict) else None
            top_n_spec = {
                "limit": max(1, min(n_val, 500)),
                "direction": direction,
                "field": order_metric or field,
            }
        elif op == "custom_expression":
            expr = validate_filter_expression_ast(str(val), allowed_columns=available_columns)
            where_clauses.append(f"({expr})")

    return {"where_clauses": where_clauses, "params": params, "top_n": top_n_spec}


def get_cascading_filter_options(
    warehouse_table: str,
    field_name: str,
    parent_filters: list[dict[str, Any]],
    rls_clauses: list[str],
    rls_params: list[Any],
    search: Optional[str] = None,
    limit: int = 200,
    sec_ctx: Optional[DataSecurityContext] = None,
) -> list[Any]:
    if not IDENT_RE.match(warehouse_table) or not IDENT_RE.match(field_name):
        raise ValueError("Invalid table or field identifier")

    bounded_limit = max(1, min(int(limit), 1000))
    cols_meta = warehouse.describe_table(warehouse_table)
    eff_ctx = sec_ctx or DataSecurityContext(
        user_id="filter_opts",
        rls_where_clauses=list(rls_clauses or []),
        rls_params=list(rls_params or []),
        hidden_columns=set(),
        masked_columns={},
        context_hash="filter_opts",
    )

    if field_name in eff_ctx.hidden_columns:
        return []

    for pf in parent_filters or []:
        if pf.get("is_active") is False:
            continue
        pfield = str(pf.get("field_name") or pf.get("field") or "").strip()
        if pfield and pfield in eff_ctx.hidden_columns:
            raise PermissionError(f"Access denied to hidden column '{pfield}' in filter")

    sub_sql, sub_params, visible_cols = build_security_projection_and_where(
        warehouse_table, cols_meta, eff_ctx, source_schema="main"
    )
    avail = set(visible_cols)
    if field_name not in avail:
        raise ValueError("Field not found or restricted in dataset")

    compiled = compile_filters_to_sql(parent_filters, available_columns=avail)
    where_parts = list(compiled["where_clauses"])
    params = list(sub_params) + list(compiled["params"])

    if search:
        where_parts.append(f'LOWER(CAST("{field_name}" AS VARCHAR)) LIKE ?')
        params.append(f"%{search.lower()}%")

    where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
    sql = (
        f'SELECT DISTINCT "{field_name}" AS val FROM ({sub_sql}) AS _fopt '
        f"{where_sql} ORDER BY 1 ASC LIMIT {bounded_limit}"
    )
    res = warehouse.execute_query(sql, params, limit=bounded_limit)
    return [r["val"] for r in res["rows"] if r["val"] is not None]
