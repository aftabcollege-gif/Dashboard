import secrets
import time
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.api.deps import enforce_permission, get_client_ip, get_current_user
from app.core.audit import record_audit
from app.core.cache import cache_manager
from app.core.permissions import permission_resolver
from app.core.rls_cls import apply_cls_and_masking, build_data_security_context
from app.database import get_db
from app.engines.drill_engine import resolve_drill_state
from app.engines.query_engine import build_visual_query_sql, execute_dataset_sql
from app.engines.warehouse import warehouse
from app.models.dashboard import (
    Dashboard,
    DashboardComment,
    DashboardFilter,
    DashboardLayout,
    DashboardPage,
    DashboardState,
    DashboardTheme,
    DashboardVersion,
    Widget,
    WidgetConfig,
    WidgetDrillRule,
    WidgetFilter,
    WidgetQuery,
)
from app.models.data import DataLineage, Dataset, DatasetField
from app.models.identity import User
from app.models.performance import DashboardPerformanceLog, WidgetPerformanceLog

router = APIRouter(prefix="/dashboards", tags=["Dashboards, Designer & Interactive Analytics"])

ALL_30_CHART_TYPES = {
    "bar", "horizontal_bar", "stacked_bar", "stacked_bar_100",
    "line", "area", "stacked_area",
    "pie", "donut", "scatter", "bubble", "radar",
    "treemap", "sunburst", "heatmap", "waterfall", "funnel",
    "gauge", "kpi", "progress", "table", "pivot_table", "matrix",
    "calendar_heatmap", "histogram", "boxplot", "candlestick",
    "geo_map", "sankey", "combo",
}


class DashboardCreate(BaseModel):
    workspace_id: Optional[str] = None
    title: str
    description: str = ""
    theme_id: Optional[str] = None
    is_template: bool = False
    is_public: bool = False


class DashboardUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    theme_id: Optional[str] = None
    is_public: Optional[bool] = None
    refresh_interval_sec: Optional[int] = None
    settings_json: Optional[dict[str, Any]] = None


class PageCreate(BaseModel):
    title: str
    is_drill_through: bool = False


class LayoutSaveRequest(BaseModel):
    page_id: str
    columns_count: int = 12
    row_height: int = 42
    gap_px: int = 16
    grid_enabled: bool = True
    items: list[dict[str, Any]]  # [{i, x, y, w, h}]


class WidgetCreate(BaseModel):
    page_id: str
    title: str
    description: str = ""
    chart_type: str = "bar"
    dataset_id: Optional[str] = None
    layout_x: int = 0
    layout_y: int = 0
    layout_w: int = 6
    layout_h: int = 7
    query_mode: str = "visual"
    sql_text: Optional[str] = None
    visual_spec: dict[str, Any] = {}
    style: dict[str, Any] = {}
    visualization: dict[str, Any] = {"show_legend": True, "show_tooltip": True}
    interactions: dict[str, Any] = {"cross_filter": True}
    drill_hierarchy: list[dict[str, Any]] = []
    drill_rules: list[dict[str, Any]] = []
    drill_through_page_id: Optional[str] = None


class WidgetUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    chart_type: Optional[str] = None
    dataset_id: Optional[str] = None
    style_json: Optional[dict[str, Any]] = None
    visualization_json: Optional[dict[str, Any]] = None
    interactions_json: Optional[dict[str, Any]] = None
    advanced_json: Optional[dict[str, Any]] = None
    visual_spec_json: Optional[dict[str, Any]] = None
    sql_text: Optional[str] = None
    query_mode: Optional[str] = None
    drill_hierarchy: Optional[list[dict[str, Any]]] = None
    drill_rules: Optional[list[dict[str, Any]]] = None
    drill_through_page_id: Optional[str] = None


class SwitchChartRequest(BaseModel):
    target_chart_type: str


class FilterCreate(BaseModel):
    page_id: Optional[str] = None
    scope: str = "global"
    name: str
    field_name: str
    dataset_id: Optional[str] = None
    filter_type: str = "multi_select"
    operator: str = "in"
    default_value: Any = None
    target_widget_ids: list[str] = []
    excluded_widget_ids: list[str] = []
    parent_filter_id: Optional[str] = None
    is_locked: bool = False


class WidgetFilterCreate(BaseModel):
    field_name: str
    filter_type: str = "dropdown"
    operator: str = "eq"
    value: Any = None


class WidgetExecuteRequest(BaseModel):
    active_filters: list[dict[str, Any]] = []
    cross_filters: list[dict[str, Any]] = []
    drill_stack: list[dict[str, Any]] = []
    use_cache: bool = True


class BatchExecuteRequest(BaseModel):
    page_id: Optional[str] = None
    active_filters: list[dict[str, Any]] = []
    cross_filters: list[dict[str, Any]] = []
    drill_states: dict[str, list[dict[str, Any]]] = {}  # widget_id -> drill_stack
    use_cache: bool = True


class StateSaveRequest(BaseModel):
    active_page_id: Optional[str] = None
    filter_state: dict[str, Any] = {}
    drill_state: dict[str, Any] = {}
    widget_overrides: dict[str, Any] = {}


