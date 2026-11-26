"""
Process Intelligence Engine (400k+ SQL Pushdown & Multi-Process Edition):
- Schema-agnostic Event Log & Multi-Process column mapping with Persian/English collision avoidance
- 100% DuckDB SQL pushdown across 400,000+ rows (zero 25k row cutoff)
- Multi-process discovery & filtering (`process_name` / `نام فرآیند`)
- SQL Trace reconstruction (`case_id` + `timestamp`), variants, skips, self-loops & rework
- Graceful step/cartable SQL mode even when `case_id` is absent
- Wait / Processing / Cycle / Lead Time separation + SLA breaches + 5-factor bottleneck scoring
- Per-step cartable assignee drill-down & statistical correlation hypotheses ("عامل مرتبط / فرضیه")
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.rls_cls import (
    DataSecurityContext,
    apply_cls_and_masking,
    build_data_security_context,
    build_security_projection_and_where,
)
from app.engines.bottleneck_rules import load_active_bottleneck_rule, score_steps_with_rule
from app.engines.warehouse import warehouse
from app.models.data import Dataset, DatasetField
from app.models.identity import User
from app.models.intelligence import ProcessCorrectiveAction, ProcessDatasetMapping

IDENT_RE = re.compile(r"^[A-Za-z0-9_\u0600-\u06FF\s\-\.\(\)]+$")

MAPPING_FIELD_PATTERNS: dict[str, tuple[tuple[str, float], ...]] = {
    "process_name": (
        ("process_name", 1.0), ("workflow_name", 0.97), ("process_title", 0.96), ("process_type", 0.94),
        ("نام فرایندهای سازمان", 0.99), ("نام فرایند سازمان", 0.99), ("فرایندهای سازمان", 0.97),
        ("نام فرایند", 0.99), ("عنوان فرایند", 0.96), ("نوع فرایند", 0.94),
        ("فرایند", 0.85), ("process", 0.80), ("workflow", 0.80),
    ),
    "case_id": (
        ("case_id", 0.98), ("request_code", 0.96), ("trace_id", 0.96), ("process_instance", 0.95),
        ("process_instance_id", 0.95), ("ticket_id", 0.90), ("request_id", 0.88), ("order_id", 0.80),
        ("کد درخواست", 0.96), ("شماره درخواست", 0.95), ("شناسه درخواست", 0.95),
        ("شناسه پرونده", 0.96), ("شماره پرونده", 0.94), ("کد پرونده", 0.94), ("کد رهگیری", 0.92),
    ),
    "activity": (
        ("step_name", 0.98), ("activity", 0.98), ("activity_name", 0.97), ("task_name", 0.95), ("stage_name", 0.94),
        ("گامهای هر فرایند", 0.98), ("گام های هر فرایند", 0.98), ("نام گام", 0.97), ("نام مرحله", 0.97),
        ("گام فرایند", 0.96), ("مرحله فرایند", 0.96), ("stage", 0.88), ("step_id", 0.85), ("step", 0.84),
        ("گام", 0.94), ("مرحله", 0.94), ("فعالیت", 0.96),
    ),
    "timestamp": (
        ("event_time", 0.98), ("timestamp", 0.96), ("occurred_at", 0.94), ("step_date", 0.92),
        ("entered_at", 0.90), ("created_at", 0.85), ("start_time", 0.84), ("date", 0.72),
        ("تاریخ ورود", 0.90), ("زمان ورود", 0.90), ("تاریخ ثبت", 0.86), ("تاریخ", 0.82), ("زمان", 0.82),
    ),
    "start_time": (
        ("start_time", 0.98), ("started_at", 0.98), ("begin_time", 0.92), ("زمان شروع", 0.94), ("شروع", 0.90),
    ),
    "end_time": (
        ("end_time", 0.98), ("completed_at", 0.96), ("finished_at", 0.95), ("closed_at", 0.90), ("زمان پایان", 0.94), ("پایان", 0.90),
    ),
    "wait_hours": (
        ("wait_hours", 0.98), ("wait_time", 0.92), ("waiting_hours", 0.92), ("duration_hours", 0.88),
        ("queue_hours", 0.90), ("زمان انتظار", 0.95), ("مدت انتظار", 0.95), ("میزان انتظار", 0.92), ("تاخیر", 0.84),
    ),
    "sla_hours": (
        ("sla_hours", 0.98), ("sla_target", 0.92), ("sla_limit", 0.92), ("target_hours", 0.90),
        ("sla", 0.82), ("سقف مجاز", 0.92), ("مهلت", 0.86),
    ),
    "actor": (
        ("assignee_name", 0.99), ("assignee", 0.98), ("actor_name", 0.98), ("actor", 0.97),
        ("handler", 0.92), ("operator", 0.90), ("owner_name", 0.92), ("owner", 0.90), ("agent", 0.86),
        ("employee", 0.84), ("username", 0.82), ("صاحب کارتابل", 0.96), ("در کارتابل", 0.95), ("کارتابل", 0.94),
        ("نام کارشناس", 0.96), ("کارشناس", 0.94), ("نام مجری", 0.94), ("مجری", 0.92),
        ("اقدام کننده", 0.93), ("تحویل گیرنده", 0.92), ("مسئول کارتابل", 0.95), ("مسئول", 0.92),
        ("متصدی", 0.88), ("عامل", 0.86), ("نام کاربر", 0.88), ("کاربر", 0.85), ("نام و نام خانوادگی", 0.90),
    ),
    "department": (
        ("department", 0.98), ("unit", 0.88), ("team", 0.86), ("org_unit", 0.92),
        ("واحد سازمانی", 0.96), ("واحد", 0.95), ("دپارتمان", 0.95), ("بخش", 0.88),
    ),
    "status": (
        ("status", 0.98), ("state", 0.88), ("outcome", 0.84), ("وضعیت درخواست", 0.96), ("وضعیت", 0.95),
    ),
    "priority": (
        ("priority", 0.98), ("urgency", 0.92), ("severity", 0.82), ("اولویت", 0.95),
    ),
    "rework_flag": (
        ("rework", 0.98), ("is_rework", 0.98), ("loop_flag", 0.92), ("returned", 0.85), ("دوباره‌کاری", 0.95), ("برگشت", 0.88),
    ),
}


def _norm_text(val: Any) -> str:
    s = str(val or "").strip().lower()
    for a, b in (
        ("ي", "ی"),
        ("ى", "ی"),
        ("ك", "ک"),
        ("آ", "ا"),
        ("أ", "ا"),
        ("إ", "ا"),
        ("\u200c", " "),
        ("\u200f", ""),
        ("_", " "),
        ("-", " "),
    ):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def _norm_row_val(val: Any) -> str:
    """Normalizes row data value to match SQL LOWER(_sql_norm_col) without altering punctuation like hyphens."""
    s = str(val or "").strip().lower()
    for a, b in (
        ("ي", "ی"),
        ("ى", "ی"),
        ("ك", "ک"),
        ("\u200c", " "),
        ("\u200f", ""),
    ):
        s = s.replace(a, b)
    return s.strip()


def _sql_norm_str(col_expr: str) -> str:
    return (
        f"TRIM(REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(CAST({col_expr} AS VARCHAR), ''), "
        f"'ي', 'ی'), 'ى', 'ی'), 'ك', 'ک'), CHR(8204), ' '))"
    )


def _sql_norm_col(col_name: str) -> str:
    return _sql_norm_str(f'"{col_name}"')


def _sql_ts_expr(col_name: str) -> str:
    return (
        f'COALESCE(TRY_CAST("{col_name}" AS TIMESTAMP), '
        f"TRY_CAST(REPLACE(CAST(\"{col_name}\" AS VARCHAR), '/', '-') AS TIMESTAMP))"
    )


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
        import math

        f = float(val)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except Exception:
        return None


def _fetch_process_rows(
    db: Session,
    ds: Dataset,
    user: User,
    mapping: dict[str, Optional[str]],
    *,
    date_start: Optional[str] = None,
    date_end: Optional[str] = None,
    filters: Optional[dict[str, Any]] = None,
    limit: int = 50_000,
) -> tuple[list[dict[str, Any]], int]:
    sec_ctx = build_data_security_context(db, user, ds.id)
    cols_meta = warehouse.describe_table(ds.warehouse_table)
    sub_sql, sub_params, vis_cols = build_security_projection_and_where(ds.warehouse_table, cols_meta, sec_ctx)

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

    sql = f"SELECT {', '.join(selected_cols)} FROM ({sub_sql}) _p{where_sql} LIMIT {int(limit)}"
    res = warehouse.execute_query(sql, params)
    return res["rows"], total_matching


def _safe_float(val: Any, digits: int = 2) -> Optional[float]:
    if val is None:
        return None
    try:
        f = float(val)
        return round(f, digits)
    except Exception:
        return None


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(val) if val is not None else default
    except Exception:
        return default


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
    """Infer Event Log columns (Process Name, Case ID, Activity, Timestamp, Actor, etc.) cleanly."""
    sec_ctx = build_data_security_context(db, user, ds.id)
    vis_cols = _visible_field_names(db, ds, sec_ctx)
    vis_names = {c["field_name"] for c in vis_cols}

    suggested: dict[str, Optional[str]] = {k: None for k in MAPPING_FIELD_PATTERNS}
    confidences: dict[str, float] = {k: 0.0 for k in MAPPING_FIELD_PATTERNS}
    used_cols: set[str] = set()

    # Order roles cleanly so process_name & activity & actor never collide
    role_order = (
        "process_name",
        "activity",
        "case_id",
        "actor",
        "timestamp",
        "start_time",
        "end_time",
        "wait_hours",
        "sla_hours",
        "department",
        "status",
        "priority",
        "rework_flag",
    )
    proc_exclude_toks = ("step", "stage", "activity", "task", "node", "گام", "مرحله", "فعالیت", "کد", "شناسه", "id", "code", "مدت", "زمان", "تاریخ", "wait", "sla", "time")
    actor_exclude_toks = ("فرایند", "فرآیند", "گام", "مرحله", "واحد", "بخش", "دپارتمان", "شرکت")

    for role in role_order:
        patterns = MAPPING_FIELD_PATTERNS[role]
        best_col = None
        best_score = 0.0
        for col in vis_cols:
            cname = col["field_name"]
            if cname in used_cols and role not in ("timestamp", "start_time"):
                continue
            n_cname = _norm_text(cname)
            n_dname = _norm_text(col["display_name"])
            hay = f"{n_cname} {n_dname}"

            if role == "process_name" and any(_norm_text(t) in hay for t in proc_exclude_toks):
                continue
            if role == "actor" and any(_norm_text(t) in hay for t in actor_exclude_toks):
                continue

            for pat, score in patterns:
                n_pat = _norm_text(pat)
                if n_cname == n_pat or n_dname == n_pat:
                    cand_score = score
                elif n_pat in hay:
                    cand_score = round(score * 0.88, 2)
                else:
                    continue
                if cand_score > best_score:
                    best_score = cand_score
                    best_col = cname
        if best_col:
            suggested[role] = best_col
            confidences[role] = best_score
            if role in ("process_name", "case_id", "activity", "actor", "start_time", "end_time"):
                used_cols.add(best_col)

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
            if role in eff_mapping and (colname is None or colname in vis_names):
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


def _build_process_filtered_sql_base(
    db: Session,
    ds: Dataset,
    user: User,
    mapping: dict[str, Optional[str]],
    *,
    date_start: Optional[str] = None,
    date_end: Optional[str] = None,
    filters: Optional[dict[str, Any]] = None,
    process_name: Optional[str] = None,
) -> tuple[str, list[Any], str, list[Any], list[str], DataSecurityContext]:
    """Builds RLS-secured DuckDB CTEs both before process_name filter (`_p_all`) and after (`_p`)."""
    sec_ctx = build_data_security_context(db, user, ds.id)
    cols_meta = warehouse.describe_table(ds.warehouse_table)
    sub_sql, sub_params, vis_cols = build_security_projection_and_where(ds.warehouse_table, cols_meta, sec_ctx)
    vis_set = set(vis_cols)

    where_clauses: list[str] = []
    params: list[Any] = list(sub_params)
    ts_col = mapping.get("timestamp") or mapping.get("start_time")
    if ts_col and ts_col in vis_set:
        ts_sql = _sql_ts_expr(ts_col)
        if date_start:
            where_clauses.append(f"{ts_sql} >= TRY_CAST(? AS TIMESTAMP)")
            params.append(date_start)
        if date_end:
            where_clauses.append(f"{ts_sql} <= TRY_CAST(? AS TIMESTAMP)")
            params.append(date_end)

    for fk, fv in (filters or {}).items():
        if fk in vis_set and fv is not None and fv != "":
            where_clauses.append(f'CAST("{fk}" AS VARCHAR) = ?')
            params.append(str(fv))

    where_all_sql = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    base_all_cte = f"_p_sec AS ({sub_sql}), _p_all AS (SELECT *, ROW_NUMBER() OVER () AS __pi_seq_idx FROM _p_sec{where_all_sql})"

    proc_col = mapping.get("process_name")
    eff_proc = None if (not process_name or process_name == "__ALL__") else str(process_name).strip()
    if proc_col and proc_col in vis_set and eff_proc:
        proc_norm = _norm_row_val(eff_proc)
        norm_proc_col = _sql_norm_col(proc_col)
        base_cte = f"{base_all_cte}, _p AS (SELECT * FROM _p_all WHERE LOWER({norm_proc_col}) = ?)"
        proc_params = list(params) + [proc_norm]
    else:
        base_cte = f"{base_all_cte}, _p AS (SELECT * FROM _p_all)"
        proc_params = list(params)

    return base_all_cte, params, base_cte, proc_params, vis_cols, sec_ctx


def discover_dataset_processes(
    db: Session,
    ds: Dataset,
    user: User,
    *,
    mapping_override: Optional[dict[str, Optional[str]]] = None,
    date_start: Optional[str] = None,
    date_end: Optional[str] = None,
    filters: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Discovers all processes (`نام فرآیند`) across 100% of rows via DuckDB SQL."""
    inferred = infer_event_log_mapping(db, ds, user)
    mapping = dict(inferred["mapping"])
    if mapping_override:
        for k, v in mapping_override.items():
            if k in mapping:
                mapping[k] = v

    base_all_cte, all_params, _, _, vis_cols, _ = _build_process_filtered_sql_base(
        db, ds, user, mapping, date_start=date_start, date_end=date_end, filters=filters
    )
    vis_set = set(vis_cols)
    proc_col = mapping.get("process_name")
    act_col = mapping.get("activity")
    case_col = mapping.get("case_id")
    wait_col = mapping.get("wait_hours")

    cnt_res = warehouse.execute_query(f"WITH {base_all_cte} SELECT COUNT(*) AS cnt FROM _p_all", all_params)
    total_rows = _safe_int(cnt_res["rows"][0]["cnt"]) if cnt_res["rows"] else 0

    if not proc_col or proc_col not in vis_set:
        return {
            "dataset_id": ds.id,
            "process_field": None,
            "total_rows": total_rows,
            "processes": [],
        }

    norm_proc = _sql_norm_col(proc_col)
    norm_case = _sql_norm_col(case_col) if case_col and case_col in vis_set else ""
    norm_act = _sql_norm_col(act_col) if act_col and act_col in vis_set else ""
    case_expr = f"COUNT(DISTINCT NULLIF({norm_case}, ''))" if norm_case else "COUNT(*)"
    step_expr = f"COUNT(DISTINCT NULLIF({norm_act}, ''))" if norm_act else "0"
    wait_expr = (
        f'ROUND(AVG(TRY_CAST("{wait_col}" AS DOUBLE)), 2)'
        if wait_col and wait_col in vis_set
        else "NULL"
    )

    p_res = warehouse.execute_query(
        f"WITH {base_all_cte} "
        f"SELECT {norm_proc} AS process_name, "
        f"COUNT(*) AS event_count, "
        f"{case_expr} AS case_count, "
        f"{step_expr} AS step_count, "
        f"{wait_expr} AS avg_wait_hours "
        f"FROM _p_all "
        f"WHERE {norm_proc} != '' "
        f"GROUP BY 1 "
        f"ORDER BY event_count DESC "
        f"LIMIT 250",
        all_params,
    )
    processes = [
        {
            "process_name": str(r.get("process_name") or "").strip(),
            "event_count": _safe_int(r.get("event_count")),
            "case_count": _safe_int(r.get("case_count")),
            "step_count": _safe_int(r.get("step_count")),
            "avg_wait_hours": _safe_float(r.get("avg_wait_hours")),
        }
        for r in p_res["rows"]
        if str(r.get("process_name") or "").strip()
    ]
    return {
        "dataset_id": ds.id,
        "process_field": proc_col,
        "total_rows": total_rows,
        "processes": processes,
    }


