import re
from typing import Any, Optional
import pandas as pd
from app.core.rls_cls import DataSecurityContext, build_security_projection_and_where
from app.core.sql_guard import safe_eval_dataframe_formula, validate_formula_ast
from app.engines.warehouse import sanitize_identifier, warehouse

IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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
            m = re.match(rf"^{fn}\(\s*([A-Za-z_][A-Za-z0-9_]*|\*)\s*\)$", normalized, re.IGNORECASE)
            return m.group(1) if m else None

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

        if arg := _extract_arg("RANK"):
            ord_expr = f'SUM("{arg}") DESC' if arg != "*" else "COUNT(*) DESC"
            return f"RANK() OVER (ORDER BY {ord_expr})"

        if arg := _extract_arg("PERCENTAGE"):
            return f'(COALESCE(SUM("{arg}"), 0) * 100.0) / NULLIF(SUM(SUM("{arg}")) OVER (), 0)'

        if arg := _extract_arg("RUNNING_TOTAL"):
            ord_field = sanitize_identifier(order_col or time_col or arg)
            return f'SUM(COALESCE(SUM("{arg}"), 0)) OVER (ORDER BY "{ord_field}" ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)'

        if arg := _extract_arg("YOY"):
            ord_field = sanitize_identifier(order_col or time_col or "year")
            return (
                f'((SUM("{arg}") - LAG(SUM("{arg}"), 1) OVER (ORDER BY "{ord_field}")) * 100.0) '
                f'/ NULLIF(ABS(LAG(SUM("{arg}"), 1) OVER (ORDER BY "{ord_field}")), 0)'
            )

        if arg := _extract_arg("MOM"):
            ord_field = sanitize_identifier(order_col or time_col or "month_key")
            return (
                f'((SUM("{arg}") - LAG(SUM("{arg}"), 1) OVER (ORDER BY "{ord_field}")) * 100.0) '
                f'/ NULLIF(ABS(LAG(SUM("{arg}"), 1) OVER (ORDER BY "{ord_field}")), 0)'
            )

        if arg := _extract_arg("YTD"):
            tcol = sanitize_identifier(time_col or order_col or "order_date")
            return (
                f'SUM(CASE WHEN EXTRACT(YEAR FROM CAST("{tcol}" AS DATE)) = '
                f'(SELECT MAX(EXTRACT(YEAR FROM CAST("{tcol}" AS DATE))) FROM _base) '
                f'THEN "{arg}" ELSE 0 END)'
            )

        if arg := _extract_arg("MTD"):
            tcol = sanitize_identifier(time_col or order_col or "order_date")
            return (
                f'SUM(CASE WHEN DATE_TRUNC(\'month\', CAST("{tcol}" AS DATE)) = '
                f'(SELECT MAX(DATE_TRUNC(\'month\', CAST("{tcol}" AS DATE))) FROM _base) '
                f'THEN "{arg}" ELSE 0 END)'
            )

        # Arithmetic combination of SUM/AVG/COUNT calls or direct column arithmetic e.g. SUM(revenue) - SUM(cost)
        compiled = re.sub(
            r"COUNT_DISTINCT\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)",
            r'COUNT(DISTINCT "\1")',
            normalized,
            flags=re.IGNORECASE,
        )
        compiled = re.sub(
            r"\b(SUM|AVG|COUNT|MIN|MAX|MEDIAN)\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)",
            lambda m: f'{m.group(1).upper()}("{m.group(2)}")',
            compiled,
            flags=re.IGNORECASE,
        )
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
