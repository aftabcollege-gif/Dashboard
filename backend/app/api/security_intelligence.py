from datetime import datetime, timezone
from typing import Any, Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import enforce_permission, get_client_ip, get_current_user
from app.core.audit import record_audit
from app.database import get_db
from app.engines.security_intelligence import (
    SECURITY_CONNECTOR_CATALOG,
    enforce_collector_retention,
    evaluate_security_alerts,
    get_security_overview,
    ingest_security_batch,
    query_security_events_page,
)
from app.models.identity import User
from app.models.intelligence import (
    SecurityAlert,
    SecurityCollector,
    SecurityIncident,
    SecurityUnparsedEvent,
)

router = APIRouter(prefix="/security-intel", tags=["Security Log Intelligence"])


class CollectorCreate(BaseModel):
    name: str
    source_type: str = "syslog_security_jsonl"
    retention_days: int = Field(90, ge=1, le=3650)
    default_timezone: str = "UTC"
    config_json: dict[str, Any] = Field(default_factory=dict)


class SecurityBatchIngestRequest(BaseModel):
    collector_id: str
    source_name: str = "api_batch"
    items: list[Any] = Field(default_factory=list)


class IncidentCreateRequest(BaseModel):
    title: str
    description: str = ""
    severity: str = "medium"
    alert_ids: list[str] = Field(default_factory=list)
    recommended_action_fa: str = ""
    rollback_plan_fa: str = "بازگردانی دستی تنظیمات قبلی پس از تایید تیم عملیات"


class IncidentApproveRequest(BaseModel):
    human_approved: bool
    approval_note: str = ""
    rollback_plan_fa: Optional[str] = None
    new_status: Optional[str] = None


@router.get("/connectors")
def list_security_connectors(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "security_log", "*", "view")
    return {
        "connectors": SECURITY_CONNECTOR_CATALOG,
        "policy_note_fa": "فقط کانکتورهایی که برچسب پیاده‌سازی‌شده و آزمایش‌شده دارند فعال هستند؛ سایر کانکتورها به‌صورت شفاف در وضعیت طرح‌شده (نیازمند زیرساخت) نمایش داده می‌شوند.",
    }


@router.get("/collectors")
def list_collectors(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "security_log", "*", "view")
    rows = (
        db.query(SecurityCollector)
        .filter(SecurityCollector.organization_id == user.organization_id)
        .order_by(SecurityCollector.created_at.desc())
        .all()
    )
    return [
        {
            "id": c.id,
            "name": c.name,
            "source_type": c.source_type,
            "status": c.status,
            "retention_days": c.retention_days,
            "default_timezone": c.default_timezone,
            "last_run_at": c.last_run_at.isoformat() if c.last_run_at else None,
            "events_ingested": c.events_ingested,
            "events_deduplicated": c.events_deduplicated,
            "events_unparsed": c.events_unparsed,
            "retry_count": c.retry_count,
            "last_error": c.last_error,
        }
        for c in rows
    ]


