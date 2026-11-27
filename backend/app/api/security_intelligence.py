from datetime import datetime, timezone
import re
from typing import Any, Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import enforce_permission, get_client_ip, get_current_user
from app.core.audit import record_audit
from app.core.tenant import get_dataset_for_org
from app.database import get_db
from app.engines.security_classifier import THREAT_CATEGORIES
from app.engines.security_intelligence import (
    SECURITY_CONNECTOR_CATALOG,
    classify_security_dataset,
    enforce_collector_retention,
    evaluate_security_alerts,
    get_security_overview,
    ingest_security_batch,
    query_security_events_page,
    triage_security_alert,
    update_incident_lifecycle,
)
from app.engines.vulnerability_engine import ensure_default_cve_catalog, scan_asset_exposures
from app.models.identity import User
from app.models.intelligence import (
    SecurityAlert,
    SecurityAssetExposure,
    SecurityClassificationRule,
    SecurityCollector,
    SecurityIncident,
    SecurityUnparsedEvent,
    SecurityVulnerability,
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
    threat_category: Optional[str] = Query(None),
    threat_status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(db, user, "security_log", "*", "view")
    return query_security_events_page(
        db, user, action=action, severity=severity, username=username, hostname=hostname, src_ip=src_ip,
        threat_category=threat_category, threat_status=threat_status, limit=limit, offset=offset
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
            "tier": a.tier,
            "duplicate_count": a.duplicate_count,
            "false_positive": a.false_positive,
            "false_positive_reason": a.false_positive_reason,
            "notes": a.notes_json,
            "escalated_at": a.escalated_at.isoformat() if a.escalated_at else None,
        }
        for a in rows
    ]


class AlertTriageRequest(BaseModel):
    action: str  # acknowledge | false_positive | escalate_tier2 | resolve | add_note
    note: str = ""
    false_positive_reason: str = ""
    create_incident_on_escalate: bool = True


