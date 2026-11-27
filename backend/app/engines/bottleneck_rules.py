from __future__ import annotations

from typing import Any, Optional
from sqlalchemy.orm import Session
from app.models.intelligence import BpmnBottleneckRule

DEFAULT_FORMULA_WEIGHTS: dict[str, float] = {
    "avg_wait": 0.30,
    "max_wait": 0.15,
    "pending_count": 0.20,
    "overdue_ratio": 0.20,
    "rework_rate": 0.10,
    "volume_share": 0.05,
}

PRESET_TEMPLATES: list[dict[str, Any]] = [
    {
        "key": "weighted_wait_sla",
        "label_fa": "ترکیب استاندارد انتظار، کارتابل و نقض SLA",
        "weights": {
            "avg_wait": 0.30,
            "max_wait": 0.15,
            "pending_count": 0.20,
            "overdue_ratio": 0.20,
            "rework_rate": 0.10,
            "volume_share": 0.05,
        },
        "threshold_score": 60.0,
        "severity": "high",
    },
    {
        "key": "sla_strict",
        "label_fa": "سخت‌گیرانه مبتنی بر نقض SLA و بیشترین تاخیر",
        "weights": {
            "avg_wait": 0.20,
            "max_wait": 0.25,
            "pending_count": 0.15,
            "overdue_ratio": 0.35,
            "rework_rate": 0.05,
            "volume_share": 0.0,
        },
        "threshold_score": 50.0,
        "severity": "critical",
    },
    {
        "key": "queue_saturation",
        "label_fa": "اشباع صف و تراکم کارتابل",
        "weights": {
            "avg_wait": 0.20,
            "max_wait": 0.05,
            "pending_count": 0.45,
            "overdue_ratio": 0.15,
            "rework_rate": 0.05,
            "volume_share": 0.10,
        },
        "threshold_score": 55.0,
        "severity": "high",
    },
    {
        "key": "rework_pressure",
        "label_fa": "محور دوباره‌کاری و چرخش مجدد پرونده‌ها",
        "weights": {
            "avg_wait": 0.20,
            "max_wait": 0.10,
            "pending_count": 0.15,
            "overdue_ratio": 0.15,
            "rework_rate": 0.35,
            "volume_share": 0.05,
        },
        "threshold_score": 55.0,
        "severity": "medium",
    },
]


def load_active_bottleneck_rule(
    db: Session,
    *,
    organization_id: str,
    dataset_id: Optional[str] = None,
    process_name: Optional[str] = None,
) -> Optional[BpmnBottleneckRule]:
    q = db.query(BpmnBottleneckRule).filter(
        BpmnBottleneckRule.organization_id == organization_id,
        BpmnBottleneckRule.is_active == True,
    )
    if dataset_id:
        q = q.filter((BpmnBottleneckRule.dataset_id == dataset_id) | (BpmnBottleneckRule.dataset_id == None))
    rows = q.order_by(BpmnBottleneckRule.updated_at.desc()).all()
    if process_name:
        for r in rows:
            if r.process_name and r.process_name != "*" and r.process_name == process_name:
                return r
    for r in rows:
        if r.dataset_id == dataset_id and (not r.process_name or r.process_name == "*"):
            return r
    return rows[0] if rows else None


def build_formula_expression_text(weights: dict[str, float]) -> str:
    parts = [f"{round(float(v), 2)}*{k}" for k, v in weights.items() if float(v) > 0]
    return " + ".join(parts) if parts else "0.3*avg_wait + 0.2*pending_count + 0.2*overdue_ratio"


