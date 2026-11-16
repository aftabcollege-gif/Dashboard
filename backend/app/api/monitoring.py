from typing import Optional
import psutil
from fastapi import APIRouter, Depends, Response
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.api.deps import enforce_permission, get_current_user
from app.core.cache import cache_manager
from app.database import get_db
from app.engines.export_engine import export_to_csv_bytes
from app.engines.warehouse import warehouse
from app.models.identity import AuditLog, User
from app.models.performance import (
    DashboardPerformanceLog,
    QueryExecutionLog,
    WidgetPerformanceLog,
)

router = APIRouter(prefix="/monitoring", tags=["Audit & Performance Monitoring"])


@router.get("/audit-logs")
def get_audit_logs(
    action: Optional[str] = None,
    status: Optional[str] = None,
    username: Optional[str] = None,
    limit: int = 100,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(db, user, "admin", "*", "view")
    q = db.query(AuditLog).filter(AuditLog.organization_id == user.organization_id)
    if action:
        q = q.filter(AuditLog.action.ilike(f"%{action}%"))
    if status:
        q = q.filter(AuditLog.status == status)
    if username:
        q = q.filter(AuditLog.username.ilike(f"%{username}%"))
    rows = q.order_by(AuditLog.created_at.desc()).limit(min(limit, 500)).all()
    return [
        {
            "id": r.id, "user_id": r.user_id, "username": r.username, "action": r.action,
            "resource_type": r.resource_type, "resource_id": r.resource_id, "status": r.status,
            "ip_address": r.ip_address, "before": r.before_json, "after": r.after_json,
            "details": r.details_json, "created_at": r.created_at,
        }
        for r in rows
    ]


@router.get("/audit-logs/export")
def export_audit_logs_csv(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "admin", "*", "view")
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.organization_id == user.organization_id)
        .order_by(AuditLog.created_at.desc())
        .limit(1000)
        .all()
    )
    cols = ["created_at", "username", "action", "resource_type", "resource_id", "status", "ip_address"]
    dicts = [
        {
            "created_at": r.created_at.isoformat(),
            "username": r.username or "",
            "action": r.action,
            "resource_type": r.resource_type,
            "resource_id": r.resource_id or "",
            "status": r.status,
            "ip_address": r.ip_address or "",
        }
        for r in rows
    ]
    return Response(
        content=export_to_csv_bytes(cols, dicts),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="security_audit_logs.csv"'},
    )


@router.get("/performance")
def performance_dashboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "admin", "*", "view")
    proc = psutil.Process()
    avg_dash_ms = db.query(func.avg(DashboardPerformanceLog.load_ms)).scalar() or 0.0
    avg_widget_ms = db.query(func.avg(WidgetPerformanceLog.load_ms)).scalar() or 0.0
    avg_query_ms = db.query(func.avg(QueryExecutionLog.execution_ms)).scalar() or 0.0

    slowest_widgets = (
        db.query(WidgetPerformanceLog)
        .order_by(WidgetPerformanceLog.load_ms.desc())
        .limit(10)
        .all()
    )
    slowest_queries = (
        db.query(QueryExecutionLog)
        .order_by(QueryExecutionLog.execution_ms.desc())
        .limit(10)
        .all()
    )

    return {
        "system": {
            "cpu_percent": psutil.cpu_percent(interval=None),
            "memory_mb": round(proc.memory_info().rss / (1024 * 1024), 2),
            "warehouse_size_bytes": warehouse.storage_size_bytes(),
        },
        "cache": cache_manager.stats(),
        "averages": {
            "dashboard_load_ms": round(float(avg_dash_ms), 2),
            "widget_load_ms": round(float(avg_widget_ms), 2),
            "query_execution_ms": round(float(avg_query_ms), 2),
        },
        "slowest_widgets": [
            {
                "widget_id": w.widget_id, "dashboard_id": w.dashboard_id,
                "load_ms": w.load_ms, "cache_hit": w.cache_hit, "rows_returned": w.rows_returned,
                "created_at": w.created_at,
            }
            for w in slowest_widgets
        ],
        "slowest_queries": [
            {
                "query_hash": q.query_hash, "dataset_id": q.dataset_id,
                "execution_ms": q.execution_ms, "cache_hit": q.cache_hit,
                "rows_returned": q.rows_returned, "status": q.status, "created_at": q.created_at,
            }
            for q in slowest_queries
        ],
    }


@router.post("/cache/invalidate")
def invalidate_system_cache(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "admin", "*", "edit")
    cleared = cache_manager.invalidate_all()
    return {"cleared_entries": cleared}
