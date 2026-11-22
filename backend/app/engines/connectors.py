import io
import ipaddress
import json
import os
import re
import socket
import sqlite3
import zipfile
from typing import Any, Iterable, Optional
from urllib.parse import urlparse
import defusedxml.ElementTree as SafeET
import httpx
import pandas as pd
from app.config import settings
from app.core.sql_guard import validate_readonly_sql_ast
from app.engines.warehouse import sanitize_identifier

SUPPORTED_DB_TYPES = [
    "postgresql",
    "mssql",
    "mysql",
    "mariadb",
    "oracle",
    "sqlite",
    "mongodb",
    "rest_api",
    "graphql",
]

SUPPORTED_FILE_TYPES = ["excel", "csv", "json", "xml"]


def infer_bi_type(dtype_str: str) -> str:
    s = dtype_str.lower()
    if "bool" in s:
        return "boolean"
    if "int" in s:
        return "integer"
    if "float" in s or "double" in s or "decimal" in s or "numeric" in s:
        return "float"
    if "datetime" in s or "timestamp" in s:
        return "datetime"
    if "date" in s:
        return "date"
    return "string"


def infer_semantic_role(col_name: str, bi_type: str, display_name: str = "") -> str:
    low = f"{col_name} {display_name}".lower()
    if bi_type in ("date", "datetime") or any(
        k in low for k in ("date", "time", "year", "month", "quarter", "تاریخ", "سال", "ماه", "فصل")
    ):
        return "time"
    if any(
        k in low for k in ("country", "province", "state", "city", "region", "lat", "lon", "geo", "کشور", "استان", "شهر", "منطقه")
    ):
        return "geo"
    if col_name.lower() == "id" or col_name.lower().endswith("_id"):
        return "id"
    if bi_type in ("integer", "float") and not any(k in low for k in ("year", "month", "day", "code", "zip", "کد", "سال")):
        return "measure"
    return "dimension"


def validate_readonly_sql(
    sql: str,
    *,
    allowed_tables: Optional[Iterable[str]] = None,
    dialect: str = "duckdb",
    require_table_allowlist: bool = False,
) -> set[str]:
    return validate_readonly_sql_ast(
        sql,
        allowed_tables=allowed_tables,
        dialect=dialect,
        require_table_allowlist=require_table_allowlist,
    )


def _validate_external_http_url(url: str) -> None:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Only HTTP/HTTPS URLs are allowed")
    host = parsed.hostname.strip().lower()
    # Always block link-local cloud metadata endpoints
    if host in ("169.254.169.254", "metadata.google.internal"):
        raise ValueError("Access to cloud metadata endpoint is forbidden")
    try:
        ip_obj = ipaddress.ip_address(host)
        if ip_obj.is_link_local or ip_obj.is_multicast:
            raise ValueError("Unsafe IP target is forbidden")
        if settings.ENVIRONMENT == "production" and (ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_reserved):
            raise ValueError("Private/loopback SSRF targets are forbidden in production")
    except ValueError as exc:
        if "forbidden" in str(exc):
            raise
        # Hostname: resolve in production to block SSRF to internal metadata/loopback
        if settings.ENVIRONMENT == "production":
            try:
                for info in socket.getaddrinfo(host, None):
                    addr = ipaddress.ip_address(info[4][0])
                    if addr.is_link_local or addr.is_private or addr.is_loopback or addr.is_reserved:
                        raise ValueError("Resolved target IP is forbidden in production")
            except ValueError:
                raise
            except Exception:
                pass


def _guard_xlsx_zip_bomb(file_bytes: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes), "r") as zf:
            infos = zf.infolist()
            if len(infos) > 500:
                raise ValueError("Excel archive contains excessive entries (possible zip bomb)")
            total_uncompressed = 0
            for info in infos:
                total_uncompressed += int(info.file_size or 0)
                if total_uncompressed > settings.MAX_ZIP_UNCOMPRESSED_BYTES:
                    raise ValueError("Excel uncompressed size exceeds safety limit (possible zip bomb)")
                if info.compress_size > 0 and (info.file_size / info.compress_size) > 120 and info.file_size > 5 * 1024 * 1024:
                    raise ValueError("Suspicious Excel compression ratio detected (possible zip bomb)")
    except zipfile.BadZipFile as exc:
        raise ValueError("Invalid or corrupted XLSX file") from exc