@router.post("/alerts/{alert_id}/triage")
def triage_alert_endpoint(
    alert_id: str,
    body: AlertTriageRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(db, user, "security_log", "*", "manage_alerts", ip_address=get_client_ip(request))
    al = (
        db.query(SecurityAlert)
        .filter(SecurityAlert.id == alert_id, SecurityAlert.organization_id == user.organization_id)
        .first()
    )
    if not al:
        raise HTTPException(status_code=404, detail="Alert not found")
    try:
        res = triage_security_alert(
            db, al, user,
            action=body.action,
            note=body.note,
            false_positive_reason=body.false_positive_reason,
            create_incident_on_escalate=body.create_incident_on_escalate,
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    record_audit(
        db, user.organization_id, f"security_alert_{body.action}", "security_alert",
        resource_id=al.id, user_id=user.id, username=user.username, ip_address=get_client_ip(request),
        details={"tier": res["tier"], "status": res["status"], "false_positive": res["false_positive"]},
    )
    return res


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
            "mtta_minutes": i.mtta_minutes,
            "mttr_minutes": i.mttr_minutes,
            "blast_radius": i.blast_radius_json,
            "hypothesis_notes_fa": i.hypothesis_notes_fa,
            "final_report_fa": i.final_report_fa,
            "evidence": i.evidence_json,
            "timeline": i.timeline_json,
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


class IncidentLifecycleUpdate(BaseModel):
    status: Optional[str] = None
    hypothesis_notes_fa: Optional[str] = None
    final_report_fa: Optional[str] = None
    resolution_note: Optional[str] = None
    evidence_item: Optional[dict[str, Any]] = None


@router.post("/incidents/{incident_id}/lifecycle")
def update_incident_lifecycle_endpoint(
    incident_id: str,
    body: IncidentLifecycleUpdate,
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
    res = update_incident_lifecycle(
        db, inc, user,
        status=body.status,
        hypothesis_notes_fa=body.hypothesis_notes_fa,
        final_report_fa=body.final_report_fa,
        evidence_item=body.evidence_item,
        resolution_note=body.resolution_note,
    )
    record_audit(
        db, user.organization_id, "security_incident_lifecycle_updated", "security_incident",
        resource_id=inc.id, user_id=user.id, username=user.username, ip_address=get_client_ip(request),
        details={"status": res["status"], "mtta_minutes": res["mtta_minutes"], "mttr_minutes": res["mttr_minutes"]},
    )
    return res


class ClassificationRuleCreate(BaseModel):
    rule_kind: str = "status_map"  # status_map | category_map
    name: str
    pattern: str
    target_value: str
    severity_override: Optional[str] = None
    priority: int = 100
    is_active: bool = True


@router.get("/classification-rules")
def list_classification_rules(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(db, user, "security_log", "*", "view")
    rows = (
        db.query(SecurityClassificationRule)
        .filter(SecurityClassificationRule.organization_id == user.organization_id)
        .order_by(SecurityClassificationRule.priority.asc(), SecurityClassificationRule.created_at.desc())
        .all()
    )
    return {
        "categories": THREAT_CATEGORIES,
        "rules": [
            {
                "id": r.id,
                "rule_kind": r.rule_kind,
                "name": r.name,
                "pattern": r.pattern,
                "target_value": r.target_value,
                "severity_override": r.severity_override,
                "priority": r.priority,
                "is_active": r.is_active,
            }
            for r in rows
        ],
    }


@router.post("/classification-rules")
def create_classification_rule(
    body: ClassificationRuleCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(db, user, "security_log", "*", "manage_alerts", ip_address=get_client_ip(request))
    try:
        re.compile(body.pattern)
    except re.error as exc:
        raise HTTPException(status_code=400, detail=f"Invalid regex pattern: {exc}")
    rule = SecurityClassificationRule(
        organization_id=user.organization_id,
        rule_kind=body.rule_kind if body.rule_kind in ("status_map", "category_map") else "status_map",
        name=body.name.strip() or "قانون سفارشی",
        pattern=body.pattern,
        target_value=body.target_value.strip(),
        severity_override=body.severity_override,
        priority=max(1, min(1000, int(body.priority))),
        is_active=body.is_active,
        created_by=user.id,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    record_audit(
        db, user.organization_id, "security_classification_rule_created", "security_classification_rule",
        resource_id=rule.id, user_id=user.id, username=user.username, ip_address=get_client_ip(request),
    )
    return {
        "id": rule.id,
        "rule_kind": rule.rule_kind,
        "name": rule.name,
        "pattern": rule.pattern,
        "target_value": rule.target_value,
        "severity_override": rule.severity_override,
        "priority": rule.priority,
        "is_active": rule.is_active,
    }


@router.delete("/classification-rules/{rule_id}")
def delete_classification_rule(
    rule_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(db, user, "security_log", "*", "manage_alerts", ip_address=get_client_ip(request))
    r = (
        db.query(SecurityClassificationRule)
        .filter(
            SecurityClassificationRule.id == rule_id,
            SecurityClassificationRule.organization_id == user.organization_id,
        )
        .first()
    )
    if not r:
        raise HTTPException(status_code=404, detail="Classification rule not found")
    db.delete(r)
    db.commit()
    return {"status": "deleted"}


class DatasetClassifyBody(BaseModel):
    text_column: Optional[str] = None
    limit: int = 500


@router.post("/classify-dataset/{dataset_id}")
def classify_uploaded_security_dataset(
    dataset_id: str,
    body: DatasetClassifyBody = DatasetClassifyBody(),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ds = get_dataset_for_org(db, dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", ds.id, "view")
    enforce_permission(db, user, "security_log", "*", "view")
    return classify_security_dataset(db, ds, user, text_column=body.text_column, limit=body.limit)


class VulnerabilityCreate(BaseModel):
    cve_id: str
    software_product: str
    affected_version_pattern: str = ".*"
    cvss_score: float = Field(7.5, ge=0.0, le=10.0)
    severity: str = "high"
    summary: str = ""


@router.get("/vulnerabilities")
def list_vulnerabilities(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(db, user, "security_log", "*", "view")
    ensure_default_cve_catalog(db)
    rows = db.query(SecurityVulnerability).order_by(SecurityVulnerability.cvss_score.desc()).limit(200).all()
    return [
        {
            "id": v.id,
            "cve_id": v.cve_id,
            "software_product": v.software_product,
            "affected_version_pattern": v.affected_version_pattern,
            "cvss_score": v.cvss_score,
            "severity": v.severity,
            "summary": v.summary,
            "source": v.source,
        }
        for v in rows
    ]


@router.post("/vulnerabilities")
def upsert_vulnerability(
    body: VulnerabilityCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(db, user, "security_log", "*", "manage_alerts", ip_address=get_client_ip(request))
    try:
        re.compile(body.affected_version_pattern)
    except re.error as exc:
        raise HTTPException(status_code=400, detail=f"Invalid version regex: {exc}")
    cve = body.cve_id.strip().upper()
    row = db.query(SecurityVulnerability).filter(SecurityVulnerability.cve_id == cve).first()
    if not row:
        row = SecurityVulnerability(cve_id=cve, source="org_custom_catalog")
        db.add(row)
    row.software_product = body.software_product.strip().lower()
    row.affected_version_pattern = body.affected_version_pattern
    row.cvss_score = float(body.cvss_score)
    row.severity = body.severity
    row.summary = body.summary
    db.commit()
    db.refresh(row)
    return {"id": row.id, "cve_id": row.cve_id, "cvss_score": row.cvss_score}


@router.post("/exposures/scan")
def scan_exposures_endpoint(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(db, user, "security_log", "*", "manage_alerts", ip_address=get_client_ip(request))
    res = scan_asset_exposures(db, user.organization_id)
    record_audit(
        db, user.organization_id, "security_asset_exposures_scanned", "security_asset_exposure",
        user_id=user.id, username=user.username, ip_address=get_client_ip(request),
        details=res,
    )
    return res


@router.get("/exposures")
def list_exposures_endpoint(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(db, user, "security_log", "*", "view")
    rows = (
        db.query(SecurityAssetExposure)
        .filter(SecurityAssetExposure.organization_id == user.organization_id)
        .order_by(SecurityAssetExposure.cvss_score.desc(), SecurityAssetExposure.related_event_count.desc())
        .limit(250)
        .all()
    )
    return [
        {
            "id": e.id,
            "asset_name": e.asset_name,
            "software_name": e.software_name,
            "software_version": e.software_version,
            "cve_id": e.cve_id,
            "cvss_score": e.cvss_score,
            "risk_level": e.risk_level,
            "remediation_status": e.remediation_status,
            "notes": e.notes,
            "related_event_count": e.related_event_count,
            "detected_at": e.detected_at.isoformat() if e.detected_at else None,
        }
        for e in rows
    ]


class ExposureStatusBody(BaseModel):
    remediation_status: str  # open | mitigated | patched | accepted_risk


@router.post("/exposures/{exposure_id}/status")
def update_exposure_status(
    exposure_id: str,
    body: ExposureStatusBody,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(db, user, "security_log", "*", "manage_alerts", ip_address=get_client_ip(request))
    row = (
        db.query(SecurityAssetExposure)
        .filter(
            SecurityAssetExposure.id == exposure_id,
            SecurityAssetExposure.organization_id == user.organization_id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Exposure not found")
    if body.remediation_status not in ("open", "mitigated", "patched", "accepted_risk"):
        raise HTTPException(status_code=400, detail="Invalid remediation_status")
    row.remediation_status = body.remediation_status
    db.commit()
    return {"id": row.id, "remediation_status": row.remediation_status}
