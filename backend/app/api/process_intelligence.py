from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import enforce_permission, get_client_ip, get_current_user
from app.core.audit import record_audit
from app.core.permissions import permission_resolver
from app.core.tenant import get_dataset_for_org
from app.database import get_db
from app.engines.process_intelligence import (
    analyze_process_intelligence,
    evaluate_corrective_action,
    infer_event_log_mapping,
    list_or_get_case_details,
    save_event_log_mapping,
)
from app.models.identity import User
from app.models.intelligence import ProcessCorrectiveAction

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
    )


@router.get("/datasets/{dataset_id}/cases")
def drill_down_cases(
    dataset_id: str,
    activity: Optional[str] = Query(None),
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
        only_rework=only_rework,
        only_sla_breach=only_sla_breach,
        limit=limit,
        offset=offset,
    )


@router.get("/datasets/{dataset_id}/cases/{case_id}")
def drill_down_single_case(
    dataset_id: str,
    case_id: str,
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
    return list_or_get_case_details(db, ds, user, case_id=case_id)


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