def _serialize_dashboard(db: Session, dash: Dashboard, user: User) -> dict[str, Any]:
    pages = (
        db.query(DashboardPage)
        .filter(DashboardPage.dashboard_id == dash.id)
        .order_by(DashboardPage.page_order.asc())
        .all()
    )
    widgets_all = db.query(Widget).filter(Widget.dashboard_id == dash.id).all()
    dash_filters = db.query(DashboardFilter).filter(DashboardFilter.dashboard_id == dash.id).all()
    theme = db.query(DashboardTheme).filter(DashboardTheme.id == dash.theme_id).first() if dash.theme_id else None

    # Filter widgets by explicit per-widget VIEW permission! (Test B isolation!)
    visible_widgets = []
    for w in widgets_all:
        parent_chain = [("dashboard", dash.id), ("dashboard_page", w.page_id)]
        dec = permission_resolver.resolve(db, user, "widget", w.id, "view", parent_chain=parent_chain)
        if not dec.allowed:
            continue
        cfg = db.query(WidgetConfig).filter(WidgetConfig.widget_id == w.id).first()
        wq = db.query(WidgetQuery).filter(WidgetQuery.widget_id == w.id).first()
        wdr = db.query(WidgetDrillRule).filter(WidgetDrillRule.widget_id == w.id).first()
        wfs = db.query(WidgetFilter).filter(WidgetFilter.widget_id == w.id).all()
        ds = db.query(Dataset).filter(Dataset.id == w.dataset_id).first() if w.dataset_id else None

        can_edit_w = permission_resolver.resolve(db, user, "widget", w.id, "edit", parent_chain).allowed
        can_del_w = permission_resolver.resolve(db, user, "widget", w.id, "delete", parent_chain).allowed
        can_drill_w = permission_resolver.resolve(db, user, "drill_down", w.id, "drill_down", parent_chain + [("widget", w.id)]).allowed

        visible_widgets.append({
            "id": w.id,
            "dashboard_id": w.dashboard_id,
            "page_id": w.page_id,
            "title": w.title,
            "description": w.description,
            "chart_type": w.chart_type,
            "dataset_id": w.dataset_id,
            "dataset_freshness": {
                "status": ds.freshness_status if ds else "unknown",
                "last_refreshed_at": ds.last_refreshed_at if ds else None,
                "row_count": ds.row_count if ds else 0,
                "data_version": ds.data_version if ds else 1,
            },
            "layout": {"x": w.layout_x, "y": w.layout_y, "w": w.layout_w, "h": w.layout_h},
            "config": {
                "style": cfg.style_json if cfg else {},
                "visualization": cfg.visualization_json if cfg else {},
                "interactions": cfg.interactions_json if cfg else {},
                "advanced": cfg.advanced_json if cfg else {},
            },
            "query": {
                "query_mode": wq.query_mode if wq else "visual",
                "sql_text": wq.sql_text if wq else None,
                "visual_spec": wq.visual_spec_json if wq else {},
                "parameters": wq.parameters_json if wq else {},
            },
            "drill_rule": {
                "hierarchy": wdr.hierarchy_json if wdr else [],
                "level_rules": wdr.level_rules_json if wdr else [],
                "drill_through_page_id": wdr.drill_through_page_id if wdr else None,
            },
            "widget_filters": [
                {
                    "id": wf.id, "field_name": wf.field_name, "filter_type": wf.filter_type,
                    "operator": wf.operator, "value": wf.value_json, "is_active": wf.is_active,
                }
                for wf in wfs
            ],
            "permissions": {"view": True, "edit": can_edit_w, "delete": can_del_w, "drill_down": can_drill_w},
        })

    layouts_by_page = {}
    for p in pages:
        lay = db.query(DashboardLayout).filter(DashboardLayout.page_id == p.id).first()
        layouts_by_page[p.id] = {
            "columns_count": lay.columns_count if lay else 12,
            "row_height": lay.row_height if lay else 42,
            "gap_px": lay.gap_px if lay else 16,
            "grid_enabled": lay.grid_enabled if lay else True,
        }

    return {
        "id": dash.id,
        "workspace_id": dash.workspace_id,
        "title": dash.title,
        "description": dash.description,
        "theme_id": dash.theme_id,
        "theme": {
            "id": theme.id, "name": theme.name, "slug": theme.slug, "mode": theme.mode, "tokens": theme.tokens_json
        } if theme else None,
        "is_public": dash.is_public,
        "is_template": dash.is_template,
        "embed_token": dash.embed_token,
        "refresh_interval_sec": dash.refresh_interval_sec,
        "settings": dash.settings_json,
        "pages": [
            {
                "id": p.id, "title": p.title, "page_order": p.page_order,
                "is_drill_through": p.is_drill_through, "layout_settings": layouts_by_page.get(p.id, {}),
            }
            for p in pages
        ],
        "widgets": visible_widgets,
        "filters": [
            {
                "id": f.id, "page_id": f.page_id, "scope": f.scope, "name": f.name,
                "field_name": f.field_name, "dataset_id": f.dataset_id, "filter_type": f.filter_type,
                "operator": f.operator, "default_value": f.default_value_json,
                "target_widget_ids": f.target_widget_ids, "excluded_widget_ids": f.excluded_widget_ids,
                "parent_filter_id": f.parent_filter_id, "is_locked": f.is_locked,
            }
            for f in dash_filters
        ],
        "permissions": {
            "view": True,
            "edit": permission_resolver.resolve(db, user, "dashboard", dash.id, "edit").allowed,
            "delete": permission_resolver.resolve(db, user, "dashboard", dash.id, "delete").allowed,
            "add_widget": permission_resolver.resolve(db, user, "dashboard", dash.id, "add_widget").allowed,
            "change_theme": permission_resolver.resolve(db, user, "dashboard", dash.id, "change_theme").allowed,
            "export": permission_resolver.resolve(db, user, "dashboard", dash.id, "export").allowed,
        },
        "updated_at": dash.updated_at,
    }


@router.get("/public/{embed_token}")
def get_public_dashboard(embed_token: str, db: Session = Depends(get_db)):
    dash = db.query(Dashboard).filter(Dashboard.embed_token == embed_token, Dashboard.is_public == True).first()
    if not dash:
        raise HTTPException(status_code=404, detail="Shared dashboard not found or not public")
    pages = db.query(DashboardPage).filter(DashboardPage.dashboard_id == dash.id).order_by(DashboardPage.page_order.asc()).all()
    widgets = db.query(Widget).filter(Widget.dashboard_id == dash.id).all()
    theme = db.query(DashboardTheme).filter(DashboardTheme.id == dash.theme_id).first() if dash.theme_id else None
    out_widgets = []
    for w in widgets:
        wq = db.query(WidgetQuery).filter(WidgetQuery.widget_id == w.id).first()
        cfg = db.query(WidgetConfig).filter(WidgetConfig.widget_id == w.id).first()
        wdr = db.query(WidgetDrillRule).filter(WidgetDrillRule.widget_id == w.id).first()
        ds = db.query(Dataset).filter(Dataset.id == w.dataset_id).first() if w.dataset_id else None
        data = {"columns": [], "rows": []}
        if ds and wq:
            try:
                if wq.query_mode == "sql" and wq.sql_text:
                    data = warehouse.execute_query(wq.sql_text)
                else:
                    sql, params, _ = build_visual_query_sql(db, ds, wq.visual_spec_json or {}, [], [], [])
                    data = warehouse.execute_query(sql, params)
            except Exception:
                pass
        out_widgets.append({
            "id": w.id,
            "page_id": w.page_id,
            "title": w.title,
            "chart_type": w.chart_type,
            "layout": {"x": w.layout_x, "y": w.layout_y, "w": w.layout_w, "h": w.layout_h},
            "config": {
                "style": cfg.style_json if cfg else {},
                "visualization": cfg.visualization_json if cfg else {},
                "interactions": cfg.interactions_json if cfg else {"cross_filter": True},
            },
            "query": {
                "query_mode": wq.query_mode if wq else "visual",
                "visual_spec": wq.visual_spec_json if wq else {},
            },
            "drill_rule": {
                "hierarchy": wdr.hierarchy_json if wdr else [],
                "level_rules": wdr.level_rules_json if wdr else [],
            },
            "data": data,
        })
    all_themes = db.query(DashboardTheme).all()
    return {
        "id": dash.id,
        "title": dash.title,
        "description": dash.description,
        "theme_id": dash.theme_id,
        "theme": {"id": theme.id, "name": theme.name, "slug": theme.slug, "mode": theme.mode, "tokens": theme.tokens_json} if theme else None,
        "themes": [
            {"id": th.id, "name": th.name, "slug": th.slug, "mode": th.mode, "tokens": th.tokens_json}
            for th in all_themes
        ],
        "pages": [{"id": p.id, "title": p.title} for p in pages],
        "widgets": out_widgets,
    }


