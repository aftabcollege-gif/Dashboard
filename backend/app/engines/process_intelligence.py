"""
Process Intelligence Engine (Phase 2):
- Schema-agnostic Event Log column mapping with confidence scores & user confirmation
- Trace reconstruction (case_id + timestamp), variants, skips, self-loops & rework
- Clear Wait / Processing / Cycle / Lead Time separation with imperfect-data diagnostics
- SLA breach tracking & transparent multi-factor composite bottleneck scoring
- Statistical correlation-labeled root-cause hypotheses ("عامل مرتبط / فرضیه")
- Permission-enforced case trace drill-down & reproducible corrective action before/after comparison
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import math
import statistics
from typing import Any, Optional
from sqlalchemy.orm import Session

from app.core.rls_cls import (
    DataSecurityContext,
    build_data_security_context,
    build_security_projection_and_where,
)
from app.engines.warehouse import warehouse
from app.models.data import Dataset, DatasetField
from app.models.identity import User
from app.models.intelligence import ProcessCorrectiveAction, ProcessDatasetMapping

MAX_EVENTS_ANALYZED = 25_000

MAPPING_FIELD_PATTERNS: dict[str, tuple[tuple[str, float], ...]] = {
    "case_id": (
        ("case_id", 0.98), ("request_code", 0.96), ("trace_id", 0.96), ("process_instance", 0.95),
        ("ticket_id", 0.90), ("request_id", 0.88), ("order_id", 0.80), ("کد درخواست", 0.95), ("شناسه پرونده", 0.96),
    ),
    "activity": (
        ("step_name", 0.98), ("activity", 0.98), ("task_name", 0.95), ("stage_name", 0.92),
        ("stage", 0.88), ("step_id", 0.85), ("step", 0.82), ("گام", 0.94), ("مرحله", 0.94), ("فعالیت", 0.96),
    ),
    "timestamp": (
        ("event_time", 0.98), ("timestamp", 0.96), ("occurred_at", 0.94), ("step_date", 0.92),
        ("created_at", 0.85), ("start_time", 0.84), ("date", 0.72), ("تاریخ", 0.85), ("زمان", 0.85),
    ),
    "start_time": (
        ("start_time", 0.98), ("started_at", 0.98), ("begin_time", 0.92), ("شروع", 0.90),
    ),
    "end_time": (
        ("end_time", 0.98), ("completed_at", 0.96), ("finished_at", 0.95), ("closed_at", 0.90), ("پایان", 0.90),
    ),
    "wait_hours": (
        ("wait_hours", 0.98), ("wait_time", 0.92), ("duration_hours", 0.88), ("queue_hours", 0.90), ("زمان انتظار", 0.94),
    ),
    "sla_hours": (
        ("sla_hours", 0.98), ("sla_target", 0.92), ("sla_limit", 0.92), ("sla", 0.82), ("سقف مجاز", 0.90),
    ),
    "actor": (
        ("assignee", 0.98), ("actor", 0.97), ("owner", 0.90), ("handler", 0.88), ("agent", 0.86),
        ("username", 0.82), ("کارشناس", 0.92), ("مسئول", 0.92), ("کاربر", 0.85),
    ),
    "department": (
        ("department", 0.98), ("unit", 0.88), ("team", 0.86), ("org_unit", 0.92), ("واحد", 0.95), ("دپارتمان", 0.95),
    ),
    "status": (
        ("status", 0.98), ("state", 0.88), ("outcome", 0.84), ("وضعیت", 0.95),
    ),
    "priority": (
        ("priority", 0.98), ("urgency", 0.92), ("severity", 0.82), ("اولویت", 0.95),
    ),
    "rework_flag": (
        ("rework", 0.98), ("is_rework", 0.98), ("loop_flag", 0.92), ("returned", 0.85), ("دوباره‌کاری", 0.95), ("برگشت", 0.88),
    ),
}


def _parse_ts(val: Any) -> Optional[datetime]:
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        return val.replace(tzinfo=timezone.utc) if val.tzinfo is None else val.astimezone(timezone.utc)
    s = str(val).strip()
    if not s:
        return None
    for fmt in (
        None,
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d",
    ):
        try:
            if fmt is None:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            else:
                dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
        except Exception:
            continue
    return None


def _to_float(val: Any) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        f = float(val)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except Exception:
        return None


def _to_bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val != 0
    return str(val or "").strip().lower() in ("1", "true", "yes", "y", "rework", "returned", "بله")


def _visible_field_names(db: Session, ds: Dataset, sec_ctx: DataSecurityContext) -> list[dict[str, str]]:
    cols = warehouse.describe_table(ds.warehouse_table)
    db_fields = {
        f.field_name: f.display_name
        for f in db.query(DatasetField).filter(DatasetField.dataset_id == ds.id).all()
    }
    return [
        {
            "field_name": c["column_name"],
            "display_name": db_fields.get(c["column_name"], c["column_name"]),
            "type": str(c.get("column_type", "")),
        }
        for c in cols
        if c["column_name"] not in sec_ctx.hidden_columns
    ]


def infer_event_log_mapping(
    db: Session,
    ds: Dataset,
    user: User,
) -> dict[str, Any]:
    """Infer Event Log columns (Case ID, Activity, Timestamp, etc.) with confidence scores and persisted overrides."""
    sec_ctx = build_data_security_context(db, user, ds.id)
    vis_cols = _visible_field_names(db, ds, sec_ctx)
    vis_names = {c["field_name"] for c in vis_cols}

    suggested: dict[str, Optional[str]] = {k: None for k in MAPPING_FIELD_PATTERNS}
    confidences: dict[str, float] = {k: 0.0 for k in MAPPING_FIELD_PATTERNS}
    used_cols: set[str] = set()

    for role, patterns in MAPPING_FIELD_PATTERNS.items():
        best_col = None
        best_score = 0.0
        for col in vis_cols:
            cname = col["field_name"]
            if cname in used_cols and role not in ("timestamp", "start_time"):
                continue
            hay = f"{cname} {col['display_name']}".lower()
            for pat, score in patterns:
                if cname.lower() == pat:
                    cand_score = score
                elif pat in hay:
                    cand_score = round(score * 0.88, 2)
                else:
                    continue
                if cand_score > best_score:
                    best_score = cand_score
                    best_col = cname
        if best_col:
            suggested[role] = best_col
            confidences[role] = best_score
            if role in ("case_id", "activity", "start_time", "end_time"):
                used_cols.add(best_col)

    # Check persisted user mapping
    saved = (
        db.query(ProcessDatasetMapping)
        .filter(
            ProcessDatasetMapping.organization_id == user.organization_id,
            ProcessDatasetMapping.dataset_id == ds.id,
        )
        .first()
    )
    eff_mapping = dict(suggested)
    ref_seq: list[str] = []
    user_confirmed = False
    if saved:
        for role, colname in (saved.mapping_json or {}).items():
            if colname is None or colname in vis_names:
                eff_mapping[role] = colname
                if colname:
                    confidences[role] = 1.0
        ref_seq = list(saved.reference_sequence_json or [])
        user_confirmed = bool(saved.user_confirmed)

    if not eff_mapping.get("timestamp") and eff_mapping.get("start_time"):
        eff_mapping["timestamp"] = eff_mapping["start_time"]
        confidences["timestamp"] = max(confidences.get("timestamp", 0.0), confidences.get("start_time", 0.0))

    req_roles = ("case_id", "activity", "timestamp")
    missing_req = [r for r in req_roles if not eff_mapping.get(r)]
    low_conf = [r for r in req_roles if eff_mapping.get(r) and confidences.get(r, 0.0) < 0.75]

    return {
        "dataset_id": ds.id,
        "dataset_name": ds.name,
        "mapping": eff_mapping,
        "confidences": confidences,
        "reference_sequence": ref_seq,
        "user_confirmed": user_confirmed,
        "needs_confirmation": (not user_confirmed) and (bool(missing_req) or bool(low_conf) or any(confidences.get(r, 0.0) < 0.90 for r in req_roles)),
        "missing_required_roles": missing_req,
        "low_confidence_roles": low_conf,
        "available_columns": vis_cols,
    }


def save_event_log_mapping(
    db: Session,
    ds: Dataset,
    user: User,
    mapping: dict[str, Optional[str]],
    reference_sequence: Optional[list[str]] = None,
    confirmed: bool = True,
) -> dict[str, Any]:
    sec_ctx = build_data_security_context(db, user, ds.id)
    vis_names = {c["field_name"] for c in _visible_field_names(db, ds, sec_ctx)}
    cleaned: dict[str, Optional[str]] = {}
    for role in MAPPING_FIELD_PATTERNS:
        val = mapping.get(role)
        if val:
            if val not in vis_names:
                raise ValueError(f"ستون '{val}' در داده‌های قابل دسترس یافت نشد یا توسط CLS مخفی است")
            cleaned[role] = val
        else:
            cleaned[role] = None

    row = (
        db.query(ProcessDatasetMapping)
        .filter(
            ProcessDatasetMapping.organization_id == user.organization_id,
            ProcessDatasetMapping.dataset_id == ds.id,
        )
        .first()
    )
    now = datetime.now(timezone.utc)
    if not row:
        row = ProcessDatasetMapping(
            organization_id=user.organization_id,
            dataset_id=ds.id,
        )
        db.add(row)
    row.mapping_json = cleaned
    if reference_sequence is not None:
        row.reference_sequence_json = [str(s).strip() for s in reference_sequence if str(s).strip()]
    row.user_confirmed = bool(confirmed)
    row.confidence_json = {k: (1.0 if v else 0.0) for k, v in cleaned.items()}
    row.confirmed_by = user.id if confirmed else None
    row.confirmed_at = now if confirmed else None
    db.commit()
    return infer_event_log_mapping(db, ds, user)


def _fetch_process_rows(
    db: Session,
    ds: Dataset,
    user: User,
    mapping: dict[str, Optional[str]],
    *,
    date_start: Optional[str] = None,
    date_end: Optional[str] = None,
    filters: Optional[dict[str, Any]] = None,
) -> tuple[list[dict[str, Any]], int]:
    sec_ctx = build_data_security_context(db, user, ds.id)
    cols_meta = warehouse.describe_table(ds.warehouse_table)
    sub_sql, sub_params, vis_cols = build_security_projection_and_where(ds.warehouse_table, cols_meta, sec_ctx)

    # Project only mapped columns (plus any filtered column) for performance & CLS isolation
    selected_cols = []
    seen = set()
    for col in list(mapping.values()) + list((filters or {}).keys()):
        if col and col in vis_cols and col not in seen:
            selected_cols.append(f'"{col}"')
            seen.add(col)
    if not selected_cols:
        selected_cols = [f'"{c}"' for c in vis_cols[:12]]

    where_clauses: list[str] = []
    params: list[Any] = list(sub_params)
    ts_col = mapping.get("timestamp") or mapping.get("start_time")
    if ts_col and ts_col in vis_cols:
        if date_start:
            where_clauses.append(f'TRY_CAST("{ts_col}" AS TIMESTAMP) >= TRY_CAST(? AS TIMESTAMP)')
            params.append(date_start)
        if date_end:
            where_clauses.append(f'TRY_CAST("{ts_col}" AS TIMESTAMP) <= TRY_CAST(? AS TIMESTAMP)')
            params.append(date_end)

    for fk, fv in (filters or {}).items():
        if fk in vis_cols and fv is not None and fv != "":
            where_clauses.append(f'CAST("{fk}" AS VARCHAR) = ?')
            params.append(str(fv))

    where_sql = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    cnt_res = warehouse.execute_query(f"SELECT COUNT(*) AS cnt FROM ({sub_sql}) _p{where_sql}", params)
    total_matching = int((cnt_res["rows"][0] if cnt_res["rows"] else {}).get("cnt") or 0)

    sql = f"SELECT {', '.join(selected_cols)} FROM ({sub_sql}) _p{where_sql} LIMIT {MAX_EVENTS_ANALYZED}"
    res = warehouse.execute_query(sql, params)
    return res["rows"], total_matching


def analyze_process_intelligence(
    db: Session,
    ds: Dataset,
    user: User,
    *,
    mapping_override: Optional[dict[str, Optional[str]]] = None,
    reference_sequence_override: Optional[list[str]] = None,
    date_start: Optional[str] = None,
    date_end: Optional[str] = None,
    filters: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Full Process Mining analysis:
    - Trace reconstruction per case_id
    - Process graph (nodes + transitions)
    - Variants & deviations
    - Rework, self-loops & reference-based skips
    - Wait / Processing / Cycle / Lead Time with explicit basis & quality warnings
    - SLA breach rates & multi-factor bottleneck scoring
    - Statistical correlation-labeled hypotheses ("عامل مرتبط / فرضیه")
    """
    inferred = infer_event_log_mapping(db, ds, user)
    mapping = dict(inferred["mapping"])
    if mapping_override:
        for k, v in mapping_override.items():
            if k in mapping:
                mapping[k] = v

    ref_seq = list(reference_sequence_override) if reference_sequence_override is not None else list(inferred["reference_sequence"])

    case_col = mapping.get("case_id")
    act_col = mapping.get("activity")
    ts_col = mapping.get("timestamp")
    start_col = mapping.get("start_time")
    end_col = mapping.get("end_time")
    wait_col = mapping.get("wait_hours")
    sla_col = mapping.get("sla_hours")
    actor_col = mapping.get("actor")
    dept_col = mapping.get("department")
    status_col = mapping.get("status")
    prio_col = mapping.get("priority")
    rework_col = mapping.get("rework_flag")

    if not case_col or not act_col:
        return {
            "status": "needs_mapping",
            "mapping_info": inferred,
            "error_fa": "برای بازسازی مسیر پرونده‌ها، حداقل ستون شناسه پرونده (Case ID) و ستون فعالیت (Activity) لازم است.",
        }

    rows, total_matching = _fetch_process_rows(
        db, ds, user, mapping, date_start=date_start, date_end=date_end, filters=filters
    )
    if not rows:
        return {
            "status": "empty",
            "mapping_info": inferred,
            "summary": {"total_events": 0, "total_cases": 0},
            "error_fa": "هیچ رکوردی در بازه/فیلتر انتخاب‌شده یافت نشد.",
        }

    # Group events into cases
    cases_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    missing_case_events = 0
    missing_ts_events = 0
    min_dt: Optional[datetime] = None
    max_dt: Optional[datetime] = None

    for idx, r in enumerate(rows):
        cid = str(r.get(case_col) or "").strip()
        act = str(r.get(act_col) or "").strip()
        if not cid or not act:
            missing_case_events += 1
            continue
        ts_dt = _parse_ts(r.get(ts_col)) if ts_col else None
        st_dt = _parse_ts(r.get(start_col)) if start_col else ts_dt
        en_dt = _parse_ts(r.get(end_col)) if end_col else None
        eff_dt = ts_dt or st_dt or en_dt
        if eff_dt is None:
            missing_ts_events += 1
        else:
            if min_dt is None or eff_dt < min_dt:
                min_dt = eff_dt
            if max_dt is None or eff_dt > max_dt:
                max_dt = eff_dt

        cases_events[cid].append({
            "seq_idx": idx,
            "activity": act,
            "ts": eff_dt,
            "start_dt": st_dt,
            "end_dt": en_dt,
            "wait_hours": _to_float(r.get(wait_col)) if wait_col else None,
            "sla_hours": _to_float(r.get(sla_col)) if sla_col else None,
            "actor": str(r.get(actor_col) or "").strip() if actor_col else "",
            "department": str(r.get(dept_col) or "").strip() if dept_col else "",
            "status": str(r.get(status_col) or "").strip() if status_col else "",
            "priority": str(r.get(prio_col) or "").strip() if prio_col else "",
            "rework_flag": _to_bool(r.get(rework_col)) if rework_col else False,
        })

    # Reconstruct each case trace
    variant_stats: dict[tuple[str, ...], dict[str, Any]] = {}
    activity_stats: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "activity": "",
        "event_count": 0,
        "cases": set(),
        "wait_hours_list": [],
        "proc_hours_list": [],
        "sla_breaches": 0,
        "sla_evaluated": 0,
        "rework_events": 0,
        "self_loops": 0,
        "actors": set(),
    })
    edge_stats: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {
        "source": "",
        "target": "",
        "count": 0,
        "cases": set(),
        "transition_hours": [],
    })

    case_records: list[dict[str, Any]] = []
    out_of_order_cases = 0
    single_event_cases = 0
    total_rework_cases = 0
    total_self_loop_cases = 0
    total_skip_cases = 0
    total_sla_breach_cases = 0
    total_sla_eval_cases = 0

    all_case_cycle_hours: list[float] = []
    all_case_wait_hours: list[float] = []
    all_case_proc_hours: list[float] = []

    stratum_records: list[dict[str, Any]] = []

    for cid, evs in cases_events.items():
        # Check out-of-order relative to file order if timestamps exist
        orig_ts = [e["ts"] for e in evs if e["ts"] is not None]
        if len(orig_ts) >= 2 and any(orig_ts[i] > orig_ts[i + 1] for i in range(len(orig_ts) - 1)):
            out_of_order_cases += 1
        evs_sorted = sorted(evs, key=lambda e: (e["ts"] or datetime.max.replace(tzinfo=timezone.utc), e["seq_idx"]))

        if len(evs_sorted) == 1:
            single_event_cases += 1

        path_tuple = tuple(e["activity"] for e in evs_sorted)
        seen_acts: set[str] = set()
        case_rework_acts: set[str] = set()
        case_self_loops = 0
        case_wait_sum = 0.0
        has_case_wait = False
        case_proc_sum = 0.0
        has_case_proc = False
        case_sla_breached = False
        case_sla_eval = False
        case_depts: set[str] = set()
        case_actors: set[str] = set()
        case_prios: set[str] = set()

        prev_ev: Optional[dict[str, Any]] = None
        for ev in evs_sorted:
            act = ev["activity"]
            astat = activity_stats[act]
            astat["activity"] = act
            astat["event_count"] += 1
            astat["cases"].add(cid)
            if ev["actor"]:
                astat["actors"].add(ev["actor"])
                case_actors.add(ev["actor"])
            if ev["department"]:
                case_depts.add(ev["department"])
            if ev["priority"]:
                case_prios.add(ev["priority"])

            # Rework (repeat within same trace or explicit rework flag)
            if act in seen_acts or ev["rework_flag"]:
                case_rework_acts.add(act)
                astat["rework_events"] += 1
            seen_acts.add(act)

            # Self-loop (A -> A consecutive)
            if prev_ev is not None and prev_ev["activity"] == act:
                case_self_loops += 1
                astat["self_loops"] += 1

            # Wait time calculation (explicit wait_hours column OR timestamp gap from previous event end/ts)
            step_wait: Optional[float] = ev["wait_hours"]
            if step_wait is None and prev_ev is not None:
                prev_anchor = prev_ev["end_dt"] or prev_ev["ts"]
                curr_anchor = ev["start_dt"] or ev["ts"]
                if prev_anchor and curr_anchor:
                    delta_h = (curr_anchor - prev_anchor).total_seconds() / 3600.0
                    if delta_h >= 0:
                        step_wait = round(delta_h, 3)

            if step_wait is not None:
                astat["wait_hours_list"].append(step_wait)
                case_wait_sum += step_wait
                has_case_wait = True

            # Processing time calculation (strictly when both start_dt and end_dt exist)
            if ev["start_dt"] is not None and ev["end_dt"] is not None:
                proc_h = (ev["end_dt"] - ev["start_dt"]).total_seconds() / 3600.0
                if proc_h >= 0:
                    astat["proc_hours_list"].append(proc_h)
                    case_proc_sum += proc_h
                    has_case_proc = True

            # SLA check
            if ev["sla_hours"] is not None and step_wait is not None:
                astat["sla_evaluated"] += 1
                case_sla_eval = True
                if step_wait > ev["sla_hours"]:
                    astat["sla_breaches"] += 1
                    case_sla_breached = True
            elif ev["status"].lower() in ("overdue", "breached", "delayed", "تأخیر", "نقض"):
                astat["sla_evaluated"] += 1
                case_sla_eval = True
                astat["sla_breaches"] += 1
                case_sla_breached = True

            # Edge transition
            if prev_ev is not None:
                ekey = (prev_ev["activity"], act)
                estat = edge_stats[ekey]
                estat["source"] = prev_ev["activity"]
                estat["target"] = act
                estat["count"] += 1
                estat["cases"].add(cid)
                if step_wait is not None:
                    estat["transition_hours"].append(step_wait)

            prev_ev = ev

        # Cycle / Lead time
        ts_list = [e["ts"] for e in evs_sorted if e["ts"] is not None]
        end_list = [e["end_dt"] or e["ts"] for e in evs_sorted if (e["end_dt"] or e["ts"]) is not None]
        case_cycle_h: Optional[float] = None
        if ts_list and end_list and (len(evs_sorted) >= 2 or end_list[-1] > ts_list[0]):
            case_cycle_h = round(max(0.0, (max(end_list) - min(ts_list)).total_seconds() / 3600.0), 3)
        elif has_case_wait:
            case_cycle_h = round(case_wait_sum, 3)

        if case_cycle_h is not None:
            all_case_cycle_hours.append(case_cycle_h)
        if has_case_wait:
            all_case_wait_hours.append(round(case_wait_sum, 3))
        if has_case_proc:
            all_case_proc_hours.append(round(case_proc_sum, 3))

        # Reference sequence skip check (ONLY if reference_sequence is provided!)
        skipped_steps = [s for s in ref_seq if s not in seen_acts] if ref_seq else []
        if case_rework_acts:
            total_rework_cases += 1
        if case_self_loops > 0:
            total_self_loop_cases += 1
        if skipped_steps:
            total_skip_cases += 1
        if case_sla_eval:
            total_sla_eval_cases += 1
            if case_sla_breached:
                total_sla_breach_cases += 1

        # Variant aggregation
        if path_tuple not in variant_stats:
            variant_stats[path_tuple] = {
                "variant_key": " -> ".join(path_tuple),
                "steps": list(path_tuple),
                "case_count": 0,
                "cycle_hours_list": [],
                "rework_cases": 0,
                "sla_breach_cases": 0,
                "skipped_steps": skipped_steps,
            }
        vs = variant_stats[path_tuple]
        vs["case_count"] += 1
        if case_cycle_h is not None:
            vs["cycle_hours_list"].append(case_cycle_h)
        if case_rework_acts:
            vs["rework_cases"] += 1
        if case_sla_breached:
            vs["sla_breach_cases"] += 1

        case_rec = {
            "case_id": cid,
            "event_count": len(evs_sorted),
            "variant": " -> ".join(path_tuple),
            "cycle_hours": case_cycle_h,
            "wait_hours": round(case_wait_sum, 3) if has_case_wait else None,
            "processing_hours": round(case_proc_sum, 3) if has_case_proc else None,
            "rework_count": len(case_rework_acts),
            "rework_activities": sorted(case_rework_acts),
            "self_loop_count": case_self_loops,
            "skipped_steps": skipped_steps,
            "sla_breached": case_sla_breached,
            "department": next(iter(case_depts), ""),
            "actors": sorted(case_actors),
            "priority": next(iter(case_prios), ""),
            "started_at": min(ts_list).isoformat() if ts_list else None,
            "ended_at": max(end_list).isoformat() if end_list else None,
        }
        case_records.append(case_rec)
        stratum_records.append(case_rec)

    total_cases = len(cases_events)

    # Sort variants by case frequency and flag deviations
    variants_list = sorted(variant_stats.values(), key=lambda x: -x["case_count"])
    dominant_steps = set(variants_list[0]["steps"]) if variants_list else set()
    formatted_variants = []
    for idx, v in enumerate(variants_list[:25]):
        ch = v["cycle_hours_list"]
        is_dev = bool(v["skipped_steps"]) or (bool(ref_seq) and v["steps"] != ref_seq) or (not ref_seq and idx > 0 and v["case_count"] / max(total_cases, 1) < 0.25)
        formatted_variants.append({
            "rank": idx + 1,
            "variant_key": v["variant_key"],
            "steps": v["steps"],
            "case_count": v["case_count"],
            "share_pct": round(v["case_count"] * 100.0 / max(total_cases, 1), 2),
            "avg_cycle_hours": round(sum(ch) / len(ch), 2) if ch else None,
            "rework_rate_pct": round(v["rework_cases"] * 100.0 / max(v["case_count"], 1), 1),
            "sla_breach_pct": round(v["sla_breach_cases"] * 100.0 / max(v["case_count"], 1), 1),
            "skipped_steps": v["skipped_steps"],
            "is_deviant": is_dev,
        })

    # Composite Multi-Factor Bottleneck Ranking
    total_wait_portfolio = sum(sum(a["wait_hours_list"]) for a in activity_stats.values()) or 1.0
    max_avg_wait = max(((sum(a["wait_hours_list"]) / len(a["wait_hours_list"])) for a in activity_stats.values() if a["wait_hours_list"]), default=1.0) or 1.0
    max_volume = max((len(a["cases"]) for a in activity_stats.values()), default=1) or 1
    max_rework_rate = max((a["rework_events"] / max(a["event_count"], 1) for a in activity_stats.values()), default=0.01) or 0.01
    max_cap_load = max((len(a["cases"]) / max(len(a["actors"]), 1) for a in activity_stats.values()), default=1.0) or 1.0

    bottlenecks = []
    for act, a in activity_stats.items():
        avg_w = (sum(a["wait_hours_list"]) / len(a["wait_hours_list"])) if a["wait_hours_list"] else 0.0
        avg_p = (sum(a["proc_hours_list"]) / len(a["proc_hours_list"])) if a["proc_hours_list"] else None
        vol = len(a["cases"])
        rew_rate = a["rework_events"] / max(a["event_count"], 1)
        cycle_share = sum(a["wait_hours_list"]) / total_wait_portfolio
        cap_load = vol / max(len(a["actors"]), 1)

        f_dur = min(1.0, avg_w / max_avg_wait) * 100.0
        f_vol = min(1.0, vol / max_volume) * 100.0
        f_rew = min(1.0, rew_rate / max_rework_rate) * 100.0 if a["rework_events"] > 0 else 0.0
        f_cyc = min(1.0, cycle_share * 2.5) * 100.0
        f_cap = min(1.0, cap_load / max_cap_load) * 100.0

        composite_score = round(
            0.30 * f_dur + 0.20 * f_vol + 0.20 * f_rew + 0.20 * f_cyc + 0.10 * f_cap,
            1,
        )
        bottlenecks.append({
            "activity": act,
            "case_count": vol,
            "event_count": a["event_count"],
            "avg_wait_hours": round(avg_w, 2) if a["wait_hours_list"] else None,
            "max_wait_hours": round(max(a["wait_hours_list"]), 2) if a["wait_hours_list"] else None,
            "avg_processing_hours": round(avg_p, 2) if avg_p is not None else None,
            "rework_events": a["rework_events"],
            "rework_rate_pct": round(rew_rate * 100.0, 1),
            "self_loops": a["self_loops"],
            "sla_breach_pct": round(a["sla_breaches"] * 100.0 / max(a["sla_evaluated"], 1), 1) if a["sla_evaluated"] > 0 else None,
            "actor_count": len(a["actors"]),
            "composite_bottleneck_score": composite_score,
            "factor_breakdown": {
                "duration_score": round(f_dur, 1),
                "volume_score": round(f_vol, 1),
                "rework_score": round(f_rew, 1),
                "cycle_impact_score": round(f_cyc, 1),
                "capacity_load_score": round(f_cap, 1),
                "weights": {"duration": 0.30, "volume": 0.20, "rework": 0.20, "cycle_impact": 0.20, "capacity_load": 0.10},
            },
        })
    bottlenecks.sort(key=lambda x: -x["composite_bottleneck_score"])

    # Graph edges
    process_edges = [
        {
            "source": e["source"],
            "target": e["target"],
            "transition_count": e["count"],
            "case_count": len(e["cases"]),
            "avg_transition_hours": round(sum(e["transition_hours"]) / len(e["transition_hours"]), 2) if e["transition_hours"] else None,
        }
        for e in sorted(edge_stats.values(), key=lambda x: -x["count"])
    ]

    # Root Cause Hypotheses (Explicit Correlation vs Causation guard!)
    hypotheses: list[dict[str, Any]] = []
    overall_avg_cycle = (sum(all_case_cycle_hours) / len(all_case_cycle_hours)) if all_case_cycle_hours else None
    overall_rework_rate = total_rework_cases / max(total_cases, 1)
    time_range_str = (
        f"{min_dt.date().isoformat()} تا {max_dt.date().isoformat()}"
        if min_dt and max_dt else f"{total_cases} پرونده"
    )
    for dim_name, getter in (
        ("department", lambda r: r["department"]),
        ("priority", lambda r: r["priority"]),
        ("actor", lambda r: r["actors"][0] if len(r["actors"]) == 1 else ""),
    ):
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for cr in stratum_records:
            key = getter(cr)
            if key:
                grouped[key].append(cr)
        for gval, grecs in grouped.items():
            if len(grecs) < 3:
                continue
            g_cycles = [r["cycle_hours"] for r in grecs if r["cycle_hours"] is not None]
            if overall_avg_cycle and overall_avg_cycle > 0 and g_cycles:
                g_avg_c = sum(g_cycles) / len(g_cycles)
                ratio = round(g_avg_c / overall_avg_cycle, 2)
                if ratio >= 1.35:
                    hypotheses.append({
                        "label_fa": "عامل مرتبط / فرضیه (همبستگی آماری — غیرعلّی)",
                        "dimension": dim_name,
                        "value": gval,
                        "metric": "avg_cycle_hours",
                        "observed_value": round(g_avg_c, 2),
                        "baseline_value": round(overall_avg_cycle, 2),
                        "ratio": ratio,
                        "sample_count": len(grecs),
                        "time_range": time_range_str,
                        "summary_fa": (
                            f"در «{dim_name} = {gval}» میانگین زمان چرخه {round(g_avg_c, 2)} ساعت است که "
                            f"{ratio} برابر میانگین کل ({round(overall_avg_cycle, 2)} ساعت) می‌باشد (n={len(grecs)})."
                        ),
                        "limitations_fa": "این یافته صرفاً یک همبستگی مشاهده‌شده در زیرگروه داده است و بدون کنترل متغیرهای مداخله‌گر به معنای علیت قطعی نیست.",
                    })
            g_rew = sum(1 for r in grecs if r["rework_count"] > 0) / len(grecs)
            if overall_rework_rate > 0 and g_rew / overall_rework_rate >= 1.5 and sum(1 for r in grecs if r["rework_count"] > 0) >= 2:
                hypotheses.append({
                    "label_fa": "عامل مرتبط / فرضیه (همبستگی آماری — غیرعلّی)",
                    "dimension": dim_name,
                    "value": gval,
                    "metric": "rework_rate",
                    "observed_value": round(g_rew * 100.0, 1),
                    "baseline_value": round(overall_rework_rate * 100.0, 1),
                    "ratio": round(g_rew / overall_rework_rate, 2),
                    "sample_count": len(grecs),
                    "time_range": time_range_str,
                    "summary_fa": (
                        f"نرخ دوباره‌کاری در «{dim_name} = {gval}» برابر {round(g_rew * 100, 1)}% در مقایسه با میانگین "
                        f"{round(overall_rework_rate * 100, 1)}% است (n={len(grecs)})."
                    ),
                    "limitations_fa": "ارتباط آماری نیازمند بررسی کارشناسی و تایید میدانی است.",
                })
    hypotheses.sort(key=lambda h: (-h["ratio"], -h["sample_count"]))

    # Explicit time definitions & imperfect data warnings
    wait_basis = "ستون صریح wait_hours" if wait_col else "اختلاف زمانی بین پایان گام قبلی و شروع گام بعدی"
    proc_status = "computed" if all_case_proc_hours else "needs_data"
    dq_warnings: list[str] = []
    if total_matching > MAX_EVENTS_ANALYZED:
        dq_warnings.append(f"به دلیل حجم بالا، {MAX_EVENTS_ANALYZED:,} رویداد از {total_matching:,} رویداد واکاوی شد.")
    if missing_ts_events > 0:
        dq_warnings.append(f"{missing_ts_events} رویداد فاقد برچسب زمانی معتبر بودند.")
    if out_of_order_cases > 0:
        dq_warnings.append(f"{out_of_order_cases} پرونده با ترتیب زمانی نامنظم بر اساس زمان مرتب‌سازی مجدد شدند.")
    if single_event_cases > 0:
        dq_warnings.append(f"{single_event_cases} پرونده تک‌رویدادی هستند (نشانگر فرآیند ناتمام یا ثبت خلاصه‌شده).")
    if not ref_seq:
        dq_warnings.append("مسیر مرجع (Reference Sequence) تعریف نشده است؛ تشخیص گام‌های پرش‌شده (Skips) نیازمند تعریف مسیر مرجع است.")

    return {
        "status": "ok",
        "dataset_id": ds.id,
        "dataset_name": ds.name,
        "mapping_info": {**inferred, "effective_mapping": mapping, "reference_sequence": ref_seq},
        "summary": {
            "total_events_analyzed": len(rows),
            "total_events_matching": total_matching,
            "total_cases": total_cases,
            "unique_activities": len(activity_stats),
            "unique_variants": len(variant_stats),
            "time_range": time_range_str,
            "avg_cycle_hours": round(sum(all_case_cycle_hours) / len(all_case_cycle_hours), 2) if all_case_cycle_hours else None,
            "median_cycle_hours": round(statistics.median(all_case_cycle_hours), 2) if all_case_cycle_hours else None,
            "avg_wait_hours": round(sum(all_case_wait_hours) / len(all_case_wait_hours), 2) if all_case_wait_hours else None,
            "avg_processing_hours": round(sum(all_case_proc_hours) / len(all_case_proc_hours), 2) if all_case_proc_hours else None,
            "processing_time_status": proc_status,
            "processing_time_note_fa": (
                "محاسبه‌شده از اختلاف end_time و start_time" if proc_status == "computed"
                else "نیازمند هر دو ستون start_time و end_time برای محاسبه مجزای زمان پردازش واقعی است."
            ),
            "wait_time_basis_fa": wait_basis,
            "rework_case_count": total_rework_cases,
            "rework_rate_pct": round(total_rework_cases * 100.0 / max(total_cases, 1), 1),
            "self_loop_case_count": total_self_loop_cases,
            "skip_case_count": total_skip_cases if ref_seq else None,
            "sla_evaluated_cases": total_sla_eval_cases,
            "sla_breach_cases": total_sla_breach_cases,
            "sla_breach_rate_pct": round(total_sla_breach_cases * 100.0 / max(total_sla_eval_cases, 1), 1) if total_sla_eval_cases > 0 else None,
        },
        "data_quality": {
            "missing_case_or_activity_events": missing_case_events,
            "missing_timestamp_events": missing_ts_events,
            "out_of_order_cases": out_of_order_cases,
            "single_event_cases": single_event_cases,
            "warnings_fa": dq_warnings,
        },
        "process_graph": {
            "nodes": bottlenecks,
            "edges": process_edges,
        },
        "variants": formatted_variants,
        "bottlenecks": bottlenecks,
        "hypotheses": hypotheses[:12],
        "cases_preview": sorted(case_records, key=lambda c: -(c["cycle_hours"] or 0.0))[:50],
    }


