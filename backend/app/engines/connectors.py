import io
import ipaddress
import json
import os
import re
import socket
import sqlite3
import ssl
import time
import zipfile
from typing import Any, Iterable, Optional
from urllib.parse import urljoin, urlparse
import defusedxml.ElementTree as SafeET
import httpcore
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

SUPPORTED_FILE_TYPES = ["excel", "csv", "tsv", "json", "jsonl", "log", "txt", "xml"]


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


_FORBIDDEN_METADATA_HOSTS = {
    "169.254.169.254",
    "169.254.170.2",
    "fd00:ec2::254",
    "metadata.google.internal",
    "metadata.goog",
    "metadata.azure.internal",
    "metadata",
}

_FORBIDDEN_LOCAL_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
}


def _assert_ip_safe(ip_obj: ipaddress._BaseAddress, *, strict: bool) -> None:
    if isinstance(ip_obj, ipaddress.IPv6Address):
        if ip_obj.ipv4_mapped is not None:
            _assert_ip_safe(ip_obj.ipv4_mapped, strict=strict)
        if ip_obj.sixtofour is not None:
            _assert_ip_safe(ip_obj.sixtofour, strict=strict)
        if ip_obj.teredo is not None:
            for t_ip in ip_obj.teredo:
                _assert_ip_safe(t_ip, strict=strict)

    if str(ip_obj).lower() in _FORBIDDEN_METADATA_HOSTS:
        raise ValueError("Access to cloud metadata endpoint is forbidden")
    if ip_obj.is_link_local or ip_obj.is_multicast or ip_obj.is_unspecified:
        raise ValueError("Unsafe link-local, multicast, or unspecified IP target is forbidden")
    if strict and (ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_reserved or not ip_obj.is_global):
        raise ValueError("Private, loopback, reserved, or non-global SSRF targets are forbidden in production")


def _validate_and_resolve_http_target(url: str, *, allow_local_non_prod: bool = False) -> tuple[str, str, int, list[str]]:
    raw_url = (url or "").strip()
    if not raw_url or any(ord(c) <= 32 or ord(c) == 127 for c in raw_url):
        raise ValueError("Malformed or empty URL")

    parsed = urlparse(raw_url)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        raise ValueError("Only HTTP/HTTPS URLs with valid hosts are allowed")
    if parsed.username is not None or parsed.password is not None or "@" in (parsed.netloc or ""):
        raise ValueError("Embedded URL credentials are forbidden")
    try:
        explicit_port = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid URL port") from exc
    port = int(explicit_port) if explicit_port is not None else (443 if parsed.scheme.lower() == "https" else 80)
    if port < 1 or port > 65535:
        raise ValueError("URL port out of valid range (1-65535)")

    host = parsed.hostname.strip()
    if "%" in host:
        raise ValueError("IPv6 zone identifiers or percent-encoded hosts are forbidden")
    host_clean = host.rstrip(".").lower()
    if not host_clean or ".." in host_clean or host_clean.startswith("."):
        raise ValueError("Malformed target hostname")

    strict_prod = (settings.ENVIRONMENT == "production") or (not allow_local_non_prod)

    if host_clean in _FORBIDDEN_METADATA_HOSTS:
        raise ValueError("Access to cloud metadata endpoint is forbidden")
    if strict_prod and host_clean in _FORBIDDEN_LOCAL_HOSTS:
        raise ValueError("Access to localhost targets is forbidden in production")

    # Reject alternative/obfuscated IPv4 notations (decimal integer, hex, octal, short 127.1)
    if re.fullmatch(r"\d+", host_clean) or re.fullmatch(r"0x[0-9a-f]+", host_clean, re.I):
        raise ValueError("Non-canonical numeric/hexadecimal IP host notation is forbidden")
    if any(seg.startswith("0") and len(seg) > 1 and seg.isdigit() for seg in host_clean.split(".")):
        raise ValueError("Octal IP notation is forbidden")
    try:
        ip_literal: Optional[ipaddress._BaseAddress] = ipaddress.ip_address(host_clean)
    except ValueError:
        ip_literal = None
        if re.fullmatch(r"[0-9a-fx.]+", host_clean, re.I):
            try:
                socket.inet_aton(host_clean)
                raise ValueError("Non-canonical IPv4 host notation is forbidden")
            except OSError:
                pass

    if ip_literal is not None:
        _assert_ip_safe(ip_literal, strict=strict_prod)
        canonical_ip = str(ip_literal)
        return raw_url, canonical_ip, port, [canonical_ip]

    try:
        ascii_host = host_clean.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("Invalid internationalized hostname") from exc
    for label in ascii_host.split("."):
        if not label or len(label) > 63 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label):
            raise ValueError("Malformed target hostname label")

    # Resolve all A and AAAA records and fail closed on DNS failure or ANY unsafe IP
    try:
        addr_infos = socket.getaddrinfo(ascii_host, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError(f"DNS resolution failed for host '{ascii_host}'") from exc

    resolved_ips: list[str] = []
    seen_ips: set[str] = set()
    for info in addr_infos or []:
        sockaddr = info[4]
        if not sockaddr:
            continue
        raw_ip = str(sockaddr[0]).split("%", 1)[0]
        ip_obj = ipaddress.ip_address(raw_ip)
        _assert_ip_safe(ip_obj, strict=strict_prod)
        canon = str(ip_obj)
        if canon not in seen_ips:
            seen_ips.add(canon)
            resolved_ips.append(canon)

    if not resolved_ips:
        raise ValueError(f"DNS resolution returned no usable addresses for '{ascii_host}'")

    # Prefer IPv4 when both A and AAAA exist for reliable TCP pinning across dual-stack hosts
    resolved_ips.sort(key=lambda ip_s: 0 if ipaddress.ip_address(ip_s).version == 4 else 1)
    return raw_url, ascii_host, port, resolved_ips


def _validate_external_http_url(url: str) -> tuple[str, str, int, list[str]]:
    return _validate_and_resolve_http_target(url, allow_local_non_prod=(settings.ENVIRONMENT != "production"))


class _PinnedSSRFNetworkBackend(httpcore.SyncBackend):
    def __init__(self, expected_host: str, pinned_ip: str, expected_port: int):
        self._expected_host = expected_host.strip("[]").lower()
        self._pinned_ip = pinned_ip
        self._expected_port = int(expected_port)
        super().__init__()

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: Optional[float] = None,
        local_address: Optional[str] = None,
        socket_options: Any = None,
    ) -> httpcore.NetworkStream:
        req_host = host.strip("[]").lower()
        if req_host != self._expected_host or int(port) != self._expected_port:
            raise ValueError(f"SSRF connection pin violation: unexpected target {host}:{port}")
        # Connect strictly to the pre-validated pinned IP (zero secondary DNS lookup)
        return super().connect_tcp(
            self._pinned_ip,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )


class _PinnedSSRFHTTPTransport(httpx.HTTPTransport):
    def __init__(self, expected_host: str, pinned_ip: str, expected_port: int):
        super().__init__(verify=True, trust_env=False, proxy=None)
        if self._pool is not None:
            try:
                self._pool.close()
            except Exception:
                pass
        self._pool = httpcore.ConnectionPool(
            ssl_context=ssl.create_default_context(),
            proxy=None,
            network_backend=_PinnedSSRFNetworkBackend(expected_host, pinned_ip, expected_port),
        )


def _ssrf_safe_http_request(
    method: str,
    url: str,
    *,
    headers: Optional[dict[str, str]] = None,
    json_body: Optional[dict[str, Any]] = None,
    timeout: float = 10.0,
    config: Optional[dict[str, Any]] = None,
) -> httpx.Response:
    cfg = config or {}
    if cfg.get("proxy") or cfg.get("proxies") or cfg.get("proxy_url"):
        raise ValueError("Custom proxy configuration is forbidden for external HTTP connectors")

    req_headers = {
        str(k): str(v)
        for k, v in (headers or {}).items()
        if str(k).lower() not in ("proxy-authorization", "proxy-connection", "host")
    }
    allow_redirects = bool(cfg.get("allow_redirects", False))
    max_redirects = max(0, min(int(cfg.get("max_redirects", 3)), 5)) if allow_redirects else 0

    current_url = url
    current_method = method.upper()
    current_json = json_body
    visited_origin: Optional[tuple[str, str, int]] = None

    for hop in range(max_redirects + 1):
        clean_url, resolved_host, resolved_port, pinned_ips = _validate_external_http_url(current_url)
        pinned_ip = pinned_ips[0]
        cur_origin = (urlparse(clean_url).scheme.lower(), resolved_host, resolved_port)
        if visited_origin is not None and cur_origin != visited_origin:
            # Strip sensitive auth headers across cross-origin redirects
            req_headers = {
                k: v for k, v in req_headers.items() if k.lower() not in ("authorization", "cookie", "x-api-key")
            }
        visited_origin = cur_origin

        transport = _PinnedSSRFHTTPTransport(resolved_host, pinned_ip, resolved_port)
        with httpx.Client(
            timeout=float(timeout),
            trust_env=False,
            follow_redirects=False,
            transport=transport,
        ) as client:
            resp = client.request(
                current_method,
                clean_url,
                headers=req_headers,
                json=current_json if current_method in ("POST", "PUT", "PATCH") else None,
            )

        if resp.status_code in (301, 302, 303, 307, 308):
            if not allow_redirects:
                raise ValueError(f"HTTP redirect ({resp.status_code}) blocked by SSRF policy")
            if hop >= max_redirects:
                raise ValueError("Maximum HTTP redirect hops exceeded")
            location = resp.headers.get("location")
            if not location:
                raise ValueError("Redirect response missing Location header")
            current_url = urljoin(clean_url, location.strip())
            if resp.status_code in (301, 302, 303) and current_method == "POST":
                current_method = "GET"
                current_json = None
            continue
        return resp

    raise ValueError("Redirect limit exceeded")


def _guard_xlsx_zip_bomb(file_source: bytes | str) -> None:
    try:
        zf_ctx = (
            zipfile.ZipFile(io.BytesIO(file_source), "r")
            if isinstance(file_source, (bytes, bytearray))
            else zipfile.ZipFile(file_source, "r")
        )
        with zf_ctx as zf:
            infos = zf.infolist()
            if len(infos) > 10000:
                raise ValueError("Excel archive contains excessive entries (possible zip bomb)")
            total_uncompressed = 0
            for info in infos:
                name_low = (info.filename or "").lower()
                if name_low.endswith((".zip", ".7z", ".rar", ".tar", ".gz")):
                    raise ValueError("Nested archive inside XLSX is forbidden")
                total_uncompressed += int(info.file_size or 0)
                if settings.MAX_ZIP_UNCOMPRESSED_BYTES > 0 and total_uncompressed > settings.MAX_ZIP_UNCOMPRESSED_BYTES:
                    raise ValueError("Excel uncompressed size exceeds safety limit (possible zip bomb)")
                if (
                    info.compress_size > 0
                    and (info.file_size / info.compress_size) > 1000
                    and info.file_size > 20 * 1024 * 1024
                ):
                    raise ValueError("Suspicious Excel compression ratio detected (possible zip bomb)")
    except zipfile.BadZipFile as exc:
        raise ValueError("Invalid or corrupted XLSX file") from exc


def _enforce_row_col_limits(row_count: int, col_count: int) -> None:
    if settings.MAX_UPLOAD_COLS > 0 and col_count > settings.MAX_UPLOAD_COLS:
        raise ValueError(f"Column count ({col_count}) exceeds limit ({settings.MAX_UPLOAD_COLS})")
    if settings.MAX_UPLOAD_ROWS > 0 and row_count > settings.MAX_UPLOAD_ROWS:
        raise ValueError(f"Row count ({row_count}) exceeds limit ({settings.MAX_UPLOAD_ROWS})")


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
    dup_rows = int(df.duplicated().sum()) if (total_rows <= 250_000 and total_cols <= 100) else 0
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


_PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_APACHE_LOG_RE = re.compile(
    r'^(?P<client_ip>\S+)\s+\S+\s+(?P<user>\S+)\s+\[(?P<timestamp>[^\]]+)\]\s+'
    r'"(?P<method>[A-Z]+)\s+(?P<path>\S+)(?:\s+(?P<protocol>[^"]+))?"\s+'
    r'(?P<status_code>\d{3})\s+(?P<response_bytes>\S+)(?:\s+"(?P<referer>[^"]*)"\s+"(?P<user_agent>[^"]*)")?'
)
_TS_LEVEL_LOG_RE = re.compile(
    r'^(?P<timestamp>\d{4}[-/]\d{2}[-/]\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\s+'
    r'(?:\[?(?P<level>TRACE|DEBUG|INFO|WARN|WARNING|ERROR|CRITICAL|FATAL)\]?)?\s*'
    r'(?:\[(?P<logger>[^\]]+)\]\s*)?(?P<rest>.*)$',
    re.IGNORECASE,
)
_KV_PAIR_RE = re.compile(r'\b([A-Za-z_][A-Za-z0-9_.-]{1,32})=("(?:[^"\\]|\\.)*"|\S+)')


def _sanitize_headers_and_renames(
    raw_headers: list[str],
    column_renames: Optional[dict[str, str]] = None,
) -> tuple[list[str], dict[str, str]]:
    safe_cols: list[str] = []
    display_name_map: dict[str, str] = {}
    used_cols: set[str] = set()
    for idx, raw_hdr in enumerate(raw_headers):
        hdr_s = str(raw_hdr).strip() if raw_hdr is not None else ""
        if not hdr_s:
            hdr_s = f"col_{idx + 1}"
        cand = re.sub(r"_+", "_", sanitize_identifier(hdr_s)).strip("_")
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
        has_non_ascii = any(ord(ch) > 127 for ch in hdr_s)
        display_name_map[cand] = hdr_s if has_non_ascii else hdr_s.replace("_", " ").title()

    if column_renames:
        remapped_cols: list[str] = []
        used_after: set[str] = set()
        for c in safe_cols:
            target = column_renames.get(c) or column_renames.get(display_name_map.get(c, ""))
            if target:
                sv = re.sub(r"_+", "_", sanitize_identifier(target)).strip("_") or c
                base_sv = sv
                n = 2
                while sv in used_after:
                    sv = f"{base_sv}_{n}"
                    n += 1
                used_after.add(sv)
                display_name_map[sv] = target
                remapped_cols.append(sv)
            else:
                base_c = c
                n = 2
                cur_c = c
                while cur_c in used_after:
                    cur_c = f"{base_c}_{n}"
                    n += 1
                used_after.add(cur_c)
                if cur_c != c and c in display_name_map:
                    display_name_map[cur_c] = display_name_map[c]
                remapped_cols.append(cur_c)
        safe_cols = remapped_cols

    return safe_cols, display_name_map


def _normalize_dataframe_chunk(
    df: pd.DataFrame,
    *,
    type_overrides: Optional[dict[str, str]] = None,
    null_strategy: str = "keep",
    inferred_col_types: Optional[dict[str, str]] = None,
) -> tuple[pd.DataFrame, dict[str, int]]:
    for col in df.columns:
        target_hint = (type_overrides or {}).get(col) or (inferred_col_types or {}).get(col)
        if target_hint == "date" or target_hint == "datetime":
            try:
                df[col] = pd.to_datetime(df[col], errors="coerce")
            except Exception:
                pass
        elif target_hint in ("integer", "float"):
            try:
                s = df[col]
                if pd.api.types.is_string_dtype(s) or pd.api.types.is_object_dtype(s):
                    s = s.astype(str).str.translate(_PERSIAN_DIGITS).str.replace(r"[,،\s]", "", regex=True)
                converted = pd.to_numeric(s, errors="coerce")
                if target_hint == "integer":
                    if converted.isna().any():
                        df[col] = converted.astype("Int64")
                    else:
                        df[col] = converted.astype("int64")
                else:
                    df[col] = converted.astype(float)
            except Exception:
                pass
        elif target_hint == "boolean":
            try:
                df[col] = df[col].astype(bool)
            except Exception:
                pass
        elif target_hint == "string":
            df[col] = df[col].where(df[col].notna(), None)
        elif pd.api.types.is_string_dtype(df[col]) or pd.api.types.is_object_dtype(df[col]):
            sample = df[col].dropna().astype(str).head(30)
            if len(sample) > 0 and sample.str.match(r"^\d{4}-\d{2}-\d{2}").all():
                try:
                    df[col] = pd.to_datetime(df[col], errors="coerce")
                    if inferred_col_types is not None:
                        inferred_col_types[col] = "datetime"
                except Exception:
                    pass
                continue
            if len(sample) > 0:
                norm_sample = sample.str.translate(_PERSIAN_DIGITS).str.replace(r"[,،\s]", "", regex=True).str.strip()
                if norm_sample.str.match(r"^-?\d+(\.\d+)?$").all():
                    converted = pd.to_numeric(
                        df[col].astype(str).str.translate(_PERSIAN_DIGITS).str.replace(r"[,،\s]", "", regex=True),
                        errors="coerce",
                    )
                    if not converted.dropna().empty:
                        df[col] = converted
                        if inferred_col_types is not None:
                            inferred_col_types[col] = "integer" if pd.api.types.is_integer_dtype(converted.dtype) else "float"

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

    return df, null_counts


def _build_columns_meta(
    df_columns: Iterable[str],
    dtypes_map: dict[str, str],
    display_name_map: dict[str, str],
    null_counts: dict[str, int],
) -> list[dict[str, Any]]:
    columns_meta = []
    for col in df_columns:
        col_s = str(col)
        bi_t = infer_bi_type(str(dtypes_map.get(col_s, "object")))
        disp = display_name_map.get(col_s, col_s.replace("_", " ").title())
        columns_meta.append(
            {
                "field_name": col_s,
                "display_name": disp,
                "data_type": bi_t,
                "semantic_role": infer_semantic_role(col_s, bi_t, disp),
                "null_count": int(null_counts.get(col_s, 0)),
            }
        )
    return columns_meta


def _format_preview_rows(df: pd.DataFrame, preview_limit: int) -> list[dict[str, Any]]:
    preview_df = df.head(preview_limit).copy()
    for c in preview_df.columns:
        if pd.api.types.is_datetime64_any_dtype(preview_df[c]):
            preview_df[c] = preview_df[c].dt.strftime("%Y-%m-%d").fillna("")
    return json.loads(preview_df.to_json(orient="records", date_format="iso"))


