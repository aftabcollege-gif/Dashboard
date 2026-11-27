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
from app.core.rls_cls import build_data_security_context, build_security_projection_and_where
from app.engines.security_classifier import (
    CATEGORY_LABEL_MAP,
    classify_security_text,
    load_custom_security_rules,
)
from app.engines.warehouse import warehouse
from app.models.data import Dataset
from app.models.identity import User
from app.models.intelligence import (
    SecurityAlert,
    SecurityAlertRule,
    SecurityClassificationRule,
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
    custom_rules: Optional[list[SecurityClassificationRule]] = None,
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

    # Bilingual NLP/Regex 15-category & status extraction
    nlp_text = " ".join([
        raw_result,
        str(obj.get("message") or obj.get("msg") or obj.get("description") or obj.get("status_text") or ""),
        threat_name,
        rule_name,
        product,
        raw_msg[:600],
    ])
    cls_res = classify_security_text(
        nlp_text,
        action=action,
        threat_name=threat_name,
        rule_name=rule_name,
        event_type=event_type,
        custom_rules=custom_rules,
    )
    eff_severity = cls_res["severity_override"] or severity
    sw_name = str(obj.get("software_name") or obj.get("software") or cls_res["software_name"] or product or "").strip()[:115]
    sw_ver = str(obj.get("software_version") or obj.get("version") or cls_res["software_version"] or "").strip()[:60]

    return {
        "dedup_hash": dedup_hash,
        "event_time": event_dt,
        "source_timezone": str(obj.get("timezone") or default_tz or "UTC"),
        "vendor": vendor,
        "product": product,
        "device": device,
        "event_type": event_type or "security_event",
        "severity": eff_severity,
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
        "threat_category": cls_res["threat_category"],
        "threat_status": cls_res["threat_status"],
        "extraction_reason": cls_res["extraction_reason"],
        "software_name": sw_name,
        "software_version": sw_ver,
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
            "threat_category": r.threat_category,
            "threat_status": r.threat_status,
            "software_name": r.software_name,
            "software_version": r.software_version,
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
    custom_rules = load_custom_security_rules(db, org_id)
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
            norm = parse_single_security_record(
                raw_item, default_tz=collector.default_timezone or "UTC", custom_rules=custom_rules
            )
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
    cat_counts = Counter((ev.threat_category or "other_security_event") for ev in events)
    status_counts = Counter((ev.threat_status or "requires_review") for ev in events)
    top_hosts = Counter(ev.hostname for ev in events if ev.hostname).most_common(10)
    top_users = Counter(ev.username for ev in events if ev.username).most_common(10)
    top_src_ips = Counter(ev.src_ip for ev in events if ev.src_ip).most_common(10)

    all_incidents = (
        db.query(SecurityIncident)
        .filter(SecurityIncident.organization_id == org_id)
        .all()
    )
    mtta_vals = [float(i.mtta_minutes) for i in all_incidents if i.mtta_minutes is not None]
    mttr_vals = [float(i.mttr_minutes) for i in all_incidents if i.mttr_minutes is not None]
    tier2_alerts = (
        db.query(SecurityAlert)
        .filter(SecurityAlert.organization_id == org_id, SecurityAlert.tier >= 2)
        .count()
    )
    fp_alerts = (
        db.query(SecurityAlert)
        .filter(SecurityAlert.organization_id == org_id, SecurityAlert.false_positive == True)
        .count()
    )

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
        "threat_category_breakdown": [
            {"key": k, "label_fa": CATEGORY_LABEL_MAP.get(k, k), "count": c}
            for k, c in cat_counts.most_common()
        ],
        "threat_status_breakdown": dict(status_counts),
        "soc_metrics": {
            "avg_mtta_minutes": round(statistics.mean(mtta_vals), 1) if mtta_vals else None,
            "avg_mttr_minutes": round(statistics.mean(mttr_vals), 1) if mttr_vals else None,
            "tier2_alerts_count": tier2_alerts,
            "false_positive_alerts_count": fp_alerts,
            "total_incidents_count": len(all_incidents),
        },
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
    threat_category: Optional[str] = None,
    threat_status: Optional[str] = None,
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
    if threat_category:
        q = q.filter(SecurityEvent.threat_category == threat_category)
    if threat_status:
        q = q.filter(SecurityEvent.threat_status == threat_status)
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
            "threat_category": r.threat_category or "other_security_event",
            "threat_category_fa": CATEGORY_LABEL_MAP.get(r.threat_category or "other_security_event", r.threat_category or ""),
            "threat_status": r.threat_status or "requires_review",
            "extraction_reason": r.extraction_reason or "",
            "software_name": r.software_name or "",
            "software_version": r.software_version or "",
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

    open_existing = (
        db.query(SecurityAlert)
        .filter(
            SecurityAlert.organization_id == org_id,
            SecurityAlert.status.in_(["open", "acknowledged"]),
        )
        .all()
    )

    def _dedup_or_create(title: str, sev: str, obs: float, mean_v: float, std_v: float, delta: float, sample_c: int, ev_json: dict[str, Any], lim: str) -> None:
        mkey_cur = ev_json.get("metric")
        bkey_cur = ev_json.get("bucket")
        for ex in open_existing:
            ex_ev = ex.evidence_json or {}
            if ex_ev.get("metric") == mkey_cur and ex_ev.get("bucket") == bkey_cur:
                ex.duplicate_count = int(ex.duplicate_count or 1) + 1
                ex.observed_value = obs
                ex.delta_value = delta
                ex.sample_count = sample_c
                ex.evidence_json = ev_json
                return
        al = SecurityAlert(
            organization_id=org_id,
            title=title,
            severity=sev,
            observed_value=obs,
            baseline_value=mean_v,
            baseline_stddev=std_v,
            delta_value=delta,
            sample_count=sample_c,
            window_start=w_start,
            window_end=w_end,
            evidence_json=ev_json,
            limitations_fa=lim,
            status="open",
            tier=1,
            duplicate_count=1,
        )
        db.add(al)
        db.flush()
        open_existing.append(al)
        created_alerts.append({
            "id": al.id, "title": al.title, "severity": al.severity,
            "observed_value": obs, "baseline_value": mean_v, "baseline_stddev": std_v,
            "delta_value": delta, "sample_count": al.sample_count, "limitations_fa": al.limitations_fa,
        })

    for mkey, series in metric_series.items():
        obs = float(series[-1])
        hist = [float(x) for x in series[:-1]]
        if not hist:
            if obs >= min_spike_count:
                _dedup_or_create(
                    f"{metric_titles[mkey]} (آستانه اولیه)",
                    "high" if mkey == "high_critical_count" else "medium",
                    obs,
                    0.0,
                    0.0,
                    obs,
                    len(latest_events),
                    {
                        "metric": mkey,
                        "bucket": latest_key,
                        "top_hosts": Counter(e.hostname for e in latest_events if e.hostname).most_common(5),
                        "top_users": Counter(e.username for e in latest_events if e.username).most_common(5),
                    },
                    "سابقه تاریخی کافی (حداقل ۲ پنجره زمانی) وجود ندارد؛ ارزیابی فقط بر اساس آستانه پایه انجام شد.",
                )
            continue

        mean_val = statistics.mean(hist)
        std_val = statistics.stdev(hist) if len(hist) >= 2 else 0.0
        delta = round(obs - mean_val, 2)
        z = ((obs - mean_val) / std_val) if std_val > 0 else (obs / max(mean_val, 1.0))
        triggered = obs >= min_spike_count and (
            (std_val > 0 and z >= zscore_threshold) or (std_val == 0 and obs > max(mean_val * 2.5, float(min_spike_count)))
        )
        if triggered:
            _dedup_or_create(
                metric_titles[mkey],
                "critical" if z >= 4.0 else ("high" if z >= 2.5 else "medium"),
                obs,
                round(mean_val, 2),
                round(std_val, 2),
                delta,
                len(events),
                {
                    "metric": mkey,
                    "bucket": latest_key,
                    "zscore": round(z, 2),
                    "history_windows": len(hist),
                    "top_hosts": Counter(e.hostname for e in latest_events if e.hostname).most_common(5),
                    "top_users": Counter(e.username for e in latest_events if e.username).most_common(5),
                    "top_src_ips": Counter(e.src_ip for e in latest_events if e.src_ip).most_common(5),
                },
                f"مبنای مقایسه میانگین {len(hist)} بازه قبلی است؛ تغییرات برنامه‌ریزی‌شده (مانند اسکن امنیتی یا به‌روزرسانی) ممکن است باعث افزایش موقت شود.",
            )

    db.commit()
    return created_alerts


def triage_security_alert(
    db: Session,
    alert: SecurityAlert,
    user: User,
    *,
    action: str,
    note: str = "",
    false_positive_reason: str = "",
    create_incident_on_escalate: bool = True,
) -> dict[str, Any]:
    now_utc = datetime.now(timezone.utc)
    notes = list(alert.notes_json or [])
    if note.strip():
        notes.append({"by": user.username, "at": now_utc.isoformat(), "note": note.strip()})

    escalated_incident_id: Optional[str] = None
    if action == "acknowledge":
        alert.status = "acknowledged"
        alert.acknowledged_by = user.id
        alert.assigned_to = alert.assigned_to or user.id
    elif action == "false_positive":
        alert.status = "closed"
        alert.false_positive = True
        alert.false_positive_reason = false_positive_reason.strip() or note.strip() or "مثبت کاذب تاییدشده توسط تحلیلگر Tier 1"
        alert.acknowledged_by = user.id
    elif action == "escalate_tier2":
        alert.tier = 2
        alert.status = "escalated"
        alert.escalated_at = now_utc
        alert.assigned_to = alert.assigned_to or user.id
        if create_incident_on_escalate:
            ev = alert.evidence_json or {}
            hosts = [h[0] if isinstance(h, (list, tuple)) else str(h) for h in (ev.get("top_hosts") or [])]
            users = [u[0] if isinstance(u, (list, tuple)) else str(u) for u in (ev.get("top_users") or [])]
            ips = [x[0] if isinstance(x, (list, tuple)) else str(x) for x in (ev.get("top_src_ips") or [])]
            inc = SecurityIncident(
                organization_id=alert.organization_id,
                title=f"رخداد Tier 2: {alert.title}",
                description=f"ارتقا از هشدار {alert.id} — {note or alert.limitations_fa}",
                severity=alert.severity,
                status="triaging",
                alert_ids_json=[alert.id],
                recommended_action_fa="بررسی شواهد، جداسازی پیشنهادی دارایی و تایید انسانی قبل از هرگونه اقدام",
                rollback_plan_fa="بازگردانی دستی تنظیمات قبلی پس از تایید تیم امنیت",
                created_by=user.id,
                assigned_to=user.id,
                started_at=now_utc,
                escalated_at=now_utc,
                mtta_minutes=round(
                    max(0.0, (now_utc - (alert.created_at.replace(tzinfo=timezone.utc) if alert.created_at.tzinfo is None else alert.created_at)).total_seconds() / 60.0),
                    2,
                ),
                blast_radius_json={
                    "affected_hosts": hosts,
                    "affected_users": users,
                    "source_ips": ips,
                    "sample_events": alert.sample_count,
                },
                timeline_json=[
                    {"at": now_utc.isoformat(), "actor": user.username, "event": "Escalated from Tier 1 Alert to Tier 2 Incident"}
                ],
            )
            db.add(inc)
            db.flush()
            escalated_incident_id = inc.id
    elif action == "resolve":
        alert.status = "resolved"
    elif action == "add_note":
        pass
    else:
        raise ValueError(f"Unsupported triage action: {action}")

    alert.notes_json = notes
    db.commit()
    db.refresh(alert)
    return {
        "id": alert.id,
        "status": alert.status,
        "tier": alert.tier,
        "false_positive": alert.false_positive,
        "false_positive_reason": alert.false_positive_reason,
        "duplicate_count": alert.duplicate_count,
        "escalated_incident_id": escalated_incident_id,
        "notes": alert.notes_json,
    }


def update_incident_lifecycle(
    db: Session,
    inc: SecurityIncident,
    user: User,
    *,
    status: Optional[str] = None,
    hypothesis_notes_fa: Optional[str] = None,
    final_report_fa: Optional[str] = None,
    evidence_item: Optional[dict[str, Any]] = None,
    resolution_note: Optional[str] = None,
) -> dict[str, Any]:
    now_utc = datetime.now(timezone.utc)
    created_dt = inc.created_at.replace(tzinfo=timezone.utc) if inc.created_at and inc.created_at.tzinfo is None else (inc.created_at or now_utc)
    tl = list(inc.timeline_json or [])

    if inc.started_at is None:
        inc.started_at = now_utc
    if inc.mtta_minutes is None:
        inc.mtta_minutes = round(max(0.0, (now_utc - created_dt).total_seconds() / 60.0), 2)

    if status and status in ("open", "triaging", "investigating", "contained_proposal", "resolved", "closed"):
        inc.status = status
        tl.append({"at": now_utc.isoformat(), "actor": user.username, "event": f"وضعیت به {status} تغییر یافت"})
        if status in ("resolved", "closed"):
            inc.closed_at = now_utc
            inc.mttr_minutes = round(max(0.0, (now_utc - created_dt).total_seconds() / 60.0), 2)

    if hypothesis_notes_fa is not None:
        inc.hypothesis_notes_fa = hypothesis_notes_fa
    if final_report_fa is not None:
        inc.final_report_fa = final_report_fa
    if resolution_note is not None:
        inc.resolution_note = resolution_note
    if evidence_item:
        evs = list(inc.evidence_json or [])
        evs.append({**evidence_item, "added_by": user.username, "at": now_utc.isoformat()})
        inc.evidence_json = evs
        tl.append({"at": now_utc.isoformat(), "actor": user.username, "event": "شاهد جدید ثبت شد"})

    inc.timeline_json = tl
    db.commit()
    db.refresh(inc)
    return {
        "id": inc.id,
        "status": inc.status,
        "mtta_minutes": inc.mtta_minutes,
        "mttr_minutes": inc.mttr_minutes,
        "blast_radius": inc.blast_radius_json,
        "hypothesis_notes_fa": inc.hypothesis_notes_fa,
        "final_report_fa": inc.final_report_fa,
        "evidence": inc.evidence_json,
        "timeline": inc.timeline_json,
    }


def classify_security_dataset(
    db: Session,
    ds: Dataset,
    user: User,
    *,
    text_column: Optional[str] = None,
    limit: int = 500,
) -> dict[str, Any]:
    """Run bilingual NLP/Regex classifier & 15 threat categories across an uploaded Excel/Log dataset."""
    sec_ctx = build_data_security_context(db, user, ds.id)
    cols_meta = warehouse.describe_table(ds.warehouse_table)
    sub_sql, params, vis_cols = build_security_projection_and_where(ds.warehouse_table, cols_meta, sec_ctx)
    custom_rules = load_custom_security_rules(db, user.organization_id)

    chosen_col = text_column if (text_column and text_column in vis_cols) else None
    if not chosen_col:
        for cand in vis_cols:
            if any(k in cand.lower() for k in ("status", "message", "desc", "detail", "threat", "note", "وضعیت", "شرح", "پیام", "توضیح")):
                chosen_col = cand
                break
        if not chosen_col and vis_cols:
            chosen_col = vis_cols[0]

    safe_lim = max(1, min(int(limit), 5000))
    res = warehouse.execute_query(
        f"SELECT * FROM ({sub_sql}) AS _sec_ds LIMIT {safe_lim}",
        params,
        limit=safe_lim,
    )
    rows = res["rows"]
    cat_counter: Counter[str] = Counter()
    status_counter: Counter[str] = Counter()
    samples: list[dict[str, Any]] = []

    for idx, r in enumerate(rows):
        combined_text = str(r.get(chosen_col) or "") if chosen_col else ""
        if not combined_text.strip():
            combined_text = " ".join(str(v) for v in r.values() if v is not None)
        out = classify_security_text(combined_text, custom_rules=custom_rules)
        cat_counter[out["threat_category"]] += 1
        status_counter[out["threat_status"]] += 1
        if len(samples) < 25:
            samples.append(
                {
                    "row_index": idx + 1,
                    "source_text": combined_text[:180],
                    "threat_category": out["threat_category"],
                    "threat_category_fa": out["threat_category_fa"],
                    "threat_status": out["threat_status"],
                    "extraction_reason": out["extraction_reason"],
                    "cve_mentioned": out["cve_mentioned"],
                }
            )

    return {
        "dataset_id": ds.id,
        "text_column": chosen_col,
        "rows_evaluated": len(rows),
        "category_breakdown": [
            {"key": k, "label_fa": CATEGORY_LABEL_MAP.get(k, k), "count": c}
            for k, c in cat_counter.most_common()
        ],
        "status_breakdown": dict(status_counter),
        "samples": samples,
    }
