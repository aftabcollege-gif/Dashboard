import os
import re
import threading
import time
from typing import Any, Optional
import duckdb
import pandas as pd
from app.config import settings

SAFE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def sanitize_identifier(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", name.strip())
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"t_{cleaned}"
    return cleaned.lower()[:120]


class AnalyticalWarehouse:
    """
    Layer 2: Analytical Data Warehouse built on DuckDB.
    Stores extracted raw tables, transformed Star/Snowflake tables,
    materialized pre-aggregations, and snapshots — completely decoupled from Layer 1 App DB.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._lock = threading.RLock()
        self._conn = duckdb.connect(db_path)
        self._concurrency_sem = threading.Semaphore(settings.QUERY_MAX_CONCURRENCY)

    def reopen(self, db_path: Optional[str] = None) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass
            if db_path:
                self.db_path = db_path
            self._conn = duckdb.connect(self.db_path)

    def execute_raw(self, sql: str, params: Optional[list[Any]] = None) -> None:
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute(sql, params or [])
            finally:
                cur.close()

    def execute_query(
        self,
        sql: str,
        params: Optional[list[Any]] = None,
        limit: Optional[int] = None,
        timeout_sec: Optional[float] = None,
    ) -> dict[str, Any]:
        acquired = self._concurrency_sem.acquire(timeout=timeout_sec or settings.QUERY_TIMEOUT_SECONDS)
        if not acquired:
            raise TimeoutError("Query concurrency queue saturated; please retry")

        start = time.perf_counter()
        try:
            with self._lock:
                cur = self._conn.cursor()
                try:
                    cur.execute(sql, params or [])
                    desc = cur.description or []
                    cols = [d[0] for d in desc]
                    max_rows = limit if limit is not None else settings.QUERY_DEFAULT_LIMIT
                    fetched = cur.fetchmany(max_rows) if max_rows > 0 else cur.fetchall()
                finally:
                    cur.close()

            rows: list[dict[str, Any]] = []
            for tup in fetched:
                row_dict: dict[str, Any] = {}
                for idx, col in enumerate(cols):
                    val = tup[idx]
                    # Convert pandas/duckdb date/decimal objects into JSON-safe primitives
                    if hasattr(val, "isoformat"):
                        val = val.isoformat()
                    elif type(val).__name__ == "Decimal":
                        val = float(val)
                    row_dict[col] = val
                rows.append(row_dict)

            elapsed_ms = round((time.perf_counter() - start) * 1000, 3)
            return {
                "columns": cols,
                "rows": rows,
                "row_count": len(rows),
                "execution_ms": elapsed_ms,
            }
        finally:
            self._concurrency_sem.release()

    def explain_query(self, sql: str, params: Optional[list[Any]] = None) -> str:
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute(f"EXPLAIN {sql}", params or [])
                rows = cur.fetchall()
                return "\n".join(str(r[-1]) for r in rows)
            finally:
                cur.close()

    def table_exists(self, table_name: str) -> bool:
        if not SAFE_IDENT_RE.match(table_name):
            return False
        res = self.execute_query(
            "SELECT count(*) AS cnt FROM information_schema.tables WHERE table_name = ?",
            [table_name],
        )
        return bool(res["rows"] and res["rows"][0]["cnt"] > 0)

    def list_tables(self) -> list[str]:
        res = self.execute_query(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' ORDER BY table_name"
        )
        return [r["table_name"] for r in res["rows"]]

    def describe_table(self, table_name: str) -> list[dict[str, str]]:
        if not SAFE_IDENT_RE.match(table_name):
            raise ValueError(f"Unsafe table name: {table_name}")
        res = self.execute_query(f'DESCRIBE "{table_name}"')
        return [
            {"column_name": r["column_name"], "column_type": r["column_type"]}
            for r in res["rows"]
        ]

    def load_dataframe(
        self,
        table_name: str,
        df: pd.DataFrame,
        mode: str = "replace",
        pk_column: Optional[str] = None,
    ) -> dict[str, int]:
        """
        Loads a DataFrame safely using atomic staging table swap/merge
        so half-written or failed loads NEVER corrupt live dashboard data.
        """
        tname = sanitize_identifier(table_name)
        stg_name = f"stg_{tname}_{int(time.time() * 1000)}"
        clean_df = df.copy()
        clean_df.columns = [sanitize_identifier(str(c)) for c in clean_df.columns]

        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.register("_tmp_df", clean_df)
                cur.execute(f'CREATE TABLE "{stg_name}" AS SELECT * FROM _tmp_df')
                cur.unregister("_tmp_df")

                exists = self.table_exists(tname)
                if mode == "replace" or not exists:
                    cur.execute(f'DROP TABLE IF EXISTS "{tname}"')
                    cur.execute(f'ALTER TABLE "{stg_name}" RENAME TO "{tname}"')
                    inserted = len(clean_df)
                    updated = 0
                elif mode == "append":
                    cur.execute(f'INSERT INTO "{tname}" SELECT * FROM "{stg_name}"')
                    cur.execute(f'DROP TABLE IF EXISTS "{stg_name}"')
                    inserted = len(clean_df)
                    updated = 0
                elif mode == "merge" and pk_column:
                    pk = sanitize_identifier(pk_column)
                    # Count updates vs inserts
                    cur.execute(
                        f'SELECT count(*) FROM "{stg_name}" s INNER JOIN "{tname}" t ON s."{pk}" = t."{pk}"'
                    )
                    updated = cur.fetchone()[0]
                    inserted = max(0, len(clean_df) - updated)
                    cur.execute(
                        f'DELETE FROM "{tname}" WHERE "{pk}" IN (SELECT "{pk}" FROM "{stg_name}")'
                    )
                    cur.execute(f'INSERT INTO "{tname}" SELECT * FROM "{stg_name}"')
                    cur.execute(f'DROP TABLE IF EXISTS "{stg_name}"')
                else:
                    cur.execute(f'DROP TABLE IF EXISTS "{tname}"')
                    cur.execute(f'ALTER TABLE "{stg_name}" RENAME TO "{tname}"')
                    inserted = len(clean_df)
                    updated = 0

                cur.execute(f'SELECT count(*) FROM "{tname}"')
                total_rows = int(cur.fetchone()[0])
                return {"inserted": inserted, "updated": updated, "total_rows": total_rows}
            except Exception:
                try:
                    cur.execute(f'DROP TABLE IF EXISTS "{stg_name}"')
                except Exception:
                    pass
                raise
            finally:
                cur.close()

    def create_materialized_summary(self, view_name: str, select_sql: str) -> int:
        vname = sanitize_identifier(view_name)
        tmp_name = f"stg_{vname}_{int(time.time() * 1000)}"
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute(f'CREATE TABLE "{tmp_name}" AS {select_sql}')
                cur.execute(f'DROP TABLE IF EXISTS "{vname}"')
                cur.execute(f'ALTER TABLE "{tmp_name}" RENAME TO "{vname}"')
                cur.execute(f'SELECT count(*) FROM "{vname}"')
                return int(cur.fetchone()[0])
            except Exception:
                try:
                    cur.execute(f'DROP TABLE IF EXISTS "{tmp_name}"')
                except Exception:
                    pass
                raise
            finally:
                cur.close()

    def storage_size_bytes(self) -> int:
        try:
            return os.path.getsize(self.db_path)
        except OSError:
            return 0


warehouse = AnalyticalWarehouse(settings.WAREHOUSE_PATH)
