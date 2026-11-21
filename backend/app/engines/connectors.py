import io
import json
import os
import re
import sqlite3
import xml.etree.ElementTree as ET
from typing import Any, Optional
import httpx
import pandas as pd
import sqlparse
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


def validate_readonly_sql(sql: str) -> None:
    parsed = sqlparse.parse(sql)
    if not parsed:
        raise ValueError("Empty SQL query")
    if len(parsed) > 1:
        # Allow trailing semicolon only
        non_empty = [s for s in parsed if s.value.strip().strip(";")]
        if len(non_empty) > 1:
            raise ValueError("Multiple SQL statements are prohibited for security")

    forbidden = {
        "DROP",
        "DELETE",
        "UPDATE",
        "INSERT",
        "ALTER",
        "TRUNCATE",
        "CREATE",
        "REPLACE",
        "ATTACH",
        "DETACH",
        "COPY",
        "EXPORT",
        "PRAGMA",
        "GRANT",
        "REVOKE",
        "CALL",
        "EXEC",
    }
    for stmt in parsed:
        for tok in stmt.flatten():
            if tok.ttype in (sqlparse.tokens.Keyword, sqlparse.tokens.Keyword.DDL, sqlparse.tokens.Keyword.DML):
                if tok.value.upper() in forbidden:
                    raise ValueError(f"Disallowed SQL keyword in read-only mode: {tok.value.upper()}")


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
    ext = os.path.splitext(filename.lower())[1]
    sheets: list[str] = []

    if ext in (".xlsx", ".xls"):
        engine = "openpyxl" if ext == ".xlsx" else "xlrd"
        xl = pd.ExcelFile(io.BytesIO(file_bytes), engine=engine)
        sheets = list(xl.sheet_names)
        chosen_sheet = sheet_name if sheet_name in sheets else sheets[0]
        df = pd.read_excel(
            io.BytesIO(file_bytes),
            sheet_name=chosen_sheet,
            header=0 if has_header else None,
            engine=engine,
        )
        if not has_header:
            df.columns = [f"col_{i+1}" for i in range(len(df.columns))]
    elif ext == ".csv":
        df = pd.read_csv(io.BytesIO(file_bytes), header=0 if has_header else None)
        if not has_header:
            df.columns = [f"col_{i+1}" for i in range(len(df.columns))]
    elif ext == ".json":
        payload = json.loads(file_bytes.decode("utf-8"))
        if isinstance(payload, dict):
            # Find first list value if wrapped
            for v in payload.values():
                if isinstance(v, list):
                    payload = v
                    break
        df = pd.json_normalize(payload)
    elif ext == ".xml":
        root = ET.fromstring(file_bytes.decode("utf-8"))
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

    # Clean column names while preserving original labels (supports Persian/Unicode headers cleanly)
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
        # Preserve original Unicode/Persian label if header had non-ASCII chars, else pretty title
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

    # Detect duplicate count before dedup
    duplicate_count = int(df.duplicated().sum())
    if deduplicate and duplicate_count > 0:
        df = df.drop_duplicates().reset_index(drop=True)

    # Auto-parse date-like strings
    for col in df.columns:
        if df[col].dtype == object:
            sample = df[col].dropna().astype(str).head(20)
            if len(sample) > 0 and sample.str.match(r"^\d{4}-\d{2}-\d{2}").all():
                try:
                    df[col] = pd.to_datetime(df[col], errors="coerce")
                except Exception:
                    pass

    # Apply type overrides
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

    # Null handling
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
        "columns": columns_meta,
        "preview_rows": preview_rows,
    }


class DataConnectorEngine:
    """Real multi-protocol Data Connectivity Engine."""

    @staticmethod
    def test_connection(
        source_type: str, config: dict[str, Any], credentials: dict[str, Any]
    ) -> dict[str, Any]:
        st = source_type.lower()
        try:
            if st == "sqlite":
                db_path = config.get("file_path") or config.get("database")
                if not db_path or not os.path.exists(db_path):
                    return {"ok": False, "error": f"SQLite file not found: {db_path}"}
                conn = sqlite3.connect(db_path, timeout=5)
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
                # Verify TCP socket reachability if proprietary ODBC/InstantClient isn't installed
                import socket
                host = config.get("host", "")
                port = int(config.get("port", 1433 if st == "mssql" else 1521))
                with socket.create_connection((host, port), timeout=4):
                    return {"ok": True, "message": f"{st.upper()} TCP endpoint reachable"}

            if st in ("rest_api", "graphql"):
                url = config.get("url") or config.get("base_url")
                if not url:
                    return {"ok": False, "error": "URL is required"}
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
            db_path = config.get("file_path") or config.get("database")
            if not db_path or not os.path.exists(db_path):
                raise ConnectionError(f"SQLite source unreachable: {db_path}")
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
            try:
                objs = conn.execute(
                    "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
                result = []
                for name, obj_type in objs:
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
                    result.append({"table_name": name, "object_type": obj_type, "columns": cols})
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
    ) -> pd.DataFrame:
        st = source_type.lower()
        sql = query or (f'SELECT * FROM "{table_name}"' if table_name else None)
        if read_only and sql:
            validate_readonly_sql(sql)

        if st == "sqlite":
            db_path = config.get("file_path") or config.get("database")
            if not db_path or not os.path.exists(db_path):
                raise ConnectionError(f"Operational SQLite source offline or missing: {db_path}")
            uri_mode = f"file:{db_path}?mode=ro" if read_only else f"file:{db_path}"
            conn = sqlite3.connect(uri_mode, uri=True, timeout=5)
            try:
                return pd.read_sql_query(sql, conn)
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
                return pd.read_sql_query(sql, conn)
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
                return pd.read_sql_query(sql, conn)
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