def compute_dataframe_quality(df: pd.DataFrame) -> dict[str, Any]:
    """Computes Data Quality metrics (completeness, uniqueness, column consistency, score 0..100)."""
    total_rows = len(df)
    total_cols = len(df.columns)
    if total_rows == 0 or total_cols == 0:
        return {
            "dq_score": 100.0 if total_cols > 0 else 0.0,
            "completeness_pct": 100.0 if total_cols > 0 else 0.0,
            "uniqueness_pct": 100.0,
            "duplicate_rows": 0,
            "total_null_cells": 0,
            "total_cells": 0,
        }
    total_cells = total_rows * total_cols
    total_nulls = int(df.isna().sum().sum())
    dup_rows = int(df.duplicated().sum())
    completeness = max(0.0, (1.0 - (total_nulls / total_cells)) * 100.0)
    uniqueness = max(0.0, (1.0 - (dup_rows / total_rows)) * 100.0)
    dq_score = round((0.7 * completeness) + (0.3 * uniqueness), 2)
    return {
        "dq_score": dq_score,
        "completeness_pct": round(completeness, 2),
        "uniqueness_pct": round(uniqueness, 2),
        "duplicate_rows": dup_rows,
        "total_null_cells": total_nulls,
        "total_cells": total_cells,
    }