@router.post("/public/{embed_token}/execute-batch")
def execute_public_dashboard_batch(
    embed_token: str,
    body: BatchExecuteRequest,
    db: Session = Depends(get_db),
):
    dash = db.query(Dashboard).filter(Dashboard.embed_token == embed_token, Dashboard.is_public == True).first()
    if not dash:
        raise HTTPException(status_code=404, detail="Shared dashboard not found or not public")

    t0 = time.perf_counter()
    widgets = db.query(Widget).filter(Widget.dashboard_id == dash.id).all()
    results: dict[str, Any] = {}

    for w in widgets:
        wq = db.query(WidgetQuery).filter(WidgetQuery.widget_id == w.id).first()
        wdr = db.query(WidgetDrillRule).filter(WidgetDrillRule.widget_id == w.id).first()
        ds = db.query(Dataset).filter(Dataset.id == (wq.dataset_id if wq and wq.dataset_id else w.dataset_id)).first()
        hierarchy = (wdr.hierarchy_json if wdr else []) or []
        level_rules = (wdr.level_rules_json if wdr else []) or []
        d_stack = body.drill_states.get(w.id, [])
        level = min(len(d_stack), len(hierarchy) - 1) if hierarchy else 0

        eff_dim: Optional[str] = None
        eff_chart = w.chart_type
        if hierarchy and 0 <= level < len(hierarchy):
            step = hierarchy[level]
            eff_dim = step.get("field") if isinstance(step, dict) else str(step)
        for lr in level_rules:
            if isinstance(lr, dict) and int(lr.get("level", -1)) == level:
                if lr.get("chart_type"):
                    eff_chart = str(lr["chart_type"])
                if lr.get("field"):
                    eff_dim = str(lr["field"])

        breadcrumbs = [{"level": 0, "label": "All", "field": None, "value": None}]
        drill_filters: list[dict[str, Any]] = []
        for idx, item in enumerate(d_stack[: max(0, len(hierarchy) - 1)]):
            fn = item.get("field")
            fv = item.get("value")
            if fn and fv is not None:
                drill_filters.append({"field_name": fn, "operator": "eq", "value": fv, "is_active": True})
                breadcrumbs.append({"level": idx + 1, "label": f"{fn}: {fv}", "field": fn, "value": fv})

        eff_filters = list(body.active_filters or [])
        for cf in body.cross_filters or []:
            if cf.get("source_widget_id") != w.id:
                eff_filters.append(cf)
        eff_filters.extend(drill_filters)

        data = {"columns": [], "rows": [], "row_count": 0, "execution_ms": 0.0}
        if ds and wq:
            try:
                if wq.query_mode == "sql" and wq.sql_text:
                    data = warehouse.execute_query(wq.sql_text)
                else:
                    sql, params, _ = build_visual_query_sql(
                        db, ds, wq.visual_spec_json or {}, eff_filters, [], [], override_dimension=eff_dim
                    )
                    data = warehouse.execute_query(sql, params)
            except Exception as exc:
                data = {"columns": [], "rows": [], "error": str(exc)}

        results[w.id] = {
            "widget_id": w.id,
            "effective_chart_type": eff_chart,
            "drill": {
                "level": len(drill_filters),
                "breadcrumbs": breadcrumbs,
                "can_drill_down": bool(hierarchy and len(drill_filters) < len(hierarchy) - 1),
                "effective_dimension": eff_dim,
            },
            **data,
        }

    return {
        "dashboard_id": dash.id,
        "load_ms": round((time.perf_counter() - t0) * 1000, 3),
        "widgets": results,
    }


