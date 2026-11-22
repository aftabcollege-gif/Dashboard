"""
AST-based SQL Security & Read-Only Guard for DuckDB & Operational Sources.

Replaces fragile keyword blacklists with strict sqlglot AST inspection:
- Allows strictly a single read-only SELECT (including CTEs and set operations UNION/INTERSECT/EXCEPT).
- Rejects all DDL, DML, COPY, ATTACH, PRAGMA, INSTALL, LOAD, SET, EXPLAIN, SHOW, DESCRIBE, CALL.
- Rejects DuckDB file/network/system table functions and scalar functions:
  read_csv*, read_parquet*, read_json*, read_text*, read_blob*, glob, sqlite_*,
  postgres_*, mysql_*, duckdb_*, pragma_*, iceberg_*, delta_*, http*, getenv, system, etc.
- Rejects string literal file/URL table sources (e.g. FROM '/etc/passwd' or 'https://...').
- Rejects access to internal system schemas (information_schema, pg_catalog, system, duckdb_internal).
- Extracts all referenced physical table names (excluding local CTE aliases) for tenant allowlist verification.
"""

from __future__ import annotations

import ast
import re
from typing import Iterable, Optional
import sqlglot
from sqlglot import exp


FORBIDDEN_EXACT_FUNCTIONS = {
    "glob",
    "getenv",
    "system",
    "current_setting",
    "set_config",
    "query_table",
    "query",
    "sniff_csv",
    "parquet_metadata",
    "parquet_schema",
    "parquet_file_metadata",
    "parquet_kv_metadata",
    "export_database",
    "copy_to",
    "copy_from",
    "load_aws_credentials",
    "checkpoint",
    "force_checkpoint",
    "vacuum",
    "analyze",
    "enable_profile",
    "enable_logging",
}

FORBIDDEN_FUNC_PREFIXES = (
    "read_",
    "write_",
    "copy_",
    "export_",
    "import_",
    "duckdb_",
    "pragma_",
    "sqlite_",
    "postgres_",
    "mysql_",
    "http",
    "iceberg_",
    "delta_",
    "azure_",
    "s3_",
    "gcs_",
    "excel_",
    "parquet_",
    "csv_",
    "json_",
    "hf_",
    "ducklake_",
    "odbc_",
    "jdbc_",
)

FORBIDDEN_FUNC_SUFFIXES = (
    "_scan",
    "_attach",
    "_metadata",
    "_secrets",
    "_settings",
)

FORBIDDEN_SCHEMAS = {
    "information_schema",
    "pg_catalog",
    "sys",
    "system",
    "duckdb_internal",
}

FORBIDDEN_RAW_PATTERNS = re.compile(
    r"\b(INSTALL|LOAD|ATTACH|DETACH|PRAGMA|COPY|EXPORT|IMPORT|CALL|SUMMARIZE|DESCRIBE|SHOW|USE|SET|RESET|VACUUM|CHECKPOINT)\b",
    re.IGNORECASE,
)


def _node_func_name(node: exp.Expression) -> str:
    if isinstance(node, exp.Anonymous):
        return (node.name or "").strip().lower()
    sql_name = getattr(node, "sql_name", None)
    if callable(sql_name):
        try:
            return str(sql_name() or "").strip().lower()
        except Exception:
            pass
    return type(node).__name__.lower()


