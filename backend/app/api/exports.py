import os
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.api.dashboards import _execute_single_widget
from app.api.deps import enforce_permission, get_client_ip, get_current_user
from app.config import settings
from app.core.audit import record_audit
from app.core.permissions import permission_resolver
from app.database import get_db
from app.engines.export_engine import (
    export_to_csv_bytes,
    export_to_excel_bytes,
    export_to_pdf_bytes,
)
from app.models.dashboard import Dashboard, Widget
from app.models.etl import ScheduledReport
from app.models.identity import User

router = APIRouter(prefix="/exports", tags=["Exports & Scheduled Reports"])


class ScheduledReportCreate(BaseModel):
    dashboard_id: str
    name: str
    format: str = "pdf"
    cron_expression: str = "0 8 * * 1"
    recipients: list[str] = []


@router.get("/dashboards/{dashboard_id}/{fmt}")
def export_dashboard(
    dashboard_id: str,
    fmt: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dash = db.query(Dashboard).filter(Dashboard.id == dashboard_id).first()
    if not dash:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    fmt_low = fmt.lower()
    if not permission_resolver.resolve(db, user, "dashboard", dash.id, "export").allowed:
        enforce_permission(db, user, "export", f"export_{fmt_low}", f"export_{fmt_low}", ip_address=get_client_ip(request))

    widgets = db.query(Widget).filter(Widget.dashboard_id == dash.id).all()
    sections = []
    for w in widgets:
        parent_chain = [("dashboard", dash.id), ("dashboard_page", w.page_id)]
        if not permission_resolver.resolve(db, user, "widget", w.id, "view", parent_chain).allowed:
            continue
        res = _execute_single_widget(db, user, w, [], [], [], use_cache=True, ip_address=get_client_ip(request))
        sections.append({"name": w.title, "columns": res.get("columns", []), "rows": res.get("rows", [])})

    record_audit(
        db, user.organization_id, f"export_{fmt_low}", "dashboard", resource_id=dash.id,
        user_id=user.id, username=user.username, ip_address=get_client_ip(request)
    )

    if fmt_low == "csv":
        all_cols = ["widget"]
        combined_rows = []
        for sec in sections:
            for c in sec["columns"]:
                if c not in all_cols:
                    all_cols.append(c)
            for r in sec["rows"]:
                combined_rows.append({"widget": sec["name"], **r})
        data = export_to_csv_bytes(all_cols, combined_rows)
        return Response(
            content=data,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="dashboard_{dash.id[:8]}.csv"'},
        )
    if fmt_low in ("excel", "xlsx"):
        data = export_to_excel_bytes(dash.title, sections)
        return Response(
            content=data,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="dashboard_{dash.id[:8]}.xlsx"'},
        )
    if fmt_low == "pdf":
        data = export_to_pdf_bytes(dash.title, dash.description, sections)
        return Response(
            content=data,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="dashboard_{dash.id[:8]}.pdf"'},
        )
    raise HTTPException(status_code=400, detail="Unsupported export format; use pdf, xlsx, or csv")


@router.get("/widgets/{widget_id}/{fmt}")
def export_widget(
    widget_id: str,
    fmt: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    w = db.query(Widget).filter(Widget.id == widget_id).first()
    if not w:
        raise HTTPException(status_code=404, detail="Widget not found")
    parent_chain = [("dashboard", w.dashboard_id), ("dashboard_page", w.page_id)]
    if not permission_resolver.resolve(db, user, "widget", w.id, "export", parent_chain).allowed:
        enforce_permission(db, user, "dashboard", w.dashboard_id, "export", ip_address=get_client_ip(request))

    res = _execute_single_widget(db, user, w, [], [], [], use_cache=True, ip_address=get_client_ip(request))
    fmt_low = fmt.lower()
    record_audit(
        db, user.organization_id, f"export_widget_{fmt_low}", "widget", resource_id=w.id,
        user_id=user.id, username=user.username, ip_address=get_client_ip(request)
    )

    if fmt_low == "csv":
        data = export_to_csv_bytes(res.get("columns", []), res.get("rows", []))
        return Response(content=data, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="widget_{w.id[:8]}.csv"'})
    if fmt_low in ("excel", "xlsx"):
        data = export_to_excel_bytes(w.title, [{"name": w.title, "columns": res.get("columns", []), "rows": res.get("rows", [])}])
        return Response(content=data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="widget_{w.id[:8]}.xlsx"'})
    if fmt_low == "pdf":
        data = export_to_pdf_bytes(w.title, w.description, [{"name": w.title, "columns": res.get("columns", []), "rows": res.get("rows", [])}])
        return Response(content=data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="widget_{w.id[:8]}.pdf"'})
    raise HTTPException(status_code=400, detail="Unsupported format")


@router.get("/scheduled-reports")
def list_scheduled_reports(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(ScheduledReport).filter(ScheduledReport.organization_id == user.organization_id).all()
    return [
        {
            "id": r.id, "dashboard_id": r.dashboard_id, "name": r.name, "format": r.format,
            "cron_expression": r.cron_expression, "recipients": r.recipients_json,
            "is_enabled": r.is_enabled, "last_sent_at": r.last_sent_at,
        }
        for r in rows
    ]


@router.post("/scheduled-reports")
def create_scheduled_report(
    body: ScheduledReportCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    enforce_permission(db, user, "dashboard", body.dashboard_id, "export")
    sr = ScheduledReport(
        organization_id=user.organization_id, dashboard_id=body.dashboard_id, name=body.name,
        format=body.format, cron_expression=body.cron_expression, recipients_json=body.recipients,
    )
    db.add(sr)
    db.commit()
    return {"id": sr.id, "name": sr.name}


@router.post("/scheduled-reports/{report_id}/run")
def run_scheduled_report_now(
    report_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    sr = db.query(ScheduledReport).filter(ScheduledReport.id == report_id).first()
    if not sr:
        raise HTTPException(status_code=404, detail="Scheduled report not found")
    dash = db.query(Dashboard).filter(Dashboard.id == sr.dashboard_id).first()
    widgets = db.query(Widget).filter(Widget.dashboard_id == sr.dashboard_id).all()
    sections = []
    for w in widgets:
        res = _execute_single_widget(db, user, w, [], [], [], use_cache=True)
        sections.append({"name": w.title, "columns": res.get("columns", []), "rows": res.get("rows", [])})

    ext = "xlsx" if sr.format in ("excel", "xlsx") else sr.format
    out_path = os.path.join(settings.EXPORTS_DIR, f"report_{sr.id[:8]}.{ext}")
    if ext == "pdf":
        b = export_to_pdf_bytes(dash.title, dash.description, sections)
    elif ext == "xlsx":
        b = export_to_excel_bytes(dash.title, sections)
    else:
        first = sections[0] if sections else {"columns": [], "rows": []}
        b = export_to_csv_bytes(first["columns"], first["rows"])
    with open(out_path, "wb") as f:
        f.write(b)
    sr.last_sent_at = datetime.now(timezone.utc)
    sr.last_file_path = out_path
    db.commit()
    return {"status": "generated", "file_path": out_path, "bytes": len(b)}