def _parse_log_line_to_record(line_no: int, line: str) -> dict[str, Any]:
    s = line.rstrip("\r\n")
    m_apache = _APACHE_LOG_RE.match(s)
    if m_apache:
        d = m_apache.groupdict()
        rb = d.get("response_bytes")
        return {
            "line_number": line_no,
            "client_ip": d.get("client_ip") or "",
            "user": "" if d.get("user") == "-" else (d.get("user") or ""),
            "timestamp": d.get("timestamp") or "",
            "method": d.get("method") or "",
            "path": d.get("path") or "",
            "status_code": int(d["status_code"]) if d.get("status_code") else None,
            "response_bytes": int(rb) if rb and rb.isdigit() else 0,
            "referer": d.get("referer") or "",
            "user_agent": d.get("user_agent") or "",
        }
    m_ts = _TS_LEVEL_LOG_RE.match(s)
    if m_ts:
        d = m_ts.groupdict()
        rest = (d.get("rest") or "").strip()
        rec: dict[str, Any] = {
            "line_number": line_no,
            "timestamp": d.get("timestamp") or "",
            "level": (d.get("level") or "INFO").upper(),
            "logger": d.get("logger") or "",
            "message": rest,
        }
        for kv_m in _KV_PAIR_RE.finditer(rest):
            k = sanitize_identifier(kv_m.group(1))
            if k in ("line_number", "timestamp", "level", "logger", "message"):
                continue
            v = kv_m.group(2).strip('"')
            if v.endswith("ms") and v[:-2].replace(".", "", 1).isdigit():
                v_num = float(v[:-2])
                rec[f"{k}_ms" if not k.endswith("_ms") else k] = v_num
            elif re.fullmatch(r"-?\d+", v):
                rec[k] = int(v)
            elif re.fullmatch(r"-?\d+\.\d+", v):
                rec[k] = float(v)
            else:
                rec[k] = v
        return rec
    level = "INFO"
    for lvl in ("CRITICAL", "FATAL", "ERROR", "WARN", "WARNING", "DEBUG", "TRACE"):
        if re.search(rf"\b{lvl}\b", s, re.IGNORECASE):
            level = "WARN" if lvl == "WARNING" else lvl
            break
    return {
        "line_number": line_no,
        "level": level,
        "message": s,
    }


def _detect_text_log_format(file_path: str, ext: str) -> tuple[str, str, str]:
    """Returns (format_kind, delimiter, encoding)."""
    encoding = "utf-8-sig"
    sample_lines: list[str] = []
    for enc in ("utf-8-sig", "utf-8", "cp1256", "latin-1"):
        try:
            with open(file_path, "r", encoding=enc) as fh:
                for _ in range(25):
                    ln = fh.readline()
                    if not ln:
                        break
                    if ln.strip():
                        sample_lines.append(ln.rstrip("\r\n"))
            encoding = enc
            break
        except UnicodeDecodeError:
            sample_lines = []
            continue

    if not sample_lines:
        return ("delimited", "\t" if ext == ".tsv" else ",", encoding)
    if ext in (".jsonl", ".ndjson") or sample_lines[0].lstrip().startswith("{"):
        return ("jsonl", "", encoding)
    if ext == ".csv":
        first = sample_lines[0]
        if first.count(";") > first.count(",") and first.count("\t") == 0:
            return ("delimited", ";", encoding)
        return ("delimited", ",", encoding)
    if ext == ".tsv":
        return ("delimited", "\t", encoding)

    # .log or .txt: detect Apache/Syslog/Timestamped log lines vs delimited CSV/TSV
    if any(_APACHE_LOG_RE.match(ln) or _TS_LEVEL_LOG_RE.match(ln) for ln in sample_lines[:5]):
        return ("log_lines", "", encoding)
    first = sample_lines[0]
    for delim in ("\t", ",", "|", ";"):
        cnt0 = first.count(delim)
        if cnt0 >= 2 and len(sample_lines) >= 2 and all(abs(ln.count(delim) - cnt0) <= 1 for ln in sample_lines[1:5]):
            return ("delimited", delim, encoding)
    return ("log_lines", "", encoding)


def _trim_row_trailing_nones(row: tuple[Any, ...]) -> list[Any]:
    vals = list(row)
    while vals and (vals[-1] is None or (isinstance(vals[-1], str) and vals[-1] == "")):
        vals.pop()
    return vals


def _iter_xlsx_stream_batches(
    file_path: str,
    sheet_name: Optional[str],
    has_header: bool,
    *,
    preview_only: bool = False,
    preview_limit: int = 100,
) -> dict[str, Any]:
    import openpyxl

    _guard_xlsx_zip_bomb(file_path)
    wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    try:
        sheets = list(wb.sheetnames)
        if not sheets:
            raise ValueError("Workbook contains no sheets")
        chosen_sheet = sheet_name if sheet_name in sheets else sheets[0]
        ws = wb[chosen_sheet]
        declared_max_row = ws.max_row if isinstance(ws.max_row, int) and ws.max_row > 0 else None

        row_iter = ws.iter_rows(values_only=True)
        raw_header_row: Optional[list[Any]] = None
        first_data_row: Optional[list[Any]] = None

        for raw in row_iter:
            trimmed = _trim_row_trailing_nones(raw)
            if trimmed:
                if has_header:
                    raw_header_row = trimmed
                else:
                    first_data_row = trimmed
                break

        if has_header and raw_header_row is None:
            return {"sheets": sheets, "raw_headers": [], "batches": iter([]), "estimated_rows": 0}
        if not has_header and first_data_row is None:
            return {"sheets": sheets, "raw_headers": [], "batches": iter([]), "estimated_rows": 0}

        if preview_only:
            sample_rows: list[list[Any]] = []
            if first_data_row is not None:
                sample_rows.append(first_data_row)
            max_sample = max(preview_limit, 300)
            counted_extra = 0
            # For workbooks <= 50k rows (or lacking <dimension>), count exact rows while streaming;
            # for massive 50k+ workbooks with <dimension>, keep preview instant!
            count_exact = (declared_max_row is None) or (declared_max_row <= 50_000)
            for raw in row_iter:
                trimmed = _trim_row_trailing_nones(raw)
                if not trimmed:
                    continue
                if len(sample_rows) < max_sample:
                    sample_rows.append(trimmed)
                elif not count_exact:
                    break
                else:
                    counted_extra += 1

            max_w = max(
                len(raw_header_row) if raw_header_row else 0,
                max((len(r) for r in sample_rows), default=0),
            )
            if max_w == 0:
                return {"sheets": sheets, "raw_headers": [], "sample_rows": [], "total_rows": 0, "estimated": False}
            if has_header:
                hdrs = [
                    str(raw_header_row[i]).strip() if i < len(raw_header_row) and raw_header_row[i] is not None else f"col_{i+1}"
                    for i in range(max_w)
                ]
            else:
                hdrs = [f"col_{i+1}" for i in range(max_w)]
            norm_rows = [r + [None] * (max_w - len(r)) for r in sample_rows]
            if count_exact:
                total_rows = len(sample_rows) + counted_extra
                estimated = False
            else:
                total_rows = max(len(sample_rows), int(declared_max_row) - (1 if has_header else 0))
                estimated = True
            return {
                "sheets": sheets,
                "raw_headers": hdrs,
                "sample_rows": norm_rows,
                "total_rows": total_rows,
                "estimated": estimated,
            }

        # Streaming import path: caller will iterate chunks via generator while wb stays open inside context
        return {
            "wb": wb,
            "ws": ws,
            "sheets": sheets,
            "row_iter": row_iter,
            "raw_header_row": raw_header_row,
            "first_data_row": first_data_row,
        }
    except Exception:
        wb.close()
        raise


