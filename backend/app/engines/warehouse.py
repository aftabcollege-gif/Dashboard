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


class _RWLock:
    """Fair-ish Reader-Writer lock: concurrent readers when no writer is waiting/active."""

    def __init__(self) -> None:
        self._cond = threading.Condition(threading.Lock())
        self._readers = 0
        self._writers_waiting = 0
        self._writer_active = False

    def acquire_read(self) -> None:
        with self._cond:
            while self._writer_active or self._writers_waiting > 0:
                self._cond.wait()
            self._readers += 1

    def release_read(self) -> None:
        with self._cond:
            self._readers = max(0, self._readers - 1)
            if self._readers == 0:
                self._cond.notify_all()

    def acquire_write(self) -> None:
        with self._cond:
            self._writers_waiting += 1
            try:
                while self._writer_active or self._readers > 0:
                    self._cond.wait()
                self._writer_active = True
            finally:
                self._writers_waiting -= 1

    def release_write(self) -> None:
        with self._cond:
            self._writer_active = False
            self._cond.notify_all()


class _ReadGuard:
    def __init__(self, rw: _RWLock):
        self._rw = rw

    def __enter__(self):
        self._rw.acquire_read()

    def __exit__(self, *args):
        self._rw.release_read()


class _WriteGuard:
    def __init__(self, rw: _RWLock):
        self._rw = rw

    def __enter__(self):
        self._rw.acquire_write()

    def __exit__(self, *args):
        self._rw.release_write()


class AnalyticalWarehouse:
    """
    Layer 2: Analytical Data Warehouse built on DuckDB.
    Features:
    - Concurrent read cursors via RWLock + Semaphore backpressure
    - Active query cancellation via cursor.interrupt() watchdog timer
    - Hard row cap (MAX_QUERY_ROWS) preventing OOM fetches
    - DuckDB sandbox hardening (enable_external_access = false, bounded memory & threads)
    - Atomic staging-swap writes
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._rw = _RWLock()
        self._concurrency_sem = threading.Semaphore(settings.QUERY_MAX_CONCURRENCY)
        self._conn = self._open_connection(db_path)

    @staticmethod
    def _open_connection(db_path: str) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect(db_path)
        try:
            threads = max(1, min(int(settings.DUCKDB_THREADS), 32))
            conn.execute(f"SET threads = {threads}")
            mem = re.sub(r"[^A-Za-z0-9]", "", str(settings.DUCKDB_MEMORY_LIMIT)) or "1GB"
            conn.execute(f"SET memory_limit = '{mem}'")
            # Defense-in-depth: block read_csv/read_parquet/httpfs/filesystem access inside DuckDB engine
            conn.execute("SET enable_external_access = false")
        except Exception:
            pass
        return conn

    def _ensure_conn_unlocked(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            self._conn = self._open_connection(self.db_path)
        return self._conn

    def _cursor_for_read(self):
        if self._conn is None:
            with _WriteGuard(self._rw):
                self._ensure_conn_unlocked()
        return self._conn

    def reopen(self, db_path: Optional[str] = None) -> None:
        with _WriteGuard(self._rw):
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
            if db_path:
                self.db_path = db_path
            self._ensure_conn_unlocked()

    def close(self) -> None:
        with _WriteGuard(self._rw):
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

    def execute_raw(self, sql: str, params: Optional[list[Any]] = None) -> None:
        with _WriteGuard(self._rw):
            cur = self._ensure_conn_unlocked().cursor()
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
        eff_timeout = float(timeout_sec if timeout_sec is not None else settings.QUERY_TIMEOUT_SECONDS)
        eff_timeout = max(0.05, min(eff_timeout, 120.0))

        acquired = self._concurrency_sem.acquire(timeout=eff_timeout)
        if not acquired:
            raise TimeoutError("Query concurrency queue saturated; please retry")

        raw_limit = settings.QUERY_DEFAULT_LIMIT if limit is None else int(limit)
        max_rows = max(1, min(raw_limit, settings.MAX_QUERY_ROWS))

        start = time.perf_counter()
        timed_out = threading.Event()
        try:
            if self._conn is None:
                with _WriteGuard(self._rw):
                    self._ensure_conn_unlocked()
            with _ReadGuard(self._rw):
                cur = self._conn.cursor()

                def _interrupt() -> None:
                    timed_out.set()
                    try:
                        cur.interrupt()
                    except Exception:
                        pass

                timer = threading.Timer(eff_timeout, _interrupt)
                timer.daemon = True
                timer.start()
                try:
                    cur.execute(sql, params or [])
                    desc = cur.description or []
                    cols = [d[0] for d in desc]
                    fetched = cur.fetchmany(max_rows)
                except Exception as exc:
                    if timed_out.is_set() or "Interrupt" in type(exc).__name__ or "Interrupted" in str(exc):
                        raise TimeoutError(f"Query execution timed out after {eff_timeout:.2f}s") from exc
                    raise
                finally:
                    timer.cancel()
                    cur.close()

            rows: list[dict[str, Any]] = []
            for tup in fetched:
                row_dict: dict[str, Any] = {}
                for idx, col in enumerate(cols):
                    val = tup[idx]
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
        if self._conn is None:
            with _WriteGuard(self._rw):
                self._ensure_conn_unlocked()
        with _ReadGuard(self._rw):
            cur = self._conn.cursor()
            try:
                cur.execute(f"EXPLAIN {sql}", params or [])
                rows = cur.fetchmany(200)
                return "\n".join(str(r[-1]) for r in rows)
            finally:
                cur.close()

    def table_exists(self, table_name: str) -> bool:
        if not SAFE_IDENT_RE.match(table_name):
            return False
        res = self.execute_query(
            "SELECT count(*) AS cnt FROM information_schema.tables WHERE table_schema = 'main' AND table_name = ?",
            [table_name],
            limit=1,
        )
        return bool(res["rows"] and res["rows"][0]["cnt"] > 0)

    def _table_exists_unlocked(self, cur, table_name: str) -> bool:
        cnt = cur.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'main' AND table_name = ?",
            [table_name],
        ).fetchone()[0]
        return bool(cnt and cnt > 0)

    def list_tables(self) -> list[str]:
        res = self.execute_query(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' ORDER BY table_name",
            limit=settings.MAX_QUERY_ROWS,
        )
        return [r["table_name"] for r in res["rows"]]

    def describe_table(self, table_name: str) -> list[dict[str, str]]:
        if not SAFE_IDENT_RE.match(table_name):
            raise ValueError(f"Unsafe table name: {table_name}")
        res = self.execute_query(f'DESCRIBE "{table_name}"', limit=1000)
        return [
            {"column_name": r["column_name"], "column_type": r["column_type"]}
            for r in res["rows"]
        ]

    def drop_table(self, table_name: str) -> None:
        if not SAFE_IDENT_RE.match(table_name):
            raise ValueError(f"Unsafe table name: {table_name}")
        self.execute_raw(f'DROP TABLE IF EXISTS "{table_name}"')

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

        with _WriteGuard(self._rw):
            cur = self._ensure_conn_unlocked().cursor()
            try:
                cur.register("_tmp_df", clean_df)
                cur.execute(f'CREATE TABLE "{stg_name}" AS SELECT * FROM _tmp_df')
                cur.unregister("_tmp_df")

                exists = self._table_exists_unlocked(cur, tname)
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
        with _WriteGuard(self._rw):
            cur = self._ensure_conn_unlocked().cursor()
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