def parse_file_to_preview(
    file_bytes: bytes,
    filename: str,
    sheet_name: Optional[str] = None,
    has_header: bool = True,
    null_strategy: str = "keep",
    deduplicate: bool = False,
    column_renames: Optional[dict[str, str]] = None,
    type_overrides: Optional[dict[str, str]] = None,
    preview_limit: int = 100,
) -> dict[str, Any]:
    if len(file_bytes) > settings.MAX_UPLOAD_BYTES:
        raise ValueError(
            f"File exceeds maximum allowed size ({settings.MAX_UPLOAD_BYTES // (1024 * 1024)} MB)"
        )

    ext = os.path.splitext(filename.lower())[1]
    sheets: list[str] = []

    if ext in (".xlsx", ".xls"):
        if ext == ".xlsx":
            _guard_xlsx_zip_bomb(file_bytes)
        engine = "openpyxl" if ext == ".xlsx" else "xlrd"
        xl = pd.ExcelFile(io.BytesIO(file_bytes), engine=engine)
        sheets = list(xl.sheet_names)
        if not sheets:
            raise ValueError("Workbook contains no sheets")
        chosen_sheet = sheet_name if sheet_name in sheets else sheets[0]
        df = pd.read_excel(
            io.BytesIO(file_bytes),
            sheet_name=chosen_sheet,
            header=0 if has_header else None,
            engine=engine,
        )
        if not has_header:
            df.columns = [f"col_{i+1}" for i in range(len(df.columns))]
    elif ext in (".csv", ".tsv"):
        df = pd.read_csv(io.BytesIO(file_bytes), sep="\t" if ext == ".tsv" else ",", header=0 if has_header else None)
        if not has_header:
            df.columns = [f"col_{i+1}" for i in range(len(df.columns))]
    elif ext == ".json":
        payload = json.loads(file_bytes.decode("utf-8"))
        if isinstance(payload, dict):
            for v in payload.values():
                if isinstance(v, list):
                    payload = v
                    break
        df = pd.json_normalize(payload)
    elif ext == ".xml":
        raw_text = file_bytes.decode("utf-8")
        if re.search(r"<!\s*(DOCTYPE|ENTITY)", raw_text, re.IGNORECASE):
            raise ValueError("XML DOCTYPE/ENTITY declarations are forbidden for security")
        root = SafeET.fromstring(raw_text)
        records = []
        for child in root:
            rec = {}
            for elem in child:
                rec[elem.tag] = elem.text
            if not rec and child.attrib:
                rec = dict(child.attrib)
            if rec:
                records.append(rec)
        df = pd.DataFrame(records)
    else:
        raise ValueError(f"Unsupported file format: {ext}")

    if len(df.columns) > settings.MAX_UPLOAD_COLS:
        raise ValueError(f"Column count ({len(df.columns)}) exceeds limit ({settings.MAX_UPLOAD_COLS})")
    if len(df) > settings.MAX_UPLOAD_ROWS:
        raise ValueError(f"Row count ({len(df)}) exceeds limit ({settings.MAX_UPLOAD_ROWS})")

    raw_headers = [str(c).strip() or f"col_{idx + 1}" for idx, c in enumerate(df.columns)]
    safe_cols: list[str] = []
    display_name_map: dict[str, str] = {}
    used_cols: set[str] = set()
    for idx, raw_hdr in enumerate(raw_headers):
        cand = re.sub(r"_+", "_", sanitize_identifier(raw_hdr)).strip("_")
        if not cand or not re.search(r"[a-z0-9]", cand):
            cand = f"col_{idx + 1}"
        if cand[0].isdigit():
            cand = f"c_{cand}"
        base_cand = cand
        suffix = 2
        while cand in used_cols:
            cand = f"{base_cand}_{suffix}"
            suffix += 1
        used_cols.add(cand)
        safe_cols.append(cand)
        has_non_ascii = any(ord(ch) > 127 for ch in raw_hdr)
        display_name_map[cand] = raw_hdr if has_non_ascii else raw_hdr.replace("_", " ").title()

    df.columns = safe_cols

    if column_renames:
        rename_map = {}
        for k, v in column_renames.items():
            if not k or not v:
                continue
            sk = re.sub(r"_+", "_", sanitize_identifier(k)).strip("_")
            sv = re.sub(r"_+", "_", sanitize_identifier(v)).strip("_") or sk
            if sk in df.columns:
                rename_map[sk] = sv
                display_name_map[sv] = v
        if rename_map:
            df = df.rename(columns=rename_map)

    duplicate_count = int(df.duplicated().sum())
    if deduplicate and duplicate_count > 0:
        df = df.drop_duplicates().reset_index(drop=True)

    persian_digits = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    for col in df.columns:
        if pd.api.types.is_string_dtype(df[col]) or pd.api.types.is_object_dtype(df[col]):
            sample = df[col].dropna().astype(str).head(30)
            if len(sample) > 0 and sample.str.match(r"^\d{4}-\d{2}-\d{2}").all():
                try:
                    df[col] = pd.to_datetime(df[col], errors="coerce")
                except Exception:
                    pass
                continue
            if len(sample) > 0:
                norm_sample = sample.str.translate(persian_digits).str.replace(r"[,،\s]", "", regex=True).str.strip()
                if norm_sample.str.match(r"^-?\d+(\.\d+)?$").all():
                    converted = pd.to_numeric(
                        df[col].astype(str).str.translate(persian_digits).str.replace(r"[,،\s]", "", regex=True),
                        errors="coerce",
                    )
                    if not converted.dropna().empty:
                        df[col] = converted

    if type_overrides:
        for col, target_type in type_overrides.items():
            scol = sanitize_identifier(col)
            if scol not in df.columns:
                continue
            try:
                if target_type == "integer":
                    df[scol] = pd.to_numeric(df[scol], errors="coerce").fillna(0).astype(int)
                elif target_type == "float":
                    df[scol] = pd.to_numeric(df[scol], errors="coerce").astype(float)
                elif target_type in ("date", "datetime"):
                    df[scol] = pd.to_datetime(df[scol], errors="coerce")
                elif target_type == "boolean":
                    df[scol] = df[scol].astype(bool)
                elif target_type == "string":
                    df[scol] = df[scol].astype(str)
            except Exception:
                pass

    null_counts = {str(c): int(df[c].isna().sum()) for c in df.columns}
    if null_strategy == "drop_rows":
        df = df.dropna().reset_index(drop=True)
    elif null_strategy == "fill_zero":
        for c in df.columns:
            if pd.api.types.is_numeric_dtype(df[c]):
                df[c] = df[c].fillna(0)
            else:
                df[c] = df[c].fillna("Unknown")
    elif null_strategy == "fill_mean":
        for c in df.columns:
            if pd.api.types.is_numeric_dtype(df[c]):
                df[c] = df[c].fillna(df[c].mean() if not df[c].dropna().empty else 0)
            else:
                df[c] = df[c].fillna("Unknown")

    dq = compute_dataframe_quality(df)

    columns_meta = []
    for col in df.columns:
        bi_t = infer_bi_type(str(df[col].dtype))
        disp = display_name_map.get(str(col), str(col).replace("_", " ").title())
        columns_meta.append(
            {
                "field_name": str(col),
                "display_name": disp,
                "data_type": bi_t,
                "semantic_role": infer_semantic_role(str(col), bi_t, disp),
                "null_count": null_counts.get(str(col), 0),
            }
        )

    preview_df = df.head(preview_limit).copy()
    for c in preview_df.columns:
        if pd.api.types.is_datetime64_any_dtype(preview_df[c]):
            preview_df[c] = preview_df[c].dt.strftime("%Y-%m-%d").fillna("")
    preview_rows = json.loads(preview_df.to_json(orient="records", date_format="iso"))

    return {
        "dataframe": df,
        "sheets": sheets,
        "total_rows": len(df),
        "duplicate_count": duplicate_count,
        "null_counts": null_counts,
        "data_quality": dq,
        "columns": columns_meta,
        "preview_rows": preview_rows,
    }