def list_or_get_case_details(
    db: Session,
    ds: Dataset,
    user: User,
    *,
    case_id: Optional[str] = None,
    activity_filter: Optional[str] = None,
    only_rework: bool = False,
    only_sla_breach: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Permission-enforced drill-down into filtered cases or single case chronological timeline."""
    analysis = analyze_process_intelligence(db, ds, user)
    if analysis.get("status") != "ok":
        return analysis

    mapping = analysis["mapping_info"]["effective_mapping"]
    case_col = mapping["case_id"]
    act_col = mapping["activity"]
    ts_col = mapping.get("timestamp") or mapping.get("start_time")

    if case_id:
        rows, _ = _fetch_process_rows(db, ds, user, mapping, filters={case_col: case_id})
        events = []
        for idx, r in enumerate(rows):
            dt = _parse_ts(r.get(ts_col)) if ts_col else None
            events.append({
                "seq": idx + 1,
                "activity": r.get(act_col),
                "timestamp": dt.isoformat() if dt else r.get(ts_col),
                "actor": r.get(mapping["actor"]) if mapping.get("actor") else None,
                "department": r.get(mapping["department"]) if mapping.get("department") else None,
                "wait_hours": _to_float(r.get(mapping["wait_hours"])) if mapping.get("wait_hours") else None,
                "sla_hours": _to_float(r.get(mapping["sla_hours"])) if mapping.get("sla_hours") else None,
                "status": r.get(mapping["status"]) if mapping.get("status") else None,
                "rework_flag": _to_bool(r.get(mapping["rework_flag"])) if mapping.get("rework_flag") else False,
            })
        events.sort(key=lambda e: (str(e.get("timestamp") or ""), e["seq"]))
        case_summary = next((c for c in analysis["cases_preview"] if c["case_id"] == case_id), None)
        return {
            "case_id": case_id,
            "case_summary": case_summary,
            "events": events,
        }

    # Re-analyze full cases_preview or compute full filtered cases cleanly
    # Note: analyze_process_intelligence returned cases_preview capped at 50; for arbitrary filters we compute directly
    all_rows, _ = _fetch_process_rows(db, ds, user, mapping)
    by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for idx, r in enumerate(all_rows):
        cid = str(r.get(case_col) or "").strip()
        if cid:
            by_case[cid].append({"row": r, "idx": idx})

    matched_cases = []
    for cid, items in by_case.items():
        acts = [str(it["row"].get(act_col) or "").strip() for it in items]
        if activity_filter and activity_filter not in acts:
            continue
        has_rework = len(acts) != len(set(acts)) or (
            bool(mapping.get("rework_flag")) and any(_to_bool(it["row"].get(mapping["rework_flag"])) for it in items)
        )
        if only_rework and not has_rework:
            continue
        w_sum = sum(_to_float(it["row"].get(mapping["wait_hours"])) or 0.0 for it in items) if mapping.get("wait_hours") else None
        sla_breached = False
        if mapping.get("sla_hours") and mapping.get("wait_hours"):
            for it in items:
                wv = _to_float(it["row"].get(mapping["wait_hours"]))
                sv = _to_float(it["row"].get(mapping["sla_hours"]))
                if wv is not None and sv is not None and wv > sv:
                    sla_breached = True
        if only_sla_breach and not sla_breached:
            continue
        matched_cases.append({
            "case_id": cid,
            "event_count": len(items),
            "activities": acts,
            "has_rework": has_rework,
            "sla_breached": sla_breached,
            "wait_hours": round(w_sum, 2) if w_sum is not None else None,
        })

    matched_cases.sort(key=lambda c: (-(c["wait_hours"] or 0.0), -c["event_count"]))
    return {
        "total": len(matched_cases),
        "limit": limit,
        "offset": offset,
        "cases": matched_cases[offset: offset + limit],
    }


def evaluate_corrective_action(
    db: Session,
    ds: Dataset,
    user: User,
    action: ProcessCorrectiveAction,
) -> dict[str, Any]:
    """Reproducible Before/After KPI comparison using identical mapping & saved filters."""
    flt = dict(action.filter_snapshot_json or {})
    before_res = analyze_process_intelligence(
        db, ds, user, date_start=action.baseline_start, date_end=action.baseline_end, filters=flt
    )
    after_res = analyze_process_intelligence(
        db, ds, user, date_start=action.post_start, date_end=action.post_end, filters=flt
    )

    def _extract_kpi(res: dict[str, Any]) -> tuple[Optional[float], int]:
        if res.get("status") != "ok":
            return None, 0
        s = res.get("summary") or {}
        if action.target_activity:
            b = next((x for x in (res.get("bottlenecks") or []) if x["activity"] == action.target_activity), None)
            if b:
                if action.kpi_metric == "avg_wait_hours":
                    return b.get("avg_wait_hours"), int(b.get("case_count") or 0)
                if action.kpi_metric == "sla_breach_pct":
                    return b.get("sla_breach_pct"), int(b.get("case_count") or 0)
                if action.kpi_metric == "rework_rate_pct":
                    return b.get("rework_rate_pct"), int(b.get("case_count") or 0)
                return b.get("composite_bottleneck_score"), int(b.get("case_count") or 0)
        return s.get(action.kpi_metric), int(s.get("total_cases") or 0)

    val_before, n_before = _extract_kpi(before_res)
    val_after, n_after = _extract_kpi(after_res)
    delta_pct = None
    if val_before is not None and val_after is not None and val_before != 0:
        delta_pct = round(((val_after - val_before) / abs(val_before)) * 100.0, 2)

    action.measured_before = val_before
    action.measured_after = val_after
    action.delta_pct = delta_pct
    action.sample_before = n_before
    action.sample_after = n_after
    if val_before is not None and val_after is not None:
        action.status = "evaluated"
    action.evaluation_notes_json = {
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "kpi_metric": action.kpi_metric,
        "target_activity": action.target_activity,
        "baseline_window": [action.baseline_start, action.baseline_end],
        "post_window": [action.post_start, action.post_end],
        "filters_applied": flt,
        "limitations_fa": "مقایسه قبل و بعد با تعریف یکسان شاخص و فیلتر ثابت انجام شد؛ عوامل فصلی یا تغییر ترکیب پرونده‌ها باید توسط تحلیلگر کنترل شود.",
    }
    db.commit()
    return {
        "id": action.id,
        "title": action.title,
        "status": action.status,
        "kpi_metric": action.kpi_metric,
        "target_activity": action.target_activity,
        "measured_before": val_before,
        "measured_after": val_after,
        "delta_pct": delta_pct,
        "sample_before": n_before,
        "sample_after": n_after,
        "evaluation_notes": action.evaluation_notes_json,
    }
