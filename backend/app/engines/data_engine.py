import re
from typing import Any, Optional
import pandas as pd
from app.core.rls_cls import DataSecurityContext, build_security_projection_and_where, is_safe_column_ident
from app.core.sql_guard import safe_eval_dataframe_formula, validate_formula_ast
from app.engines.warehouse import sanitize_identifier, warehouse

IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _safe_order_col(col: Optional[str], fallback: str) -> str:
    if col and is_safe_column_ident(str(col)):
        return str(col).strip()
    return sanitize_identifier(fallback)


class FormulaCompiler:
    """
    Intelligent BI Formula & Expression Compiler (Section 3).
    Safely translates declarative BI expressions into DuckDB Analytical SQL:
    SUM(), AVG(), COUNT(), COUNT_DISTINCT() / COUNT DISTINCT, MIN(), MAX(),
    MEDIAN(), RANK(), PERCENTAGE(), YOY(), MOM(), YTD(), MTD(), RUNNING_TOTAL().
    """

    @staticmethod
    def compile_measure(
        expr: str,
        time_col: Optional[str] = None,
        order_col: Optional[str] = None,
        partition_col: Optional[str] = None,
    ) -> str:
        raw = expr.strip()

        normalized = re.sub(r"\bCOUNT\s+DISTINCT\s*\(", "COUNT_DISTINCT(", raw, flags=re.IGNORECASE)

        def _extract_arg(fn: str) -> Optional[str]:
            m = re.match(rf'^{fn}\(\s*(?:"([^"]+)"|([^)]+?))\s*\)$', normalized, re.IGNORECASE)
            if not m:
                return None
            cand = (m.group(1) if m.group(1) is not None else m.group(2)).strip()
            if cand == "*" or is_safe_column_ident(cand):
                return cand
            return None

        if arg := _extract_arg("SUM"):
            return f'COALESCE(SUM("{arg}"), 0)'
        if arg := _extract_arg("AVG"):
            return f'AVG("{arg}")'
        if arg := _extract_arg("COUNT"):
            return "COUNT(*)" if arg == "*" else f'COUNT("{arg}")'
        if arg := _extract_arg("COUNT_DISTINCT"):
            return f'COUNT(DISTINCT "{arg}")'
        if arg := _extract_arg("MIN"):
            return f'MIN("{arg}")'
        if arg := _extract_arg("MAX"):
            return f'MAX("{arg}")'
        if arg := _extract_arg("MEDIAN"):
            return f'MEDIAN("{arg}")'
        if arg := _extract_arg("STDDEV"):
            return f'STDDEV_SAMP("{arg}")'
        if arg := _extract_arg("STDDEV_SAMP"):
            return f'STDDEV_SAMP("{arg}")'
        if arg := _extract_arg("VARIANCE"):
            return f'VAR_SAMP("{arg}")'
        if arg := _extract_arg("P25"):
            return f'QUANTILE_CONT("{arg}", 0.25)'
        if arg := _extract_arg("P75"):
            return f'QUANTILE_CONT("{arg}", 0.75)'
        if arg := _extract_arg("P90"):
            return f'QUANTILE_CONT("{arg}", 0.90)'
        if arg := _extract_arg("P95"):
            return f'QUANTILE_CONT("{arg}", 0.95)'
        if arg := _extract_arg("LAST_VALUE"):
            if time_col and is_safe_column_ident(str(time_col)):
                return f'ARG_MAX("{arg}", "{str(time_col).strip()}")'
            return f'ANY_VALUE("{arg}")'

        if arg := _extract_arg("RANK"):
            ord_expr = f'SUM("{arg}") DESC' if arg != "*" else "COUNT(*) DESC"
            return f"RANK() OVER (ORDER BY {ord_expr})"

        if arg := _extract_arg("PERCENTAGE"):
            return f'(COALESCE(SUM("{arg}"), 0) * 100.0) / NULLIF(SUM(SUM("{arg}")) OVER (), 0)'

        if arg := _extract_arg("RUNNING_TOTAL"):
            ord_field = _safe_order_col(order_col or time_col, arg)
            return f'SUM(COALESCE(SUM("{arg}"), 0)) OVER (ORDER BY "{ord_field}" ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)'

        if arg := _extract_arg("YOY"):
            ord_field = _safe_order_col(order_col or time_col, "year")
            return (
                f'((SUM("{arg}") - LAG(SUM("{arg}"), 1) OVER (ORDER BY "{ord_field}")) * 100.0) '
                f'/ NULLIF(ABS(LAG(SUM("{arg}"), 1) OVER (ORDER BY "{ord_field}")), 0)'
            )

        if arg := _extract_arg("MOM"):
            ord_field = _safe_order_col(order_col or time_col, "month_key")
            return (
                f'((SUM("{arg}") - LAG(SUM("{arg}"), 1) OVER (ORDER BY "{ord_field}")) * 100.0) '
                f'/ NULLIF(ABS(LAG(SUM("{arg}"), 1) OVER (ORDER BY "{ord_field}")), 0)'
            )

        if arg := _extract_arg("YTD"):
            tcol = _safe_order_col(time_col or order_col, "order_date")
            return (
                f'SUM(CASE WHEN EXTRACT(YEAR FROM CAST("{tcol}" AS DATE)) = '
                f'(SELECT MAX(EXTRACT(YEAR FROM CAST("{tcol}" AS DATE))) FROM _base) '
                f'THEN "{arg}" ELSE 0 END)'
            )

        if arg := _extract_arg("MTD"):
            tcol = _safe_order_col(time_col or order_col, "order_date")
            return (
                f'SUM(CASE WHEN DATE_TRUNC(\'month\', CAST("{tcol}" AS DATE)) = '
                f'(SELECT MAX(DATE_TRUNC(\'month\', CAST("{tcol}" AS DATE))) FROM _base) '
                f'THEN "{arg}" ELSE 0 END)'
            )

        # General compound formulas (aggregate / arithmetic / math / string / date / Jalali / conditional):
        # Validate strict sqlglot AST allowlist + expand Jalali helpers safely for DuckDB
        compiled = re.sub(
            r"COUNT_DISTINCT\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)",
            r'COUNT(DISTINCT "\1")',
            normalized,
            flags=re.IGNORECASE,
        )
        compiled = re.sub(
            r"\b(SUM|AVG|COUNT|MIN|MAX|MEDIAN|STDDEV|VARIANCE)\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)",
            lambda m: f'{m.group(1).upper()}("{m.group(2)}")',
            compiled,
            flags=re.IGNORECASE,
        )
        from app.engines.formula_engine import expand_jalali_sql_calls
        compiled = expand_jalali_sql_calls(compiled)
        validate_formula_ast(compiled)
        return compiled