def _step_summary_only_analysis(
    db: Session,
    ds: Dataset,
    inferred: dict[str, Any],
    mapping: dict[str, Optional[str]],
    base_cte: str,
    proc_params: list[Any],
    vis_cols: list[str],
    sec_ctx: DataSecurityContext,
    processes: list[dict[str, Any]],
    active_process: Optional[str],
    total_matching: int,
) -> dict[str, Any]:
    """SQL analytics when dataset has Activity/Process/Wait/Assignee columns without Case ID."""
    vis_set = set(vis_cols)
    act_col = mapping["activity"]
    wait_col = mapping.get("wait_hours") if mapping.get("wait_hours") in vis_set else None
    sla_col = mapping.get("sla_hours") if mapping.get("sla_hours") in vis_set else None
    actor_col = mapping.get("actor") if mapping.get("actor") in vis_set else None
    status_col = mapping.get("status") if mapping.get("status") in vis_set else None

    norm_act = _sql_norm_col(act_col)
    wait_expr = f'TRY_CAST("{wait_col}" AS DOUBLE)' if wait_col else "NULL"
    sla_expr = f'TRY_CAST("{sla_col}" AS DOUBLE)' if sla_col else "NULL"
    norm_actor = _sql_norm_col(actor_col) if actor_col else ""
    actor_cnt_expr = f"COUNT(DISTINCT NULLIF({norm_actor}, ''))" if actor_col else "0"
    sla_eval_expr = (
        f"CASE WHEN ({wait_expr} IS NOT NULL AND {sla_expr} IS NOT NULL) "
        f"OR LOWER(COALESCE(CAST(\"{status_col}\" AS VARCHAR), '')) IN ('overdue','breached','delayed','تأخیر','تاخیر','نقض') "
        f"THEN 1 ELSE 0 END"
        if status_col
        else (f"CASE WHEN ({wait_expr} IS NOT NULL AND {sla_expr} IS NOT NULL) THEN 1 ELSE 0 END" if (wait_col and sla_col) else "0")
    )
    sla_breach_expr = (
        f"CASE WHEN ({wait_expr} IS NOT NULL AND {sla_expr} IS NOT NULL AND {wait_expr} > {sla_expr}) "
        f"OR LOWER(COALESCE(CAST(\"{status_col}\" AS VARCHAR), '')) IN ('overdue','breached','delayed','تأخیر','تاخیر','نقض') "
        f"THEN 1 ELSE 0 END"
        if status_col
        else (f"CASE WHEN ({wait_expr} IS NOT NULL AND {sla_expr} IS NOT NULL AND {wait_expr} > {sla_expr}) THEN 1 ELSE 0 END" if (wait_col and sla_col) else "0")
    )

    step_res = warehouse.execute_query(
        f"WITH {base_cte} "
        f"SELECT {norm_act} AS activity, "
        f"COUNT(*) AS event_count, "
        f"AVG({wait_expr}) AS avg_wait_hours, "
        f"MAX({wait_expr}) AS max_wait_hours, "
        f"SUM(COALESCE({wait_expr}, 0.0)) AS sum_wait_hours, "
        f"SUM({sla_eval_expr}) AS sla_evaluated, "
        f"SUM({sla_breach_expr}) AS sla_breaches, "
        f"{actor_cnt_expr} AS actor_count "
        f"FROM _p WHERE {norm_act} != '' "
        f"GROUP BY 1 ORDER BY event_count DESC LIMIT 100",
        proc_params,
    )
    rows = step_res["rows"]
    total_wait_portfolio = sum(float(r.get("sum_wait_hours") or 0.0) for r in rows) or 1.0
    max_avg_wait = max((float(r.get("avg_wait_hours") or 0.0) for r in rows), default=1.0) or 1.0
    max_vol = max((int(r.get("event_count") or 0) for r in rows), default=1) or 1
    max_cap = max((int(r.get("event_count") or 0) / max(int(r.get("actor_count") or 0), 1) for r in rows), default=1.0) or 1.0

    assignees_by_step: dict[str, list[dict[str, Any]]] = {}
    if actor_col:
        a_res = warehouse.execute_query(
            f"WITH {base_cte} "
            f"SELECT {norm_act} AS activity, CAST(\"{actor_col}\" AS VARCHAR) AS \"{actor_col}\", "
            f"COUNT(*) AS event_count, "
            f"ROUND(AVG({wait_expr}), 2) AS avg_wait_hours, "
            f"SUM({sla_breach_expr}) AS sla_breaches "
            f"FROM _p WHERE {norm_act} != '' "
            f"GROUP BY 1, 2 ORDER BY activity ASC, event_count DESC LIMIT 1500",
            proc_params,
        )
        _, masked_a = apply_cls_and_masking(a_res["columns"], a_res["rows"], sec_ctx)
        for ar in masked_a:
            act_k = str(ar.get("activity") or "").strip()
            actor_v = str(ar.get(actor_col) or "").strip() or "تخصیص‌نیافته"
            bucket = assignees_by_step.setdefault(act_k, [])
            if len(bucket) < 30:
                bucket.append({
                    "actor": actor_v,
                    "event_count": _safe_int(ar.get("event_count")),
                    "avg_wait_hours": _safe_float(ar.get("avg_wait_hours")),
                    "sla_breaches": _safe_int(ar.get("sla_breaches")),
                })

    bottlenecks = []
    for r in rows:
        act = str(r.get("activity") or "").strip()
        avg_w = _safe_float(r.get("avg_wait_hours"))
        vol = _safe_int(r.get("event_count"))
        acnt = _safe_int(r.get("actor_count"))
        f_dur = min(1.0, (avg_w or 0.0) / max_avg_wait) * 100.0
        f_vol = min(1.0, vol / max_vol) * 100.0
        f_cyc = min(1.0, (float(r.get("sum_wait_hours") or 0.0) / total_wait_portfolio) * 2.5) * 100.0
        f_cap = min(1.0, (vol / max(acnt, 1)) / max_cap) * 100.0
        score = round(0.30 * f_dur + 0.25 * f_vol + 0.25 * f_cyc + 0.20 * f_cap, 1)
        s_eval = _safe_int(r.get("sla_evaluated"))
        s_br = _safe_int(r.get("sla_breaches"))
        bottlenecks.append({
            "activity": act,
            "case_count": vol,
            "event_count": vol,
            "avg_wait_hours": avg_w,
            "max_wait_hours": _safe_float(r.get("max_wait_hours")),
            "avg_processing_hours": None,
            "rework_events": 0,
            "rework_rate_pct": 0.0,
            "self_loops": 0,
            "sla_breach_pct": round(s_br * 100.0 / max(s_eval, 1), 1) if s_eval > 0 else None,
            "actor_count": acnt,
            "top_assignees": assignees_by_step.get(act, [])[:10],
            "composite_bottleneck_score": score,
            "factor_breakdown": {
                "duration_score": round(f_dur, 1),
                "volume_score": round(f_vol, 1),
                "rework_score": 0.0,
                "cycle_impact_score": round(f_cyc, 1),
                "capacity_load_score": round(f_cap, 1),
                "weights": {"duration": 0.30, "volume": 0.25, "rework": 0.0, "cycle_impact": 0.25, "capacity_load": 0.20},
            },
        })
    bottlenecks.sort(key=lambda x: -x["composite_bottleneck_score"])

    edges = [
        {
            "source": bottlenecks[i]["activity"],
            "target": bottlenecks[i + 1]["activity"],
            "transition_count": min(bottlenecks[i]["event_count"], bottlenecks[i + 1]["event_count"]),
            "case_count": min(bottlenecks[i]["event_count"], bottlenecks[i + 1]["event_count"]),
            "avg_transition_hours": bottlenecks[i + 1]["avg_wait_hours"],
        }
        for i in range(max(0, len(bottlenecks) - 1))
    ]
    overall_avg_w = (
        round(sum(float(r.get("sum_wait_hours") or 0.0) for r in rows) / max(total_matching, 1), 2)
        if wait_col
        else None
    )

    return {
        "status": "ok",
        "trace_mode": "step_summary_only",
        "dataset_id": ds.id,
        "dataset_name": ds.name,
        "active_process": active_process,
        "processes": processes,
        "mapping_info": {**inferred, "effective_mapping": mapping, "reference_sequence": []},
        "summary": {
            "total_events_analyzed": total_matching,
            "total_events_matching": total_matching,
            "total_cases": total_matching,
            "unique_activities": len(bottlenecks),
            "unique_variants": 0,
            "time_range": f"{total_matching:,} رویداد",
            "avg_cycle_hours": overall_avg_w,
            "median_cycle_hours": None,
            "avg_wait_hours": overall_avg_w,
            "avg_processing_hours": None,
            "processing_time_status": "needs_data",
            "processing_time_note_fa": "ستون شناسه پرونده (Case ID) موجود نیست؛ تحلیل گام‌ها و کارتابل کارشناسان در SQL انجام شد.",
            "wait_time_basis_fa": "ستون صریح wait_hours" if wait_col else "مقدار انتظار ثبت نشده",
            "rework_case_count": 0,
            "rework_rate_pct": 0.0,
            "self_loop_case_count": 0,
            "skip_case_count": None,
            "sla_evaluated_cases": sum(_safe_int(r.get("sla_evaluated")) for r in rows),
            "sla_breach_cases": sum(_safe_int(r.get("sla_breaches")) for r in rows),
            "sla_breach_rate_pct": None,
        },
        "data_quality": {
            "missing_case_or_activity_events": 0,
            "missing_timestamp_events": total_matching,
            "out_of_order_cases": 0,
            "single_event_cases": 0,
            "warnings_fa": [
                "ستون شناسه پرونده (Case ID) برای این مجموعه داده نگاشت نشده است؛ تحلیل گلوگاه گام‌ها و کارتابل افراد برای ۱۰۰٪ رکوردها بدون بازسازی مسیر انجام شد."
            ],
        },
        "process_graph": {"nodes": bottlenecks, "edges": edges},
        "variants": [],
        "bottlenecks": bottlenecks,
        "assignees_by_step": assignees_by_step,
        "hypotheses": [],
        "cases_preview": [],
    }


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
    process_name: Optional[str] = None,
) -> dict[str, Any]:
    """
    Full Process Mining analysis running directly inside DuckDB SQL across 100% of matching rows (400k+ rows):
    - Multi-process discovery & filtering (`process_name` / `نام فرآیند`)
    - Trace reconstruction per case_id (or process::case_id across mixed processes)
    - Process graph (nodes + transitions) + per-step cartable assignees
    - Variants & deviations, rework, self-loops & reference-based skips
    - Wait / Processing / Cycle / Lead Time with explicit basis & quality warnings
    - SLA breach rates & multi-factor composite bottleneck scoring
    - Statistical correlation-labeled hypotheses ("عامل مرتبط / فرضیه")
    """
    inferred = infer_event_log_mapping(db, ds, user)
    mapping = dict(inferred["mapping"])
    if mapping_override:
        for k, v in mapping_override.items():
            if k in mapping:
                mapping[k] = v

    ref_seq = list(reference_sequence_override) if reference_sequence_override is not None else list(inferred["reference_sequence"])

    act_col = mapping.get("activity")
    if not act_col:
        return {
            "status": "needs_mapping",
            "mapping_info": inferred,
            "error_fa": "برای تحلیل فرآیند، حداقل ستون گام / فعالیت (Activity) لازم است.",
        }

    base_all_cte, all_params, base_cte, proc_params, vis_cols, sec_ctx = _build_process_filtered_sql_base(
        db, ds, user, mapping,
        date_start=date_start, date_end=date_end, filters=filters, process_name=process_name,
    )
    vis_set = set(vis_cols)

    # Discover processes across _p_all
    proc_col = mapping.get("process_name") if mapping.get("process_name") in vis_set else None
    case_col = mapping.get("case_id") if mapping.get("case_id") in vis_set else None
    wait_col = mapping.get("wait_hours") if mapping.get("wait_hours") in vis_set else None
    processes: list[dict[str, Any]] = []
    if proc_col:
        np_sql = _sql_norm_col(proc_col)
        na_sql = _sql_norm_col(act_col)
        nc_sql = _sql_norm_col(case_col) if case_col else ""
        c_cnt = f"COUNT(DISTINCT NULLIF({nc_sql}, ''))" if case_col else "COUNT(*)"
        w_avg = f'ROUND(AVG(TRY_CAST("{wait_col}" AS DOUBLE)), 2)' if wait_col else "NULL"
        pr_res = warehouse.execute_query(
            f"WITH {base_all_cte} "
            f"SELECT {np_sql} AS process_name, COUNT(*) AS event_count, {c_cnt} AS case_count, "
            f"COUNT(DISTINCT NULLIF({na_sql}, '')) AS step_count, "
            f"{w_avg} AS avg_wait_hours "
            f"FROM _p_all WHERE {np_sql} != '' GROUP BY 1 ORDER BY event_count DESC LIMIT 250",
            all_params,
        )
        processes = [
            {
                "process_name": str(r.get("process_name") or "").strip(),
                "event_count": _safe_int(r.get("event_count")),
                "case_count": _safe_int(r.get("case_count")),
                "step_count": _safe_int(r.get("step_count")),
                "avg_wait_hours": _safe_float(r.get("avg_wait_hours")),
            }
            for r in pr_res["rows"]
            if str(r.get("process_name") or "").strip()
        ]

    active_process = None if (not process_name or process_name == "__ALL__") else str(process_name).strip()

    cnt_res = warehouse.execute_query(f"WITH {base_cte} SELECT COUNT(*) AS cnt FROM _p", proc_params)
    total_matching = _safe_int(cnt_res["rows"][0]["cnt"]) if cnt_res["rows"] else 0
    if total_matching == 0:
        return {
            "status": "empty",
            "active_process": active_process,
            "processes": processes,
            "mapping_info": inferred,
            "summary": {"total_events": 0, "total_cases": 0},
            "error_fa": "هیچ رکوردی در بازه/فیلتر انتخاب‌شده یافت نشد.",
        }

    # If case_id column does not exist, still deliver 100%-row SQL Step & Cartable intelligence
    if not case_col:
        return _step_summary_only_analysis(
            db, ds, inferred, mapping, base_cte, proc_params, vis_cols, sec_ctx,
            processes, active_process, total_matching,
        )

    ts_col = mapping.get("timestamp") if mapping.get("timestamp") in vis_set else None
    start_col = mapping.get("start_time") if mapping.get("start_time") in vis_set else None
    end_col = mapping.get("end_time") if mapping.get("end_time") in vis_set else None
    sla_col = mapping.get("sla_hours") if mapping.get("sla_hours") in vis_set else None
    actor_col = mapping.get("actor") if mapping.get("actor") in vis_set else None
    dept_col = mapping.get("department") if mapping.get("department") in vis_set else None
    status_col = mapping.get("status") if mapping.get("status") in vis_set else None
    prio_col = mapping.get("priority") if mapping.get("priority") in vis_set else None
    rework_col = mapping.get("rework_flag") if mapping.get("rework_flag") in vis_set else None

    ts_expr = _sql_ts_expr(ts_col) if ts_col else "CAST(NULL AS TIMESTAMP)"
    st_expr = _sql_ts_expr(start_col) if start_col else ts_expr
    en_expr = _sql_ts_expr(end_col) if end_col else "CAST(NULL AS TIMESTAMP)"
    eff_ts_expr = f"COALESCE({ts_expr}, {st_expr}, {en_expr})"
    end_eff_expr = f"COALESCE({en_expr}, {eff_ts_expr})"

    # Partition case_id cleanly by process_name when multiple processes are analyzed simultaneously
    raw_cid_expr = _sql_norm_col(case_col)
    if proc_col and not active_process:
        norm_proc_e = _sql_norm_col(proc_col)
        cid_expr = (
            f"CASE WHEN {norm_proc_e} != '' "
            f"THEN ({norm_proc_e} || '::' || {raw_cid_expr}) "
            f"ELSE {raw_cid_expr} END"
        )
    else:
        cid_expr = raw_cid_expr

    act_expr = _sql_norm_col(act_col)
    wait_explicit_expr = f'TRY_CAST("{wait_col}" AS DOUBLE)' if wait_col else "CAST(NULL AS DOUBLE)"
    sla_val_expr = f'TRY_CAST("{sla_col}" AS DOUBLE)' if sla_col else "CAST(NULL AS DOUBLE)"
    actor_raw_col = f'CAST("{actor_col}" AS VARCHAR)' if actor_col else "''"
    dept_expr = _sql_norm_col(dept_col) if dept_col else "''"
    status_expr = f'LOWER(TRIM(COALESCE(CAST("{status_col}" AS VARCHAR), \'\')))' if status_col else "''"
    prio_expr = _sql_norm_col(prio_col) if prio_col else "''"
    rework_bool_expr = (
        f"(LOWER(TRIM(COALESCE(CAST(\"{rework_col}\" AS VARCHAR), ''))) IN ('1','true','yes','y','rework','returned','بله'))"
        if rework_col
        else "FALSE"
    )

    order_key = f"COALESCE(eff_ts, TIMESTAMP '9999-12-31 00:00:00'), __pi_seq_idx"

    # Build unified SQL CTE pipeline operating on 100% of rows in DuckDB
    events_ctes = (
        f"{base_cte}, "
        f"_ev_raw AS ("
        f"  SELECT __pi_seq_idx, {raw_cid_expr} AS raw_case_id, {cid_expr} AS cid, {act_expr} AS act, "
        f"  {eff_ts_expr} AS eff_ts, {st_expr} AS start_dt, {en_expr} AS end_dt, {end_eff_expr} AS end_eff_ts, "
        f"  {wait_explicit_expr} AS wait_explicit, {sla_val_expr} AS sla_val, "
        f"  {actor_raw_col} AS raw_actor, {dept_expr} AS department, {status_expr} AS status_low, "
        f"  {prio_expr} AS priority, {rework_bool_expr} AS rework_flag "
        f"  FROM _p"
        f"), "
        f"_ev_valid AS ("
        f"  SELECT *, "
        f"  MAX(eff_ts) OVER (PARTITION BY cid ORDER BY __pi_seq_idx ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS max_file_prev_ts "
        f"  FROM _ev_raw WHERE raw_case_id != '' AND act != ''"
        f"), "
        f"_ev_win AS ("
        f"  SELECT *, "
        f"  LAG(act) OVER (PARTITION BY cid ORDER BY {order_key}) AS prev_act, "
        f"  LAG(COALESCE(end_dt, eff_ts)) OVER (PARTITION BY cid ORDER BY {order_key}) AS prev_anchor, "
        f"  COALESCE(start_dt, eff_ts) AS curr_anchor, "
        f"  ROW_NUMBER() OVER (PARTITION BY cid, act ORDER BY {order_key}) AS act_occ "
        f"  FROM _ev_valid"
        f"), "
        f"_ev_calc AS ("
        f"  SELECT *, "
        f"  CASE "
        f"    WHEN wait_explicit IS NOT NULL THEN wait_explicit "
        f"    WHEN prev_anchor IS NOT NULL AND curr_anchor IS NOT NULL AND curr_anchor >= prev_anchor "
        f"      THEN ROUND(EXTRACT(EPOCH FROM (curr_anchor - prev_anchor)) / 3600.0, 3) "
        f"    ELSE CAST(NULL AS DOUBLE) "
        f"  END AS step_wait, "
        f"  CASE "
        f"    WHEN start_dt IS NOT NULL AND end_dt IS NOT NULL AND end_dt >= start_dt "
        f"      THEN ROUND(EXTRACT(EPOCH FROM (end_dt - start_dt)) / 3600.0, 3) "
        f"    ELSE CAST(NULL AS DOUBLE) "
        f"  END AS step_proc, "
        f"  CASE WHEN act_occ > 1 OR rework_flag THEN 1 ELSE 0 END AS is_rework_ev, "
        f"  CASE WHEN prev_act IS NOT NULL AND prev_act = act THEN 1 ELSE 0 END AS is_self_loop_ev "
        f"  FROM _ev_win"
        f"), "
        f"_ev_sla AS ("
        f"  SELECT *, "
        f"  CASE WHEN (sla_val IS NOT NULL AND step_wait IS NOT NULL) OR status_low IN ('overdue','breached','delayed','تأخیر','تاخیر','نقض') THEN 1 ELSE 0 END AS sla_eval_ev, "
        f"  CASE WHEN (sla_val IS NOT NULL AND step_wait IS NOT NULL AND step_wait > sla_val) OR status_low IN ('overdue','breached','delayed','تأخیر','تاخیر','نقض') THEN 1 ELSE 0 END AS sla_breach_ev "
        f"  FROM _ev_calc"
        f")"
    )

    ref_set = list(dict.fromkeys([str(s).strip() for s in ref_seq if str(s).strip()]))
    if ref_set:
        ph = ", ".join(["?"] * len(ref_set))
        skip_sql = f"CASE WHEN COUNT(DISTINCT CASE WHEN act IN ({ph}) THEN act END) < {len(ref_set)} THEN 1 ELSE 0 END"
        skip_params = list(ref_set)
    else:
        skip_sql = "0"
        skip_params = []

    cases_cte = (
        f"{events_ctes}, "
        f"_cases AS ("
        f"  SELECT cid, ANY_VALUE(raw_case_id) AS case_id, "
        f"  COUNT(*) AS event_count, "
        f"  COUNT(DISTINCT act) AS uniq_acts, "
        f"  STRING_AGG(act, ' -> ' ORDER BY {order_key}) AS variant_key, "
        f"  MIN(eff_ts) AS started_at, MAX(end_eff_ts) AS ended_at, "
        f"  SUM(step_wait) AS case_wait_sum, COUNT(step_wait) AS wait_cnt, "
        f"  SUM(step_proc) AS case_proc_sum, COUNT(step_proc) AS proc_cnt, "
        f"  CASE "
        f"    WHEN MIN(eff_ts) IS NOT NULL AND MAX(end_eff_ts) IS NOT NULL AND (COUNT(*) >= 2 OR MAX(end_eff_ts) > MIN(eff_ts)) "
        f"      THEN ROUND(GREATEST(0.0, EXTRACT(EPOCH FROM (MAX(end_eff_ts) - MIN(eff_ts))) / 3600.0), 3) "
        f"    WHEN COUNT(step_wait) > 0 THEN ROUND(SUM(step_wait), 3) "
        f"    ELSE CAST(NULL AS DOUBLE) "
        f"  END AS cycle_hours, "
        f"  MAX(is_rework_ev) AS has_rework, "
        f"  SUM(is_self_loop_ev) AS self_loop_count, "
        f"  MAX(CASE WHEN is_self_loop_ev = 1 THEN 1 ELSE 0 END) AS has_self_loop, "
        f"  MAX(sla_eval_ev) AS sla_eval_case, "
        f"  MAX(sla_breach_ev) AS sla_breached, "
        f"  MAX(CASE WHEN eff_ts IS NOT NULL AND max_file_prev_ts IS NOT NULL AND eff_ts < max_file_prev_ts THEN 1 ELSE 0 END) AS is_out_of_order, "
        f"  CASE WHEN COUNT(*) = 1 THEN 1 ELSE 0 END AS is_single_event, "
        f"  {skip_sql} AS has_skip, "
        f"  MIN(NULLIF(department, '')) AS department, "
        f"  MIN(NULLIF(priority, '')) AS priority, "
        f"  CASE WHEN COUNT(DISTINCT NULLIF(TRIM(raw_actor), '')) = 1 THEN MAX(NULLIF(TRIM(raw_actor), '')) ELSE NULL END AS single_actor "
        f"  FROM _ev_sla GROUP BY cid"
        f")"
    )

    full_params = list(proc_params) + skip_params

    # 1) Summary & Data Quality metrics across 100% of rows
    dq_res = warehouse.execute_query(
        f"WITH {events_ctes} "
        f"SELECT "
        f"(SELECT COUNT(*) FROM _ev_raw WHERE raw_case_id = '' OR act = '') AS missing_case_or_activity, "
        f"(SELECT COUNT(*) FROM _ev_valid WHERE eff_ts IS NULL) AS missing_ts_events, "
        f"(SELECT COUNT(*) FROM _ev_valid) AS valid_events, "
        f"(SELECT COUNT(DISTINCT act) FROM _ev_valid) AS unique_activities, "
        f"(SELECT MIN(eff_ts) FROM _ev_valid WHERE eff_ts IS NOT NULL) AS min_dt, "
        f"(SELECT MAX(end_eff_ts) FROM _ev_valid WHERE end_eff_ts IS NOT NULL) AS max_dt",
        proc_params,
    )
    dq = dq_res["rows"][0] if dq_res["rows"] else {}

    csum_res = warehouse.execute_query(
        f"WITH {cases_cte} "
        f"SELECT COUNT(*) AS total_cases, "
        f"COUNT(DISTINCT variant_key) AS unique_variants, "
        f"AVG(cycle_hours) AS avg_cycle_hours, "
        f"MEDIAN(cycle_hours) AS median_cycle_hours, "
        f"AVG(CASE WHEN wait_cnt > 0 THEN case_wait_sum END) AS avg_wait_hours, "
        f"AVG(CASE WHEN proc_cnt > 0 THEN case_proc_sum END) AS avg_processing_hours, "
        f"SUM(proc_cnt) AS total_proc_cnt, "
        f"SUM(has_rework) AS rework_case_count, "
        f"SUM(has_self_loop) AS self_loop_case_count, "
        f"SUM(has_skip) AS skip_case_count, "
        f"SUM(sla_eval_case) AS sla_evaluated_cases, "
        f"SUM(sla_breached) AS sla_breach_cases, "
        f"SUM(is_out_of_order) AS out_of_order_cases, "
        f"SUM(is_single_event) AS single_event_cases "
        f"FROM _cases",
        full_params,
    )
    cs = csum_res["rows"][0] if csum_res["rows"] else {}
    total_cases = _safe_int(cs.get("total_cases"))

    # 2) Activity bottlenecks across 100% of rows
    b_res = warehouse.execute_query(
        f"WITH {events_ctes} "
        f"SELECT act AS activity, "
        f"COUNT(DISTINCT cid) AS case_count, "
        f"COUNT(*) AS event_count, "
        f"AVG(step_wait) AS avg_wait_hours, "
        f"MAX(step_wait) AS max_wait_hours, "
        f"SUM(COALESCE(step_wait, 0.0)) AS sum_wait_hours, "
        f"AVG(step_proc) AS avg_processing_hours, "
        f"SUM(is_rework_ev) AS rework_events, "
        f"SUM(is_self_loop_ev) AS self_loops, "
        f"SUM(sla_eval_ev) AS sla_evaluated, "
        f"SUM(sla_breach_ev) AS sla_breaches, "
        f"COUNT(DISTINCT NULLIF(TRIM(raw_actor), '')) AS actor_count "
        f"FROM _ev_sla GROUP BY act ORDER BY event_count DESC LIMIT 120",
        proc_params,
    )
    b_rows = b_res["rows"]

    # Step assignees (CLS-masked!)
    assignees_by_step: dict[str, list[dict[str, Any]]] = {}
    if actor_col:
        as_res = warehouse.execute_query(
            f"WITH {events_ctes} "
            f"SELECT act AS activity, raw_actor AS \"{actor_col}\", "
            f"COUNT(*) AS event_count, "
            f"COUNT(DISTINCT cid) AS case_count, "
            f"ROUND(AVG(step_wait), 2) AS avg_wait_hours, "
            f"SUM(sla_breach_ev) AS sla_breaches "
            f"FROM _ev_sla GROUP BY 1, 2 ORDER BY activity ASC, event_count DESC LIMIT 2000",
            proc_params,
        )
        _, masked_as = apply_cls_and_masking(as_res["columns"], as_res["rows"], sec_ctx)
        for ar in masked_as:
            act_k = str(ar.get("activity") or "").strip()
            actor_v = str(ar.get(actor_col) or "").strip() or "تخصیص‌نیافته"
            bucket = assignees_by_step.setdefault(act_k, [])
            if len(bucket) < 30:
                bucket.append({
                    "actor": actor_v,
                    "event_count": _safe_int(ar.get("event_count")),
                    "case_count": _safe_int(ar.get("case_count")),
                    "avg_wait_hours": _safe_float(ar.get("avg_wait_hours")),
                    "sla_breaches": _safe_int(ar.get("sla_breaches")),
                })

    total_wait_portfolio = sum(float(r.get("sum_wait_hours") or 0.0) for r in b_rows) or 1.0
    max_avg_wait = max((float(r.get("avg_wait_hours") or 0.0) for r in b_rows), default=1.0) or 1.0
    max_volume = max((_safe_int(r.get("case_count")) for r in b_rows), default=1) or 1
    max_rework_rate = max((_safe_int(r.get("rework_events")) / max(_safe_int(r.get("event_count")), 1) for r in b_rows), default=0.01) or 0.01
    max_cap_load = max((_safe_int(r.get("case_count")) / max(_safe_int(r.get("actor_count")), 1) for r in b_rows), default=1.0) or 1.0

    bottlenecks = []
    for r in b_rows:
        act = str(r.get("activity") or "").strip()
        avg_w = float(r["avg_wait_hours"]) if r.get("avg_wait_hours") is not None else 0.0
        avg_p = _safe_float(r.get("avg_processing_hours"))
        vol = _safe_int(r.get("case_count"))
        evc = _safe_int(r.get("event_count"))
        rew = _safe_int(r.get("rework_events"))
        rew_rate = rew / max(evc, 1)
        cycle_share = float(r.get("sum_wait_hours") or 0.0) / total_wait_portfolio
        acnt = _safe_int(r.get("actor_count"))
        cap_load = vol / max(acnt, 1)

        f_dur = min(1.0, avg_w / max_avg_wait) * 100.0
        f_vol = min(1.0, vol / max_volume) * 100.0
        f_rew = min(1.0, rew_rate / max_rework_rate) * 100.0 if rew > 0 else 0.0
        f_cyc = min(1.0, cycle_share * 2.5) * 100.0
        f_cap = min(1.0, cap_load / max_cap_load) * 100.0
        score = round(0.30 * f_dur + 0.20 * f_vol + 0.20 * f_rew + 0.20 * f_cyc + 0.10 * f_cap, 1)

        s_eval = _safe_int(r.get("sla_evaluated"))
        s_br = _safe_int(r.get("sla_breaches"))
        bottlenecks.append({
            "activity": act,
            "case_count": vol,
            "event_count": evc,
            "avg_wait_hours": _safe_float(r.get("avg_wait_hours")),
            "max_wait_hours": _safe_float(r.get("max_wait_hours")),
            "avg_processing_hours": avg_p,
            "rework_events": rew,
            "rework_rate_pct": round(rew_rate * 100.0, 1),
            "self_loops": _safe_int(r.get("self_loops")),
            "sla_breach_pct": round(s_br * 100.0 / max(s_eval, 1), 1) if s_eval > 0 else None,
            "actor_count": acnt,
            "top_assignees": assignees_by_step.get(act, [])[:10],
            "composite_bottleneck_score": score,
            "factor_breakdown": {
                "duration_score": round(f_dur, 1),
                "volume_score": round(f_vol, 1),
                "rework_score": round(f_rew, 1),
                "cycle_impact_score": round(f_cyc, 1),
                "capacity_load_score": round(f_cap, 1),
                "weights": {"duration": 0.30, "volume": 0.20, "rework": 0.20, "cycle_impact": 0.20, "capacity_load": 0.10},
            },
        })
    active_rule = load_active_bottleneck_rule(
        db, organization_id=ds.organization_id, dataset_id=ds.id, process_name=active_process
    )
    if active_rule and bottlenecks:
        proxy_steps = [
            {
                "step_name": b["activity"],
                "avg_wait_hours": b.get("avg_wait_hours") or 0.0,
                "max_wait_hours": b.get("max_wait_hours") or 0.0,
                "pending_count": b.get("case_count") or 0,
                "total_count": b.get("event_count") or 0,
                "overdue_count": 0,
                "sla_breach_pct": b.get("sla_breach_pct") or 0.0,
                "rework_rate": b.get("rework_rate_pct") or 0.0,
            }
            for b in bottlenecks
        ]
        score_steps_with_rule(proxy_steps, rule=active_rule)
        for b, ps in zip(bottlenecks, proxy_steps):
            b["composite_bottleneck_score"] = ps["bottleneck_score"]
            b["is_bottleneck"] = ps["is_bottleneck"]
            b["matched_rule"] = ps["matched_rule"]
            b["severity"] = ps["severity"]
    bottlenecks.sort(key=lambda x: -x["composite_bottleneck_score"])

    # 3) Transitions across 100% of rows
    e_res = warehouse.execute_query(
        f"WITH {events_ctes} "
        f"SELECT prev_act AS source, act AS target, COUNT(*) AS transition_count, "
        f"COUNT(DISTINCT cid) AS case_count, ROUND(AVG(step_wait), 2) AS avg_transition_hours "
        f"FROM _ev_sla WHERE prev_act IS NOT NULL "
        f"GROUP BY 1, 2 ORDER BY transition_count DESC LIMIT 200",
        proc_params,
    )
    process_edges = [
        {
            "source": str(er.get("source") or "").strip(),
            "target": str(er.get("target") or "").strip(),
            "transition_count": _safe_int(er.get("transition_count")),
            "case_count": _safe_int(er.get("case_count")),
            "avg_transition_hours": _safe_float(er.get("avg_transition_hours")),
        }
        for er in e_res["rows"]
    ]

    # 4) Top 25 Variants across 100% of cases
    v_res = warehouse.execute_query(
        f"WITH {cases_cte} "
        f"SELECT variant_key, COUNT(*) AS case_count, "
        f"ROUND(AVG(cycle_hours), 2) AS avg_cycle_hours, "
        f"SUM(has_rework) AS rework_cases, "
        f"SUM(sla_breached) AS sla_breach_cases "
        f"FROM _cases GROUP BY variant_key ORDER BY case_count DESC LIMIT 25",
        full_params,
    )
    formatted_variants = []
    for idx, vr in enumerate(v_res["rows"]):
        vkey = str(vr.get("variant_key") or "")
        steps = [s.strip() for s in vkey.split(" -> ") if s.strip()]
        ccnt = _safe_int(vr.get("case_count"))
        skipped = [s for s in ref_seq if s not in set(steps)] if ref_seq else []
        is_dev = bool(skipped) or (bool(ref_seq) and steps != ref_seq) or (not ref_seq and idx > 0 and ccnt / max(total_cases, 1) < 0.25)
        formatted_variants.append({
            "rank": idx + 1,
            "variant_key": vkey,
            "steps": steps,
            "case_count": ccnt,
            "share_pct": round(ccnt * 100.0 / max(total_cases, 1), 2),
            "avg_cycle_hours": _safe_float(vr.get("avg_cycle_hours")),
            "rework_rate_pct": round(_safe_int(vr.get("rework_cases")) * 100.0 / max(ccnt, 1), 1),
            "sla_breach_pct": round(_safe_int(vr.get("sla_breach_cases")) * 100.0 / max(ccnt, 1), 1),
            "skipped_steps": skipped,
            "is_deviant": is_dev,
        })

    # 5) Top 50 Cases Preview (CLS-masked!)
    cp_res = warehouse.execute_query(
        f"WITH {cases_cte} "
        f"SELECT case_id, event_count, variant_key AS variant, cycle_hours, "
        f"ROUND(CASE WHEN wait_cnt > 0 THEN case_wait_sum END, 3) AS wait_hours, "
        f"ROUND(CASE WHEN proc_cnt > 0 THEN case_proc_sum END, 3) AS processing_hours, "
        f"has_rework AS rework_count, self_loop_count, sla_breached, "
        f"COALESCE(department, '') AS \"{dept_col or 'department'}\", "
        f"COALESCE(priority, '') AS \"{prio_col or 'priority'}\", "
        f"COALESCE(single_actor, '') AS \"{actor_col or 'actor'}\", "
        f"CAST(started_at AS VARCHAR) AS started_at, CAST(ended_at AS VARCHAR) AS ended_at "
        f"FROM _cases ORDER BY cycle_hours DESC NULLS LAST LIMIT 50",
        full_params,
    )
    _, masked_cp = apply_cls_and_masking(cp_res["columns"], cp_res["rows"], sec_ctx)
    cases_preview = []
    for cr in masked_cp:
        steps_list = [s.strip() for s in str(cr.get("variant") or "").split(" -> ") if s.strip()]
        skipped = [s for s in ref_seq if s not in set(steps_list)] if ref_seq else []
        a_val = str(cr.get(actor_col or "actor") or "").strip()
        cases_preview.append({
            "case_id": str(cr.get("case_id") or ""),
            "event_count": _safe_int(cr.get("event_count")),
            "variant": cr.get("variant"),
            "cycle_hours": _safe_float(cr.get("cycle_hours"), 3),
            "wait_hours": _safe_float(cr.get("wait_hours"), 3),
            "processing_hours": _safe_float(cr.get("processing_hours"), 3),
            "rework_count": _safe_int(cr.get("rework_count")),
            "self_loop_count": _safe_int(cr.get("self_loop_count")),
            "skipped_steps": skipped,
            "sla_breached": bool(cr.get("sla_breached")),
            "department": str(cr.get(dept_col or "department") or ""),
            "actors": [a_val] if a_val else [],
            "priority": str(cr.get(prio_col or "priority") or ""),
            "started_at": cr.get("started_at"),
            "ended_at": cr.get("ended_at"),
        })

    # 6) Root Cause Hypotheses across 100% of cases in DuckDB SQL
    min_dt = dq.get("min_dt")
    max_dt = dq.get("max_dt")
    min_str = min_dt.date().isoformat() if hasattr(min_dt, "date") else (str(min_dt)[:10] if min_dt else None)
    max_str = max_dt.date().isoformat() if hasattr(max_dt, "date") else (str(max_dt)[:10] if max_dt else None)
    time_range_str = f"{min_str} تا {max_str}" if (min_str and max_str) else f"{total_cases:,} پرونده"

    overall_avg_cycle = float(cs["avg_cycle_hours"]) if cs.get("avg_cycle_hours") is not None else None
    overall_rework_rate = _safe_int(cs.get("rework_case_count")) / max(total_cases, 1)

    hypotheses: list[dict[str, Any]] = []
    for dim_name, col_alias, real_col in (
        ("department", "department", dept_col),
        ("priority", "priority", prio_col),
        ("actor", "single_actor", actor_col),
    ):
        if not real_col:
            continue
        h_res = warehouse.execute_query(
            f"WITH {cases_cte} "
            f"SELECT {col_alias} AS \"{real_col}\", COUNT(*) AS sample_count, "
            f"AVG(cycle_hours) AS avg_cycle, "
            f"SUM(has_rework) AS rework_cnt "
            f"FROM _cases WHERE {col_alias} IS NOT NULL AND {col_alias} != '' "
            f"GROUP BY 1 HAVING COUNT(*) >= 3",
            full_params,
        )
        _, masked_h = apply_cls_and_masking(h_res["columns"], h_res["rows"], sec_ctx)
        for hr in masked_h:
            gval = str(hr.get(real_col) or "").strip()
            scnt = _safe_int(hr.get("sample_count"))
            if not gval:
                continue
            if overall_avg_cycle and overall_avg_cycle > 0 and hr.get("avg_cycle") is not None:
                g_avg_c = float(hr["avg_cycle"])
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
                        "sample_count": scnt,
                        "time_range": time_range_str,
                        "summary_fa": (
                            f"در «{dim_name} = {gval}» میانگین زمان چرخه {round(g_avg_c, 2)} ساعت است که "
                            f"{ratio} برابر میانگین کل ({round(overall_avg_cycle, 2)} ساعت) می‌باشد (n={scnt})."
                        ),
                        "limitations_fa": "این یافته صرفاً یک همبستگی مشاهده‌شده در زیرگروه داده است و بدون کنترل متغیرهای مداخله‌گر به معنای علیت قطعی نیست.",
                    })
            rew_cnt = _safe_int(hr.get("rework_cnt"))
            g_rew = rew_cnt / max(scnt, 1)
            if overall_rework_rate > 0 and g_rew / overall_rework_rate >= 1.5 and rew_cnt >= 2:
                hypotheses.append({
                    "label_fa": "عامل مرتبط / فرضیه (همبستگی آماری — غیرعلّی)",
                    "dimension": dim_name,
                    "value": gval,
                    "metric": "rework_rate",
                    "observed_value": round(g_rew * 100.0, 1),
                    "baseline_value": round(overall_rework_rate * 100.0, 1),
                    "ratio": round(g_rew / overall_rework_rate, 2),
                    "sample_count": scnt,
                    "time_range": time_range_str,
                    "summary_fa": (
                        f"نرخ دوباره‌کاری در «{dim_name} = {gval}» برابر {round(g_rew * 100, 1)}% در مقایسه با میانگین "
                        f"{round(overall_rework_rate * 100, 1)}% است (n={scnt})."
                    ),
                    "limitations_fa": "ارتباط آماری نیازمند بررسی کارشناسی و تایید میدانی است.",
                })
    hypotheses.sort(key=lambda h: (-h["ratio"], -h["sample_count"]))

    proc_status = "computed" if _safe_int(cs.get("total_proc_cnt")) > 0 else "needs_data"
    wait_basis = "ستون صریح wait_hours" if wait_col else "اختلاف زمانی بین پایان گام قبلی و شروع گام بعدی"
    missing_ts_events = _safe_int(dq.get("missing_ts_events"))
    out_of_order_cases = _safe_int(cs.get("out_of_order_cases"))
    single_event_cases = _safe_int(cs.get("single_event_cases"))

    dq_warnings: list[str] = []
    if missing_ts_events > 0:
        dq_warnings.append(f"{missing_ts_events:,} رویداد فاقد برچسب زمانی معتبر بودند.")
    if out_of_order_cases > 0:
        dq_warnings.append(f"{out_of_order_cases:,} پرونده با ترتیب زمانی نامنظم بر اساس زمان مرتب‌سازی مجدد شدند.")
    if single_event_cases > 0:
        dq_warnings.append(f"{single_event_cases:,} پرونده تک‌رویدادی هستند (نشانگر فرآیند ناتمام یا ثبت خلاصه‌شده).")
    if not ref_seq:
        dq_warnings.append("مسیر مرجع (Reference Sequence) تعریف نشده است؛ تشخیص گام‌های پرش‌شده (Skips) نیازمند تعریف مسیر مرجع است.")

    valid_events = _safe_int(dq.get("valid_events"), total_matching)
    sla_eval_cases = _safe_int(cs.get("sla_evaluated_cases"))
    sla_br_cases = _safe_int(cs.get("sla_breach_cases"))

    return {
        "status": "ok",
        "trace_mode": "full_sql_traces",
        "dataset_id": ds.id,
        "dataset_name": ds.name,
        "active_process": active_process,
        "processes": processes,
        "mapping_info": {**inferred, "effective_mapping": mapping, "reference_sequence": ref_seq},
        "summary": {
            "total_events_analyzed": total_matching,
            "total_events_matching": total_matching,
            "valid_events": valid_events,
            "total_cases": total_cases,
            "unique_activities": _safe_int(dq.get("unique_activities")),
            "unique_variants": _safe_int(cs.get("unique_variants")),
            "time_range": time_range_str,
            "avg_cycle_hours": _safe_float(cs.get("avg_cycle_hours")),
            "median_cycle_hours": _safe_float(cs.get("median_cycle_hours")),
            "avg_wait_hours": _safe_float(cs.get("avg_wait_hours")),
            "avg_processing_hours": _safe_float(cs.get("avg_processing_hours")),
            "processing_time_status": proc_status,
            "processing_time_note_fa": (
                "محاسبه‌شده از اختلاف end_time و start_time" if proc_status == "computed"
                else "نیازمند هر دو ستون start_time و end_time برای محاسبه مجزای زمان پردازش واقعی است."
            ),
            "wait_time_basis_fa": wait_basis,
            "rework_case_count": _safe_int(cs.get("rework_case_count")),
            "rework_rate_pct": round(_safe_int(cs.get("rework_case_count")) * 100.0 / max(total_cases, 1), 1),
            "self_loop_case_count": _safe_int(cs.get("self_loop_case_count")),
            "skip_case_count": _safe_int(cs.get("skip_case_count")) if ref_seq else None,
            "sla_evaluated_cases": sla_eval_cases,
            "sla_breach_cases": sla_br_cases,
            "sla_breach_rate_pct": round(sla_br_cases * 100.0 / max(sla_eval_cases, 1), 1) if sla_eval_cases > 0 else None,
        },
        "data_quality": {
            "missing_case_or_activity_events": _safe_int(dq.get("missing_case_or_activity")),
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
        "assignees_by_step": assignees_by_step,
        "hypotheses": hypotheses[:12],
        "cases_preview": cases_preview,
    }


def list_or_get_case_details(
    db: Session,
    ds: Dataset,
    user: User,
    *,
    case_id: Optional[str] = None,
    activity_filter: Optional[str] = None,
    process_name: Optional[str] = None,
    only_rework: bool = False,
    only_sla_breach: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Permission-enforced DuckDB SQL drill-down into filtered cases or single case chronological timeline."""
    inferred = infer_event_log_mapping(db, ds, user)
    mapping = dict(inferred["mapping"])
    base_all_cte, _, base_cte, proc_params, vis_cols, sec_ctx = _build_process_filtered_sql_base(
        db, ds, user, mapping, process_name=process_name
    )
    vis_set = set(vis_cols)
    case_col = mapping.get("case_id") if mapping.get("case_id") in vis_set else None
    act_col = mapping.get("activity") if mapping.get("activity") in vis_set else None
    if not case_col or not act_col:
        return {
            "status": "needs_mapping",
            "mapping_info": inferred,
            "error_fa": "برای مشاهده پرونده‌ها، ستون شناسه پرونده (Case ID) و گام لازم است.",
            "total": 0,
            "cases": [],
            "events": [],
        }

    ts_col = mapping.get("timestamp") if mapping.get("timestamp") in vis_set else (mapping.get("start_time") if mapping.get("start_time") in vis_set else None)
    actor_col = mapping.get("actor") if mapping.get("actor") in vis_set else None
    dept_col = mapping.get("department") if mapping.get("department") in vis_set else None
    wait_col = mapping.get("wait_hours") if mapping.get("wait_hours") in vis_set else None
    sla_col = mapping.get("sla_hours") if mapping.get("sla_hours") in vis_set else None
    status_col = mapping.get("status") if mapping.get("status") in vis_set else None
    rework_col = mapping.get("rework_flag") if mapping.get("rework_flag") in vis_set else None

    if case_id:
        # Handle composite process::case_id or plain case_id cleanly
        raw_cid = case_id.split("::", 1)[-1] if "::" in case_id else case_id
        ts_sql = _sql_ts_expr(ts_col) if ts_col else "CAST(NULL AS TIMESTAMP)"
        sel_parts = [f'__pi_seq_idx', f'CAST("{act_col}" AS VARCHAR) AS activity', f'CAST({ts_sql} AS VARCHAR) AS ts_iso']
        if ts_col:
            sel_parts.append(f'CAST("{ts_col}" AS VARCHAR) AS ts_raw')
        if actor_col:
            sel_parts.append(f'CAST("{actor_col}" AS VARCHAR) AS "{actor_col}"')
        if dept_col:
            sel_parts.append(f'CAST("{dept_col}" AS VARCHAR) AS "{dept_col}"')
        if wait_col:
            sel_parts.append(f'TRY_CAST("{wait_col}" AS DOUBLE) AS wait_hours')
        if sla_col:
            sel_parts.append(f'TRY_CAST("{sla_col}" AS DOUBLE) AS sla_hours')
        if status_col:
            sel_parts.append(f'CAST("{status_col}" AS VARCHAR) AS status')
        if rework_col:
            sel_parts.append(f'CAST("{rework_col}" AS VARCHAR) AS rework_raw')

        norm_case_col = _sql_norm_col(case_col)
        ev_res = warehouse.execute_query(
            f"WITH {base_cte} "
            f"SELECT {', '.join(sel_parts)} FROM _p "
            f"WHERE LOWER({norm_case_col}) = ? "
            f"ORDER BY ts_iso NULLS LAST, __pi_seq_idx LIMIT 500",
            proc_params + [_norm_row_val(raw_cid)],
        )
        _, masked_evs = apply_cls_and_masking(ev_res["columns"], ev_res["rows"], sec_ctx)
        events = []
        for idx, r in enumerate(masked_evs):
            rw = str(r.get("rework_raw") or "").strip().lower() in ("1", "true", "yes", "y", "rework", "returned", "بله")
            events.append({
                "seq": idx + 1,
                "activity": r.get("activity"),
                "timestamp": r.get("ts_iso") or r.get("ts_raw"),
                "actor": r.get(actor_col) if actor_col else None,
                "department": r.get(dept_col) if dept_col else None,
                "wait_hours": _safe_float(r.get("wait_hours")),
                "sla_hours": _safe_float(r.get("sla_hours")),
                "status": r.get("status"),
                "rework_flag": rw,
            })
        return {
            "case_id": raw_cid,
            "case_summary": None,
            "events": events,
        }

    # SQL case aggregation across 100% of rows
    wait_expr = f'TRY_CAST("{wait_col}" AS DOUBLE)' if wait_col else "CAST(NULL AS DOUBLE)"
    sla_expr = f'TRY_CAST("{sla_col}" AS DOUBLE)' if sla_col else "CAST(NULL AS DOUBLE)"
    rw_sql = (
        f"CASE WHEN LOWER(TRIM(COALESCE(CAST(\"{rework_col}\" AS VARCHAR), ''))) IN ('1','true','yes','y','rework','returned','بله') THEN 1 ELSE 0 END"
        if rework_col
        else "0"
    )
    sla_br_sql = (
        f"CASE WHEN {wait_expr} IS NOT NULL AND {sla_expr} IS NOT NULL AND {wait_expr} > {sla_expr} THEN 1 ELSE 0 END"
        if (wait_col and sla_col)
        else "0"
    )
    ts_sql = _sql_ts_expr(ts_col) if ts_col else "CAST(NULL AS TIMESTAMP)"

    having_parts: list[str] = []
    q_params = list(proc_params)
    norm_act_col = _sql_norm_col(act_col)
    norm_case_col = _sql_norm_col(case_col)
    if activity_filter:
        having_parts.append(f"SUM(CASE WHEN LOWER({norm_act_col}) = ? THEN 1 ELSE 0 END) > 0")
        q_params.append(_norm_row_val(activity_filter))
    if only_rework:
        having_parts.append("(COUNT(*) > COUNT(DISTINCT act_norm) OR MAX(rw_flag) > 0)")
    if only_sla_breach:
        having_parts.append("MAX(sla_br) > 0")

    having_sql = f" HAVING {' AND '.join(having_parts)}" if having_parts else ""
    safe_limit = max(1, min(int(limit), 500))
    safe_offset = max(0, int(offset))

    case_cte = (
        f"{base_cte}, "
        f"_c_ev AS ("
        f"  SELECT {norm_case_col} AS case_id, "
        f"  {norm_act_col} AS act_norm, "
        f"  {ts_sql} AS eff_ts, __pi_seq_idx, "
        f"  {wait_expr} AS wv, {rw_sql} AS rw_flag, {sla_br_sql} AS sla_br "
        f"  FROM _p WHERE {norm_case_col} != ''"
        f"), "
        f"_c_agg AS ("
        f"  SELECT case_id, COUNT(*) AS event_count, "
        f"  STRING_AGG(act_norm, '|||' ORDER BY COALESCE(eff_ts, TIMESTAMP '9999-12-31 00:00:00'), __pi_seq_idx) AS acts_joined, "
        f"  CASE WHEN (COUNT(*) > COUNT(DISTINCT act_norm) OR MAX(rw_flag) > 0) THEN TRUE ELSE FALSE END AS has_rework, "
        f"  CASE WHEN MAX(sla_br) > 0 THEN TRUE ELSE FALSE END AS sla_breached, "
        f"  ROUND(SUM(wv), 2) AS wait_hours "
        f"  FROM _c_ev GROUP BY case_id{having_sql}"
        f")"
    )
    tot_res = warehouse.execute_query(f"WITH {case_cte} SELECT COUNT(*) AS cnt FROM _c_agg", q_params)
    total = _safe_int(tot_res["rows"][0]["cnt"]) if tot_res["rows"] else 0

    rows_res = warehouse.execute_query(
        f"WITH {case_cte} SELECT case_id, event_count, acts_joined, has_rework, sla_breached, wait_hours "
        f"FROM _c_agg ORDER BY wait_hours DESC NULLS LAST, event_count DESC LIMIT {safe_limit} OFFSET {safe_offset}",
        q_params,
    )
    cases = [
        {
            "case_id": r["case_id"],
            "event_count": _safe_int(r["event_count"]),
            "activities": [a for a in str(r.get("acts_joined") or "").split("|||") if a],
            "has_rework": bool(r.get("has_rework")),
            "sla_breached": bool(r.get("sla_breached")),
            "wait_hours": _safe_float(r.get("wait_hours")),
        }
        for r in rows_res["rows"]
    ]
    return {
        "total": total,
        "limit": safe_limit,
        "offset": safe_offset,
        "cases": cases,
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