def _validate_ast_tree(tree: exp.Expression) -> set[str]:
    allowed_roots = (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery)
    if not isinstance(tree, allowed_roots):
        raise ValueError(f"Only read-only SELECT queries are allowed (got {type(tree).__name__})")

    cte_names: set[str] = set()
    for cte in tree.find_all(exp.CTE):
        alias = cte.alias_or_name
        if alias:
            cte_names.add(alias.strip().lower())

    referenced_tables: set[str] = set()

    for node in tree.walk():
        # Block any nested command or DML inside expressions
        if isinstance(
            node,
            (
                exp.Insert,
                exp.Update,
                exp.Delete,
                exp.Drop,
                exp.Create,
                exp.Alter,
                exp.Command,
                exp.Copy,
                exp.Pragma,
                exp.Set,
                exp.Use,
                exp.Attach,
                exp.Detach,
                exp.Transaction,
                exp.Commit,
                exp.Rollback,
                exp.Merge,
                exp.Grant,
                exp.Revoke,
                exp.Describe,
                exp.Show,
                exp.ReadCSV,
            ),
        ):
            raise ValueError(f"Forbidden SQL statement or clause: {type(node).__name__}")

        # Block dangerous functions / table-valued file/network readers
        if isinstance(node, exp.Func):
            fname = _node_func_name(node)
            if (
                fname in FORBIDDEN_EXACT_FUNCTIONS
                or any(fname.startswith(p) for p in FORBIDDEN_FUNC_PREFIXES)
                or any(fname.endswith(s) for s in FORBIDDEN_FUNC_SUFFIXES)
            ):
                raise ValueError(f"Forbidden SQL function: {fname}")

        # Inspect table references
        if isinstance(node, exp.Table):
            # Literal string in FROM '/etc/passwd' or FROM 'https://...'
            if isinstance(node.this, exp.Literal):
                raise ValueError("Reading directly from file paths or URLs is forbidden")

            schema_name = (node.db or "").strip().lower()
            catalog_name = (node.catalog or "").strip().lower()
            if catalog_name or (schema_name and schema_name != "main"):
                raise ValueError(f"Access to external/system catalog or schema '{catalog_name or schema_name}' is forbidden")

            tname = (node.name or "").strip()
            if not tname:
                # Could be a table function wrapper inside Table
                if isinstance(node.this, exp.Func):
                    fname = _node_func_name(node.this)
                    raise ValueError(f"Table-valued function '{fname}' is not permitted")
                continue

            low_t = tname.lower()
            if low_t.startswith(("duckdb_", "sqlite_", "pragma_")) or low_t in ("sqlite_master", "sqlite_schema"):
                raise ValueError(f"Access to system table '{tname}' is forbidden")

            if "/" in tname or "\\" in tname or "://" in tname:
                raise ValueError("File path or URL table references are forbidden")

            if low_t not in cte_names:
                referenced_tables.add(tname)

    return referenced_tables


def validate_readonly_sql_ast(
    sql: str,
    *,
    allowed_tables: Optional[Iterable[str]] = None,
    dialect: str = "duckdb",
    require_table_allowlist: bool = False,
) -> set[str]:
    """
    Validate a SQL string is strictly read-only SELECT and return referenced physical tables.
    If `allowed_tables` is provided (or `require_table_allowlist=True`), ensures every referenced
    physical table belongs to `allowed_tables`.
    """
    cleaned = (sql or "").strip()
    if not cleaned:
        raise ValueError("Empty SQL query")

    # Strip single trailing semicolon only
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].strip()
    if not cleaned:
        raise ValueError("Empty SQL query")

    try:
        parsed_list = sqlglot.parse(cleaned, read=dialect)
    except Exception as exc:
        raise ValueError(f"Invalid SQL syntax: {exc}") from exc

    statements = [s for s in parsed_list if s is not None]
    if len(statements) != 1:
        raise ValueError("Only a single SELECT statement is allowed")

    tree = statements[0]
    referenced = _validate_ast_tree(tree)

    # Extra defense-in-depth regex check on unparsed command keywords outside quoted literals
    # Remove single-quoted string literals before checking raw command tokens
    stripped_literals = re.sub(r"'(?:''|[^'])*'", "''", cleaned)
    if FORBIDDEN_RAW_PATTERNS.search(stripped_literals):
        # Verify whether token is an actual keyword vs column/table name substring is handled by \b,
        # reject explicit DuckDB control keywords like SUMMARIZE, INSTALL, LOAD, COPY, PRAGMA, ATTACH.
        m = FORBIDDEN_RAW_PATTERNS.search(stripped_literals)
        kw = m.group(1).upper() if m else ""
        if kw in {"INSTALL", "LOAD", "ATTACH", "DETACH", "PRAGMA", "COPY", "SUMMARIZE", "VACUUM", "CHECKPOINT"}:
            raise ValueError(f"Forbidden SQL keyword: {kw}")

    if allowed_tables is not None or require_table_allowlist:
        allow_lower = {t.strip().lower() for t in (allowed_tables or []) if t and t.strip()}
        for ref in referenced:
            if ref.lower() not in allow_lower:
                raise PermissionError(f"Access denied to table '{ref}' outside your organization scope")

    return referenced