@router.post("/collectors")
def create_collector(
    body: CollectorCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(db, user, "security_log", "*", "ingest", ip_address=get_client_ip(request))
    if body.source_type != "syslog_security_jsonl":
        raise HTTPException(
            status_code=400,
            detail=f"کانکتور '{body.source_type}' در حال حاضر در وضعیت طرح‌شده و آزمایش‌نشده است (نیازمند زیرساخت).",
        )
    col = SecurityCollector(
        organization_id=user.organization_id,
        name=body.name,
        source_type=body.source_type,
        status="active",
        retention_days=body.retention_days,
        default_timezone=body.default_timezone,
        config_json=body.config_json,
        created_by=user.id,
    )
    db.add(col)
    db.commit()
    record_audit(
        db, user.organization_id, "security_collector_created", "security_collector",
        resource_id=col.id, user_id=user.id, username=user.username, ip_address=get_client_ip(request),
    )
    return {"id": col.id, "name": col.name, "source_type": col.source_type, "status": col.status}


@router.post("/ingest/batch")
def ingest_batch_events(
    body: SecurityBatchIngestRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(db, user, "security_log", "*", "ingest", ip_address=get_client_ip(request))
    col = (
        db.query(SecurityCollector)
        .filter(
            SecurityCollector.id == body.collector_id,
            SecurityCollector.organization_id == user.organization_id,
        )
        .first()
    )
    if not col:
        raise HTTPException(status_code=404, detail="Collector not found")
    try:
        res = ingest_security_batch(db, user.organization_id, col, body.items, source_name=body.source_name)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    record_audit(
        db, user.organization_id, "security_batch_ingested", "security_collector",
        resource_id=col.id, user_id=user.id, username=user.username, ip_address=get_client_ip(request),
        details={"ingested": res["ingested"], "dedup": res["deduplicated"], "unparsed": res["unparsed_quarantined"]},
    )
    return res


@router.post("/ingest/file")
async def ingest_log_file(
    request: Request,
    collector_id: str = Form(...),
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(db, user, "security_log", "*", "ingest", ip_address=get_client_ip(request))
    col = (
        db.query(SecurityCollector)
        .filter(
            SecurityCollector.id == collector_id,
            SecurityCollector.organization_id == user.organization_id,
        )
        .first()
    )
    if not col:
        raise HTTPException(status_code=404, detail="Collector not found")
    raw_bytes = await file.read()
    if len(raw_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="حداکثر حجم فایل ۱۰ مگابایت است")
    lines = [ln for ln in raw_bytes.decode("utf-8", errors="replace").splitlines() if ln.strip()]
    res = ingest_security_batch(db, user.organization_id, col, lines, source_name=file.filename or "upload.log")
    record_audit(
        db, user.organization_id, "security_file_ingested", "security_collector",
        resource_id=col.id, user_id=user.id, username=user.username, ip_address=get_client_ip(request),
        details={"filename": file.filename, "ingested": res["ingested"], "unparsed": res["unparsed_quarantined"]},
    )
    return res


@router.post("/collectors/{collector_id}/enforce-retention")
def run_collector_retention(
    collector_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(db, user, "security_log", "*", "ingest", ip_address=get_client_ip(request))
    col = (
        db.query(SecurityCollector)
        .filter(SecurityCollector.id == collector_id, SecurityCollector.organization_id == user.organization_id)
        .first()
    )
    if not col:
        raise HTTPException(status_code=404, detail="Collector not found")
    purged = enforce_collector_retention(db, user.organization_id, col)
    record_audit(
        db, user.organization_id, "security_retention_purged", "security_collector",
        resource_id=col.id, user_id=user.id, username=user.username, details={"purged": purged},
    )
    return {"collector_id": col.id, "purged_events": purged}


@router.get("/overview")
def security_overview(
    hours: int = Query(168, ge=1, le=8760),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(db, user, "security_log", "*", "view")
    return get_security_overview(db, user.organization_id, hours=hours)


@router.get("/events")
def list_security_events(
    action: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    username: Optional[str] = Query(None),
    hostname: Optional[str] = Query(None),
    src_ip: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(db, user, "security_log", "*", "view")
    return query_security_events_page(
        db, user, action=action, severity=severity, username=username, hostname=hostname, src_ip=src_ip, limit=limit, offset=offset
    )


@router.get("/unparsed")
def list_unparsed_events(
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(db, user, "security_log", "*", "view")
    # Strict independent permission required for raw quarantined lines!
    enforce_permission(db, user, "security_log", "*", "view_raw")
    rows = (
        db.query(SecurityUnparsedEvent)
        .filter(SecurityUnparsedEvent.organization_id == user.organization_id)
        .order_by(SecurityUnparsedEvent.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "collector_id": r.collector_id,
            "source_name": r.source_name,
            "line_number": r.line_number,
            "raw_line": r.raw_line,
            "parse_error": r.parse_error,
            "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.post("/alerts/evaluate")
def run_alert_evaluation(
    request: Request,
    zscore_threshold: float = Query(2.0, ge=1.0, le=10.0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(db, user, "security_log", "*", "manage_alerts", ip_address=get_client_ip(request))
    alerts = evaluate_security_alerts(db, user.organization_id, zscore_threshold=zscore_threshold)
    record_audit(
        db, user.organization_id, "security_alerts_evaluated", "security_alert",
        user_id=user.id, username=user.username, details={"created_alerts": len(alerts)},
    )
    return {"created": alerts, "count": len(alerts)}


@router.get("/alerts")
def list_security_alerts(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(db, user, "security_log", "*", "view")
    rows = (
        db.query(SecurityAlert)
        .filter(SecurityAlert.organization_id == user.organization_id)
        .order_by(SecurityAlert.created_at.desc())
        .limit(100)
        .all()
    )
    return [
        {
            "id": a.id,
            "title": a.title,
            "severity": a.severity,
            "observed_value": a.observed_value,
            "baseline_value": a.baseline_value,
            "baseline_stddev": a.baseline_stddev,
            "delta_value": a.delta_value,
            "sample_count": a.sample_count,
            "window_start": a.window_start.isoformat() if a.window_start else None,
            "window_end": a.window_end.isoformat() if a.window_end else None,
            "evidence": a.evidence_json,
            "limitations_fa": a.limitations_fa,
            "status": a.status,
        }
        for a in rows
    ]


@router.get("/incidents")
def list_incidents(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "security_log", "*", "view")
    rows = (
        db.query(SecurityIncident)
        .filter(SecurityIncident.organization_id == user.organization_id)
        .order_by(SecurityIncident.created_at.desc())
        .limit(100)
        .all()
    )
    return [
        {
            "id": i.id,
            "title": i.title,
            "description": i.description,
            "severity": i.severity,
            "status": i.status,
            "alert_ids": i.alert_ids_json,
            "recommended_action_fa": i.recommended_action_fa,
            "human_approved": i.human_approved,
            "approved_by": i.approved_by,
            "approved_at": i.approved_at.isoformat() if i.approved_at else None,
            "approval_note": i.approval_note,
            "rollback_plan_fa": i.rollback_plan_fa,
            "resolution_note": i.resolution_note,
            "created_at": i.created_at.isoformat() if i.created_at else None,
        }
        for i in rows
    ]


@router.post("/incidents")
def create_incident(
    body: IncidentCreateRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(db, user, "security_log", "*", "manage_incidents", ip_address=get_client_ip(request))
    inc = SecurityIncident(
        organization_id=user.organization_id,
        title=body.title,
        description=body.description,
        severity=body.severity,
        status="open",
        alert_ids_json=body.alert_ids,
        recommended_action_fa=body.recommended_action_fa or "بررسی دستی توسط تحلیلگر امنیتی (بدون اقدام مسدودسازی خودکار)",
        human_approved=False,
        rollback_plan_fa=body.rollback_plan_fa,
        created_by=user.id,
    )
    db.add(inc)
    db.commit()
    record_audit(
        db, user.organization_id, "security_incident_created", "security_incident",
        resource_id=inc.id, user_id=user.id, username=user.username, ip_address=get_client_ip(request),
    )
    return {"id": inc.id, "status": inc.status, "human_approved": inc.human_approved}


@router.post("/incidents/{incident_id}/approve")
def approve_incident_recommendation(
    incident_id: str,
    body: IncidentApproveRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(db, user, "security_log", "*", "manage_incidents", ip_address=get_client_ip(request))
    inc = (
        db.query(SecurityIncident)
        .filter(SecurityIncident.id == incident_id, SecurityIncident.organization_id == user.organization_id)
        .first()
    )
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")
    inc.human_approved = bool(body.human_approved)
    inc.approved_by = user.id if body.human_approved else None
    inc.approved_at = datetime.now(timezone.utc) if body.human_approved else None
    inc.approval_note = body.approval_note or ""
    if body.rollback_plan_fa:
        inc.rollback_plan_fa = body.rollback_plan_fa
    if body.new_status and body.new_status in ("open", "triaging", "contained_proposal", "resolved", "closed"):
        inc.status = body.new_status
    elif body.human_approved and inc.status == "open":
        inc.status = "triaging"
    db.commit()
    record_audit(
        db, user.organization_id, "security_incident_human_approval", "security_incident",
        resource_id=inc.id, user_id=user.id, username=user.username, ip_address=get_client_ip(request),
        details={"human_approved": inc.human_approved, "status": inc.status, "note": inc.approval_note},
    )
    return {
        "id": inc.id,
        "status": inc.status,
        "human_approved": inc.human_approved,
        "approved_by": inc.approved_by,
        "rollback_plan_fa": inc.rollback_plan_fa,
        "execution_mode_note_fa": "پلتفرم تنها توصیه و ثبت تایید انسانی انجام می‌دهد؛ هیچ دستور مسدودسازی خودکار در شبکه/فایروال اجرا نمی‌شود.",
    }