@router.get("/workspaces")
def list_workspaces(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.models.dashboard import Workspace
    rows = db.query(Workspace).filter(Workspace.organization_id == user.organization_id).all()
    return [{"id": w.id, "name": w.name, "description": w.description} for w in rows]


@router.get("/templates")
def list_templates(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(Dashboard).filter(Dashboard.is_template == True).all()
    return [{"id": d.id, "title": d.title, "description": d.description, "theme_id": d.theme_id} for d in rows]


@router.get("")
def list_dashboards(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    all_dash = db.query(Dashboard).all()
    visible = []
    for d in all_dash:
        if d.is_public or permission_resolver.resolve(db, user, "dashboard", d.id, "view").allowed:
            visible.append({
                "id": d.id, "workspace_id": d.workspace_id, "title": d.title,
                "description": d.description, "theme_id": d.theme_id, "is_public": d.is_public,
                "is_template": d.is_template, "updated_at": d.updated_at,
                "widget_count": db.query(Widget).filter(Widget.dashboard_id == d.id).count(),
            })
    return visible


@router.post("")
def create_dashboard(
    body: DashboardCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.models.dashboard import Workspace
    enforce_permission(db, user, "dashboard", "*", "add", ip_address=get_client_ip(request))
    ws_id = body.workspace_id
    if not ws_id:
        ws = db.query(Workspace).filter(Workspace.organization_id == user.organization_id).first()
        ws_id = ws.id
    d = Dashboard(
        workspace_id=ws_id,
        title=body.title,
        description=body.description,
        theme_id=body.theme_id,
        is_template=body.is_template,
        is_public=body.is_public,
        created_by=user.id,
    )
    db.add(d)
    db.flush()
    p = DashboardPage(dashboard_id=d.id, title="Overview", page_order=0)
    db.add(p)
    db.flush()
    db.add(DashboardLayout(page_id=p.id, columns_count=12, layout_json=[]))
    record_audit(
        db, user.organization_id, "dashboard_created", "dashboard", resource_id=d.id,
        user_id=user.id, username=user.username, ip_address=get_client_ip(request)
    )
    db.commit()
    return {"id": d.id, "title": d.title, "default_page_id": p.id}


@router.get("/{dashboard_id}")
def get_dashboard(
    dashboard_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    dash = db.query(Dashboard).filter(Dashboard.id == dashboard_id).first()
    if not dash:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    if not dash.is_public:
        enforce_permission(db, user, "dashboard", dash.id, "view", ip_address=get_client_ip(request))
    record_audit(
        db, user.organization_id, "dashboard_viewed", "dashboard", resource_id=dash.id,
        user_id=user.id, username=user.username, ip_address=get_client_ip(request)
    )
    return _serialize_dashboard(db, dash, user)


@router.put("/{dashboard_id}")
def update_dashboard(
    dashboard_id: str,
    body: DashboardUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dash = db.query(Dashboard).filter(Dashboard.id == dashboard_id).first()
    if not dash:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    if body.theme_id is not None:
        # Either edit or change_theme
        if not permission_resolver.resolve(db, user, "dashboard", dash.id, "change_theme").allowed:
            enforce_permission(db, user, "dashboard", dash.id, "edit", ip_address=get_client_ip(request))
        dash.theme_id = body.theme_id
    else:
        enforce_permission(db, user, "dashboard", dash.id, "edit", ip_address=get_client_ip(request))

    if body.title is not None:
        dash.title = body.title
    if body.description is not None:
        dash.description = body.description
    if body.is_public is not None:
        dash.is_public = body.is_public
    if body.refresh_interval_sec is not None:
        dash.refresh_interval_sec = body.refresh_interval_sec
    if body.settings_json is not None:
        dash.settings_json = body.settings_json
    db.commit()
    return {"status": "updated"}


@router.delete("/{dashboard_id}")
def delete_dashboard(
    dashboard_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    enforce_permission(db, user, "dashboard", dashboard_id, "delete", ip_address=get_client_ip(request))
    dash = db.query(Dashboard).filter(Dashboard.id == dashboard_id).first()
    if not dash:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    db.delete(dash)
    db.commit()
    return {"status": "deleted"}


@router.post("/{dashboard_id}/pages")
def add_dashboard_page(
    dashboard_id: str, body: PageCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    enforce_permission(db, user, "dashboard", dashboard_id, "add_page")
    order = db.query(DashboardPage).filter(DashboardPage.dashboard_id == dashboard_id).count()
    p = DashboardPage(dashboard_id=dashboard_id, title=body.title, page_order=order, is_drill_through=body.is_drill_through)
    db.add(p)
    db.flush()
    db.add(DashboardLayout(page_id=p.id, columns_count=12, layout_json=[]))
    db.commit()
    return {"id": p.id, "title": p.title, "page_order": p.page_order}


@router.put("/{dashboard_id}/layout")
def save_dashboard_layout(
    dashboard_id: str, body: LayoutSaveRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    enforce_permission(db, user, "dashboard", dashboard_id, "change_layout")
    lay = db.query(DashboardLayout).filter(DashboardLayout.page_id == body.page_id).first()
    if not lay:
        lay = DashboardLayout(page_id=body.page_id)
        db.add(lay)
    lay.columns_count = body.columns_count
    lay.row_height = body.row_height
    lay.gap_px = body.gap_px
    lay.grid_enabled = body.grid_enabled
    lay.layout_json = body.items
    for item in body.items:
        wid = item.get("i")
        w = db.query(Widget).filter(Widget.id == wid, Widget.dashboard_id == dashboard_id).first()
        if w:
            w.layout_x = int(item.get("x", w.layout_x))
            w.layout_y = int(item.get("y", w.layout_y))
            w.layout_w = int(item.get("w", w.layout_w))
            w.layout_h = int(item.get("h", w.layout_h))
    db.commit()
    return {"status": "layout_saved"}


# ---------------- WIDGET CRUD + DUPLICATE + CHART SWITCHER ----------------
@router.post("/{dashboard_id}/widgets")
def create_widget(
    dashboard_id: str, body: WidgetCreate, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    enforce_permission(db, user, "dashboard", dashboard_id, "add_widget", ip_address=get_client_ip(request))
    if body.chart_type not in ALL_30_CHART_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported chart_type: {body.chart_type}")
    w = Widget(
        dashboard_id=dashboard_id, page_id=body.page_id, title=body.title,
        description=body.description, chart_type=body.chart_type, dataset_id=body.dataset_id,
        layout_x=body.layout_x, layout_y=body.layout_y, layout_w=body.layout_w, layout_h=body.layout_h,
    )
    db.add(w)
    db.flush()
    db.add(WidgetConfig(
        widget_id=w.id, style_json=body.style, visualization_json=body.visualization,
        interactions_json=body.interactions, advanced_json={},
    ))
    db.add(WidgetQuery(
        widget_id=w.id, dataset_id=body.dataset_id, query_mode=body.query_mode,
        sql_text=body.sql_text, visual_spec_json=body.visual_spec,
    ))
    if body.drill_hierarchy or body.drill_rules:
        db.add(WidgetDrillRule(
            widget_id=w.id, hierarchy_json=body.drill_hierarchy,
            level_rules_json=body.drill_rules, drill_through_page_id=body.drill_through_page_id,
        ))
    if body.dataset_id:
        if not db.query(DataLineage).filter(
            DataLineage.source_type == "dataset", DataLineage.source_id == body.dataset_id,
            DataLineage.target_type == "dashboard", DataLineage.target_id == dashboard_id,
        ).first():
            db.add(DataLineage(source_type="dataset", source_id=body.dataset_id, target_type="dashboard", target_id=dashboard_id))
    db.commit()
    return {"id": w.id, "title": w.title}


@router.put("/widgets/{widget_id}")
def update_widget(
    widget_id: str, body: WidgetUpdate, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    w = db.query(Widget).filter(Widget.id == widget_id).first()
    if not w:
        raise HTTPException(status_code=404, detail="Widget not found")
    parent_chain = [("dashboard", w.dashboard_id), ("dashboard_page", w.page_id)]
    enforce_permission(db, user, "widget", w.id, "edit", parent_chain=parent_chain, ip_address=get_client_ip(request))

    if body.title is not None:
        w.title = body.title
    if body.description is not None:
        w.description = body.description
    if body.chart_type is not None:
        if body.chart_type not in ALL_30_CHART_TYPES:
            raise HTTPException(status_code=400, detail=f"Unsupported chart_type: {body.chart_type}")
        w.chart_type = body.chart_type
    if body.dataset_id is not None:
        w.dataset_id = body.dataset_id

    cfg = db.query(WidgetConfig).filter(WidgetConfig.widget_id == w.id).first()
    if cfg:
        if body.style_json is not None:
            cfg.style_json = body.style_json
        if body.visualization_json is not None:
            cfg.visualization_json = body.visualization_json
        if body.interactions_json is not None:
            cfg.interactions_json = body.interactions_json
        if body.advanced_json is not None:
            cfg.advanced_json = body.advanced_json

    wq = db.query(WidgetQuery).filter(WidgetQuery.widget_id == w.id).first()
    if wq:
        if body.dataset_id is not None:
            wq.dataset_id = body.dataset_id
        if body.visual_spec_json is not None:
            wq.visual_spec_json = body.visual_spec_json
        if body.sql_text is not None:
            wq.sql_text = body.sql_text
        if body.query_mode is not None:
            wq.query_mode = body.query_mode

    if body.drill_hierarchy is not None or body.drill_rules is not None or body.drill_through_page_id is not None:
        wdr = db.query(WidgetDrillRule).filter(WidgetDrillRule.widget_id == w.id).first()
        if not wdr:
            wdr = WidgetDrillRule(widget_id=w.id)
            db.add(wdr)
        if body.drill_hierarchy is not None:
            wdr.hierarchy_json = body.drill_hierarchy
        if body.drill_rules is not None:
            wdr.level_rules_json = body.drill_rules
        if body.drill_through_page_id is not None:
            wdr.drill_through_page_id = body.drill_through_page_id

    cache_manager.invalidate_dashboard(w.dashboard_id)
    db.commit()
    return {"status": "updated"}


@router.delete("/widgets/{widget_id}")
def delete_widget(
    widget_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    w = db.query(Widget).filter(Widget.id == widget_id).first()
    if not w:
        raise HTTPException(status_code=404, detail="Widget not found")
    parent_chain = [("dashboard", w.dashboard_id), ("dashboard_page", w.page_id)]
    enforce_permission(db, user, "widget", w.id, "delete", parent_chain=parent_chain, ip_address=get_client_ip(request))
    db.delete(w)
    cache_manager.invalidate_dashboard(w.dashboard_id)
    db.commit()
    return {"status": "deleted"}


@router.post("/widgets/{widget_id}/duplicate")
def duplicate_widget(widget_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    w = db.query(Widget).filter(Widget.id == widget_id).first()
    if not w:
        raise HTTPException(status_code=404, detail="Widget not found")
    enforce_permission(db, user, "dashboard", w.dashboard_id, "add_widget")
    nw = Widget(
        dashboard_id=w.dashboard_id, page_id=w.page_id, title=f"{w.title} (Copy)",
        description=w.description, chart_type=w.chart_type, dataset_id=w.dataset_id,
        layout_x=(w.layout_x + 1) % 8, layout_y=w.layout_y + w.layout_h,
        layout_w=w.layout_w, layout_h=w.layout_h,
    )
    db.add(nw)
    db.flush()
    cfg = db.query(WidgetConfig).filter(WidgetConfig.widget_id == w.id).first()
    wq = db.query(WidgetQuery).filter(WidgetQuery.widget_id == w.id).first()
    wdr = db.query(WidgetDrillRule).filter(WidgetDrillRule.widget_id == w.id).first()
    if cfg:
        db.add(WidgetConfig(widget_id=nw.id, style_json=cfg.style_json, visualization_json=cfg.visualization_json, interactions_json=cfg.interactions_json, advanced_json=cfg.advanced_json))
    if wq:
        db.add(WidgetQuery(widget_id=nw.id, dataset_id=wq.dataset_id, saved_query_id=wq.saved_query_id, query_mode=wq.query_mode, sql_text=wq.sql_text, visual_spec_json=wq.visual_spec_json, parameters_json=wq.parameters_json))
    if wdr:
        db.add(WidgetDrillRule(widget_id=nw.id, hierarchy_json=wdr.hierarchy_json, level_rules_json=wdr.level_rules_json, drill_through_page_id=wdr.drill_through_page_id))
    for wf in db.query(WidgetFilter).filter(WidgetFilter.widget_id == w.id).all():
        db.add(WidgetFilter(widget_id=nw.id, field_name=wf.field_name, filter_type=wf.filter_type, operator=wf.operator, value_json=wf.value_json, is_active=wf.is_active))
    db.commit()
    return {"id": nw.id, "title": nw.title}


@router.post("/widgets/{widget_id}/switch-chart")
def switch_widget_chart_type(
    widget_id: str, body: SwitchChartRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """
    Runtime Chart Type Switching (Section 5):
    Preserves dataset, color/style settings, active widget filters, and intelligently maps X/Y/series fields.
    Returns explicit diagnostic message if target chart structure is incompatible.
    """
    w = db.query(Widget).filter(Widget.id == widget_id).first()
    if not w:
        raise HTTPException(status_code=404, detail="Widget not found")
    parent_chain = [("dashboard", w.dashboard_id), ("dashboard_page", w.page_id)]
    # Allow if user has change_chart_type OR edit
    if not permission_resolver.resolve(db, user, "widget", w.id, "change_chart_type", parent_chain).allowed:
        enforce_permission(db, user, "widget", w.id, "edit", parent_chain)

    target = body.target_chart_type
    if target not in ALL_30_CHART_TYPES:
        raise HTTPException(status_code=400, detail=f"Unknown chart type '{target}'")

    wq = db.query(WidgetQuery).filter(WidgetQuery.widget_id == w.id).first()
    spec = dict(wq.visual_spec_json) if wq else {}
    fields = db.query(DatasetField).filter(DatasetField.dataset_id == w.dataset_id).all() if w.dataset_id else []
    dim_fields = [f.field_name for f in fields if f.semantic_role in ("dimension", "time", "geo")]
    num_fields = [f.field_name for f in fields if f.semantic_role == "measure"]

    warnings: list[str] = []

    # Smart field mapping when switching chart families
    if target in ("kpi", "gauge", "progress"):
        if not spec.get("y_field"):
            if num_fields:
                spec["y_field"] = num_fields[0]
                spec.setdefault("aggregation", "SUM")
            else:
                spec["y_field"] = "*"
                spec["aggregation"] = "COUNT"
        if target in ("kpi", "gauge"):
            spec.pop("x_field", None)
    else:
        # Needs at least an x_field for categorical/time charts
        if not spec.get("x_field") and spec.get("columns"):
            # Switching from raw Table -> Bar/Line
            cols = spec["columns"]
            spec["x_field"] = next((c for c in cols if c in dim_fields), cols[0] if cols else None)
            spec["y_field"] = next((c for c in cols if c in num_fields), num_fields[0] if num_fields else "*")
            spec["aggregation"] = "SUM" if spec["y_field"] != "*" else "COUNT"
        if not spec.get("x_field"):
            if dim_fields:
                spec["x_field"] = dim_fields[0]
            else:
                raise HTTPException(
                    status_code=422,
                    detail=f"Incompatible dataset structure for '{target}': requires at least one categorical or time dimension field.",
                )
        if not spec.get("y_field"):
            spec["y_field"] = num_fields[0] if num_fields else "*"
            spec.setdefault("aggregation", "SUM" if spec["y_field"] != "*" else "COUNT")

    if target in ("stacked_bar", "stacked_bar_100", "stacked_area", "heatmap", "pivot_table", "matrix", "sankey"):
        if not spec.get("series_field"):
            cand = [d for d in dim_fields if d != spec.get("x_field")]
            if cand:
                spec["series_field"] = cand[0]
            elif target == "sankey":
                raise HTTPException(
                    status_code=422,
                    detail="Sankey diagram requires two dimension fields (Source and Target) plus a numeric measure.",
                )
            else:
                warnings.append("No secondary series dimension selected; showing single-series grouping.")

    if target in ("combo", "bubble", "scatter") and not spec.get("secondary_y_field"):
        cand_num = [m for m in num_fields if m != spec.get("y_field")]
        if cand_num:
            spec["secondary_y_field"] = cand_num[0]
            spec.setdefault("secondary_aggregation", "SUM")
        elif target in ("bubble", "scatter"):
            warnings.append("Only one numeric measure available; mapped secondary axis to count/value.")

    if target == "candlestick":
        ohlc = {"open", "close", "low", "high"}
        col_set = {f.field_name.lower() for f in fields}
        if not ohlc.issubset(col_set):
            warnings.append("Dataset lacks native OHLC columns; rendering synthesized range candlestick from primary measure.")

    w.chart_type = target
    if wq:
        wq.visual_spec_json = spec
        if wq.query_mode == "visual" and spec.get("aggregation") == "RAW" and target != "table":
            spec["aggregation"] = "SUM"
            wq.visual_spec_json = spec
    cache_manager.invalidate_dashboard(w.dashboard_id)
    db.commit()

    return {
        "widget_id": w.id,
        "chart_type": w.chart_type,
        "visual_spec": spec,
        "warnings": warnings,
        "preserved": ["dataset_id", "style", "widget_filters", "drill_rules"],
    }


# ---------------- FILTERS ----------------
@router.post("/{dashboard_id}/filters")
def add_dashboard_filter(
    dashboard_id: str, body: FilterCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    enforce_permission(db, user, "dashboard", dashboard_id, "manage_filters")
    df = DashboardFilter(
        dashboard_id=dashboard_id,
        page_id=body.page_id,
        scope=body.scope,
        name=body.name,
        field_name=body.field_name,
        dataset_id=body.dataset_id,
        filter_type=body.filter_type,
        operator=body.operator,
        default_value_json={"value": body.default_value} if body.default_value is not None else None,
        target_widget_ids=body.target_widget_ids,
        excluded_widget_ids=body.excluded_widget_ids,
        parent_filter_id=body.parent_filter_id,
        is_locked=body.is_locked,
    )
    db.add(df)
    db.commit()
    return {"id": df.id, "name": df.name}


@router.delete("/filters/{filter_id}")
def delete_dashboard_filter(filter_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    df = db.query(DashboardFilter).filter(DashboardFilter.id == filter_id).first()
    if not df:
        raise HTTPException(status_code=404, detail="Filter not found")
    enforce_permission(db, user, "dashboard", df.dashboard_id, "manage_filters")
    db.delete(df)
    db.commit()
    return {"status": "deleted"}


@router.post("/widgets/{widget_id}/filters")
def add_widget_filter(
    widget_id: str, body: WidgetFilterCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    w = db.query(Widget).filter(Widget.id == widget_id).first()
    if not w:
        raise HTTPException(status_code=404, detail="Widget not found")
    parent_chain = [("dashboard", w.dashboard_id), ("dashboard_page", w.page_id)]
    if not permission_resolver.resolve(db, user, "widget", w.id, "change_filter", parent_chain).allowed:
        enforce_permission(db, user, "widget", w.id, "edit", parent_chain)
    wf = WidgetFilter(
        widget_id=widget_id, field_name=body.field_name, filter_type=body.filter_type,
        operator=body.operator, value_json={"value": body.value}, is_active=True,
    )
    db.add(wf)
    cache_manager.invalidate_dashboard(w.dashboard_id)
    db.commit()
    return {"id": wf.id}


@router.delete("/widget-filters/{wf_id}")
def delete_widget_filter(wf_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    wf = db.query(WidgetFilter).filter(WidgetFilter.id == wf_id).first()
    if not wf:
        raise HTTPException(status_code=404, detail="Widget filter not found")
    w = db.query(Widget).filter(Widget.id == wf.widget_id).first()
    if w:
        enforce_permission(db, user, "widget", w.id, "edit", [("dashboard", w.dashboard_id)])
        cache_manager.invalidate_dashboard(w.dashboard_id)
    db.delete(wf)
    db.commit()
    return {"status": "deleted"}


# ---------------- WIDGET DATA EXECUTION (WITH CACHE, RLS, CLS, FILTERS, DRILL DOWN) ----------------
def _execute_single_widget(
    db: Session,
    user: User,
    w: Widget,
    active_filters: list[dict[str, Any]],
    cross_filters: list[dict[str, Any]],
    drill_stack: list[dict[str, Any]],
    use_cache: bool,
    ip_address: Optional[str] = None,
) -> dict[str, Any]:
    parent_chain = [("dashboard", w.dashboard_id), ("dashboard_page", w.page_id)]
    enforce_permission(db, user, "widget", w.id, "view", parent_chain=parent_chain, ip_address=ip_address)

    wq = db.query(WidgetQuery).filter(WidgetQuery.widget_id == w.id).first()
    wdr = db.query(WidgetDrillRule).filter(WidgetDrillRule.widget_id == w.id).first()
    ds = db.query(Dataset).filter(Dataset.id == (wq.dataset_id if wq and wq.dataset_id else w.dataset_id)).first()

    # 1. Resolve Drill Down (and verify Drill RBAC strictly!)
    try:
        drill_info = resolve_drill_state(db, user, w, wdr, drill_stack)
    except PermissionError as exc:
        record_audit(
            db, user.organization_id, "drill_down_denied", "drill_down", resource_id=w.id,
            status="denied", user_id=user.id, username=user.username, ip_address=ip_address,
            details={"reason": str(exc)}
        )
        raise HTTPException(status_code=403, detail=str(exc))

    if drill_stack:
        record_audit(
            db, user.organization_id, "drill_down", "widget", resource_id=w.id,
            status="success", user_id=user.id, username=user.username, ip_address=ip_address,
            details={"level": drill_info["level"], "stack": drill_stack}
        )

    # 2. Scope applicable filters (Global + Dashboard/Page + Excluded/Included Widgets + Widget Filters + Cross Filters + Drill Filters)
    dash_filters_db = db.query(DashboardFilter).filter(DashboardFilter.dashboard_id == w.dashboard_id).all()
    df_meta = {df.id: df for df in dash_filters_db}

    effective_filters: list[dict[str, Any]] = []
    for af in active_filters:
        fid = af.get("filter_id")
        meta = df_meta.get(fid) if fid else None
        if meta:
            # Check page scope if dashboard filter
            if meta.scope == "dashboard" and meta.page_id and meta.page_id != w.page_id:
                continue
            if meta.excluded_widget_ids and w.id in meta.excluded_widget_ids:
                continue
            if meta.target_widget_ids and w.id not in meta.target_widget_ids:
                continue
        effective_filters.append(af)

    # Cross filters (exclude the originating widget itself so it doesn't filter itself out unless configured)
    for cf in cross_filters:
        if cf.get("source_widget_id") != w.id:
            effective_filters.append(cf)

    # Widget-isolated filters
    for wf in db.query(WidgetFilter).filter(WidgetFilter.widget_id == w.id, WidgetFilter.is_active == True).all():
        val = wf.value_json.get("value") if isinstance(wf.value_json, dict) and "value" in wf.value_json else wf.value_json
        effective_filters.append({"field_name": wf.field_name, "operator": wf.operator, "value": val, "is_active": True})

    # Drill-down context filters
    effective_filters.extend(drill_info["drill_filters"])

    t0 = time.perf_counter()
    if wq and wq.query_mode == "sql" and wq.sql_text:
        res = execute_dataset_sql(
            db, user, wq.sql_text, dataset=ds,
            named_params=wq.parameters_json or {},
            filters=effective_filters,
            dashboard_id=w.dashboard_id, widget_id=w.id, use_cache=use_cache,
        )
    elif ds:
        sec_ctx = build_data_security_context(db, user, ds.id)
        perm_hash = permission_resolver.get_permission_context_hash(db, user) + ":" + sec_ctx.context_hash
        visual_spec = dict(wq.visual_spec_json) if wq else {}
        q_hash = cache_manager.hash_dict({
            "spec": visual_spec, "dim": drill_info["effective_dimension"],
            "raw": drill_info["is_raw_level"], "chart": drill_info["effective_chart_type"],
        })
        f_hash = cache_manager.hash_dict(effective_filters)
        cache_key = cache_manager.build_key(
            organization_id=user.organization_id, dataset_id=ds.id, data_version=ds.data_version,
            query_hash=q_hash, filter_hash=f_hash, permission_hash=perm_hash,
            dashboard_id=w.dashboard_id, widget_id=w.id,
        )
        cached = cache_manager.get(cache_key, db=db) if use_cache else None
        if cached is not None:
            res = {**cached, "cache_hit": True}
        else:
            sql, params, _ = build_visual_query_sql(
                db, ds, visual_spec, effective_filters, sec_ctx.rls_where_clauses, sec_ctx.rls_params,
                override_dimension=drill_info["effective_dimension"],
                raw_mode=drill_info["is_raw_level"],
            )
            raw_res = warehouse.execute_query(sql, params)
            vis_cols, masked_rows = apply_cls_and_masking(raw_res["columns"], raw_res["rows"], sec_ctx)
            res = {
                "columns": vis_cols,
                "rows": masked_rows,
                "row_count": len(masked_rows),
                "execution_ms": raw_res["execution_ms"],
                "cache_hit": False,
                "data_version": ds.data_version,
                "generated_sql": sql,
            }
            if use_cache:
                cache_manager.set(
                    cache_key, res,
                    meta_info={
                        "organization_id": user.organization_id, "dashboard_id": w.dashboard_id,
                        "widget_id": w.id, "dataset_id": ds.id, "data_version": ds.data_version,
                        "permission_hash": perm_hash, "filter_hash": f_hash,
                    },
                    db=db,
                )
    else:
        res = {"columns": [], "rows": [], "row_count": 0, "execution_ms": 0.0, "cache_hit": False}

    load_ms = round((time.perf_counter() - t0) * 1000, 3)
    db.add(WidgetPerformanceLog(
        widget_id=w.id, dashboard_id=w.dashboard_id, user_id=user.id,
        load_ms=load_ms, cache_hit=res.get("cache_hit", False), rows_returned=res.get("row_count", 0),
    ))
    db.commit()

    return {
        "widget_id": w.id,
        "effective_chart_type": drill_info["effective_chart_type"],
        "drill": {
            "level": drill_info["level"],
            "breadcrumbs": drill_info["breadcrumbs"],
            "can_drill_down": drill_info["can_drill_down"],
            "effective_dimension": drill_info["effective_dimension"],
            "drill_through_page_id": drill_info["drill_through_page_id"],
        },
        "freshness": {
            "status": ds.freshness_status if ds else "unknown",
            "last_refreshed_at": ds.last_refreshed_at if ds else None,
            "data_version": ds.data_version if ds else 1,
        },
        **res,
    }


@router.post("/widgets/{widget_id}/data")
def get_widget_data(
    widget_id: str,
    body: WidgetExecuteRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    w = db.query(Widget).filter(Widget.id == widget_id).first()
    if not w:
        raise HTTPException(status_code=404, detail="Widget not found")
    return _execute_single_widget(
        db, user, w, body.active_filters, body.cross_filters, body.drill_stack,
        body.use_cache, ip_address=get_client_ip(request)
    )


@router.post("/{dashboard_id}/execute-batch")
def execute_dashboard_batch(
    dashboard_id: str,
    body: BatchExecuteRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dash = db.query(Dashboard).filter(Dashboard.id == dashboard_id).first()
    if not dash:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    if not dash.is_public:
        enforce_permission(db, user, "dashboard", dash.id, "view", ip_address=get_client_ip(request))

    t0 = time.perf_counter()
    wq = db.query(Widget).filter(Widget.dashboard_id == dash.id)
    if body.page_id:
        wq = wq.filter(Widget.page_id == body.page_id)
    widgets = wq.all()

    results: dict[str, Any] = {}
    hits = 0
    misses = 0
    for w in widgets:
        parent_chain = [("dashboard", dash.id), ("dashboard_page", w.page_id)]
        if not permission_resolver.resolve(db, user, "widget", w.id, "view", parent_chain).allowed:
            continue
        try:
            d_stack = body.drill_states.get(w.id, [])
            r = _execute_single_widget(
                db, user, w, body.active_filters, body.cross_filters, d_stack,
                body.use_cache, ip_address=get_client_ip(request)
            )
            results[w.id] = r
            if r.get("cache_hit"):
                hits += 1
            else:
                misses += 1
        except Exception as exc:
            results[w.id] = {"widget_id": w.id, "error": str(exc), "columns": [], "rows": []}

    total_ms = round((time.perf_counter() - t0) * 1000, 3)
    db.add(DashboardPerformanceLog(
        dashboard_id=dash.id, user_id=user.id, load_ms=total_ms,
        widget_count=len(results), cache_hits=hits, cache_misses=misses,
    ))
    db.commit()
    return {"dashboard_id": dash.id, "load_ms": total_ms, "cache_hits": hits, "cache_misses": misses, "widgets": results}


# ---------------- DASHBOARD STATE ENGINE (Resume exact filter/drill/page state) ----------------
@router.get("/{dashboard_id}/state")
def get_dashboard_state(dashboard_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    st = db.query(DashboardState).filter(
        DashboardState.dashboard_id == dashboard_id, DashboardState.user_id == user.id
    ).first()
    if not st:
        return {"active_page_id": None, "filter_state": {}, "drill_state": {}, "widget_overrides": {}}
    return {
        "active_page_id": st.active_page_id,
        "filter_state": st.filter_state_json,
        "drill_state": st.drill_state_json,
        "widget_overrides": st.widget_overrides_json,
    }


@router.put("/{dashboard_id}/state")
def save_dashboard_state(
    dashboard_id: str, body: StateSaveRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    st = db.query(DashboardState).filter(
        DashboardState.dashboard_id == dashboard_id, DashboardState.user_id == user.id
    ).first()
    if not st:
        st = DashboardState(dashboard_id=dashboard_id, user_id=user.id)
        db.add(st)
    st.active_page_id = body.active_page_id
    st.filter_state_json = body.filter_state
    st.drill_state_json = body.drill_state
    st.widget_overrides_json = body.widget_overrides
    db.commit()
    return {"status": "state_saved"}


# ---------------- VERSIONING, COMMENTS, TEMPLATES & SHARING ----------------
@router.get("/{dashboard_id}/versions")
def list_versions(dashboard_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "dashboard", dashboard_id, "view")
    vers = db.query(DashboardVersion).filter(DashboardVersion.dashboard_id == dashboard_id).order_by(DashboardVersion.version_number.desc()).all()
    return [{"id": v.id, "version_number": v.version_number, "label": v.label, "created_at": v.created_at} for v in vers]


@router.post("/{dashboard_id}/versions")
def create_version(
    dashboard_id: str, body: dict[str, str], user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    enforce_permission(db, user, "dashboard", dashboard_id, "edit")
    dash = db.query(Dashboard).filter(Dashboard.id == dashboard_id).first()
    if not dash:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    snap = _serialize_dashboard(db, dash, user)
    latest = db.query(DashboardVersion).filter(DashboardVersion.dashboard_id == dashboard_id).order_by(DashboardVersion.version_number.desc()).first()
    next_num = (latest.version_number + 1) if latest else 1
    v = DashboardVersion(
        dashboard_id=dashboard_id, version_number=next_num,
        label=body.get("label", f"Snapshot v{next_num}"), snapshot_json=snap, created_by=user.id,
    )
    db.add(v)
    db.commit()
    return {"id": v.id, "version_number": v.version_number, "label": v.label}


@router.get("/{dashboard_id}/comments")
def list_comments(dashboard_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "dashboard", dashboard_id, "view")
    rows = db.query(DashboardComment).filter(DashboardComment.dashboard_id == dashboard_id).order_by(DashboardComment.created_at.desc()).all()
    return [{"id": c.id, "widget_id": c.widget_id, "author_name": c.author_name, "content": c.content, "created_at": c.created_at} for c in rows]


@router.post("/{dashboard_id}/comments")
def add_comment(
    dashboard_id: str, body: dict[str, Optional[str]], user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    enforce_permission(db, user, "dashboard", dashboard_id, "view")
    c = DashboardComment(
        dashboard_id=dashboard_id, widget_id=body.get("widget_id"),
        user_id=user.id, author_name=user.profile.full_name if user.profile else user.username,
        content=body.get("content", ""),
    )
    db.add(c)
    db.commit()
    return {"id": c.id, "author_name": c.author_name, "content": c.content, "created_at": c.created_at}


@router.post("/{dashboard_id}/share")
def generate_share_link(dashboard_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "dashboard", dashboard_id, "share")
    dash = db.query(Dashboard).filter(Dashboard.id == dashboard_id).first()
    if not dash:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    if not dash.embed_token:
        dash.embed_token = secrets.token_urlsafe(24)
    dash.is_public = True
    db.commit()
    return {"embed_token": dash.embed_token, "is_public": dash.is_public}


@router.post("/{dashboard_id}/clone-template")
def clone_from_template(dashboard_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "dashboard", "*", "add")
    src = db.query(Dashboard).filter(Dashboard.id == dashboard_id).first()
    if not src:
        raise HTTPException(status_code=404, detail="Template not found")
    nd = Dashboard(
        workspace_id=src.workspace_id, title=f"{src.title} (Workspace Copy)",
        description=src.description, theme_id=src.theme_id, is_template=False, created_by=user.id,
    )
    db.add(nd)
    db.flush()
    page_map = {}
    for p in db.query(DashboardPage).filter(DashboardPage.dashboard_id == src.id).all():
        np = DashboardPage(dashboard_id=nd.id, title=p.title, page_order=p.page_order, is_drill_through=p.is_drill_through)
        db.add(np)
        db.flush()
        page_map[p.id] = np.id
        db.add(DashboardLayout(page_id=np.id, columns_count=12, layout_json=[]))
    for w in db.query(Widget).filter(Widget.dashboard_id == src.id).all():
        nw = Widget(
            dashboard_id=nd.id, page_id=page_map[w.page_id], title=w.title, description=w.description,
            chart_type=w.chart_type, dataset_id=w.dataset_id, layout_x=w.layout_x, layout_y=w.layout_y,
            layout_w=w.layout_w, layout_h=w.layout_h,
        )
        db.add(nw)
        db.flush()
        cfg = db.query(WidgetConfig).filter(WidgetConfig.widget_id == w.id).first()
        wq = db.query(WidgetQuery).filter(WidgetQuery.widget_id == w.id).first()
        if cfg:
            db.add(WidgetConfig(widget_id=nw.id, style_json=cfg.style_json, visualization_json=cfg.visualization_json, interactions_json=cfg.interactions_json, advanced_json=cfg.advanced_json))
        if wq:
            db.add(WidgetQuery(widget_id=nw.id, dataset_id=wq.dataset_id, query_mode=wq.query_mode, sql_text=wq.sql_text, visual_spec_json=wq.visual_spec_json))
    db.commit()
    return {"id": nd.id, "title": nd.title}
