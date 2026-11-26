from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import enforce_permission, get_client_ip, get_current_user
from app.core.audit import record_audit
from app.core.cache import cache_manager
from app.core.permissions import permission_resolver
from app.core.tenant import get_dataset_for_org
from app.database import get_db
from app.engines.bottleneck_rules import PRESET_TEMPLATES, build_formula_expression_text
from app.engines.process_intelligence import (
    analyze_process_intelligence,
    discover_dataset_processes,
    evaluate_corrective_action,
    infer_event_log_mapping,
    list_or_get_case_details,
    save_event_log_mapping,
)
from app.models.identity import User
from app.models.intelligence import BpmnBottleneckRule, BpmnLayoutConfig, ProcessCorrectiveAction

router = APIRouter(prefix="/process-intelligence", tags=["Process Intelligence & BPMN"])


class ProcessMappingUpdate(BaseModel):
    mapping: dict[str, Optional[str]] = Field(default_factory=dict)
    reference_sequence: Optional[list[str]] = None
    confirmed: bool = True


class ProcessAnalyzeRequest(BaseModel):
    mapping: Optional[dict[str, Optional[str]]] = None
    reference_sequence: Optional[list[str]] = None
    date_start: Optional[str] = None
    date_end: Optional[str] = None
    filters: Optional[dict[str, Any]] = None
    process_name: Optional[str] = None


class CorrectiveActionCreate(BaseModel):
    title: str
    description: str = ""
    target_activity: str = ""
    kpi_metric: str = "avg_cycle_hours"
    baseline_start: Optional[str] = None
    baseline_end: Optional[str] = None
    post_start: Optional[str] = None
    post_end: Optional[str] = None
    filter_snapshot_json: dict[str, Any] = Field(default_factory=dict)


