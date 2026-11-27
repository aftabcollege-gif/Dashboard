"""
Cross-Domain Process & Security Correlation Engine (Phase 4):
- Correlates process case traces with normalized security events strictly via traceable keys
  (username / actor, hostname, device, src_ip/dst_ip) + configurable time window
- Reports match quality, unmatched cases, and ambiguous multi-matches transparently
- Produces a shared chronological timeline WITHOUT claiming proven causation
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from sqlalchemy.orm import Session

from app.engines.process_intelligence import _fetch_process_rows, _parse_ts, _to_float, infer_event_log_mapping
from app.models.data import Dataset
from app.models.identity import User
from app.models.intelligence import SecurityEvent


def correlate_process_and_security(
    db: Session,
    user: User,
    process_dataset: Dataset,
    *,
    correlation_key: str = "username",  # username | hostname | ip
    window_minutes: int = 120,
    process_field_override: Optional[str] = None,
) -> dict[str, Any]:
    inferred = infer_event_log_mapping(db, process_dataset, user)
    mapping = dict(inferred["mapping"])

    case_col = mapping.get("case_id")
    act_col = mapping.get("activity")
    ts_col = mapping.get("timestamp") or mapping.get("start_time")

    if not case_col or not act_col or not ts_col:
        return {
            "status": "needs_mapping",
            "error_fa": "برای همبستگی زمانی، دیتاست فرآیند باید دارای ستون‌های Case ID، Activity و Timestamp باشد.",
        }

    # Resolve join key column in process dataset
    proc_key_col = process_field_override
    if not proc_key_col:
        if correlation_key == "username":
            proc_key_col = mapping.get("actor")
        else:
            avail = [c["field_name"] for c in inferred["available_columns"]]
            hints = ("hostname", "host", "device", "machine") if correlation_key == "hostname" else ("ip", "src_ip", "ip_address")
            proc_key_col = next((c for c in avail if any(h in c.lower() for h in hints)), None)

    if not proc_key_col:
        return {
            "status": "missing_correlation_key",
            "correlation_key": correlation_key,
            "error_fa": f"در دیتاست فرآیند هیچ ستون قابل تطبیقی برای کلید «{correlation_key}» یافت نشد.",
            "available_columns": inferred["available_columns"],
        }

    # Load process rows under RLS/CLS + security events under org scope
    extra_map = dict(mapping)
    extra_map["_corr_key"] = proc_key_col
    proc_rows, total_proc = _fetch_process_rows(db, process_dataset, user, extra_map)
    sec_events = (
        db.query(SecurityEvent)
        .filter(SecurityEvent.organization_id == user.organization_id)
        .order_by(SecurityEvent.event_time.asc())
        .limit(20_000)
        .all()
    )

    if not sec_events:
        return {
            "status": "no_security_events",
            "error_fa": "هیچ رویداد امنیتی نرمال‌شده‌ای برای سازمان ثبت نشده است.",
        }

    # Index security events by normalized key
    sec_by_key: dict[str, list[SecurityEvent]] = {}
    for ev in sec_events:
        keys = []
        if correlation_key == "username" and ev.username:
            keys.append(ev.username.strip().lower())
        elif correlation_key == "hostname" and (ev.hostname or ev.device):
            if ev.hostname:
                keys.append(ev.hostname.strip().lower())
            if ev.device:
                keys.append(ev.device.strip().lower())
        elif correlation_key == "ip":
            if ev.src_ip:
                keys.append(ev.src_ip.strip().lower())
            if ev.dst_ip:
                keys.append(ev.dst_ip.strip().lower())
        for k in set(keys):
            sec_by_key.setdefault(k, []).append(ev)

    win = timedelta(minutes=max(1, min(int(window_minutes), 10080)))

    matched_cases: set[str] = set()
    unmatched_cases: set[str] = set()
    matched_sec_ids: set[str] = set()
    ambiguous_matches = 0
    timeline_events: list[dict[str, Any]] = []
    correlations: list[dict[str, Any]] = []

    wait_col = mapping.get("wait_hours")
    sla_col = mapping.get("sla_hours")

    for r in proc_rows:
        cid = str(r.get(case_col) or "").strip()
        act = str(r.get(act_col) or "").strip()
        p_dt = _parse_ts(r.get(ts_col))
        pkey = str(r.get(proc_key_col) or "").strip().lower()
        if not cid or not p_dt:
            continue

        wait_val = _to_float(r.get(wait_col)) if wait_col else None
        sla_val = _to_float(r.get(sla_col)) if sla_col else None
        sla_breach = bool(wait_val is not None and sla_val is not None and wait_val > sla_val)

        cands = sec_by_key.get(pkey, []) if pkey else []
        nearby = [
            ev for ev in cands
            if abs((ev.event_time.replace(tzinfo=timezone.utc) if ev.event_time.tzinfo is None else ev.event_time) - p_dt) <= win
        ]
        if not nearby:
            unmatched_cases.add(cid)
            continue

        matched_cases.add(cid)
        if len(nearby) > 1:
            ambiguous_matches += 1

        timeline_events.append({
            "timestamp": p_dt.isoformat(),
            "domain": "process",
            "case_id": cid,
            "activity": act,
            "key": pkey,
            "wait_hours": wait_val,
            "sla_breached": sla_breach,
            "summary": f"پرونده {cid} — گام «{act}»" + (" (تأخیر SLA)" if sla_breach else ""),
        })

        for ev in nearby[:5]:
            ev_dt = ev.event_time.replace(tzinfo=timezone.utc) if ev.event_time.tzinfo is None else ev.event_time
            delta_min = round((ev_dt - p_dt).total_seconds() / 60.0, 1)
            if ev.id not in matched_sec_ids:
                matched_sec_ids.add(ev.id)
                timeline_events.append({
                    "timestamp": ev_dt.isoformat(),
                    "domain": "security",
                    "event_id": ev.id,
                    "action": ev.action,
                    "severity": ev.severity,
                    "event_type": ev.event_type,
                    "key": pkey,
                    "summary": f"رویداد امنیتی {ev.action} ({ev.severity}) روی کلید «{pkey}»",
                })
            correlations.append({
                "case_id": cid,
                "activity": act,
                "process_time": p_dt.isoformat(),
                "security_event_id": ev.id,
                "security_time": ev_dt.isoformat(),
                "security_action": ev.action,
                "security_severity": ev.severity,
                "match_key": correlation_key,
                "match_value": pkey,
                "delta_minutes": delta_min,
                "process_sla_breached": sla_breach,
            })

    timeline_events.sort(key=lambda x: x["timestamp"])
    total_cases_seen = len(matched_cases | unmatched_cases)

    return {
        "status": "ok",
        "process_dataset_id": process_dataset.id,
        "process_dataset_name": process_dataset.name,
        "correlation_key": correlation_key,
        "process_key_column": proc_key_col,
        "window_minutes": window_minutes,
        "match_quality": {
            "total_process_cases_evaluated": total_cases_seen,
            "matched_process_cases": len(matched_cases),
            "unmatched_process_cases": len(unmatched_cases - matched_cases),
            "matched_security_events": len(matched_sec_ids),
            "ambiguous_multi_event_matches": ambiguous_matches,
            "match_rate_pct": round(len(matched_cases) * 100.0 / max(total_cases_seen, 1), 1),
        },
        "correlations": correlations[:100],
        "shared_timeline": timeline_events[:150],
        "causation_disclaimer_fa": "هم‌زمانی و همبستگی زمانی بر اساس کلید مشترک و پنجره زمانی صرفاً نشانگر هم‌رخدادی مشاهداتی است و به‌هیچ‌وجه رابطه علت و معلولی قطعی را اثبات نمی‌کند.",
    }