def validate_filter_expression_ast(expr_sql: str, allowed_columns: Optional[set[str]] = None) -> str:
    """Validate custom_expression filter clause using sqlglot AST (no subqueries, no unsafe functions)."""
    cleaned = (expr_sql or "").strip()
    if not cleaned or len(cleaned) > 500:
        raise ValueError("Invalid custom filter expression")
    try:
        tree = sqlglot.parse_one(f"SELECT 1 WHERE {cleaned}", read="duckdb")
    except Exception as exc:
        raise ValueError(f"Invalid custom filter expression syntax: {exc}") from exc

    where_clause = tree.args.get("where")
    if not where_clause:
        raise ValueError("Invalid custom filter expression")

    for node in where_clause.walk():
        if isinstance(node, (exp.Select, exp.Subquery, exp.Union, exp.Table, exp.Command)):
            raise ValueError("Subqueries and table references are not permitted in filter expressions")
        if isinstance(node, exp.Func):
            fname = _node_func_name(node)
            if (
                fname in FORBIDDEN_EXACT_FUNCTIONS
                or any(fname.startswith(p) for p in FORBIDDEN_FUNC_PREFIXES)
                or any(fname.endswith(s) for s in FORBIDDEN_FUNC_SUFFIXES)
            ):
                raise ValueError(f"Forbidden function in filter expression: {fname}")
        if isinstance(node, exp.Column) and allowed_columns is not None:
            col_name = (node.name or "").strip()
            if col_name and col_name not in allowed_columns:
                raise ValueError(f"Unknown or restricted column in filter expression: {col_name}")
    return cleaned


SAFE_FORMULA_FUNCS = {"SUM", "AVG", "COUNT", "MIN", "MAX", "MEDIAN", "COALESCE", "ABS", "ROUND", "NULLIF"}


def validate_formula_ast(compiled_sql_expr: str) -> None:
    """Validate compiled aggregate formula contains no subqueries, tables, or unsafe functions."""
    try:
        tree = sqlglot.parse_one(f"SELECT {compiled_sql_expr} AS _m", read="duckdb")
    except Exception as exc:
        raise ValueError(f"Invalid formula expression: {exc}") from exc

    for node in tree.walk():
        if isinstance(node, (exp.Subquery, exp.Table, exp.Command)):
            # Note: YTD/MTD intentionally reference `_base` subquery in FormulaCompiler,
            # so only validate compound user expressions that call this guard.
            raise ValueError("Subqueries and arbitrary tables are not permitted in custom formulas")
        if isinstance(node, exp.Func):
            fname = _node_func_name(node)
            if (
                fname in FORBIDDEN_EXACT_FUNCTIONS
                or any(fname.startswith(p) for p in FORBIDDEN_FUNC_PREFIXES)
                or any(fname.endswith(s) for s in FORBIDDEN_FUNC_SUFFIXES)
            ):
                raise ValueError(f"Forbidden function in formula: {fname}")


def safe_eval_dataframe_formula(df, formula: str):
    """
    Safely evaluate arithmetic column formula over pandas DataFrame without pandas.eval().
    Supports only: +, -, *, /, //, %, parentheses, numeric constants, and dataframe column names.
    """
    tree = ast.parse(formula, mode="eval")

    def _eval(node):
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in df.columns:
                raise ValueError(f"Unknown column '{node.id}' in transformation formula")
            return df[node.id]
        if isinstance(node, ast.UnaryOp):
            val = _eval(node.operand)
            if isinstance(node.op, ast.UAdd):
                return +val
            if isinstance(node.op, ast.USub):
                return -val
            raise ValueError("Unsupported unary operator")
        if isinstance(node, ast.BinOp):
            left = _eval(node.left)
            right = _eval(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.FloorDiv):
                return left // right
            if isinstance(node.op, ast.Mod):
                return left % right
            raise ValueError("Unsupported binary operator")
        raise ValueError(f"Unsupported AST node in formula: {type(node).__name__}")

    return _eval(tree)
