from typing import Any, Optional
import sqlparse
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.api.deps import enforce_permission, get_client_ip, get_current_user
from app.core.audit import record_audit
from app.core.rls_cls import apply_cls_and_masking, build_data_security_context
from app.database import get_db
from app.engines.query_engine import build_visual_query_sql, execute_dataset_sql
from app.engines.warehouse import warehouse
from app.models.dashboard import (
    DashboardPage,
    Widget,
    WidgetConfig,
    WidgetQuery,
)
from app.models.data import DataLineage, Dataset, QueryParameter, SavedQuery
from app.models.identity import User
from app.models.performance import QueryExecutionLog

router = APIRouter(prefix="/queries", tags=["Query Builder & SQL Editor"])


class SqlExecuteRequest(BaseModel):
    sql_text: str
    dataset_id: Optional[str] = None
    parameters: dict[str, Any] = {}
    limit: int = 500
    include_explain: bool = True


class VisualExecuteRequest(BaseModel):
    dataset_id: str
    visual_spec: dict[str, Any]
    filters: list[dict[str, Any]] = []


class SaveQueryRequest(BaseModel):
    name: str
    description: str = ""
    dataset_id: Optional[str] = None
    query_mode: str = "sql"
    sql_text: str
    visual_spec_json: dict[str, Any] = {}
    parameters: list[dict[str, Any]] = []


class ConvertToWidgetRequest(BaseModel):
    dashboard_id: str
    page_id: str
    title: str
    chart_type: str = "bar"
    dataset_id: Optional[str] = None
    query_mode: str = "sql"
    sql_text: Optional[str] = None
    saved_query_id: Optional[str] = None
    visual_spec: dict[str, Any] = {}
    style: dict[str, Any] = {}
    parameters: dict[str, Any] = {}


@router.post("/execute-sql")
def execute_sql_editor(
    body: SqlExecuteRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(db, user, "query", "*", "execute", ip_address=get_client_ip(request))
    ds = db.query(Dataset).filter(Dataset.id == body.dataset_id).first() if body.dataset_id else None
    try:
        result = execute_dataset_sql(
            db,
            user,
            body.sql_text,
            dataset=ds,
            named_params=body.parameters,
            limit=body.limit,
            include_explain=body.include_explain,
            use_cache=False,
        )
        record_audit(
            db, user.organization_id, "sql_query_executed", "query",
            resource_id=body.dataset_id, user_id=user.id, username=user.username,
            ip_address=get_client_ip(request),
            details={"rows": result["row_count"], "ms": result["execution_ms"]}
        )
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/execute-visual")
def execute_visual_builder(
    body: VisualExecuteRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(db, user, "query", "*", "execute")
    ds = db.query(Dataset).filter(Dataset.id == body.dataset_id).first()
    if not ds:
        raise HTTPException(status_code=404, detail="Dataset not found")
    try:
        sec_ctx = build_data_security_context(db, user, ds.id)
        sql, params, mode = build_visual_query_sql(
            db, ds, body.visual_spec, body.filters, sec_ctx.rls_where_clauses, sec_ctx.rls_params
        )
        res = warehouse.execute_query(sql, params)
        vis_cols, masked_rows = apply_cls_and_masking(res["columns"], res["rows"], sec_ctx)
        return {
            "generated_sql": sql,
            "columns": vis_cols,
            "rows": masked_rows,
            "row_count": len(masked_rows),
            "execution_ms": res["execution_ms"],
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/format-sql")
def format_sql(body: dict[str, str], user: User = Depends(get_current_user)):
    sql = body.get("sql_text", "")
    formatted = sqlparse.format(sql, reindent=True, keyword_case="upper")
    return {"formatted_sql": formatted}


@router.get("/saved")
def list_saved_queries(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "query", "*", "view")
    rows = db.query(SavedQuery).filter(SavedQuery.organization_id == user.organization_id).all()
    out = []
    for q in rows:
        params = db.query(QueryParameter).filter(QueryParameter.query_id == q.id).all()
        out.append({
            "id": q.id, "name": q.name, "description": q.description, "dataset_id": q.dataset_id,
            "query_mode": q.query_mode, "sql_text": q.sql_text, "visual_spec_json": q.visual_spec_json,
            "parameters": [{"name": p.param_name, "type": p.param_type, "default": p.default_value} for p in params],
            "created_at": q.created_at,
        })
    return out


@router.post("/saved")
def save_query(body: SaveQueryRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "query", "*", "add")
    sq = SavedQuery(
        organization_id=user.organization_id,
        name=body.name,
        description=body.description,
        dataset_id=body.dataset_id,
        query_mode=body.query_mode,
        sql_text=body.sql_text,
        visual_spec_json=body.visual_spec_json,
        created_by=user.id,
    )
    db.add(sq)
    db.flush()
    for p in body.parameters:
        db.add(
            QueryParameter(
                query_id=sq.id,
                param_name=p["name"],
                param_type=p.get("type", "string"),
                default_value=str(p.get("default", "")),
            )
        )
    db.commit()
    return {"id": sq.id, "name": sq.name}


@router.get("/history")
def query_history(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "query", "*", "view")
    logs = (
        db.query(QueryExecutionLog)
        .filter(QueryExecutionLog.organization_id == user.organization_id)
        .order_by(QueryExecutionLog.created_at.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "id": l.id, "dataset_id": l.dataset_id, "widget_id": l.widget_id,
            "query_hash": l.query_hash, "execution_ms": l.execution_ms,
            "rows_returned": l.rows_returned, "cache_hit": l.cache_hit,
            "status": l.status, "error_message": l.error_message, "created_at": l.created_at,
        }
        for l in logs
    ]


@router.post("/convert-to-widget")
def convert_query_to_widget(
    body: ConvertToWidgetRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(
        db, user, "dashboard", body.dashboard_id, "add_widget", ip_address=get_client_ip(request)
    )
    page = db.query(DashboardPage).filter(DashboardPage.id == body.page_id).first()
    if not page:
        raise HTTPException(status_code=404, detail="Dashboard page not found")

    w = Widget(
        dashboard_id=body.dashboard_id,
        page_id=body.page_id,
        title=body.title,
        chart_type=body.chart_type,
        dataset_id=body.dataset_id,
        layout_x=0,
        layout_y=99,
        layout_w=6,
        layout_h=7,
    )
    db.add(w)
    db.flush()
    db.add(
        WidgetConfig(
            widget_id=w.id,
            style_json=body.style,
            visualization_json={"show_legend": True, "show_tooltip": True},
            interactions_json={"cross_filter": True},
            advanced_json={},
        )
    )
    db.add(
        WidgetQuery(
            widget_id=w.id,
            dataset_id=body.dataset_id,
            saved_query_id=body.saved_query_id,
            query_mode=body.query_mode,
            sql_text=body.sql_text,
            visual_spec_json=body.visual_spec,
            parameters_json=body.parameters,
        )
    )
    if body.dataset_id:
        existing_lin = (
            db.query(DataLineage)
            .filter(
                DataLineage.source_type == "dataset",
                DataLineage.source_id == body.dataset_id,
                DataLineage.target_type == "dashboard",
                DataLineage.target_id == body.dashboard_id,
            )
            .first()
        )
        if not existing_lin:
            db.add(
                DataLineage(
                    source_type="dataset",
                    source_id=body.dataset_id,
                    target_type="dashboard",
                    target_id=body.dashboard_id,
                )
            )
    db.commit()
    return {"widget_id": w.id, "title": w.title, "chart_type": w.chart_type}
