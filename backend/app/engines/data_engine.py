import re
from typing import Any, Optional
import pandas as pd
from app.engines.warehouse import warehouse, sanitize_identifier

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
        upper = raw.upper()

        # Normalize "COUNT DISTINCT(col)" -> "COUNT_DISTINCT(col)"
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
        # Validate only safe SQL tokens appear
        if re.search(r"[;]|(--)|(/\*)|(\bDROP\b|\bDELETE\b|\bUPDATE\b|\bINSERT\b)", upper):
            raise ValueError(f"Unsafe formula expression: {raw}")

        # Replace COUNT_DISTINCT(x) inside compound formulas
        compiled = re.sub(
            r"COUNT_DISTINCT\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)",
            r'COUNT(DISTINCT "\1")',
            normalized,
            flags=re.IGNORECASE,
        )
        # Quote column identifiers inside standard aggregates
        compiled = re.sub(
            r"\b(SUM|AVG|COUNT|MIN|MAX|MEDIAN)\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)",
            lambda m: f'{m.group(1).upper()}("{m.group(2)}")',
            compiled,
            flags=re.IGNORECASE,
        )
        return compiled


class DataProfiler:
    """Profiles Warehouse datasets and recommends joins, KPIs, and schema insights."""

    @staticmethod
    def profile_table(table_name: str) -> dict[str, Any]:
        cols_info = warehouse.describe_table(table_name)
        count_res = warehouse.execute_query(f'SELECT COUNT(*) AS cnt FROM "{table_name}"')
        row_count = int(count_res["rows"][0]["cnt"]) if count_res["rows"] else 0

        field_stats: list[dict[str, Any]] = []
        for c in cols_info:
            cname = c["column_name"]
            ctype = c["column_type"].lower()
            is_num = any(k in ctype for k in ("int", "double", "float", "decimal", "hugeint", "bigint"))
            if is_num:
                q = f'''
                    SELECT
                        COUNT(DISTINCT "{cname}") AS distinct_cnt,
                        COUNT(*) - COUNT("{cname}") AS null_cnt,
                        MIN("{cname}") AS min_val,
                        MAX("{cname}") AS max_val,
                        AVG("{cname}") AS avg_val,
                        MEDIAN("{cname}") AS median_val
                    FROM "{table_name}"
                '''
            else:
                q = f'''
                    SELECT
                        COUNT(DISTINCT "{cname}") AS distinct_cnt,
                        COUNT(*) - COUNT("{cname}") AS null_cnt,
                        CAST(MIN("{cname}") AS VARCHAR) AS min_val,
                        CAST(MAX("{cname}") AS VARCHAR) AS max_val,
                        NULL AS avg_val,
                        NULL AS median_val
                    FROM "{table_name}"
                '''
            stat = warehouse.execute_query(q)["rows"][0]
            field_stats.append(
                {
                    "field_name": cname,
                    "raw_type": c["column_type"],
                    "distinct_count": int(stat["distinct_cnt"] or 0),
                    "null_count": int(stat["null_cnt"] or 0),
                    "min": stat["min_val"],
                    "max": stat["max_val"],
                    "avg": round(float(stat["avg_val"]), 4) if stat["avg_val"] is not None else None,
                    "median": round(float(stat["median_val"]), 4) if stat["median_val"] is not None else None,
                }
            )
        return {"table_name": table_name, "row_count": row_count, "fields": field_stats}

    @staticmethod
    def apply_transformations(df: pd.DataFrame, steps: list[dict[str, Any]]) -> pd.DataFrame:
        out = df.copy()
        for step in steps:
            if not step.get("is_enabled", True):
                continue
            ttype = step.get("transform_type")
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
                # Safe arithmetic expressions over columns like "revenue - cost"
                tcol = sanitize_identifier(cfg["target_column"])
                formula = str(cfg["formula"])
                if re.match(r"^[A-Za-z0-9_+\-*/().\s]+$", formula):
                    out[tcol] = out.eval(formula)
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
