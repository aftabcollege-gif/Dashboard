"""
AST-based SQL Security & Read-Only Guard for DuckDB & Operational Sources.

Enforces strict sqlglot AST inspection:
- Allows strictly a single read-only SELECT (including CTEs and set operations UNION/INTERSECT/EXCEPT).
- Rejects all DDL, DML, COPY, ATTACH, PRAGMA, INSTALL, LOAD, SET, EXPLAIN, SHOW, DESCRIBE, CALL, SELECT INTO.
- Rejects DuckDB file/network/system table functions and scalar functions:
  read_csv*, read_parquet*, read_json*, read_text*, read_blob*, glob, sqlite_*,
  postgres_*, mysql_*, duckdb_*, pragma_*, iceberg_*, delta_*, http*, getenv, system, etc.
- Rejects string literal file/URL table sources (e.g. FROM '/etc/passwd' or 'https://...').
- Rejects ANY schema/catalog qualification on tables (including `main.*`, `information_schema.*`, `pg_catalog.*`).
- Uses lexical scope-aware CTE tracking so CTE aliases only shadow within their enclosing scope
  and cannot hide physical table references inside their own definition or sibling/outer subqueries.
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
    "current_catalog",
    "current_database",
    "current_schema",
    "current_schemas",
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
    "version",
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

FORBIDDEN_NODE_TYPES = (
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
    exp.Into,
)

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


def _validate_node_rules(node: exp.Expression, active_ctes: frozenset[str], referenced_tables: set[str]) -> None:
    if isinstance(node, FORBIDDEN_NODE_TYPES):
        raise ValueError(f"Forbidden SQL statement or clause: {type(node).__name__}")

    if isinstance(node, exp.Select) and node.args.get("into") is not None:
        raise ValueError("SELECT INTO statements are forbidden")

    if isinstance(node, exp.Func):
        fname = _node_func_name(node)
        if (
            fname in FORBIDDEN_EXACT_FUNCTIONS
            or any(fname.startswith(p) for p in FORBIDDEN_FUNC_PREFIXES)
            or any(fname.endswith(s) for s in FORBIDDEN_FUNC_SUFFIXES)
        ):
            raise ValueError(f"Forbidden SQL function: {fname}")

    if isinstance(node, exp.Table):
        if isinstance(node.this, exp.Literal):
            raise ValueError("Reading directly from file paths or URLs is forbidden")

        schema_name = (node.db or "").strip().lower()
        catalog_name = (node.catalog or "").strip().lower()
        if catalog_name or schema_name:
            raise ValueError(
                f"Qualified table references ('{catalog_name or schema_name}.*') are forbidden; reference registered dataset tables directly"
            )

        tname = (node.name or "").strip()
        if not tname:
            if isinstance(node.this, exp.Func):
                fname = _node_func_name(node.this)
                raise ValueError(f"Table-valued function '{fname}' is not permitted")
            return

        low_t = tname.lower()
        if low_t.startswith(("duckdb_", "sqlite_", "pragma_")) or low_t in ("sqlite_master", "sqlite_schema"):
            raise ValueError(f"Access to system table '{tname}' is forbidden")

        if "/" in tname or "\\" in tname or "://" in tname:
            raise ValueError("File path or URL table references are forbidden")

        if low_t not in active_ctes:
            referenced_tables.add(tname)


def _walk_scoped(
    node: exp.Expression,
    active_ctes: frozenset[str],
    referenced_tables: set[str],
    protected_tables: frozenset[str] = frozenset(),
) -> None:
    _validate_node_rules(node, active_ctes, referenced_tables)

    # Handle CTE scopes lexically so CTE aliases only shadow subsequent CTEs
    # and the body of their own query, never their own non-recursive definition
    # nor sibling/outer queries. Also forbid CTE aliases that shadow physical tables.
    with_node = node.args.get("with_") if hasattr(node, "args") else None
    child_scope = set(active_ctes)
    if isinstance(with_node, exp.With):
        is_recursive = bool(with_node.args.get("recursive"))
        for cte in with_node.expressions:
            alias = (cte.alias_or_name or "").strip().lower()
            if alias and alias in protected_tables:
                raise ValueError(f"CTE alias '{cte.alias_or_name}' cannot shadow a registered table")
            cte_body = cte.this
            if cte_body is not None:
                if is_recursive and alias and isinstance(cte_body, exp.Union):
                    # Base term (left) cannot shadow physical table; recursive term (right) may reference alias
                    if cte_body.left is not None:
                        _walk_scoped(cte_body.left, frozenset(child_scope), referenced_tables, protected_tables)
                    rec_scope = frozenset(child_scope | {alias})
                    if cte_body.right is not None:
                        _walk_scoped(cte_body.right, rec_scope, referenced_tables, protected_tables)
                else:
                    _walk_scoped(cte_body, frozenset(child_scope), referenced_tables, protected_tables)
            if alias:
                child_scope.add(alias)

    frozen_child = frozenset(child_scope)
    for key, val in node.args.items():
        if key == "with_" or val is None:
            continue
        if isinstance(val, exp.Expression):
            _walk_scoped(val, frozen_child, referenced_tables, protected_tables)
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, exp.Expression):
                    _walk_scoped(item, frozen_child, referenced_tables, protected_tables)


def _validate_ast_tree(
    tree: exp.Expression,
    protected_tables: frozenset[str] = frozenset(),
) -> set[str]:
    allowed_roots = (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery)
    if not isinstance(tree, allowed_roots):
        raise ValueError(f"Only read-only SELECT queries are allowed (got {type(tree).__name__})")

    referenced_tables: set[str] = set()
    _walk_scoped(tree, frozenset(), referenced_tables, protected_tables)
    return referenced_tables


def _normalize_placeholders_for_ast(sql: str, dialect: str) -> str:
    """Replace DB-API %s placeholders outside single-quoted string literals with '?' for sqlglot parsing."""
    if dialect not in ("mysql", "postgres"):
        return sql
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
        if not in_quote and ch == "%" and i + 1 < n and sql[i + 1] == "s":
            out.append("?")
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def validate_readonly_sql_ast(
    sql: str,
    *,
    allowed_tables: Optional[Iterable[str]] = None,
    protected_tables: Optional[Iterable[str]] = None,
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

    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].strip()
    if not cleaned:
        raise ValueError("Empty SQL query")

    parse_input = _normalize_placeholders_for_ast(cleaned, dialect)
    try:
        parsed_list = sqlglot.parse(parse_input, read=dialect)
    except Exception as exc:
        raise ValueError(f"Invalid SQL syntax: {exc}") from exc

    statements = [s for s in parsed_list if s is not None]
    if len(statements) != 1:
        raise ValueError("Only a single SELECT statement is allowed")

    protected_set = frozenset(
        t.strip().lower()
        for t in list(allowed_tables or []) + list(protected_tables or [])
        if t and t.strip()
    )
    tree = statements[0]
    referenced = _validate_ast_tree(tree, protected_tables=protected_set)

    stripped_literals = re.sub(r"'(?:''|[^'])*'", "''", cleaned)
    if FORBIDDEN_RAW_PATTERNS.search(stripped_literals):
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


def extract_ast_column_references(sql: str, dialect: str = "duckdb") -> list[tuple[Optional[str], str]]:
    """Return list of (table_qualifier_or_none, column_name) referenced in SQL AST."""
    cleaned = (sql or "").strip().rstrip(";")
    if not cleaned:
        return []
    tree = sqlglot.parse_one(cleaned, read=dialect)
    out: list[tuple[Optional[str], str]] = []
    for col in tree.find_all(exp.Column):
        cname = (col.name or "").strip()
        tbl = (col.table or "").strip() or None
        if cname:
            out.append((tbl, cname))
    return out


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