def score_steps_with_rule(
    steps: list[dict[str, Any]],
    rule: Optional[BpmnBottleneckRule] = None,
    weights_override: Optional[dict[str, float]] = None,
    threshold_override: Optional[float] = None,
) -> list[dict[str, Any]]:
    if not steps:
        return steps

    raw_weights = dict(DEFAULT_FORMULA_WEIGHTS)
    warn_threshold = 40.0
    crit_threshold = 70.0
    rule_name = "فرمول پیش‌فرض پلتفرم"
    min_wait_floor = 0.0

    if rule:
        mw = rule.metric_weights_json or {}
        w = mw.get("weights") if isinstance(mw.get("weights"), dict) else mw
        if isinstance(w, dict) and w:
            for k in raw_weights:
                if k in w:
                    try:
                        raw_weights[k] = max(0.0, float(w[k]))
                    except Exception:
                        pass
        if isinstance(mw, dict) and mw.get("min_wait_hours") is not None:
            try:
                min_wait_floor = max(0.0, float(mw.get("min_wait_hours")))
            except Exception:
                pass
        warn_threshold = float(rule.warning_threshold or 40.0)
        crit_threshold = float(rule.critical_threshold or 70.0)
        rule_name = rule.name or rule_name

    if isinstance(weights_override, dict) and weights_override:
        for k in raw_weights:
            if k in weights_override:
                try:
                    raw_weights[k] = max(0.0, float(weights_override[k]))
                except Exception:
                    pass
    if threshold_override is not None:
        warn_threshold = float(threshold_override)

    w_sum = sum(raw_weights.values()) or 1.0
    norm_w = {k: v / w_sum for k, v in raw_weights.items()}

    max_avg = max((float(s.get("avg_wait_hours") or 0.0) for s in steps), default=1.0) or 1.0
    max_max = max((float(s.get("max_wait_hours") or 0.0) for s in steps), default=1.0) or 1.0
    max_pend = max((float(s.get("pending_count") or 0.0) for s in steps), default=1.0) or 1.0
    total_vol = sum((float(s.get("total_count") or 0.0) for s in steps)) or 1.0

    max_score_idx = -1
    max_score_val = -1.0

    for idx, s in enumerate(steps):
        avg_w = float(s.get("avg_wait_hours") or 0.0)
        max_w = float(s.get("max_wait_hours") or 0.0)
        pend = float(s.get("pending_count") or 0.0)
        tot = max(1.0, float(s.get("total_count") or 0.0))
        overdue = float(s.get("overdue_count") or 0.0)
        sla_breach_pct = float(s.get("sla_breach_pct") or ((overdue / tot) * 100.0))
        rework_rate = float(s.get("rework_rate") or 0.0)
        vol_share = (tot / total_vol) * 100.0

        c_avg = min(1.0, avg_w / max_avg) * 100.0
        c_max = min(1.0, max_w / max_max) * 100.0
        c_pend = min(1.0, pend / max_pend) * 100.0
        c_over = min(100.0, sla_breach_pct)
        c_rew = min(100.0, rework_rate)
        c_vol = min(100.0, vol_share * 2.0)

        score = round(
            norm_w["avg_wait"] * c_avg
            + norm_w["max_wait"] * c_max
            + norm_w["pending_count"] * c_pend
            + norm_w["overdue_ratio"] * c_over
            + norm_w["rework_rate"] * c_rew
            + norm_w["volume_share"] * c_vol,
            1,
        )
        is_crit = (score >= crit_threshold) and (avg_w >= min_wait_floor)
        is_bn = (score >= warn_threshold) and (avg_w >= min_wait_floor)
        sev = "critical" if is_crit else ("high" if is_bn else ("medium" if score >= 30 else "low"))

        s["bottleneck_score"] = score
        s["flow_share_pct"] = round(vol_share, 1)
        s["is_bottleneck"] = is_bn
        s["matched_rule"] = rule_name if is_bn else None
        s["severity"] = sev

        if score > max_score_val:
            max_score_val = score
            max_score_idx = idx

    if max_score_idx >= 0 and not any(s.get("is_bottleneck") for s in steps) and max_score_val > 0:
        steps[max_score_idx]["is_bottleneck"] = True
        steps[max_score_idx]["severity"] = "high"
        steps[max_score_idx]["matched_rule"] = rule_name

    return steps