class DataProfiler:
    """Profiles Warehouse datasets while respecting RLS/CLS security context."""

    @staticmethod
    def profile_table(
        table_name: str,
        rls_clauses: Optional[list[str]] = None,
        rls_params: Optional[list[Any]] = None,
        hidden_columns: Optional[set[str]] = None,
        sec_ctx: Optional[DataSecurityContext] = None,
    ) -> dict[str, Any]:
        cols_info = warehouse.describe_table(table_name)
        eff_ctx = sec_ctx or DataSecurityContext(
            user_id="profiler",
            rls_where_clauses=list(rls_clauses or []),
            rls_params=list(rls_params or []),
            hidden_columns=set(hidden_columns or set()),
            masked_columns={},
            context_hash="profiler",
        )
        sub_sql, params, visible_cols = build_security_projection_and_where(
            table_name, cols_info, eff_ctx, source_schema="main"
        )
        count_res = warehouse.execute_query(f"SELECT COUNT(*) AS cnt FROM ({sub_sql}) AS _prof", params)
        row_count = int(count_res["rows"][0]["cnt"]) if count_res["rows"] else 0

        visible_set = set(visible_cols)
        visible_meta = [c for c in cols_info if c["column_name"] in visible_set]
        field_stats: list[dict[str, Any]] = []
        fast_approx = row_count > 200_000
        batch_size = 25

        for start in range(0, len(visible_meta), batch_size):
            batch = visible_meta[start : start + batch_size]
            select_exprs: list[str] = []
            col_flags: list[tuple[dict[str, str], bool, bool]] = []
            for idx, c in enumerate(batch):
                cname = c["column_name"]
                ctype = c["column_type"].lower()
                is_masked = cname in eff_ctx.masked_columns
                is_num = (not is_masked) and any(
                    k in ctype for k in ("int", "double", "float", "decimal", "hugeint", "bigint")
                )
                col_flags.append((c, is_masked, is_num))
                distinct_fn = f'APPROX_COUNT_DISTINCT("{cname}")' if fast_approx else f'COUNT(DISTINCT "{cname}")'
                select_exprs.append(f'{distinct_fn} AS "d_{idx}"')
                select_exprs.append(f'(COUNT(*) - COUNT("{cname}")) AS "n_{idx}"')
                if is_num:
                    select_exprs.append(f'MIN("{cname}") AS "mn_{idx}"')
                    select_exprs.append(f'MAX("{cname}") AS "mx_{idx}"')
                    select_exprs.append(f'AVG("{cname}") AS "av_{idx}"')
                    select_exprs.append(
                        f'NULL AS "md_{idx}"' if fast_approx else f'MEDIAN("{cname}") AS "md_{idx}"'
                    )
                else:
                    select_exprs.append(f'CAST(MIN("{cname}") AS VARCHAR) AS "mn_{idx}"')
                    select_exprs.append(f'CAST(MAX("{cname}") AS VARCHAR) AS "mx_{idx}"')
                    select_exprs.append(f'NULL AS "av_{idx}"')
                    select_exprs.append(f'NULL AS "md_{idx}"')

            q = f'SELECT {", ".join(select_exprs)} FROM ({sub_sql}) AS _prof'
            stat_rows = warehouse.execute_query(q, params, limit=1)["rows"]
            stat = stat_rows[0] if stat_rows else {}
            for idx, (c, is_masked, _is_num) in enumerate(col_flags):
                av = stat.get(f"av_{idx}")
                md = stat.get(f"md_{idx}")
                field_stats.append(
                    {
                        "field_name": c["column_name"],
                        "raw_type": "VARCHAR" if is_masked else c["column_type"],
                        "distinct_count": int(stat.get(f"d_{idx}") or 0),
                        "null_count": int(stat.get(f"n_{idx}") or 0),
                        "min": stat.get(f"mn_{idx}"),
                        "max": stat.get(f"mx_{idx}"),
                        "avg": round(float(av), 4) if av is not None else None,
                        "median": round(float(md), 4) if md is not None else None,
                    }
                )
        return {"table_name": table_name, "row_count": row_count, "fields": field_stats}

    @staticmethod
    def apply_transformations(df: pd.DataFrame, steps: list[dict[str, Any]]) -> pd.DataFrame:
        out = df.copy()
        for step in steps:
            if not step.get("is_enabled", True):
                continue
            ttype = step.get("transform_type") or step.get("step_type")
            cfg = step.get("config_json", {})
            if ttype == "rename" and cfg.get("from") in out.columns and cfg.get("to"):
                out = out.rename(columns={cfg["from"]: sanitize_identifier(cfg["to"])})
            elif ttype == "deduplicate":
                subset = cfg.get("columns") or None
                out = out.drop_duplicates(subset=subset).reset_index(drop=True)
            elif ttype == "fill_null" and cfg.get("column") in out.columns:
                out[cfg["column"]] = out[cfg["column"]].fillna(cfg.get("value", 0))
            elif ttype == "filter" and cfg.get("column") in out.columns:
                col = cfg["column"]
                op = cfg.get("operator", "eq")
                val = cfg.get("value")
                if op == "eq":
                    out = out[out[col] == val]
                elif op == "neq":
                    out = out[out[col] != val]
                elif op == "gt":
                    out = out[out[col] > val]
                elif op == "not_null":
                    out = out[out[col].notna()]
            elif ttype == "calculated" and cfg.get("target_column") and cfg.get("formula"):
                tcol = sanitize_identifier(cfg["target_column"])
                formula = str(cfg["formula"]).strip()
                out[tcol] = safe_eval_dataframe_formula(out, formula)
            elif ttype == "cast" and cfg.get("column") in out.columns:
                col = cfg["column"]
                to_t = cfg.get("target_type", "string")
                if to_t == "float":
                    out[col] = pd.to_numeric(out[col], errors="coerce").astype(float)
                elif to_t == "integer":
                    out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0).astype(int)
                elif to_t == "string":
                    out[col] = out[col].astype(str)
        return out


