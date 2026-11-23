"""
Security Log Intelligence Engine (Phase 3):
- Honest Connector Capability Matrix (implemented & tested vs planned/requires-infra)
- Real structured JSONL + Syslog/KV security event parser with explicit action separation
- Quarantine DLQ for unparsed lines (never silently dropped)
- SHA-256 deduplication, timezone normalization (event_time vs ingested_at), batch retry & retention purge
- Explainable threshold & statistical-baseline anomaly alerting (with sample count & limitations)
- Strictly advisory Incident lifecycle (no auto-blocking/destructive actions; human approval + rollback + audit)
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
import statistics
from typing import Any, Optional
from zoneinfo import ZoneInfo
from sqlalchemy.orm import Session

from app.core.permissions import permission_resolver
from app.engines.warehouse import warehouse
from app.models.identity import User
from app.models.intelligence import (
    SecurityAlert,
    SecurityAlertRule,
    SecurityCollector,
    SecurityEvent,
    SecurityIncident,
    SecurityUnparsedEvent,
)

# Explicit Connector Support Matrix (Rule 5 Honesty)
SECURITY_CONNECTOR_CATALOG: list[dict[str, Any]] = [
    {
        "id": "syslog_security_jsonl",
        "name": "Syslog / Structured Security JSONL & Key-Value Collector",
        "name_fa": "جمع‌آورنده بومی لاگ امنیتی (JSONL / Syslog / Key-Value)",
        "status": "implemented_tested",
        "status_fa": "پیاده‌سازی‌شده و آزمایش‌شده (عملیاتی)",
        "tested": True,
        "supported_formats": ["jsonl", "ndjson", "syslog_kv", "text_log"],
        "requirements_fa": "آماده دریافت فایل، بسته JSONL یا خط‌های Syslog/KV از طریق API امن.",
    },
    {
        "id": "defender_edr",
        "name": "Microsoft Defender for Endpoint",
        "name_fa": "Microsoft Defender for Endpoint (EDR)",
        "status": "planned_requires_infrastructure",
        "status_fa": "طرح‌شده — نیازمند زیرساخت و اعتبارنامه (آزمایش‌نشده)",
        "tested": False,
        "supported_formats": ["graph_security_api"],
        "requirements_fa": "نیازمند Tenant ID، Client Secret دارای مجوز SecurityEvents.Read.All و دسترسی به Graph API.",
    },
    {
        "id": "eset_protect",
        "name": "ESET PROTECT / Security Management",
        "name_fa": "ESET PROTECT (آنتی‌ویروس و EDR سازمانی)",
        "status": "planned_requires_infrastructure",
        "status_fa": "طرح‌شده — نیازمند زیرساخت و اعتبارنامه (آزمایش‌نشده)",
        "tested": False,
        "supported_formats": ["syslog_json", "rest_api"],
        "requirements_fa": "نیازمند سرور ESET PROTECT با خروجی Syslog JSON یا API متصل.",
    },
    {
        "id": "kaspersky_ksc",
        "name": "Kaspersky Security Center",
        "name_fa": "Kaspersky Security Center",
        "status": "planned_requires_infrastructure",
        "status_fa": "طرح‌شده — نیازمند زیرساخت و اعتبارنامه (آزمایش‌نشده)",
        "tested": False,
        "supported_formats": ["cef_syslog"],
        "requirements_fa": "نیازمند پیکربندی خروجی CEF/Syslog روی سرور KSC و تست میدانی.",
    },
    {
        "id": "fortinet_fortigate",
        "name": "Fortinet FortiGate NGFW",
        "name_fa": "فایروال Fortinet FortiGate",
        "status": "planned_requires_infrastructure",
        "status_fa": "طرح‌شده — نیازمند زیرساخت و اعتبارنامه (آزمایش‌نشده)",
        "tested": False,
        "supported_formats": ["fortios_syslog_kv"],
        "requirements_fa": "نیازمند ارسال لاگ ترافیک/تهدید FortiOS یا توکن API خواندنی.",
    },
    {
        "id": "paloalto_ngfw",
        "name": "Palo Alto Networks NGFW",
        "name_fa": "فایروال Palo Alto PAN-OS",
        "status": "planned_requires_infrastructure",
        "status_fa": "طرح‌شده — نیازمند زیرساخت و اعتبارنامه (آزمایش‌نشده)",
        "tested": False,
        "supported_formats": ["panos_syslog_csv"],
        "requirements_fa": "نیازمند کلید API خواندنی PAN-OS یا جمع‌آورنده Syslog آزمایش‌شده.",
    },
    {
        "id": "windows_event_log",
        "name": "Windows Security Event Log (WinRM / EVTX)",
        "name_fa": "رویدادهای امنیتی ویندوز (EVTX / WEF)",
        "status": "planned_requires_infrastructure",
        "status_fa": "طرح‌شده — نیازمند زیرساخت و اعتبارنامه (آزمایش‌نشده)",
        "tested": False,
        "supported_formats": ["evtx", "wec_json"],
        "requirements_fa": "نیازمند Windows Event Collector یا استخراج JSON از رویدادهای Security (4624/4625/4688).",
    },
]

_SYSLOG_PREFIX_RE = re.compile(
    r"^(?:<\d+>)?(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\s+(\S+)\s+(.*)$"
)
_KV_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_.-]*)=("([^"]*)"|(\S+))')

ACTION_CANONICAL_MAP: dict[str, str] = {
    "detect": "Detect",
    "detected": "Detect",
    "alert": "Detect",
    "monitor": "Detect",
    "log": "Detect",
    "block": "Block",
    "blocked": "Block",
    "deny": "Block",
    "denied": "Block",
    "drop": "Block",
    "reject": "Block",
    "quarantine": "Quarantine",
    "quarantined": "Quarantine",
    "isolate": "Quarantine",
    "clean": "Clean",
    "cleaned": "Clean",
    "remediated": "Clean",
    "fail": "Failed",
    "failed": "Failed",
    "error": "Failed",
    "failure": "Failed",
    "allow": "Allow",
    "allowed": "Allow",
    "accept": "Allow",
    "success": "Allow",
    "pass": "Allow",
}

SEVERITY_CANONICAL = {"critical", "high", "medium", "low", "info"}


def _parse_event_timestamp(raw_ts: Any, default_tz_name: str = "UTC") -> datetime:
    if isinstance(raw_ts, datetime):
        if raw_ts.tzinfo is None:
            try:
                tz = ZoneInfo(default_tz_name or "UTC")
            except Exception:
                tz = timezone.utc
            return raw_ts.replace(tzinfo=tz).astimezone(timezone.utc)
        return raw_ts.astimezone(timezone.utc)

    s = str(raw_ts or "").strip()
    if not s:
        raise ValueError("Missing event timestamp")

    s_norm = s.replace("Z", "+00:00")
    dt: Optional[datetime] = None
    try:
        dt = datetime.fromisoformat(s_norm)
    except Exception:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%b %d %H:%M:%S"):
            try:
                dt = datetime.strptime(s, fmt)
                if dt.year == 1900:
                    dt = dt.replace(year=datetime.now(timezone.utc).year)
                break
            except Exception:
                continue
    if dt is None:
        raise ValueError(f"Unparseable timestamp: {s[:60]}")

    if dt.tzinfo is None:
        try:
            tz = ZoneInfo(default_tz_name or "UTC")
        except Exception:
            tz = timezone.utc
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(timezone.utc)


def _normalize_action(raw_action: Any, raw_result: Any = "") -> str:
    for cand in (raw_action, raw_result):
        low = str(cand or "").strip().lower()
        if low in ACTION_CANONICAL_MAP:
            return ACTION_CANONICAL_MAP[low]
        for k, v in ACTION_CANONICAL_MAP.items():
            if k in low and low:
                return v
    return "Detect"


def _normalize_severity(raw_sev: Any) -> str:
    low = str(raw_sev or "info").strip().lower()
    alias = {
        "crit": "critical", "emerg": "critical", "emergency": "critical", "fatal": "critical",
        "err": "high", "error": "high", "warn": "medium", "warning": "medium", "notice": "low", "informational": "info",
    }
    if low in SEVERITY_CANONICAL:
        return low
    return alias.get(low, "info")


def _safe_int(val: Any) -> Optional[int]:
    if val is None or val == "":
        return None
    try:
        iv = int(val)
        return iv if 0 <= iv <= 65535 else None
    except Exception:
        return None


def parse_single_security_record(
    raw_item: str | dict[str, Any],
    default_tz: str = "UTC",
) -> dict[str, Any]:
    """
    Parse either a JSON object/line or a Syslog/KV text line into the canonical normalized security event dict.
    Raises ValueError on malformed or unparseable input so it is quarantined rather than silently dropped.
    """
    raw_msg = raw_item if isinstance(raw_item, str) else json.dumps(raw_item, ensure_ascii=False)
    obj: dict[str, Any]
    if isinstance(raw_item, dict):
        obj = dict(raw_item)
    else:
        txt = raw_item.strip()
        if not txt:
            raise ValueError("Empty log line")
        if txt.startswith("{"):
            try:
                loaded = json.loads(txt)
                if not isinstance(loaded, dict):
                    raise ValueError("JSON root must be an object")
                obj = loaded
            except Exception as exc:
                raise ValueError(f"Malformed JSON security event: {exc}") from exc
        else:
            m = _SYSLOG_PREFIX_RE.match(txt)
            if not m:
                raise ValueError("Unrecognized text log format (expected ISO timestamp + host + key=value payload)")
            ts_str, host_tok, rest = m.group(1), m.group(2), m.group(3)
            kv_matches = _KV_RE.findall(rest)
            if not kv_matches:
                raise ValueError("Missing structured key=value attributes in log line")
            obj = {"timestamp": ts_str, "hostname": host_tok}
            for k, _, qv, barev in kv_matches:
                obj[k] = qv if qv != "" else barev

    ts_raw = (
        obj.get("event_time") or obj.get("timestamp") or obj.get("@timestamp")
        or obj.get("time") or obj.get("ts") or obj.get("occurred_at")
    )
    if not ts_raw:
        raise ValueError("Missing required timestamp / event_time field")
    event_dt = _parse_event_timestamp(ts_raw, default_tz_name=default_tz)

    event_type = str(obj.get("event_type") or obj.get("category") or obj.get("type") or "").strip()
    raw_action = obj.get("action") or obj.get("disposition") or obj.get("decision") or ""
    raw_result = str(obj.get("result") or obj.get("outcome") or obj.get("status") or "").strip()
    if not event_type and not raw_action and not raw_result and not obj.get("rule_name") and not obj.get("threat_name"):
        raise ValueError("Insufficient security attributes (requires event_type, action, result, rule_name, or threat_name)")

    vendor = str(obj.get("vendor") or obj.get("device_vendor") or "").strip()
    product = str(obj.get("product") or obj.get("device_product") or obj.get("app") or "").strip()
    device = str(obj.get("device") or obj.get("device_name") or obj.get("sensor") or "").strip()
    src_ip = str(obj.get("src_ip") or obj.get("source_ip") or obj.get("src") or "").strip()
    dst_ip = str(obj.get("dst_ip") or obj.get("dest_ip") or obj.get("destination_ip") or obj.get("dst") or "").strip()
    username = str(obj.get("username") or obj.get("user") or obj.get("account") or obj.get("actor") or "").strip()
    hostname = str(obj.get("hostname") or obj.get("host") or obj.get("computer_name") or obj.get("endpoint") or "").strip()
    threat_name = str(obj.get("threat_name") or obj.get("threat") or obj.get("signature") or obj.get("malware") or "").strip()
    file_path = str(obj.get("file_path") or obj.get("path") or obj.get("target_file") or "").strip()
    file_hash = str(obj.get("file_hash") or obj.get("sha256") or obj.get("md5") or obj.get("hash") or "").strip()
    rule_name = str(obj.get("rule_name") or obj.get("rule") or obj.get("alert_rule") or "").strip()
    policy_name = str(obj.get("policy_name") or obj.get("policy") or "").strip()
    protocol = str(obj.get("protocol") or obj.get("proto") or "").strip().upper()
    action = _normalize_action(raw_action, raw_result)
    severity = _normalize_severity(obj.get("severity") or obj.get("level"))

    known_keys = {
        "event_time", "timestamp", "@timestamp", "time", "ts", "occurred_at",
        "vendor", "device_vendor", "product", "device_product", "app", "device", "device_name", "sensor",
        "event_type", "category", "type", "severity", "level", "action", "disposition", "decision",
        "result", "outcome", "status", "src_ip", "source_ip", "src", "src_port", "source_port",
        "dst_ip", "dest_ip", "destination_ip", "dst", "dst_port", "dest_port", "protocol", "proto",
        "username", "user", "account", "actor", "hostname", "host", "computer_name", "endpoint",
        "threat_name", "threat", "signature", "malware", "file_path", "path", "target_file",
        "file_hash", "sha256", "md5", "hash", "rule_name", "rule", "alert_rule", "policy_name", "policy",
    }
    extra = {k: v for k, v in obj.items() if k not in known_keys}

    dedup_basis = "|".join([
        event_dt.isoformat(), vendor, product, device, event_type or "generic",
        action, src_ip, dst_ip, username, hostname, rule_name, file_hash, raw_msg[:300]
    ])
    dedup_hash = hashlib.sha256(dedup_basis.encode("utf-8")).hexdigest()

    return {
        "dedup_hash": dedup_hash,
        "event_time": event_dt,
        "source_timezone": str(obj.get("timezone") or default_tz or "UTC"),
        "vendor": vendor,
        "product": product,
        "device": device,
        "event_type": event_type or "security_event",
        "severity": severity,
        "action": action,
        "result": raw_result or action,
        "src_ip": src_ip,
        "src_port": _safe_int(obj.get("src_port") or obj.get("source_port")),
        "dst_ip": dst_ip,
        "dst_port": _safe_int(obj.get("dst_port") or obj.get("dest_port")),
        "protocol": protocol,
        "username": username,
        "hostname": hostname,
        "threat_name": threat_name,
        "file_path": file_path,
        "file_hash": file_hash,
        "rule_name": rule_name,
        "policy_name": policy_name,
        "raw_message": raw_msg,
        "extra_json": extra,
    }


def _sync_security_warehouse(db: Session, org_id: str) -> None:
    """Refresh DuckDB analytical view/table for organization security events (excludes raw_message by default)."""
    rows = (
        db.query(SecurityEvent)
        .filter(SecurityEvent.organization_id == org_id)
        .order_by(SecurityEvent.event_time.desc())
        .limit(50_000)
        .all()
    )
    tname = f"wh_sec_events_{re.sub(r'[^a-z0-9_]', '', org_id.lower())[:12]}"
    if not rows:
        return
    import pandas as pd
    records = [
        {
            "id": r.id,
            "event_time": r.event_time.isoformat() if r.event_time else None,
            "ingested_at": r.ingested_at.isoformat() if r.ingested_at else None,
            "vendor": r.vendor,
            "product": r.product,
            "device": r.device,
            "event_type": r.event_type,
            "severity": r.severity,
            "action": r.action,
            "result": r.result,
            "src_ip": r.src_ip,
            "src_port": r.src_port,
            "dst_ip": r.dst_ip,
            "dst_port": r.dst_port,
            "protocol": r.protocol,
            "username": r.username,
            "hostname": r.hostname,
            "threat_name": r.threat_name,
            "file_hash": r.file_hash,
            "rule_name": r.rule_name,
            "policy_name": r.policy_name,
        }
        for r in rows
    ]
    df = pd.DataFrame(records)
    warehouse.load_dataframe(tname, df, if_exists="replace")


def ingest_security_batch(
    db: Session,
    org_id: str,
    collector: SecurityCollector,
    items: list[str | dict[str, Any]],
    *,
    source_name: str = "api_batch",
    max_items: int = 5000,
) -> dict[str, Any]:
    """Ingest a batch of security log lines/events with dedup, quarantine DLQ, collector stats & DuckDB sync."""
    if collector.source_type != "syslog_security_jsonl":
        raise ValueError(
            f"کانکتور '{collector.source_type}' در وضعیت طرح‌شده (آزمایش‌نشده / نیازمند زیرساخت) است و دریافت مستقیم آن فعال نیست."
        )

    now_utc = datetime.now(timezone.utc)
    batch = items[:max_items]
    existing_hashes = {
        h[0]
        for h in db.query(SecurityEvent.dedup_hash)
        .filter(SecurityEvent.organization_id == org_id)
        .all()
    }

    ingested = 0
    deduplicated = 0
    unparsed = 0
    unparsed_samples: list[dict[str, Any]] = []

    for line_no, raw_item in enumerate(batch, start=1):
        try:
            norm = parse_single_security_record(raw_item, default_tz=collector.default_timezone or "UTC")
            if norm["dedup_hash"] in existing_hashes:
                deduplicated += 1
                continue
            existing_hashes.add(norm["dedup_hash"])
            ev = SecurityEvent(
                organization_id=org_id,
                collector_id=collector.id,
                ingested_at=now_utc,
                **norm,
            )
            db.add(ev)
            ingested += 1
        except Exception as exc:
            unparsed += 1
            raw_line = raw_item if isinstance(raw_item, str) else json.dumps(raw_item, ensure_ascii=False)
            q_row = SecurityUnparsedEvent(
                organization_id=org_id,
                collector_id=collector.id,
                source_name=source_name,
                line_number=line_no,
                raw_line=raw_line[:4000],
                parse_error=str(exc),
                retry_count=0,
                status="quarantined",
            )
            db.add(q_row)
            if len(unparsed_samples) < 10:
                unparsed_samples.append({"line": line_no, "error": str(exc)})

    collector.last_run_at = now_utc
    collector.events_ingested = int(collector.events_ingested or 0) + ingested
    collector.events_deduplicated = int(collector.events_deduplicated or 0) + deduplicated
    collector.events_unparsed = int(collector.events_unparsed or 0) + unparsed
    collector.last_error = f"{unparsed} unparsed quarantined" if unparsed > 0 else None
    collector.status = "degraded" if (unparsed > 0 and ingested == 0) else "active"
    db.commit()

    try:
        _sync_security_warehouse(db, org_id)
    except Exception:
        pass

    return {
        "collector_id": collector.id,
        "received": len(batch),
        "ingested": ingested,
        "deduplicated": deduplicated,
        "unparsed_quarantined": unparsed,
        "unparsed_samples": unparsed_samples,
        "status": collector.status,
    }


def enforce_collector_retention(db: Session, org_id: str, collector: SecurityCollector) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(collector.retention_days or 90)))
    deleted = (
        db.query(SecurityEvent)
        .filter(
            SecurityEvent.organization_id == org_id,
            SecurityEvent.collector_id == collector.id,
            SecurityEvent.event_time < cutoff,
        )
        .delete()
    )
    db.commit()
    return int(deleted or 0)


def get_security_overview(db: Session, org_id: str, hours: int = 168) -> dict[str, Any]:
    """Summarize normalized security posture without conflating actions or inferring health from missing logs."""
    since = datetime.now(timezone.utc) - timedelta(hours=max(1, hours))
    events = (
        db.query(SecurityEvent)
        .filter(SecurityEvent.organization_id == org_id, SecurityEvent.event_time >= since)
        .order_by(SecurityEvent.event_time.desc())
        .all()
    )
    collectors = db.query(SecurityCollector).filter(SecurityCollector.organization_id == org_id).all()
    unparsed_count = (
        db.query(SecurityUnparsedEvent)
        .filter(SecurityUnparsedEvent.organization_id == org_id, SecurityUnparsedEvent.status == "quarantined")
        .count()
    )
    open_alerts = (
        db.query(SecurityAlert)
        .filter(SecurityAlert.organization_id == org_id, SecurityAlert.status == "open")
        .count()
    )
    open_incidents = (
        db.query(SecurityIncident)
        .filter(SecurityIncident.organization_id == org_id, SecurityIncident.status.in_(["open", "triaging"]))
        .count()
    )

    action_counts = Counter(ev.action for ev in events)
    sev_counts = Counter(ev.severity for ev in events)
    vendor_counts = Counter(ev.vendor or "Unknown" for ev in events)
    top_hosts = Counter(ev.hostname for ev in events if ev.hostname).most_common(10)
    top_users = Counter(ev.username for ev in events if ev.username).most_common(10)
    top_src_ips = Counter(ev.src_ip for ev in events if ev.src_ip).most_common(10)

    # Hourly timeline bucketed by action
    timeline_buckets: dict[str, dict[str, Any]] = {}
    for ev in events:
        bkey = ev.event_time.astimezone(timezone.utc).strftime("%Y-%m-%d %H:00")
        b = timeline_buckets.setdefault(bkey, {
            "bucket": bkey, "total": 0, "Detect": 0, "Block": 0, "Quarantine": 0, "Clean": 0, "Failed": 0, "Allow": 0
        })
        b["total"] += 1
        b[ev.action if ev.action in b else "Detect"] += 1
    timeline = [timeline_buckets[k] for k in sorted(timeline_buckets.keys())]

    stale_collectors = [
        c.name for c in collectors
        if c.last_run_at is None or (datetime.now(timezone.utc) - (c.last_run_at.replace(tzinfo=timezone.utc) if c.last_run_at.tzinfo is None else c.last_run_at)).total_seconds() > 86400
    ]

    honesty_notes = [
        "اقدامات Detect، Block، Quarantine، Clean و Failed به‌صورت کاملاً تفکیک‌شده گزارش می‌شوند.",
        "کاهش یا صفر بودن رویدادها به‌معنای سلامت قطعی سیستم نیست و ممکن است ناشی از قطع ارسال لاگ یا خرابی جمع‌آورنده باشد.",
    ]
    if stale_collectors:
        honesty_notes.append(f"هشدار جمع‌آورنده: {', '.join(stale_collectors)} بیش از ۲۴ ساعت است که داده‌ای دریافت نکرده است.")

    return {
        "window_hours": hours,
        "total_events": len(events),
        "action_breakdown": {
            "Detect": action_counts.get("Detect", 0),
            "Block": action_counts.get("Block", 0),
            "Quarantine": action_counts.get("Quarantine", 0),
            "Clean": action_counts.get("Clean", 0),
            "Failed": action_counts.get("Failed", 0),
            "Allow": action_counts.get("Allow", 0),
        },
        "severity_breakdown": dict(sev_counts),
        "vendor_breakdown": dict(vendor_counts),
        "top_hosts": [{"hostname": k, "count": v} for k, v in top_hosts],
        "top_users": [{"username": k, "count": v} for k, v in top_users],
        "top_src_ips": [{"src_ip": k, "count": v} for k, v in top_src_ips],
        "timeline": timeline[-72:],
        "collectors_count": len(collectors),
        "unparsed_quarantined_count": unparsed_count,
        "open_alerts_count": open_alerts,
        "open_incidents_count": open_incidents,
        "interpretation_notes_fa": honesty_notes,
    }


def query_security_events_page(
    db: Session,
    user: User,
    *,
    action: Optional[str] = None,
    severity: Optional[str] = None,
    username: Optional[str] = None,
    hostname: Optional[str] = None,
    src_ip: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    can_view_raw = permission_resolver.resolve(db, user, "security_log", "*", "view_raw").allowed
    q = db.query(SecurityEvent).filter(SecurityEvent.organization_id == user.organization_id)
    if action:
        q = q.filter(SecurityEvent.action == action)
    if severity:
        q = q.filter(SecurityEvent.severity == severity)
    if username:
        q = q.filter(SecurityEvent.username == username)
    if hostname:
        q = q.filter(SecurityEvent.hostname == hostname)
    if src_ip:
        q = q.filter(SecurityEvent.src_ip == src_ip)
    total = q.count()
    rows = q.order_by(SecurityEvent.event_time.desc()).offset(offset).limit(limit).all()

    items = []
    for r in rows:
        rec = {
            "id": r.id,
            "collector_id": r.collector_id,
            "event_time": r.event_time.isoformat() if r.event_time else None,
            "ingested_at": r.ingested_at.isoformat() if r.ingested_at else None,
            "source_timezone": r.source_timezone,
            "vendor": r.vendor,
            "product": r.product,
            "device": r.device,
            "event_type": r.event_type,
            "severity": r.severity,
            "action": r.action,
            "result": r.result,
            "src_ip": r.src_ip,
            "src_port": r.src_port,
            "dst_ip": r.dst_ip,
            "dst_port": r.dst_port,
            "protocol": r.protocol,
            "username": r.username,
            "hostname": r.hostname,
            "threat_name": r.threat_name,
            "file_path": r.file_path,
            "file_hash": r.file_hash,
            "rule_name": r.rule_name,
            "policy_name": r.policy_name,
            "raw_message_accessible": can_view_raw,
            "raw_message": r.raw_message if can_view_raw else None,
        }
        items.append(rec)

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "can_view_raw": can_view_raw,
        "events": items,
    }


def evaluate_security_alerts(
    db: Session,
    org_id: str,
    *,
    zscore_threshold: float = 2.0,
    min_spike_count: int = 3,
) -> list[dict[str, Any]]:
    """
    Detect explainable anomalies via threshold & statistical baseline (hourly buckets vs historical mean/stddev).
    Retains observed vs baseline values, sample size, time range, and limitations.
    """
    events = (
        db.query(SecurityEvent)
        .filter(SecurityEvent.organization_id == org_id)
        .order_by(SecurityEvent.event_time.asc())
        .all()
    )
    if not events:
        return []

    buckets: dict[str, list[SecurityEvent]] = defaultdict(list)
    for ev in events:
        b = ev.event_time.astimezone(timezone.utc).strftime("%Y-%m-%d %H:00")
        buckets[b].append(ev)

    sorted_keys = sorted(buckets.keys())
    metric_series = {
        "failed_auth_count": [sum(1 for e in buckets[k] if e.action == "Failed") for k in sorted_keys],
        "high_critical_count": [sum(1 for e in buckets[k] if e.severity in ("high", "critical")) for k in sorted_keys],
        "block_quarantine_count": [sum(1 for e in buckets[k] if e.action in ("Block", "Quarantine")) for k in sorted_keys],
    }

    metric_titles = {
        "failed_auth_count": "جهش غیرعادی در رویدادهای ناموفق (Failed)",
        "high_critical_count": "افزایش رویدادهای با شدت بالا/بحرانی",
        "block_quarantine_count": "جهش در رویدادهای مسدودسازی و قرنطینه",
    }

    created_alerts: list[dict[str, Any]] = []
    latest_key = sorted_keys[-1]
    latest_events = buckets[latest_key]
    w_start = latest_events[0].event_time
    w_end = latest_events[-1].event_time

    for mkey, series in metric_series.items():
        obs = float(series[-1])
        hist = [float(x) for x in series[:-1]]
        if not hist:
            # Single-window fallback: threshold check only
            if obs >= min_spike_count:
                al = SecurityAlert(
                    organization_id=org_id,
                    title=f"{metric_titles[mkey]} (آستانه اولیه)",
                    severity="high" if mkey == "high_critical_count" else "medium",
                    observed_value=obs,
                    baseline_value=0.0,
                    baseline_stddev=0.0,
                    delta_value=obs,
                    sample_count=len(latest_events),
                    window_start=w_start,
                    window_end=w_end,
                    evidence_json={
                        "metric": mkey,
                        "bucket": latest_key,
                        "top_hosts": Counter(e.hostname for e in latest_events if e.hostname).most_common(5),
                        "top_users": Counter(e.username for e in latest_events if e.username).most_common(5),
                    },
                    limitations_fa="سابقه تاریخی کافی (حداقل ۲ پنجره زمانی) وجود ندارد؛ ارزیابی فقط بر اساس آستانه پایه انجام شد.",
                    status="open",
                )
                db.add(al)
                db.flush()
                created_alerts.append({
                    "id": al.id, "title": al.title, "severity": al.severity,
                    "observed_value": obs, "baseline_value": 0.0, "delta_value": obs,
                    "sample_count": al.sample_count, "limitations_fa": al.limitations_fa,
                })
            continue

        mean_val = statistics.mean(hist)
        std_val = statistics.stdev(hist) if len(hist) >= 2 else 0.0
        delta = round(obs - mean_val, 2)
        z = ((obs - mean_val) / std_val) if std_val > 0 else (obs / max(mean_val, 1.0))
        triggered = obs >= min_spike_count and (
            (std_val > 0 and z >= zscore_threshold) or (std_val == 0 and obs > max(mean_val * 2.5, float(min_spike_count)))
        )
        if triggered:
            al = SecurityAlert(
                organization_id=org_id,
                title=metric_titles[mkey],
                severity="critical" if z >= 4.0 else ("high" if z >= 2.5 else "medium"),
                observed_value=obs,
                baseline_value=round(mean_val, 2),
                baseline_stddev=round(std_val, 2),
                delta_value=delta,
                sample_count=len(events),
                window_start=w_start,
                window_end=w_end,
                evidence_json={
                    "metric": mkey,
                    "bucket": latest_key,
                    "zscore": round(z, 2),
                    "history_windows": len(hist),
                    "top_hosts": Counter(e.hostname for e in latest_events if e.hostname).most_common(5),
                    "top_users": Counter(e.username for e in latest_events if e.username).most_common(5),
                    "top_src_ips": Counter(e.src_ip for e in latest_events if e.src_ip).most_common(5),
                },
                limitations_fa=(
                    f"مبنای مقایسه میانگین {len(hist)} بازه قبلی است؛ تغییرات برنامه‌ریزی‌شده (مانند اسکن امنیتی یا به‌روزرسانی) ممکن است باعث افزایش موقت شود."
                ),
                status="open",
            )
            db.add(al)
            db.flush()
            created_alerts.append({
                "id": al.id, "title": al.title, "severity": al.severity,
                "observed_value": obs, "baseline_value": round(mean_val, 2),
                "baseline_stddev": round(std_val, 2), "delta_value": delta,
                "sample_count": al.sample_count, "limitations_fa": al.limitations_fa,
            })

    db.commit()
    return created_alerts