class DataConnectorEngine:
    """Real multi-protocol Data Connectivity Engine."""

    @staticmethod
    def _validate_sqlite_path(db_path: Optional[str]) -> str:
        if not db_path:
            raise ValueError("SQLite file path is required")
        resolved = os.path.realpath(db_path)
        if not os.path.exists(resolved):
            raise ConnectionError(f"SQLite file not found: {db_path}")
        if settings.ENVIRONMENT == "production":
            allowed_roots = [os.path.realpath(settings.UPLOADS_DIR), os.path.realpath(os.path.dirname(settings.SAMPLE_ERP_DB_PATH))]
            if not any(resolved.startswith(r + os.sep) or resolved == r for r in allowed_roots):
                raise PermissionError("SQLite path outside permitted data directory in production")
        return resolved

    @staticmethod
    def test_connection(
        source_type: str, config: dict[str, Any], credentials: dict[str, Any]
    ) -> dict[str, Any]:
        st = source_type.lower()
        try:
            if st == "sqlite":
                db_path = DataConnectorEngine._validate_sqlite_path(
                    config.get("file_path") or config.get("database")
                )
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
                conn.execute("SELECT 1").fetchone()
                conn.close()
                return {"ok": True, "message": "SQLite connection verified"}

            if st == "postgresql":
                import psycopg2
                conn = psycopg2.connect(
                    host=config.get("host", "localhost"),
                    port=int(config.get("port", 5432)),
                    dbname=config.get("database", "postgres"),
                    user=credentials.get("username", ""),
                    password=credentials.get("password", ""),
                    connect_timeout=5,
                )
                conn.close()
                return {"ok": True, "message": "PostgreSQL connection verified"}

            if st in ("mysql", "mariadb"):
                import pymysql
                conn = pymysql.connect(
                    host=config.get("host", "localhost"),
                    port=int(config.get("port", 3306)),
                    database=config.get("database", ""),
                    user=credentials.get("username", ""),
                    password=credentials.get("password", ""),
                    connect_timeout=5,
                )
                conn.close()
                return {"ok": True, "message": f"{st.upper()} connection verified"}

            if st == "mongodb":
                from pymongo import MongoClient
                uri = credentials.get("connection_uri") or config.get("uri") or (
                    f"mongodb://{config.get('host', 'localhost')}:{config.get('port', 27017)}"
                )
                client = MongoClient(uri, serverSelectionTimeoutMS=4000)
                client.admin.command("ping")
                client.close()
                return {"ok": True, "message": "MongoDB connection verified"}

            if st in ("mssql", "oracle"):
                host = config.get("host", "")
                port = int(config.get("port", 1433 if st == "mssql" else 1521))
                with socket.create_connection((host, port), timeout=4):
                    return {"ok": True, "message": f"{st.upper()} TCP endpoint reachable"}

            if st in ("rest_api", "graphql"):
                url = config.get("url") or config.get("base_url")
                if not url:
                    return {"ok": False, "error": "URL is required"}
                _validate_external_http_url(url)
                headers = dict(config.get("headers", {}))
                if credentials.get("bearer_token"):
                    headers["Authorization"] = f"Bearer {credentials['bearer_token']}"
                with httpx.Client(timeout=6.0) as client:
                    if st == "graphql":
                        resp = client.post(url, json={"query": "{ __typename }"}, headers=headers)
                    else:
                        resp = client.get(url, headers=headers)
                    return {
                        "ok": resp.status_code < 500,
                        "status_code": resp.status_code,
                        "message": f"HTTP {resp.status_code}",
                    }

            if st in SUPPORTED_FILE_TYPES:
                return {"ok": True, "message": f"File source ({st}) ready"}

            return {"ok": False, "error": f"Unsupported source type: {source_type}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @staticmethod
    def list_tables_and_columns(
        source_type: str, config: dict[str, Any], credentials: dict[str, Any]
    ) -> list[dict[str, Any]]:
        st = source_type.lower()
        if st == "sqlite":
            db_path = DataConnectorEngine._validate_sqlite_path(
                config.get("file_path") or config.get("database")
            )
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
            try:
                objs = conn.execute(
                    "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
                result = []
                for name, obj_type in objs:
                    safe_name = sanitize_identifier(name)
                    cols_raw = conn.execute(f'PRAGMA table_info("{name}")').fetchall()
                    cols = [
                        {
                            "name": c[1],
                            "type": infer_bi_type(c[2] or "text"),
                            "raw_type": c[2] or "TEXT",
                            "primary_key": bool(c[5]),
                        }
                        for c in cols_raw
                    ]
                    result.append({"table_name": name, "safe_name": safe_name, "object_type": obj_type, "columns": cols})
                return result
            finally:
                conn.close()

        if st == "postgresql":
            import psycopg2
            conn = psycopg2.connect(
                host=config.get("host", "localhost"),
                port=int(config.get("port", 5432)),
                dbname=config.get("database", "postgres"),
                user=credentials.get("username", ""),
                password=credentials.get("password", ""),
                connect_timeout=5,
            )
            try:
                cur = conn.cursor()
                schema = config.get("schema", "public")
                cur.execute(
                    "SELECT table_name, table_type FROM information_schema.tables WHERE table_schema = %s ORDER BY table_name",
                    (schema,),
                )
                tables = cur.fetchall()
                res = []
                for tname, ttype in tables:
                    cur.execute(
                        "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
                        (schema, tname),
                    )
                    cols = [
                        {"name": r[0], "type": infer_bi_type(r[1]), "raw_type": r[1], "primary_key": False}
                        for r in cur.fetchall()
                    ]
                    res.append({"table_name": tname, "object_type": ttype.lower(), "columns": cols})
                return res
            finally:
                conn.close()

        if st in ("mysql", "mariadb"):
            import pymysql
            conn = pymysql.connect(
                host=config.get("host", "localhost"),
                port=int(config.get("port", 3306)),
                database=config.get("database", ""),
                user=credentials.get("username", ""),
                password=credentials.get("password", ""),
                connect_timeout=5,
            )
            try:
                cur = conn.cursor()
                cur.execute("SHOW FULL TABLES")
                res = []
                for tname, ttype in cur.fetchall():
                    if not re.match(r"^[A-Za-z0-9_]+$", str(tname)):
                        continue
                    cur.execute(f"DESCRIBE `{tname}`")
                    cols = [
                        {
                            "name": r[0],
                            "type": infer_bi_type(r[1]),
                            "raw_type": r[1],
                            "primary_key": r[3] == "PRI",
                        }
                        for r in cur.fetchall()
                    ]
                    res.append({"table_name": tname, "object_type": ttype.lower(), "columns": cols})
                return res
            finally:
                conn.close()

        return []

    @staticmethod
    def extract_dataframe(
        source_type: str,
        config: dict[str, Any],
        credentials: dict[str, Any],
        query: Optional[str] = None,
        table_name: Optional[str] = None,
        read_only: bool = True,
        query_params: Optional[list[Any]] = None,
    ) -> pd.DataFrame:
        st = source_type.lower()
        safe_table = sanitize_identifier(table_name) if table_name else None
        sql = query or (f'SELECT * FROM "{safe_table}"' if safe_table else None)
        if read_only and sql:
            dialect = "sqlite" if st == "sqlite" else "postgres" if st == "postgresql" else "mysql" if st in ("mysql", "mariadb") else "duckdb"
            validate_readonly_sql(sql, dialect=dialect)

        if st == "sqlite":
            db_path = DataConnectorEngine._validate_sqlite_path(
                config.get("file_path") or config.get("database")
            )
            uri_mode = f"file:{db_path}?mode=ro" if read_only else f"file:{db_path}"
            conn = sqlite3.connect(uri_mode, uri=True, timeout=5)
            try:
                return pd.read_sql_query(sql, conn, params=query_params)
            finally:
                conn.close()

        if st == "postgresql":
            import psycopg2
            conn = psycopg2.connect(
                host=config.get("host", "localhost"),
                port=int(config.get("port", 5432)),
                dbname=config.get("database", "postgres"),
                user=credentials.get("username", ""),
                password=credentials.get("password", ""),
                connect_timeout=5,
            )
            try:
                return pd.read_sql_query(sql, conn, params=query_params)
            finally:
                conn.close()

        if st in ("mysql", "mariadb"):
            import pymysql
            conn = pymysql.connect(
                host=config.get("host", "localhost"),
                port=int(config.get("port", 3306)),
                database=config.get("database", ""),
                user=credentials.get("username", ""),
                password=credentials.get("password", ""),
                connect_timeout=5,
            )
            try:
                return pd.read_sql_query(sql, conn, params=query_params)
            finally:
                conn.close()

        if st == "mongodb":
            from pymongo import MongoClient
            uri = credentials.get("connection_uri") or config.get("uri")
            client = MongoClient(uri, serverSelectionTimeoutMS=4000)
            try:
                db = client[config.get("database", "test")]
                coll = db[table_name or config.get("collection", "items")]
                docs = list(coll.find({}, {"_id": 0}).limit(50000))
                return pd.json_normalize(docs)
            finally:
                client.close()

        if st in ("rest_api", "graphql"):
            url = config.get("url")
            if not url:
                raise ValueError("URL is required")
            _validate_external_http_url(url)
            headers = dict(config.get("headers", {}))
            if credentials.get("bearer_token"):
                headers["Authorization"] = f"Bearer {credentials['bearer_token']}"
            with httpx.Client(timeout=15.0) as client:
                if st == "graphql":
                    resp = client.post(url, json={"query": query or config.get("query", "")}, headers=headers)
                else:
                    resp = client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                data_path = config.get("data_path")
                if data_path and isinstance(data, dict):
                    for part in data_path.split("."):
                        data = data.get(part, [])
                if isinstance(data, dict):
                    for v in data.values():
                        if isinstance(v, list):
                            data = v
                            break
                return pd.json_normalize(data if isinstance(data, list) else [data])

        raise ValueError(f"Cannot extract live rows from source type: {source_type}")