class DataDiagnosticsEngine:
    """
    100%-row DuckDB SQL Diagnostics for Large Datasets (Section 2):
    - Outliers (IQR & Z-Score)
    - Pearson Correlation Matrix
    - Duplicate & Incomplete Data Audit
    - Time-Period Comparison & Trend Anomaly Detection
    """

    @staticmethod
    def analyze(
        table_name: str,
        *,
        sec_ctx: DataSecurityContext,
        date_column: Optional[str] = None,
        metric_column: Optional[str] = None,
    ) -> dict[str, Any]:
        cols_info = warehouse.describe_table(table_name)
        sub_sql, params, visible_cols = build_security_projection_and_where(
            table_name, cols_info, sec_ctx, source_schema="main"
        )
        visible_set = set(visible_cols)
        visible_meta = [c for c in cols_info if c["column_name"] in visible_set]

        total_res = warehouse.execute_query(f"SELECT COUNT(*) AS cnt FROM ({sub_sql}) AS _diag", params)
        total_rows = int(total_res["rows"][0]["cnt"]) if total_res["rows"] else 0

        if total_rows == 0 or not visible_cols:
            return {
                "total_rows": total_rows,
                "duplicate_rows": 0,
                "incomplete_columns": [],
                "outliers": [],
                "correlation_columns": [],
                "correlations": [],
                "period_comparison": None,
                "trend_anomalies": [],
            }

        # 1. Duplicate rows across visible columns (computed in DuckDB)
        quoted_vis = ", ".join(f'"{c}"' for c in visible_cols[:40])
        dup_sql = (
            f"SELECT COALESCE(SUM(cnt - 1), 0) AS dups FROM ("
            f"SELECT COUNT(*) AS cnt FROM ({sub_sql}) AS _d GROUP BY {quoted_vis} HAVING COUNT(*) > 1"
            f") AS _dup_g"
        )
        dup_res = warehouse.execute_query(dup_sql, params)
        duplicate_rows = int(dup_res["rows"][0]["dups"]) if dup_res["rows"] else 0

        # 2. Incomplete / null & empty string diagnostics per column
        null_exprs = []
        for idx, c in enumerate(visible_meta[:40]):
            cn = c["column_name"]
            null_exprs.append(
                f'SUM(CASE WHEN "{cn}" IS NULL OR TRIM(CAST("{cn}" AS VARCHAR)) = \'\' THEN 1 ELSE 0 END) AS "inc_{idx}"'
            )
        inc_rows = warehouse.execute_query(f"SELECT {', '.join(null_exprs)} FROM ({sub_sql}) AS _d", params)["rows"]
        inc_row = inc_rows[0] if inc_rows else {}
        incomplete_columns = []
        for idx, c in enumerate(visible_meta[:40]):
            missing = int(inc_row.get(f"inc_{idx}") or 0)
            if missing > 0:
                incomplete_columns.append(
                    {
                        "column": c["column_name"],
                        "missing_count": missing,
                        "missing_pct": round((missing / total_rows) * 100.0, 2),
                    }
                )

        # Numeric & date visible columns
        numeric_cols = [
            c["column_name"]
            for c in visible_meta
            if c["column_name"] not in sec_ctx.masked_columns
            and any(k in c["column_type"].lower() for k in ("int", "double", "float", "decimal", "hugeint", "bigint"))
        ][:10]
        date_cols = [
            c["column_name"]
            for c in visible_meta
            if c["column_name"] not in sec_ctx.masked_columns
            and any(k in c["column_type"].lower() for k in ("date", "time"))
        ]

        # 3. IQR Outliers on numeric columns
        outliers: list[dict[str, Any]] = []
        for col in numeric_cols[:6]:
            q_stats = warehouse.execute_query(
                f'SELECT QUANTILE_CONT("{col}", 0.25) AS q1, QUANTILE_CONT("{col}", 0.75) AS q3, '
                f'AVG("{col}") AS avg_v, STDDEV_SAMP("{col}") AS sd_v '
                f'FROM ({sub_sql}) AS _d WHERE "{col}" IS NOT NULL',
                params,
            )["rows"]
            if not q_stats or q_stats[0]["q1"] is None or q_stats[0]["q3"] is None:
                continue
            q1 = float(q_stats[0]["q1"])
            q3 = float(q_stats[0]["q3"])
            iqr = q3 - q1
            if iqr <= 0:
                continue
            low = q1 - 1.5 * iqr
            high = q3 + 1.5 * iqr
            out_q = warehouse.execute_query(
                f'SELECT COUNT(*) AS cnt, MIN("{col}") AS min_v, MAX("{col}") AS max_v '
                f'FROM ({sub_sql}) AS _d WHERE "{col}" < ? OR "{col}" > ?',
                params + [low, high],
            )["rows"]
            cnt = int(out_q[0]["cnt"]) if out_q else 0
            if cnt > 0:
                samples_q = warehouse.execute_query(
                    f'SELECT "{col}" AS v FROM ({sub_sql}) AS _d '
                    f'WHERE "{col}" < ? OR "{col}" > ? ORDER BY ABS("{col}" - ?) DESC LIMIT 5',
                    params + [low, high, (q1 + q3) / 2.0],
                    limit=5,
                )["rows"]
                outliers.append(
                    {
                        "column": col,
                        "q1": round(q1, 4),
                        "q3": round(q3, 4),
                        "iqr": round(iqr, 4),
                        "lower_fence": round(low, 4),
                        "upper_fence": round(high, 4),
                        "outlier_count": cnt,
                        "outlier_pct": round((cnt / total_rows) * 100.0, 2),
                        "min_val": float(out_q[0]["min_v"]) if out_q[0]["min_v"] is not None else None,
                        "max_val": float(out_q[0]["max_v"]) if out_q[0]["max_v"] is not None else None,
                        "sample_values": [round(float(r["v"]), 4) for r in samples_q if r["v"] is not None],
                    }
                )

        # 4. Pearson Correlation Matrix across numeric columns
        correlations: list[dict[str, Any]] = []
        if len(numeric_cols) >= 2:
            corr_exprs = []
            pairs: list[tuple[str, str, str]] = []
            for i in range(len(numeric_cols)):
                for j in range(i + 1, len(numeric_cols)):
                    a, b = numeric_cols[i], numeric_cols[j]
                    alias = f"c_{i}_{j}"
                    corr_exprs.append(f'CORR(CAST("{a}" AS DOUBLE), CAST("{b}" AS DOUBLE)) AS "{alias}"')
                    pairs.append((a, b, alias))
            if corr_exprs:
                c_row = warehouse.execute_query(
                    f"SELECT {', '.join(corr_exprs)} FROM ({sub_sql}) AS _d", params
                )["rows"]
                if c_row:
                    for a, b, alias in pairs:
                        val = c_row[0].get(alias)
                        if val is not None:
                            r_val = round(float(val), 4)
                            correlations.append(
                                {
                                    "column_a": a,
                                    "column_b": b,
                                    "pearson_r": r_val,
                                    "strength": "strong" if abs(r_val) >= 0.7 else ("moderate" if abs(r_val) >= 0.4 else "weak"),
                                }
                            )
            correlations.sort(key=lambda x: abs(x["pearson_r"]), reverse=True)

        # 5. Time-period comparison & trend anomaly detection
        chosen_date = date_column if (date_column and date_column in visible_set) else (date_cols[0] if date_cols else None)
        chosen_metric = metric_column if (metric_column and metric_column in numeric_cols) else (numeric_cols[0] if numeric_cols else None)
        period_comparison = None
        trend_anomalies: list[dict[str, Any]] = []
        if chosen_date:
            agg_sql = f'SUM(CAST("{chosen_metric}" AS DOUBLE))' if chosen_metric else "COUNT(*)"
            bucket_sql = (
                f'SELECT STRFTIME(DATE_TRUNC(\'month\', TRY_CAST("{chosen_date}" AS TIMESTAMP)), \'%Y-%m\') AS period_key, '
                f'COUNT(*) AS row_count, {agg_sql} AS metric_val '
                f'FROM ({sub_sql}) AS _d WHERE TRY_CAST("{chosen_date}" AS TIMESTAMP) IS NOT NULL '
                f'GROUP BY 1 ORDER BY 1 ASC LIMIT 120'
            )
            buckets = warehouse.execute_query(bucket_sql, params, limit=120)["rows"]
            if len(buckets) >= 2:
                prev_b = buckets[-2]
                curr_b = buckets[-1]
                prev_v = float(prev_b["metric_val"] or 0.0)
                curr_v = float(curr_b["metric_val"] or 0.0)
                delta = curr_v - prev_v
                delta_pct = round((delta / abs(prev_v)) * 100.0, 2) if prev_v != 0 else None
                period_comparison = {
                    "date_column": chosen_date,
                    "metric_column": chosen_metric or "row_count",
                    "previous_period": prev_b["period_key"],
                    "previous_value": round(prev_v, 4),
                    "current_period": curr_b["period_key"],
                    "current_value": round(curr_v, 4),
                    "delta_abs": round(delta, 4),
                    "delta_pct": delta_pct,
                    "buckets_count": len(buckets),
                }
            if len(buckets) >= 4:
                vals = [float(b["metric_val"] or 0.0) for b in buckets]
                mean_v = sum(vals) / len(vals)
                var_v = sum((x - mean_v) ** 2 for x in vals) / max(1, len(vals) - 1)
                std_v = var_v ** 0.5
                if std_v > 0:
                    for b in buckets:
                        v = float(b["metric_val"] or 0.0)
                        z = (v - mean_v) / std_v
                        if abs(z) >= 1.8:
                            trend_anomalies.append(
                                {
                                    "period": b["period_key"],
                                    "value": round(v, 4),
                                    "baseline_mean": round(mean_v, 4),
                                    "z_score": round(z, 2),
                                    "direction": "spike" if z > 0 else "drop",
                                }
                            )

        return {
            "total_rows": total_rows,
            "duplicate_rows": duplicate_rows,
            "incomplete_columns": incomplete_columns,
            "outliers": outliers,
            "correlation_columns": numeric_cols,
            "correlations": correlations,
            "period_comparison": period_comparison,
            "trend_anomalies": trend_anomalies,
        }