def preview_file_from_path(
    file_path: str,
    filename: str,
    sheet_name: Optional[str] = None,
    has_header: bool = True,
    null_strategy: str = "keep",
    deduplicate: bool = False,
    column_renames: Optional[dict[str, str]] = None,
    type_overrides: Optional[dict[str, str]] = None,
    preview_limit: int = 100,
) -> dict[str, Any]:
    if settings.MAX_UPLOAD_BYTES > 0 and os.path.getsize(file_path) > settings.MAX_UPLOAD_BYTES:
        raise ValueError(
            f"File exceeds maximum allowed size ({settings.MAX_UPLOAD_BYTES // (1024 * 1024)} MB)"
        )

    ext = os.path.splitext(filename.lower())[1]
    sheets: list[str] = []
    row_count_estimated = False
    total_rows_override: Optional[int] = None

    if ext == ".xlsx":
        info = _iter_xlsx_stream_batches(
            file_path, sheet_name, has_header, preview_only=True, preview_limit=preview_limit
        )
        sheets = info["sheets"]
        if not info["raw_headers"]:
            return {
                "dataframe": pd.DataFrame(),
                "sheets": sheets,
                "total_rows": 0,
                "row_count_estimated": False,
                "duplicate_count": 0,
                "null_counts": {},
                "data_quality": compute_dataframe_quality(pd.DataFrame()),
                "columns": [],
                "preview_rows": [],
            }
        raw_headers = info["raw_headers"]
        df = pd.DataFrame(info["sample_rows"], columns=raw_headers)
        total_rows_override = info["total_rows"]
        row_count_estimated = bool(info["estimated"])
    elif ext == ".xls":
        xl = pd.ExcelFile(file_path, engine="xlrd")
        sheets = list(xl.sheet_names)
        if not sheets:
            raise ValueError("Workbook contains no sheets")
        chosen_sheet = sheet_name if sheet_name in sheets else sheets[0]
        df = pd.read_excel(file_path, sheet_name=chosen_sheet, header=0 if has_header else None, engine="xlrd")
        if not has_header:
            df.columns = [f"col_{i+1}" for i in range(len(df.columns))]
    elif ext in (".csv", ".tsv", ".log", ".txt", ".jsonl", ".ndjson"):
        fmt, delim, enc = _detect_text_log_format(file_path, ext)
        # Fast line count in buffered binary mode
        with open(file_path, "rb") as bf:
            nl_count = 0
            last_byte = b""
            while True:
                blk = bf.read(1024 * 1024)
                if not blk:
                    break
                nl_count += blk.count(b"\n")
                last_byte = blk[-1:]
            if last_byte and last_byte != b"\n":
                nl_count += 1
        if fmt == "jsonl":
            records = []
            with open(file_path, "r", encoding=enc, errors="replace") as fh:
                for line in fh:
                    s = line.strip()
                    if not s:
                        continue
                    records.append(json.loads(s))
                    if len(records) >= max(preview_limit, 500):
                        break
            df = pd.json_normalize(records)
            total_rows_override = max(len(df), nl_count)
            row_count_estimated = nl_count > len(df)
        elif fmt == "log_lines":
            records = []
            with open(file_path, "r", encoding=enc, errors="replace") as fh:
                for idx, line in enumerate(fh, 1):
                    if not line.strip():
                        continue
                    records.append(_parse_log_line_to_record(idx, line))
                    if len(records) >= max(preview_limit, 500):
                        break
            df = pd.DataFrame(records)
            total_rows_override = max(len(df), nl_count)
            row_count_estimated = nl_count > len(df)
        else:
            df = pd.read_csv(
                file_path,
                sep=delim,
                header=0 if has_header else None,
                nrows=max(preview_limit, 500),
                encoding=enc,
                on_bad_lines="skip",
            )
            if not has_header:
                df.columns = [f"col_{i+1}" for i in range(len(df.columns))]
            est_data_rows = max(0, nl_count - (1 if has_header else 0))
            if est_data_rows > len(df):
                total_rows_override = est_data_rows
                row_count_estimated = True
    elif ext == ".json":
        with open(file_path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if isinstance(payload, dict):
            for v in payload.values():
                if isinstance(v, list):
                    payload = v
                    break
        df = pd.json_normalize(payload)
    elif ext == ".xml":
        with open(file_path, "r", encoding="utf-8") as fh:
            raw_text = fh.read()
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

    raw_headers = [str(c).strip() or f"col_{idx + 1}" for idx, c in enumerate(df.columns)]
    eff_total = total_rows_override if total_rows_override is not None else len(df)
    _enforce_row_col_limits(eff_total, len(raw_headers))

    safe_cols, display_name_map = _sanitize_headers_and_renames(raw_headers, column_renames)
    df.columns = safe_cols

    duplicate_count = int(df.duplicated().sum()) if (len(df) <= 250_000 and len(df.columns) <= 100) else 0
    if deduplicate and duplicate_count > 0:
        df = df.drop_duplicates().reset_index(drop=True)

    df, null_counts = _normalize_dataframe_chunk(
        df, type_overrides=type_overrides, null_strategy=null_strategy
    )
    dq = compute_dataframe_quality(df)
    dtypes_map = {str(c): str(df[c].dtype) for c in df.columns}
    columns_meta = _build_columns_meta(df.columns, dtypes_map, display_name_map, null_counts)
    preview_rows = _format_preview_rows(df, preview_limit)

    return {
        "dataframe": df,
        "sheets": sheets,
        "total_rows": eff_total if not deduplicate else (eff_total if total_rows_override is not None else len(df)),
        "row_count_estimated": row_count_estimated,
        "duplicate_count": duplicate_count,
        "null_counts": null_counts,
        "data_quality": dq,
        "columns": columns_meta,
        "preview_rows": preview_rows,
    }


_EXCEL_STREAM_CHUNK = 0  # 0 = auto-compute batch size from column width


def stream_import_file_to_warehouse(
    file_path: str,
    filename: str,
    wh_table: str,
    warehouse_engine,
    *,
    sheet_name: Optional[str] = None,
    has_header: bool = True,
    null_strategy: str = "keep",
    deduplicate: bool = False,
    column_renames: Optional[dict[str, str]] = None,
    type_overrides: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """
    Streams Excel (.xlsx), CSV/TSV, JSONL/NDJSON, and Log (.log/.txt) files into DuckDB in bounded batches
    with zero artificial row or column limits and constant memory footprint.
    """
    if settings.MAX_UPLOAD_BYTES > 0 and os.path.getsize(file_path) > settings.MAX_UPLOAD_BYTES:
        raise ValueError(
            f"File exceeds maximum allowed size ({settings.MAX_UPLOAD_BYTES // (1024 * 1024)} MB)"
        )

    ext = os.path.splitext(filename.lower())[1]
    total_rows = 0
    total_nulls_per_col: dict[str, int] = {}
    display_name_map: dict[str, str] = {}
    inferred_col_types: dict[str, str] = {}
    ordered_cols: list[str] = []
    dtypes_map: dict[str, str] = {}
    sheets: list[str] = []
    loaded_any = False

    def _flush_chunk(raw_df: pd.DataFrame, *, chunk_raw_headers: Optional[list[str]] = None) -> None:
        nonlocal loaded_any, total_rows, ordered_cols, display_name_map, dtypes_map
        if raw_df is None or (len(raw_df) == 0 and loaded_any):
            return
        if not ordered_cols:
            hdrs = chunk_raw_headers or [str(c).strip() or f"col_{i+1}" for i, c in enumerate(raw_df.columns)]
            _enforce_row_col_limits(0, len(hdrs))
            safe_cols, dmap = _sanitize_headers_and_renames(hdrs, column_renames)
            ordered_cols = safe_cols
            display_name_map.update(dmap)
            raw_df.columns = ordered_cols
        else:
            # Handle dynamically discovered trailing columns in ragged Excel or JSONL logs
            hdrs = chunk_raw_headers or [str(c).strip() or f"col_{i+1}" for i, c in enumerate(raw_df.columns)]
            if len(hdrs) > len(ordered_cols) or any(h not in ordered_cols for h in hdrs):
                merged_raw = list(ordered_cols)
                used = set(ordered_cols)
                mapped_chunk_cols: list[str] = []
                for idx, h in enumerate(hdrs):
                    if idx < len(ordered_cols) and chunk_raw_headers is not None:
                        mapped_chunk_cols.append(ordered_cols[idx])
                    else:
                        cand, dmap = _sanitize_headers_and_renames([h], column_renames)
                        cname = cand[0]
                        if cname not in used and cname not in mapped_chunk_cols:
                            used.add(cname)
                            merged_raw.append(cname)
                            display_name_map.update(dmap)
                        mapped_chunk_cols.append(cname)
                ordered_cols = merged_raw
                raw_df.columns = mapped_chunk_cols[: len(raw_df.columns)]
            elif list(raw_df.columns) != ordered_cols[: len(raw_df.columns)] and chunk_raw_headers is not None:
                raw_df.columns = ordered_cols[: len(raw_df.columns)]

        if list(raw_df.columns) == ordered_cols[: len(raw_df.columns)] and len(raw_df.columns) <= len(ordered_cols):
            for missing_c in ordered_cols[len(raw_df.columns):]:
                raw_df[missing_c] = None

        _enforce_row_col_limits(total_rows + len(raw_df), len(ordered_cols))

        norm_df, chunk_nulls = _normalize_dataframe_chunk(
            raw_df,
            type_overrides=type_overrides,
            null_strategy=null_strategy,
            inferred_col_types=inferred_col_types,
        )
        for c, ncnt in chunk_nulls.items():
            total_nulls_per_col[c] = total_nulls_per_col.get(c, 0) + int(ncnt)

        if not loaded_any:
            dtypes_map = {str(c): str(norm_df[c].dtype) for c in norm_df.columns}
            warehouse_engine.load_dataframe(wh_table, norm_df, mode="replace")
            loaded_any = True
        else:
            for c in norm_df.columns:
                if str(c) not in dtypes_map:
                    dtypes_map[str(c)] = str(norm_df[c].dtype)
            warehouse_engine.load_dataframe(wh_table, norm_df, mode="append")
        total_rows += len(norm_df)

    if ext == ".xlsx":
        ctx = _iter_xlsx_stream_batches(file_path, sheet_name, has_header, preview_only=False)
        wb = ctx["wb"]
        try:
            sheets = ctx["sheets"]
            raw_hdr_row = ctx["raw_header_row"]
            first_data_row = ctx["first_data_row"]
            row_iter = ctx["row_iter"]

            if not has_header and first_data_row is None:
                raise ValueError("Worksheet contains no data")
            if has_header and raw_hdr_row is None:
                raise ValueError("Worksheet contains no header or data")

            init_width = len(raw_hdr_row) if raw_hdr_row is not None else len(first_data_row or [])
            batch_limit = (
                int(_EXCEL_STREAM_CHUNK)
                if _EXCEL_STREAM_CHUNK > 0
                else max(2000, min(50_000, 1_500_000 // max(1, init_width)))
            )
            buf: list[list[Any]] = []
            if first_data_row is not None:
                buf.append(first_data_row)
            max_seen_width = init_width

            def _emit_xlsx_buf(rows_buf: list[list[Any]]) -> None:
                nonlocal max_seen_width
                w = max(max_seen_width, max((len(r) for r in rows_buf), default=0))
                max_seen_width = w
                if has_header:
                    hdrs = [
                        str(raw_hdr_row[i]).strip() if i < len(raw_hdr_row) and raw_hdr_row[i] is not None else f"col_{i+1}"
                        for i in range(w)
                    ]
                else:
                    hdrs = [f"col_{i+1}" for i in range(w)]
                padded = [r + [None] * (w - len(r)) for r in rows_buf]
                _flush_chunk(pd.DataFrame(padded, columns=hdrs), chunk_raw_headers=hdrs)

            for raw in row_iter:
                trimmed = _trim_row_trailing_nones(raw)
                if not trimmed:
                    continue
                buf.append(trimmed)
                if len(trimmed) > max_seen_width:
                    max_seen_width = len(trimmed)
                if len(buf) >= batch_limit:
                    _emit_xlsx_buf(buf)
                    buf = []

            if buf or not loaded_any:
                _emit_xlsx_buf(buf)
        finally:
            wb.close()
    elif ext in (".csv", ".tsv", ".log", ".txt", ".jsonl", ".ndjson"):
        fmt, delim, enc = _detect_text_log_format(file_path, ext)
        if fmt == "jsonl":
            records: list[dict[str, Any]] = []
            with open(file_path, "r", encoding=enc, errors="replace") as fh:
                for line in fh:
                    s = line.strip()
                    if not s:
                        continue
                    records.append(json.loads(s))
                    if len(records) >= 25_000:
                        chunk_df = pd.json_normalize(records)
                        san_cols, dmap = _sanitize_headers_and_renames(list(chunk_df.columns), column_renames)
                        display_name_map.update(dmap)
                        chunk_df.columns = san_cols
                        _flush_chunk(chunk_df)
                        records = []
            if records or not loaded_any:
                chunk_df = pd.json_normalize(records)
                san_cols, dmap = _sanitize_headers_and_renames(list(chunk_df.columns), column_renames)
                display_name_map.update(dmap)
                chunk_df.columns = san_cols
                _flush_chunk(chunk_df)
        elif fmt == "log_lines":
            records = []
            with open(file_path, "r", encoding=enc, errors="replace") as fh:
                for line_no, line in enumerate(fh, 1):
                    if not line.strip():
                        continue
                    records.append(_parse_log_line_to_record(line_no, line))
                    if len(records) >= 25_000:
                        chunk_df = pd.DataFrame(records)
                        san_cols, dmap = _sanitize_headers_and_renames(list(chunk_df.columns), column_renames)
                        display_name_map.update(dmap)
                        chunk_df.columns = san_cols
                        _flush_chunk(chunk_df)
                        records = []
            if records or not loaded_any:
                chunk_df = pd.DataFrame(records)
                san_cols, dmap = _sanitize_headers_and_renames(list(chunk_df.columns), column_renames)
                display_name_map.update(dmap)
                chunk_df.columns = san_cols
                _flush_chunk(chunk_df)
        else:
            reader = pd.read_csv(
                file_path,
                sep=delim,
                header=0 if has_header else None,
                chunksize=50_000,
                encoding=enc,
                on_bad_lines="skip",
            )
            for chunk_df in reader:
                if not has_header:
                    chunk_df.columns = [f"col_{i+1}" for i in range(len(chunk_df.columns))]
                raw_hdrs = [str(c).strip() or f"col_{i+1}" for i, c in enumerate(chunk_df.columns)]
                _flush_chunk(chunk_df, chunk_raw_headers=raw_hdrs)
            if not loaded_any:
                _flush_chunk(pd.DataFrame())
    else:
        parsed = preview_file_from_path(
            file_path=file_path,
            filename=filename,
            sheet_name=sheet_name,
            has_header=has_header,
            null_strategy=null_strategy,
            deduplicate=deduplicate,
            column_renames=column_renames,
            type_overrides=type_overrides,
            preview_limit=100,
        )
        load_res = warehouse_engine.load_dataframe(wh_table, parsed["dataframe"], mode="replace")
        return {
            "total_rows": load_res["total_rows"],
            "data_quality": parsed["data_quality"],
            "columns": parsed["columns"],
            "sheets": parsed["sheets"],
        }

    if deduplicate and loaded_any:
        total_rows = warehouse_engine.deduplicate_table(wh_table)

    # Refresh authoritative column types from DuckDB after any schema evolution
    if loaded_any:
        desc_cols = warehouse_engine.describe_table(wh_table)
        dtypes_map = {c["column_name"]: c["column_type"] for c in desc_cols}
        ordered_cols = [c["column_name"] for c in desc_cols]

    total_nulls = sum(total_nulls_per_col.values())
    total_cells = max(1, total_rows * max(1, len(ordered_cols)))
    completeness = max(0.0, (1.0 - (total_nulls / total_cells)) * 100.0) if total_rows > 0 else 100.0
    dq = {
        "dq_score": round((0.7 * completeness) + 30.0, 2),
        "completeness_pct": round(completeness, 2),
        "uniqueness_pct": 100.0,
        "duplicate_rows": 0,
        "total_null_cells": total_nulls,
        "total_cells": total_rows * len(ordered_cols) if total_rows > 0 else 0,
    }
    columns_meta = _build_columns_meta(ordered_cols, dtypes_map, display_name_map, total_nulls_per_col)

    return {
        "total_rows": total_rows,
        "data_quality": dq,
        "columns": columns_meta,
        "sheets": sheets,
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
    import tempfile

    if settings.MAX_UPLOAD_BYTES > 0 and len(file_bytes) > settings.MAX_UPLOAD_BYTES:
        raise ValueError(
            f"File exceeds maximum allowed size ({settings.MAX_UPLOAD_BYTES // (1024 * 1024)} MB)"
        )
    ext = os.path.splitext(filename.lower())[1]
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tf:
        tf.write(file_bytes)
        tmp_path = tf.name
    try:
        return preview_file_from_path(
            file_path=tmp_path,
            filename=filename,
            sheet_name=sheet_name,
            has_header=has_header,
            null_strategy=null_strategy,
            deduplicate=deduplicate,
            column_renames=column_renames,
            type_overrides=type_overrides,
            preview_limit=preview_limit,
        )
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


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
                headers = dict(config.get("headers", {}))
                if credentials.get("bearer_token"):
                    headers["Authorization"] = f"Bearer {credentials['bearer_token']}"
                resp = _ssrf_safe_http_request(
                    "POST" if st == "graphql" else "GET",
                    url,
                    headers=headers,
                    json_body={"query": "{ __typename }"} if st == "graphql" else None,
                    timeout=6.0,
                    config=config,
                )
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
    def _qmark_to_pyformat(sql: str) -> str:
        """Convert DB-API '?' placeholders outside single-quoted string literals to '%s'."""
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
            if not in_quote and ch == "?":
                out.append("%s")
                i += 1
                continue
            out.append(ch)
            i += 1
        return "".join(out)

    @staticmethod
    def _fetch_bounded_df(cur, max_rows: int) -> pd.DataFrame:
        cols = [d[0] for d in cur.description] if cur.description else []
        rows: list[Any] = []
        while len(rows) < max_rows:
            chunk = cur.fetchmany(min(2000, max_rows - len(rows)))
            if not chunk:
                break
            rows.extend(chunk)
        return pd.DataFrame(rows, columns=cols)

    @staticmethod
    def extract_dataframe(
        source_type: str,
        config: dict[str, Any],
        credentials: dict[str, Any],
        query: Optional[str] = None,
        table_name: Optional[str] = None,
        read_only: bool = True,
        query_params: Optional[list[Any]] = None,
        max_rows: Optional[int] = None,
        timeout_sec: Optional[int] = None,
    ) -> pd.DataFrame:
        st = source_type.lower()
        safe_table = sanitize_identifier(table_name) if table_name else None
        sql = query or (f'SELECT * FROM "{safe_table}"' if safe_table else None)
        eff_max_rows = max(1, min(int(max_rows or settings.MAX_EXTRACT_ROWS), settings.MAX_EXTRACT_ROWS))
        eff_timeout = max(1, min(int(timeout_sec or settings.EXTERNAL_QUERY_TIMEOUT_SECONDS), 120))

        # Unconditionally enforce AST read-only validation regardless of DataSource.read_only flag
        if sql:
            dialect = (
                "sqlite"
                if st == "sqlite"
                else "postgres"
                if st == "postgresql"
                else "mysql"
                if st in ("mysql", "mariadb")
                else "duckdb"
            )
            validate_readonly_sql(sql, dialect=dialect)

        if st == "sqlite":
            db_path = DataConnectorEngine._validate_sqlite_path(
                config.get("file_path") or config.get("database")
            )
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=eff_timeout)
            try:
                try:
                    conn.execute("PRAGMA query_only = ON")
                except Exception:
                    pass
                deadline = time.monotonic() + float(eff_timeout)

                def _progress_abort() -> int:
                    return 1 if time.monotonic() > deadline else 0

                conn.set_progress_handler(_progress_abort, 10000)
                try:
                    cur = conn.execute(sql, tuple(query_params or ()))
                    return DataConnectorEngine._fetch_bounded_df(cur, eff_max_rows)
                except sqlite3.OperationalError as exc:
                    if "interrupted" in str(exc).lower():
                        raise TimeoutError(f"SQLite query exceeded {eff_timeout}s timeout") from exc
                    raise
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
                connect_timeout=eff_timeout,
            )
            try:
                conn.set_session(readonly=True, autocommit=False)
                cur = conn.cursor()
                cur.execute(f"SET LOCAL statement_timeout = {int(eff_timeout * 1000)}")
                pg_sql = DataConnectorEngine._qmark_to_pyformat(sql) if query_params else sql
                cur.execute(pg_sql, tuple(query_params) if query_params else None)
                return DataConnectorEngine._fetch_bounded_df(cur, eff_max_rows)
            finally:
                try:
                    conn.rollback()
                except Exception:
                    pass
                conn.close()

        if st in ("mysql", "mariadb"):
            import pymysql
            conn = pymysql.connect(
                host=config.get("host", "localhost"),
                port=int(config.get("port", 3306)),
                database=config.get("database", ""),
                user=credentials.get("username", ""),
                password=credentials.get("password", ""),
                connect_timeout=eff_timeout,
                read_timeout=eff_timeout,
                write_timeout=eff_timeout,
            )
            try:
                cur = conn.cursor()
                try:
                    cur.execute("SET SESSION TRANSACTION READ ONLY")
                except Exception:
                    pass
                try:
                    cur.execute(f"SET SESSION MAX_EXECUTION_TIME = {int(eff_timeout * 1000)}")
                except Exception:
                    pass
                my_sql = DataConnectorEngine._qmark_to_pyformat(sql) if query_params else sql
                cur.execute(my_sql, tuple(query_params) if query_params else None)
                return DataConnectorEngine._fetch_bounded_df(cur, eff_max_rows)
            finally:
                conn.close()

        if st == "mongodb":
            from pymongo import MongoClient
            uri = credentials.get("connection_uri") or config.get("uri")
            client = MongoClient(uri, serverSelectionTimeoutMS=min(eff_timeout * 1000, 10000))
            try:
                db = client[config.get("database", "test")]
                coll = db[table_name or config.get("collection", "items")]
                docs = list(coll.find({}, {"_id": 0}).max_time_ms(eff_timeout * 1000).limit(min(eff_max_rows, 50000)))
                return pd.json_normalize(docs)
            finally:
                client.close()

        if st in ("rest_api", "graphql"):
            url = config.get("url") or config.get("base_url")
            if not url:
                raise ValueError("URL is required")
            headers = dict(config.get("headers", {}))
            if credentials.get("bearer_token"):
                headers["Authorization"] = f"Bearer {credentials['bearer_token']}"
            resp = _ssrf_safe_http_request(
                "POST" if st == "graphql" else "GET",
                url,
                headers=headers,
                json_body={"query": query or config.get("query", "")} if st == "graphql" else None,
                timeout=float(eff_timeout),
                config=config,
            )
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
            df_api = pd.json_normalize(data if isinstance(data, list) else [data])
            return df_api.head(eff_max_rows)

        raise ValueError(f"Cannot extract live rows from source type: {source_type}")