@router.get("/datasets/{dataset_id}/mapping")
def get_process_mapping(
    dataset_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ds = get_dataset_for_org(db, dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", ds.id, "view")
    enforce_permission(db, user, "process_intel", "*", "view")
    return infer_event_log_mapping(db, ds, user)


@router.post("/datasets/{dataset_id}/mapping")
def update_process_mapping(
    dataset_id: str,
    body: ProcessMappingUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ds = get_dataset_for_org(db, dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", ds.id, "view", ip_address=get_client_ip(request))
    enforce_permission(db, user, "process_intel", "*", "manage_mapping", ip_address=get_client_ip(request))
    try:
        res = save_event_log_mapping(
            db, ds, user, body.mapping, reference_sequence=body.reference_sequence, confirmed=body.confirmed
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    record_audit(
        db, user.organization_id, "process_mapping_saved", "dataset",
        resource_id=ds.id, user_id=user.id, username=user.username, ip_address=get_client_ip(request),
    )
    return res


@router.get("/datasets/{dataset_id}/processes")
def list_dataset_processes(
    dataset_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ds = get_dataset_for_org(db, dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", ds.id, "view")
    enforce_permission(db, user, "process_intel", "*", "view")
    return discover_dataset_processes(db, ds, user)


@router.post("/datasets/{dataset_id}/analyze")
def run_process_analysis(
    dataset_id: str,
    body: ProcessAnalyzeRequest = ProcessAnalyzeRequest(),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ds = get_dataset_for_org(db, dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", ds.id, "view")
    enforce_permission(db, user, "process_intel", "*", "view")
    return analyze_process_intelligence(
        db,
        ds,
        user,
        mapping_override=body.mapping,
        reference_sequence_override=body.reference_sequence,
        date_start=body.date_start,
        date_end=body.date_end,
        filters=body.filters,
        process_name=body.process_name,
    )


@router.get("/datasets/{dataset_id}/cases")
def drill_down_cases(
    dataset_id: str,
    activity: Optional[str] = Query(None),
    process_name: Optional[str] = Query(None),
    only_rework: bool = Query(False),
    only_sla_breach: bool = Query(False),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ds = get_dataset_for_org(db, dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", ds.id, "view")
    enforce_permission(db, user, "process_intel", "*", "view_case_details")
    d_raw = permission_resolver.resolve(
        db, user, "drill_down", ds.id, "view_raw_records",
        parent_chain=[("dataset", ds.id)],
    )
    if not d_raw.allowed and d_raw.reason.startswith("explicit_deny"):
        raise HTTPException(status_code=403, detail="Access denied: raw record drill-down is explicitly denied")
    return list_or_get_case_details(
        db, ds, user,
        activity_filter=activity,
        process_name=process_name,
        only_rework=only_rework,
        only_sla_breach=only_sla_breach,
        limit=limit,
        offset=offset,
    )


@router.get("/datasets/{dataset_id}/cases/{case_id}")
def drill_down_single_case(
    dataset_id: str,
    case_id: str,
    process_name: Optional[str] = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ds = get_dataset_for_org(db, dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", ds.id, "view")
    enforce_permission(db, user, "process_intel", "*", "view_case_details")
    d_raw = permission_resolver.resolve(
        db, user, "drill_down", ds.id, "view_raw_records",
        parent_chain=[("dataset", ds.id)],
    )
    if not d_raw.allowed and d_raw.reason.startswith("explicit_deny"):
        raise HTTPException(status_code=403, detail="Access denied: raw record drill-down is explicitly denied")
    return list_or_get_case_details(db, ds, user, case_id=case_id, process_name=process_name)


@router.get("/datasets/{dataset_id}/actions")
def list_corrective_actions(
    dataset_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ds = get_dataset_for_org(db, dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", ds.id, "view")
    enforce_permission(db, user, "process_intel", "*", "view")
    rows = (
        db.query(ProcessCorrectiveAction)
        .filter(
            ProcessCorrectiveAction.organization_id == user.organization_id,
            ProcessCorrectiveAction.dataset_id == ds.id,
        )
        .order_by(ProcessCorrectiveAction.created_at.desc())
        .all()
    )
    return [
        {
            "id": r.id,
            "title": r.title,
            "description": r.description,
            "target_activity": r.target_activity,
            "kpi_metric": r.kpi_metric,
            "baseline_start": r.baseline_start,
            "baseline_end": r.baseline_end,
            "post_start": r.post_start,
            "post_end": r.post_end,
            "filter_snapshot_json": r.filter_snapshot_json,
            "status": r.status,
            "measured_before": r.measured_before,
            "measured_after": r.measured_after,
            "delta_pct": r.delta_pct,
            "sample_before": r.sample_before,
            "sample_after": r.sample_after,
            "evaluation_notes": r.evaluation_notes_json,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.post("/datasets/{dataset_id}/actions")
def create_corrective_action(
    dataset_id: str,
    body: CorrectiveActionCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ds = get_dataset_for_org(db, dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", ds.id, "view", ip_address=get_client_ip(request))
    enforce_permission(db, user, "process_intel", "*", "manage_actions", ip_address=get_client_ip(request))
    act = ProcessCorrectiveAction(
        organization_id=user.organization_id,
        dataset_id=ds.id,
        title=body.title,
        description=body.description,
        target_activity=body.target_activity,
        kpi_metric=body.kpi_metric,
        baseline_start=body.baseline_start,
        baseline_end=body.baseline_end,
        post_start=body.post_start,
        post_end=body.post_end,
        filter_snapshot_json=body.filter_snapshot_json,
        status="planned",
        created_by=user.id,
    )
    db.add(act)
    db.commit()
    record_audit(
        db, user.organization_id, "process_corrective_action_created", "process_corrective_action",
        resource_id=act.id, user_id=user.id, username=user.username, ip_address=get_client_ip(request),
    )
    return evaluate_corrective_action(db, ds, user, act)


@router.post("/actions/{action_id}/evaluate")
def reevaluate_corrective_action(
    action_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    act = (
        db.query(ProcessCorrectiveAction)
        .filter(
            ProcessCorrectiveAction.id == action_id,
            ProcessCorrectiveAction.organization_id == user.organization_id,
        )
        .first()
    )
    if not act:
        raise HTTPException(status_code=404, detail="Corrective action not found")
    ds = get_dataset_for_org(db, act.dataset_id, user.organization_id)
    enforce_permission(db, user, "process_intel", "*", "manage_actions", ip_address=get_client_ip(request))
    return evaluate_corrective_action(db, ds, user, act)


class BottleneckRuleCreate(BaseModel):
    name: str
    process_name: Optional[str] = None
    weights: dict[str, float] = Field(default_factory=dict)
    min_wait_hours: float = 0.0
    threshold_score: float = 60.0
    critical_threshold: float = 75.0
    severity: str = "high"
    is_active: bool = True


class BpmnLayoutSave(BaseModel):
    dataset_id: str
    widget_id: Optional[str] = None
    process_name: Optional[str] = None
    node_positions: dict[str, dict[str, float]] = Field(default_factory=dict)
    zoom: float = 1.0
    pan_x: float = 0.0
    pan_y: float = 0.0
    reset: bool = False


def _bpmn_scope_key(dataset_id: str, widget_id: Optional[str], process_name: Optional[str]) -> str:
    return f"ds:{dataset_id}|w:{widget_id or '*'}|p:{process_name or '*'}"


@router.get("/datasets/{dataset_id}/bottleneck-rules")
def list_bottleneck_rules(
    dataset_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ds = get_dataset_for_org(db, dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", ds.id, "view")
    enforce_permission(db, user, "process_intel", "*", "view")
    rows = (
        db.query(BpmnBottleneckRule)
        .filter(
            BpmnBottleneckRule.organization_id == user.organization_id,
            (BpmnBottleneckRule.dataset_id == ds.id) | (BpmnBottleneckRule.dataset_id == None),
        )
        .order_by(BpmnBottleneckRule.updated_at.desc())
        .all()
    )
    return {
        "presets": PRESET_TEMPLATES,
        "rules": [
            {
                "id": r.id,
                "dataset_id": r.dataset_id,
                "process_name": None if r.process_name == "*" else r.process_name,
                "name": r.name,
                "formula_json": r.metric_weights_json,
                "formula_expression": r.formula_expression,
                "threshold_score": r.warning_threshold,
                "critical_threshold": r.critical_threshold,
                "severity": "critical" if r.critical_threshold <= 55 else "high",
                "is_active": r.is_active,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ],
    }


@router.post("/datasets/{dataset_id}/bottleneck-rules")
def save_bottleneck_rule(
    dataset_id: str,
    body: BottleneckRuleCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ds = get_dataset_for_org(db, dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", ds.id, "view", ip_address=get_client_ip(request))
    enforce_permission(db, user, "process_intel", "*", "manage_actions", ip_address=get_client_ip(request))

    eff_proc = body.process_name or "*"
    if body.is_active:
        db.query(BpmnBottleneckRule).filter(
            BpmnBottleneckRule.organization_id == user.organization_id,
            BpmnBottleneckRule.dataset_id == ds.id,
            BpmnBottleneckRule.process_name == eff_proc,
        ).update({"is_active": False})

    clean_weights = {k: max(0.0, float(v)) for k, v in (body.weights or {}).items()}
    warn_thr = max(1.0, min(100.0, float(body.threshold_score)))
    crit_thr = max(warn_thr, min(100.0, float(body.critical_threshold)))
    if body.severity == "critical":
        crit_thr = warn_thr

    rule = BpmnBottleneckRule(
        organization_id=user.organization_id,
        dataset_id=ds.id,
        process_name=eff_proc,
        step_name="*",
        name=body.name.strip() or "فرمول سفارشی گلوگاه",
        formula_expression=build_formula_expression_text(clean_weights),
        warning_threshold=warn_thr,
        critical_threshold=crit_thr,
        metric_weights_json={
            "weights": clean_weights,
            "min_wait_hours": max(0.0, float(body.min_wait_hours)),
        },
        is_active=body.is_active,
        created_by=user.id,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    cache_manager.invalidate_dataset(ds.id)
    record_audit(
        db, user.organization_id, "bpmn_bottleneck_rule_saved", "process_intel",
        resource_id=rule.id, user_id=user.id, username=user.username, ip_address=get_client_ip(request),
        details={"dataset_id": ds.id, "name": rule.name, "threshold": rule.warning_threshold},
    )
    return {
        "id": rule.id,
        "dataset_id": rule.dataset_id,
        "process_name": None if rule.process_name == "*" else rule.process_name,
        "name": rule.name,
        "formula_json": rule.metric_weights_json,
        "threshold_score": rule.warning_threshold,
        "critical_threshold": rule.critical_threshold,
        "severity": body.severity,
        "is_active": rule.is_active,
    }


@router.delete("/datasets/{dataset_id}/bottleneck-rules/{rule_id}")
def delete_bottleneck_rule(
    dataset_id: str,
    rule_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ds = get_dataset_for_org(db, dataset_id, user.organization_id)
    enforce_permission(db, user, "process_intel", "*", "manage_actions", ip_address=get_client_ip(request))
    rule = (
        db.query(BpmnBottleneckRule)
        .filter(
            BpmnBottleneckRule.id == rule_id,
            BpmnBottleneckRule.organization_id == user.organization_id,
            BpmnBottleneckRule.dataset_id == ds.id,
        )
        .first()
    )
    if not rule:
        raise HTTPException(status_code=404, detail="Bottleneck rule not found")
    db.delete(rule)
    db.commit()
    cache_manager.invalidate_dataset(ds.id)
    return {"status": "deleted"}


@router.get("/bpmn-layout")
def get_bpmn_layout(
    dataset_id: str,
    widget_id: Optional[str] = None,
    process_name: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ds = get_dataset_for_org(db, dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", ds.id, "view")
    skey = _bpmn_scope_key(ds.id, widget_id, process_name)
    row = (
        db.query(BpmnLayoutConfig)
        .filter(
            BpmnLayoutConfig.organization_id == user.organization_id,
            BpmnLayoutConfig.scope_key == skey,
        )
        .first()
    )
    if not row:
        return {"node_positions": {}, "zoom": 1.0, "pan_x": 0.0, "pan_y": 0.0}
    disp = row.display_config_json or {}
    return {
        "id": row.id,
        "node_positions": row.node_positions_json or {},
        "zoom": disp.get("zoom", 1.0),
        "pan_x": disp.get("pan_x", 0.0),
        "pan_y": disp.get("pan_y", 0.0),
    }


@router.put("/bpmn-layout")
def save_bpmn_layout(
    body: BpmnLayoutSave,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ds = get_dataset_for_org(db, body.dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", ds.id, "view", ip_address=get_client_ip(request))
    skey = _bpmn_scope_key(ds.id, body.widget_id, body.process_name)
    row = (
        db.query(BpmnLayoutConfig)
        .filter(
            BpmnLayoutConfig.organization_id == user.organization_id,
            BpmnLayoutConfig.scope_key == skey,
        )
        .first()
    )
    if body.reset:
        if row:
            db.delete(row)
            db.commit()
        cache_manager.invalidate_dataset(ds.id)
        return {"status": "reset", "node_positions": {}}

    if not row:
        row = BpmnLayoutConfig(
            organization_id=user.organization_id,
            scope_key=skey,
            updated_by=user.id,
        )
        db.add(row)
    row.node_positions_json = body.node_positions
    row.display_config_json = {
        "zoom": max(0.4, min(2.5, float(body.zoom or 1.0))),
        "pan_x": float(body.pan_x or 0.0),
        "pan_y": float(body.pan_y or 0.0),
    }
    row.updated_by = user.id
    db.commit()
    db.refresh(row)
    cache_manager.invalidate_dataset(ds.id)
    disp = row.display_config_json or {}
    return {
        "id": row.id,
        "node_positions": row.node_positions_json,
        "zoom": disp.get("zoom", 1.0),
        "pan_x": disp.get("pan_x", 0.0),
        "pan_y": disp.get("pan_y", 0.0),
    }
