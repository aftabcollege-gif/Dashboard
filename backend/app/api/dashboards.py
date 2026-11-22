import secrets
import time
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.api.deps import enforce_permission, get_client_ip, get_current_user
from app.config import settings
from app.core.audit import record_audit
from app.core.cache import cache_manager
from app.core.permissions import permission_resolver
from app.core.rate_limit import rate_limiter
from app.core.rls_cls import (
    DataSecurityContext,
    apply_cls_and_masking,
    build_data_security_context,
    build_public_data_security_context,
    build_security_cte_sql,
    inspect_dataset_public_safety,
)
from app.core.sql_guard import extract_ast_column_references, validate_readonly_sql_ast
from app.core.tenant import (
    get_dashboard_for_org,
    get_dataset_for_org,
    get_default_workspace_for_org,
    get_page_for_org,
    get_theme_for_org,
    get_widget_for_org,
    get_workspace_for_org,
    list_org_allowed_dataset_tables,
)
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
    Workspace,
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
    "geo_map", "sankey", "combo", "bpmn",
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
    expected_updated_at: Optional[str] = None


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
    expected_updated_at: Optional[str] = None


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


def _is_explicit_deny(
    db: Session,
    user: User,
    resource_type: str,
    resource_id: str,
    action: str,
    parent_chain: Optional[list[tuple[str, str]]] = None,
) -> bool:
    dec = permission_resolver.resolve(db, user, resource_type, resource_id, action, parent_chain=parent_chain)
    return (not dec.allowed) and dec.reason.startswith("explicit_deny")


def _check_optimistic_concurrency(dash: Dashboard, expected_updated_at: Optional[str]) -> None:
    if not expected_updated_at or not dash.updated_at:
        return
    actual = dash.updated_at.isoformat()
    # Normalize naive vs tz-aware ISO strings by stripping trailing Z/+00:00 if needed for comparison
    norm_exp = expected_updated_at.replace("Z", "+00:00")
    norm_act = actual.replace("Z", "+00:00")
    if norm_exp != norm_act and norm_exp[:19] != norm_act[:19]:
        raise HTTPException(
            status_code=409,
            detail="Dashboard was modified by another session. Refresh to get the latest layout and changes.",
        )


def _assert_dataset_public_safe(db: Session, org_id: str, ds: Dataset) -> None:
    safe, reason = inspect_dataset_public_safety(db, org_id, ds)
    if not safe:
        raise HTTPException(
            status_code=403,
            detail=f"Public sharing blocked by data protection policy: {reason}",
        )


def _assert_dashboard_public_safe(db: Session, org_id: str, dash: Dashboard) -> None:
    if (dash.settings_json or {}).get("allow_public_restricted") is True:
        return
    widgets = db.query(Widget).filter(Widget.dashboard_id == dash.id).all()
    if not widgets:
        return
    w_ids = [w.id for w in widgets]
    wqs = {wq.widget_id: wq for wq in db.query(WidgetQuery).filter(WidgetQuery.widget_id.in_(w_ids)).all()}
    org_datasets = db.query(Dataset).filter(Dataset.organization_id == org_id).all()
    ds_by_id = {d.id: d for d in org_datasets}
    ds_by_tbl = {d.warehouse_table.lower(): d for d in org_datasets if d.warehouse_table}

    checked_ds: dict[str, Dataset] = {}
    for w in widgets:
        wq = wqs.get(w.id)
        for cand_id in (w.dataset_id, wq.dataset_id if wq else None):
            if cand_id and cand_id in ds_by_id:
                checked_ds[cand_id] = ds_by_id[cand_id]
        if wq and wq.visual_spec_json:
            for j in wq.visual_spec_json.get("joins", []) or []:
                jid = j.get("dataset_id")
                if jid and jid in ds_by_id:
                    checked_ds[jid] = ds_by_id[jid]
        if wq and wq.query_mode == "sql" and wq.sql_text:
            try:
                refs = validate_readonly_sql_ast(wq.sql_text, dialect="duckdb")
                for tbl in refs:
                    d_match = ds_by_tbl.get(tbl.lower())
                    if d_match:
                        checked_ds[d_match.id] = d_match
            except Exception:
                pass
    for ds_item in checked_ds.values():
        _assert_dataset_public_safe(db, org_id, ds_item)


def _enforce_widget_dataset_permissions(
    db: Session,
    user: User,
    dataset_id: Optional[str],
    query_mode: str,
    sql_text: Optional[str],
    visual_spec: Optional[dict[str, Any]],
    ip_address: Optional[str] = None,
) -> None:
    if dataset_id:
        ds = get_dataset_for_org(db, dataset_id, user.organization_id)
        enforce_permission(db, user, "dataset", ds.id, "view", ip_address=ip_address)
    if visual_spec:
        for j in visual_spec.get("joins", []) or []:
            jid = j.get("dataset_id")
            if jid:
                jds = get_dataset_for_org(db, str(jid), user.organization_id)
                enforce_permission(db, user, "dataset", jds.id, "view", ip_address=ip_address)
    if query_mode == "sql" and sql_text:
        org_map = list_org_allowed_dataset_tables(db, user.organization_id)
        try:
            refs = validate_readonly_sql_ast(
                sql_text, allowed_tables=set(org_map.keys()), require_table_allowlist=True
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid widget SQL: {exc}")
        for tbl in refs:
            ref_ds = org_map.get(tbl.lower())
            if ref_ds:
                enforce_permission(db, user, "dataset", ref_ds.id, "view", ip_address=ip_address)


def _execute_public_sql_widget(
    db: Session,
    org_id: str,
    ds: Dataset,
    sql_text: str,
    filters: list[dict[str, Any]],
    named_params: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Executes a public SQL widget strictly bound to its single registered dataset `ds.warehouse_table`,
    enforcing public CLS/masking/all-RLS CTE isolation BEFORE user SQL executes.
    """
    from app.engines.query_engine import _bind_named_params, _combine_ctes
    from app.engines.filter_engine import compile_filters_to_sql

    cleaned_sql = (sql_text or "").strip().rstrip(";")
    bound_sql, bound_params = _bind_named_params(cleaned_sql, named_params or {})
    validate_readonly_sql_ast(
        bound_sql,
        allowed_tables={ds.warehouse_table},
        dialect="duckdb",
        require_table_allowlist=True,
    )
    sc = build_public_data_security_context(db, org_id, ds.id)
    cols_meta = warehouse.describe_table(ds.warehouse_table)
    cte_sql, cte_params, vis_cols = build_security_cte_sql(
        ds.warehouse_table, cols_meta, sc, source_schema="main"
    )
    if sc.hidden_columns:
        for tqual, cname in extract_ast_column_references(bound_sql, dialect="duckdb"):
            if (not tqual or tqual.lower() == ds.warehouse_table.lower()) and cname in sc.hidden_columns:
                raise PermissionError(f"Access denied to hidden column '{cname}'")

    final_sql = _combine_ctes([cte_sql], bound_sql)
    final_params = list(cte_params) + list(bound_params)
    if filters:
        for flt in filters:
            if flt.get("is_active") is False:
                continue
            ff = str(flt.get("field_name") or flt.get("field") or "").strip()
            if ff and ff in sc.hidden_columns:
                raise PermissionError(f"Access denied to hidden column '{ff}' in filter")
        fc = compile_filters_to_sql(filters, available_columns=set(vis_cols))
        if fc["where_clauses"]:
            final_sql = f"SELECT * FROM ({final_sql}) AS _qf WHERE {' AND '.join(fc['where_clauses'])}"
            final_params.extend(fc["params"])

    raw_res = warehouse.execute_query(final_sql, final_params, limit=1000)
    cols, rows = apply_cls_and_masking(raw_res["columns"], raw_res["rows"], sc)
    return {
        "columns": cols,
        "rows": rows,
        "row_count": len(rows),
        "execution_ms": raw_res["execution_ms"],
    }


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

    w_ids = [w.id for w in widgets_all]
    ds_ids = list({w.dataset_id for w in widgets_all if w.dataset_id})
    page_ids = [p.id for p in pages]

    cfg_map = (
        {c.widget_id: c for c in db.query(WidgetConfig).filter(WidgetConfig.widget_id.in_(w_ids)).all()}
        if w_ids
        else {}
    )
    wq_map = (
        {q.widget_id: q for q in db.query(WidgetQuery).filter(WidgetQuery.widget_id.in_(w_ids)).all()}
        if w_ids
        else {}
    )
    wdr_map = (
        {d.widget_id: d for d in db.query(WidgetDrillRule).filter(WidgetDrillRule.widget_id.in_(w_ids)).all()}
        if w_ids
        else {}
    )
    wf_map: dict[str, list[WidgetFilter]] = {}
    if w_ids:
        for wf in db.query(WidgetFilter).filter(WidgetFilter.widget_id.in_(w_ids)).all():
            wf_map.setdefault(wf.widget_id, []).append(wf)
    ds_map = (
        {d.id: d for d in db.query(Dataset).filter(Dataset.id.in_(ds_ids)).all()}
        if ds_ids
        else {}
    )
    lay_map = (
        {l.page_id: l for l in db.query(DashboardLayout).filter(DashboardLayout.page_id.in_(page_ids)).all()}
        if page_ids
        else {}
    )

    visible_widgets = []
    for w in widgets_all:
        parent_chain = [("dashboard", dash.id), ("dashboard_page", w.page_id)]
        dec = permission_resolver.resolve(db, user, "widget", w.id, "view", parent_chain=parent_chain)
        if not dec.allowed:
            continue
        cfg = cfg_map.get(w.id)
        wq = wq_map.get(w.id)
        wdr = wdr_map.get(w.id)
        wfs = wf_map.get(w.id, [])
        ds = ds_map.get(w.dataset_id) if w.dataset_id else None

        can_edit_w = permission_resolver.resolve(db, user, "widget", w.id, "edit", parent_chain).allowed
        can_del_w = (
            permission_resolver.resolve(db, user, "widget", w.id, "delete", parent_chain).allowed
            and not _is_explicit_deny(db, user, "dashboard", dash.id, "delete_widget")
        )
        can_drill_w = permission_resolver.resolve(
            db, user, "drill_down", w.id, "drill_down", parent_chain + [("widget", w.id)]
        ).allowed
        can_chart_w = can_edit_w or permission_resolver.resolve(db, user, "widget", w.id, "change_chart_type", parent_chain).allowed
        can_style_w = can_edit_w or permission_resolver.resolve(db, user, "widget", w.id, "change_style", parent_chain).allowed
        can_filter_w = can_edit_w or permission_resolver.resolve(db, user, "widget", w.id, "change_filter", parent_chain).allowed
        can_export_w = permission_resolver.resolve(db, user, "widget", w.id, "export", parent_chain).allowed

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
            "permissions": {
                "view": True,
                "edit": can_edit_w,
                "delete": can_del_w,
                "drill_down": can_drill_w,
                "change_chart_type": can_chart_w,
                "change_style": can_style_w,
                "change_filter": can_filter_w,
                "export": can_export_w,
            },
        })

    layouts_by_page = {}
    for p in pages:
        lay = lay_map.get(p.id)
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
            "delete_widget": permission_resolver.resolve(db, user, "dashboard", dash.id, "delete_widget").allowed,
            "add_page": permission_resolver.resolve(db, user, "dashboard", dash.id, "add_page").allowed,
            "delete_page": permission_resolver.resolve(db, user, "dashboard", dash.id, "delete_page").allowed,
            "change_layout": permission_resolver.resolve(db, user, "dashboard", dash.id, "change_layout").allowed,
            "change_theme": permission_resolver.resolve(db, user, "dashboard", dash.id, "change_theme").allowed,
            "manage_filters": permission_resolver.resolve(db, user, "dashboard", dash.id, "manage_filters").allowed,
            "export": permission_resolver.resolve(db, user, "dashboard", dash.id, "export").allowed,
            "share": permission_resolver.resolve(db, user, "dashboard", dash.id, "share").allowed,
        },
        "updated_at": dash.updated_at,
    }


@router.get("/public/{embed_token}")
def get_public_dashboard(embed_token: str, request: Request, db: Session = Depends(get_db)):
    ip = get_client_ip(request)
    rate_limiter.check(f"public_dash:{ip}", limit=settings.RATE_LIMIT_PUBLIC_PER_MIN, window_seconds=60)

    row = (
        db.query(Dashboard, Workspace.organization_id)
        .join(Workspace, Workspace.id == Dashboard.workspace_id)
        .filter(Dashboard.embed_token == embed_token, Dashboard.is_public == True)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Shared dashboard not found or not public")
    dash, dash_org_id = row
    _assert_dashboard_public_safe(db, dash_org_id, dash)

    pages = db.query(DashboardPage).filter(DashboardPage.dashboard_id == dash.id).order_by(DashboardPage.page_order.asc()).all()
    widgets = db.query(Widget).filter(Widget.dashboard_id == dash.id).all()
    theme = db.query(DashboardTheme).filter(DashboardTheme.id == dash.theme_id).first() if dash.theme_id else None
    w_ids = [w.id for w in widgets]
    wq_map = {q.widget_id: q for q in db.query(WidgetQuery).filter(WidgetQuery.widget_id.in_(w_ids)).all()} if w_ids else {}
    cfg_map = {c.widget_id: c for c in db.query(WidgetConfig).filter(WidgetConfig.widget_id.in_(w_ids)).all()} if w_ids else {}
    wdr_map = {d.widget_id: d for d in db.query(WidgetDrillRule).filter(WidgetDrillRule.widget_id.in_(w_ids)).all()} if w_ids else {}

    out_widgets = []
    for w in widgets:
        wq = wq_map.get(w.id)
        cfg = cfg_map.get(w.id)
        wdr = wdr_map.get(w.id)
        target_ds_id = wq.dataset_id if wq and wq.dataset_id else w.dataset_id
        ds = (
            db.query(Dataset)
            .filter(Dataset.id == target_ds_id, Dataset.organization_id == dash_org_id)
            .first()
            if target_ds_id
            else None
        )
        data: dict[str, Any] = {"columns": [], "rows": []}
        if ds and wq:
            try:
                _assert_dataset_public_safe(db, dash_org_id, ds)
                if wq.query_mode == "sql" and wq.sql_text:
                    data = _execute_public_sql_widget(
                        db, dash_org_id, ds, wq.sql_text, [], named_params=wq.parameters_json or {}
                    )
                else:
                    sec_bundle: dict[str, Any] = {}
                    sql, params, _ = build_visual_query_sql(
                        db,
                        ds,
                        wq.visual_spec_json or {},
                        [],
                        [],
                        [],
                        raw_mode=(w.chart_type == "bpmn"),
                        public_mode=True,
                        security_bundle_out=sec_bundle,
                    )
                    raw_res = warehouse.execute_query(sql, params)
                    eff_ctx = sec_bundle.get("merged_sec_ctx") or build_public_data_security_context(db, dash_org_id, ds.id)
                    vis_cols, vis_rows = apply_cls_and_masking(raw_res["columns"], raw_res["rows"], eff_ctx)
                    data = {"columns": vis_cols, "rows": vis_rows, "row_count": len(vis_rows), "execution_ms": raw_res["execution_ms"]}
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
    all_themes = (
        db.query(DashboardTheme)
        .filter((DashboardTheme.is_builtin == True) | (DashboardTheme.organization_id == dash_org_id))
        .all()
    )
    serialized_themes = [
        {"id": th.id, "name": th.name, "slug": th.slug, "mode": th.mode, "tokens": th.tokens_json}
        for th in all_themes
    ]
    return {
        "id": dash.id,
        "title": dash.title,
        "description": dash.description,
        "theme": {
            "id": theme.id, "name": theme.name, "slug": theme.slug, "mode": theme.mode, "tokens": theme.tokens_json
        } if theme else None,
        "themes": serialized_themes,
        "available_themes": serialized_themes,
        "pages": [{"id": p.id, "title": p.title} for p in pages],
        "widgets": out_widgets,
    }


@router.post("/public/{embed_token}/execute-batch")
def execute_public_dashboard_batch(
    embed_token: str,
    body: BatchExecuteRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    ip = get_client_ip(request)
    rate_limiter.check(f"public_batch:{ip}", limit=settings.RATE_LIMIT_PUBLIC_PER_MIN, window_seconds=60)

    row = (
        db.query(Dashboard, Workspace.organization_id)
        .join(Workspace, Workspace.id == Dashboard.workspace_id)
        .filter(Dashboard.embed_token == embed_token, Dashboard.is_public == True)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Shared dashboard not found or not public")
    dash, dash_org_id = row
    _assert_dashboard_public_safe(db, dash_org_id, dash)

    t0 = time.perf_counter()
    widgets = db.query(Widget).filter(Widget.dashboard_id == dash.id).all()
    w_ids = [w.id for w in widgets]
    wq_map = {q.widget_id: q for q in db.query(WidgetQuery).filter(WidgetQuery.widget_id.in_(w_ids)).all()} if w_ids else {}
    wdr_map = {d.widget_id: d for d in db.query(WidgetDrillRule).filter(WidgetDrillRule.widget_id.in_(w_ids)).all()} if w_ids else {}
    results: dict[str, Any] = {}

    for w in widgets:
        wq = wq_map.get(w.id)
        wdr = wdr_map.get(w.id)
        target_ds_id = wq.dataset_id if wq and wq.dataset_id else w.dataset_id
        ds = (
            db.query(Dataset)
            .filter(Dataset.id == target_ds_id, Dataset.organization_id == dash_org_id)
            .first()
            if target_ds_id
            else None
        )
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

        data: dict[str, Any] = {"columns": [], "rows": [], "row_count": 0, "execution_ms": 0.0}
        if ds and wq:
            try:
                _assert_dataset_public_safe(db, dash_org_id, ds)
                if wq.query_mode == "sql" and wq.sql_text:
                    data = _execute_public_sql_widget(
                        db, dash_org_id, ds, wq.sql_text, eff_filters, named_params=wq.parameters_json or {}
                    )
                else:
                    sec_bundle: dict[str, Any] = {}
                    sql, params, _ = build_visual_query_sql(
                        db,
                        ds,
                        wq.visual_spec_json or {},
                        eff_filters,
                        [],
                        [],
                        override_dimension=eff_dim,
                        raw_mode=(eff_chart == "bpmn"),
                        public_mode=True,
                        security_bundle_out=sec_bundle,
                    )
                    raw_res = warehouse.execute_query(sql, params)
                    eff_ctx = sec_bundle.get("merged_sec_ctx") or build_public_data_security_context(db, dash_org_id, ds.id)
                    vis_cols, vis_rows = apply_cls_and_masking(raw_res["columns"], raw_res["rows"], eff_ctx)
                    data = {
                        "columns": vis_cols,
                        "rows": vis_rows,
                        "row_count": len(vis_rows),
                        "execution_ms": raw_res["execution_ms"],
                    }
            except Exception:
                data = {"columns": [], "rows": [], "error": "Widget query could not be executed"}

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
    rows = db.query(Workspace).filter(Workspace.organization_id == user.organization_id).all()
    return [{"id": w.id, "name": w.name, "description": w.description} for w in rows]


@router.get("/templates")
def list_templates(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = (
        db.query(Dashboard)
        .join(Workspace, Workspace.id == Dashboard.workspace_id)
        .filter(Workspace.organization_id == user.organization_id, Dashboard.is_template == True)
        .all()
    )
    visible = []
    for d in rows:
        if d.is_public or permission_resolver.resolve(db, user, "dashboard", d.id, "view").allowed:
            visible.append({"id": d.id, "title": d.title, "description": d.description, "theme_id": d.theme_id})
    return visible


@router.get("")
def list_dashboards(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    all_dash = (
        db.query(Dashboard)
        .join(Workspace, Workspace.id == Dashboard.workspace_id)
        .filter(Workspace.organization_id == user.organization_id)
        .all()
    )
    dash_ids = [d.id for d in all_dash]
    widget_counts: dict[str, int] = (
        dict(
            db.query(Widget.dashboard_id, func.count(Widget.id))
            .filter(Widget.dashboard_id.in_(dash_ids))
            .group_by(Widget.dashboard_id)
            .all()
        )
        if dash_ids
        else {}
    )
    visible = []
    for d in all_dash:
        if d.is_public or permission_resolver.resolve(db, user, "dashboard", d.id, "view").allowed:
            visible.append({
                "id": d.id, "workspace_id": d.workspace_id, "title": d.title,
                "description": d.description, "theme_id": d.theme_id, "is_public": d.is_public,
                "is_template": d.is_template, "updated_at": d.updated_at,
                "widget_count": int(widget_counts.get(d.id, 0)),
            })
    return visible


@router.post("")
def create_dashboard(
    body: DashboardCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ip = get_client_ip(request)
    enforce_permission(db, user, "dashboard", "*", "add", ip_address=ip)
    if body.is_public:
        enforce_permission(db, user, "dashboard", "*", "share", ip_address=ip)
    if body.workspace_id:
        ws = get_workspace_for_org(db, body.workspace_id, user.organization_id)
    else:
        ws = get_default_workspace_for_org(db, user.organization_id)
        if not ws:
            raise HTTPException(status_code=404, detail="Workspace not found")
    if body.theme_id:
        get_theme_for_org(db, body.theme_id, user.organization_id)

    d = Dashboard(
        workspace_id=ws.id,
        title=body.title,
        description=body.description,
        theme_id=body.theme_id,
        is_template=body.is_template,
        is_public=body.is_public,
        embed_token=secrets.token_urlsafe(24) if body.is_public else None,
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
    dash = get_dashboard_for_org(db, dashboard_id, user.organization_id)
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
    dash = get_dashboard_for_org(db, dashboard_id, user.organization_id)
    _check_optimistic_concurrency(dash, body.expected_updated_at)

    if body.theme_id is not None and any(
        v is not None for v in (body.title, body.description, body.is_public, body.refresh_interval_sec, body.settings_json)
    ):
        enforce_permission(db, user, "dashboard", dash.id, "edit", ip_address=get_client_ip(request))
        get_theme_for_org(db, body.theme_id, user.organization_id)
        dash.theme_id = body.theme_id
    elif body.theme_id is not None:
        if not permission_resolver.resolve(db, user, "dashboard", dash.id, "change_theme").allowed:
            enforce_permission(db, user, "dashboard", dash.id, "edit", ip_address=get_client_ip(request))
        get_theme_for_org(db, body.theme_id, user.organization_id)
        dash.theme_id = body.theme_id
    else:
        enforce_permission(db, user, "dashboard", dash.id, "edit", ip_address=get_client_ip(request))

    if body.is_public is not None:
        enforce_permission(db, user, "dashboard", dash.id, "share", ip_address=get_client_ip(request))
        if body.is_public:
            _assert_dashboard_public_safe(db, user.organization_id, dash)
            if not dash.embed_token:
                dash.embed_token = secrets.token_urlsafe(24)
        dash.is_public = body.is_public
    if body.title is not None:
        dash.title = body.title
    if body.description is not None:
        dash.description = body.description
    if body.refresh_interval_sec is not None:
        dash.refresh_interval_sec = body.refresh_interval_sec
    if body.settings_json is not None:
        dash.settings_json = body.settings_json
    db.commit()
    db.refresh(dash)
    return {"status": "updated", "updated_at": dash.updated_at}


@router.delete("/{dashboard_id}")
def delete_dashboard(
    dashboard_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    dash = get_dashboard_for_org(db, dashboard_id, user.organization_id)
    enforce_permission(db, user, "dashboard", dash.id, "delete", ip_address=get_client_ip(request))
    db.delete(dash)
    cache_manager.invalidate_dashboard(dash.id)
    db.commit()
    return {"status": "deleted"}


@router.post("/{dashboard_id}/pages")
def add_dashboard_page(
    dashboard_id: str, body: PageCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    dash = get_dashboard_for_org(db, dashboard_id, user.organization_id)
    if not permission_resolver.resolve(db, user, "dashboard", dash.id, "add_page").allowed:
        enforce_permission(db, user, "dashboard", dash.id, "edit")
    order = db.query(DashboardPage).filter(DashboardPage.dashboard_id == dash.id).count()
    p = DashboardPage(dashboard_id=dash.id, title=body.title, page_order=order, is_drill_through=body.is_drill_through)
    db.add(p)
    db.flush()
    db.add(DashboardLayout(page_id=p.id, columns_count=12, layout_json=[]))
    db.commit()
    return {"id": p.id, "title": p.title, "page_order": p.page_order}


@router.put("/{dashboard_id}/layout")
def save_dashboard_layout(
    dashboard_id: str, body: LayoutSaveRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    dash = get_dashboard_for_org(db, dashboard_id, user.organization_id)
    _check_optimistic_concurrency(dash, body.expected_updated_at)
    if not permission_resolver.resolve(db, user, "dashboard", dash.id, "change_layout").allowed:
        enforce_permission(db, user, "dashboard", dash.id, "edit")
    page, page_dash = get_page_for_org(db, body.page_id, user.organization_id)
    if page_dash.id != dash.id:
        raise HTTPException(status_code=400, detail="Page does not belong to dashboard")

    lay = db.query(DashboardLayout).filter(DashboardLayout.page_id == page.id).first()
    if not lay:
        lay = DashboardLayout(page_id=page.id)
        db.add(lay)
    lay.columns_count = body.columns_count
    lay.row_height = body.row_height
    lay.gap_px = body.gap_px
    lay.grid_enabled = body.grid_enabled
    lay.layout_json = body.items
    for item in body.items:
        wid = item.get("i")
        w = db.query(Widget).filter(Widget.id == wid, Widget.dashboard_id == dash.id).first()
        if w:
            w.layout_x = int(item.get("x", w.layout_x))
            w.layout_y = int(item.get("y", w.layout_y))
            w.layout_w = int(item.get("w", w.layout_w))
            w.layout_h = int(item.get("h", w.layout_h))
    db.commit()
    db.refresh(dash)
    return {"status": "layout_saved", "updated_at": dash.updated_at}


# ---------------- WIDGET CRUD + DUPLICATE + CHART SWITCHER ----------------
@router.post("/{dashboard_id}/widgets")
def create_widget(
    dashboard_id: str, body: WidgetCreate, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    dash = get_dashboard_for_org(db, dashboard_id, user.organization_id)
    ip = get_client_ip(request)
    if not permission_resolver.resolve(db, user, "dashboard", dash.id, "add_widget").allowed:
        enforce_permission(db, user, "dashboard", dash.id, "edit", ip_address=ip)
    if _is_explicit_deny(db, user, "widget", "*", "add", [("dashboard", dash.id)]):
        enforce_permission(db, user, "widget", "*", "add", [("dashboard", dash.id)], ip_address=ip)

    page, page_dash = get_page_for_org(db, body.page_id, user.organization_id)
    if page_dash.id != dash.id:
        raise HTTPException(status_code=400, detail="Page does not belong to dashboard")
    _enforce_widget_dataset_permissions(
        db, user, body.dataset_id, body.query_mode, body.sql_text, body.visual_spec, ip_address=ip
    )
    if dash.is_public and body.dataset_id:
        ds = get_dataset_for_org(db, body.dataset_id, user.organization_id)
        _assert_dataset_public_safe(db, user.organization_id, ds)
    if body.chart_type not in ALL_30_CHART_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported chart_type: {body.chart_type}")

    w = Widget(
        dashboard_id=dash.id, page_id=page.id, title=body.title,
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
            DataLineage.target_type == "dashboard", DataLineage.target_id == dash.id,
        ).first():
            db.add(DataLineage(source_type="dataset", source_id=body.dataset_id, target_type="dashboard", target_id=dash.id))
    db.commit()
    return {"id": w.id, "title": w.title}


@router.put("/widgets/{widget_id}")
def update_widget(
    widget_id: str, body: WidgetUpdate, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    w, dash = get_widget_for_org(db, widget_id, user.organization_id)
    parent_chain = [("dashboard", dash.id), ("dashboard_page", w.page_id)]
    ip = get_client_ip(request)
    enforce_permission(db, user, "widget", w.id, "edit", parent_chain=parent_chain, ip_address=ip)

    eff_wq = db.query(WidgetQuery).filter(WidgetQuery.widget_id == w.id).first()
    eff_mode = body.query_mode or (eff_wq.query_mode if eff_wq else "visual")
    eff_ds = body.dataset_id if body.dataset_id is not None else w.dataset_id
    eff_spec = body.visual_spec_json if body.visual_spec_json is not None else (eff_wq.visual_spec_json if eff_wq else {})
    eff_sql = body.sql_text if body.sql_text is not None else (eff_wq.sql_text if eff_wq else None)
    if any(x is not None for x in (body.dataset_id, body.sql_text, body.visual_spec_json, body.query_mode)):
        _enforce_widget_dataset_permissions(db, user, eff_ds, eff_mode, eff_sql, eff_spec, ip_address=ip)
        if dash.is_public and eff_ds:
            _assert_dataset_public_safe(db, user.organization_id, get_dataset_for_org(db, eff_ds, user.organization_id))

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

    cache_manager.invalidate_dashboard(dash.id)
    db.commit()
    return {"status": "updated"}


@router.delete("/widgets/{widget_id}")
def delete_widget(
    widget_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    w, dash = get_widget_for_org(db, widget_id, user.organization_id)
    ip = get_client_ip(request)
    parent_chain = [("dashboard", dash.id), ("dashboard_page", w.page_id)]
    if _is_explicit_deny(db, user, "dashboard", dash.id, "delete_widget"):
        enforce_permission(db, user, "dashboard", dash.id, "delete_widget", ip_address=ip)
    enforce_permission(db, user, "widget", w.id, "delete", parent_chain=parent_chain, ip_address=ip)
    db.delete(w)
    cache_manager.invalidate_dashboard(dash.id)
    db.commit()
    return {"status": "deleted"}


@router.post("/widgets/{widget_id}/duplicate")
def duplicate_widget(widget_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    w, dash = get_widget_for_org(db, widget_id, user.organization_id)
    if not permission_resolver.resolve(db, user, "dashboard", dash.id, "add_widget").allowed:
        enforce_permission(db, user, "dashboard", dash.id, "edit")
    nw = Widget(
        dashboard_id=dash.id, page_id=w.page_id, title=f"{w.title} (Copy)",
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
    w, dash = get_widget_for_org(db, widget_id, user.organization_id)
    parent_chain = [("dashboard", dash.id), ("dashboard_page", w.page_id)]
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
        if not spec.get("x_field") and spec.get("columns"):
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
    cache_manager.invalidate_dashboard(dash.id)
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
    dash = get_dashboard_for_org(db, dashboard_id, user.organization_id)
    if not permission_resolver.resolve(db, user, "dashboard", dash.id, "manage_filters").allowed:
        enforce_permission(db, user, "dashboard", dash.id, "edit")
    if body.dataset_id:
        get_dataset_for_org(db, body.dataset_id, user.organization_id)
    if body.page_id:
        p, pdash = get_page_for_org(db, body.page_id, user.organization_id)
        if pdash.id != dash.id:
            raise HTTPException(status_code=400, detail="Page does not belong to dashboard")

    df = DashboardFilter(
        dashboard_id=dash.id,
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
    row = (
        db.query(DashboardFilter, Dashboard)
        .join(Dashboard, Dashboard.id == DashboardFilter.dashboard_id)
        .join(Workspace, Workspace.id == Dashboard.workspace_id)
        .filter(DashboardFilter.id == filter_id, Workspace.organization_id == user.organization_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Filter not found")
    df, dash = row
    if not permission_resolver.resolve(db, user, "dashboard", dash.id, "manage_filters").allowed:
        enforce_permission(db, user, "dashboard", dash.id, "edit")
    db.delete(df)
    db.commit()
    return {"status": "deleted"}


@router.post("/widgets/{widget_id}/filters")
def add_widget_filter(
    widget_id: str, body: WidgetFilterCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    w, dash = get_widget_for_org(db, widget_id, user.organization_id)
    parent_chain = [("dashboard", dash.id), ("dashboard_page", w.page_id)]
    if not permission_resolver.resolve(db, user, "widget", w.id, "change_filter", parent_chain).allowed:
        enforce_permission(db, user, "widget", w.id, "edit", parent_chain)
    wf = WidgetFilter(
        widget_id=w.id, field_name=body.field_name, filter_type=body.filter_type,
        operator=body.operator, value_json={"value": body.value}, is_active=True,
    )
    db.add(wf)
    cache_manager.invalidate_dashboard(dash.id)
    db.commit()
    return {"id": wf.id}


@router.delete("/widget-filters/{wf_id}")
def delete_widget_filter(wf_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = (
        db.query(WidgetFilter, Widget, Dashboard)
        .join(Widget, Widget.id == WidgetFilter.widget_id)
        .join(Dashboard, Dashboard.id == Widget.dashboard_id)
        .join(Workspace, Workspace.id == Dashboard.workspace_id)
        .filter(WidgetFilter.id == wf_id, Workspace.organization_id == user.organization_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Widget filter not found")
    wf, w, dash = row
    parent_chain = [("dashboard", dash.id), ("dashboard_page", w.page_id)]
    if not permission_resolver.resolve(db, user, "widget", w.id, "change_filter", parent_chain).allowed:
        enforce_permission(db, user, "widget", w.id, "edit", parent_chain)
    cache_manager.invalidate_dashboard(dash.id)
    db.delete(wf)
    db.commit()
    return {"status": "deleted"}


# ---------------- WIDGET DATA EXECUTION ----------------
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
    target_ds_id = wq.dataset_id if wq and wq.dataset_id else w.dataset_id
    ds = (
        db.query(Dataset)
        .filter(Dataset.id == target_ds_id, Dataset.organization_id == user.organization_id)
        .first()
        if target_ds_id
        else None
    )
    if ds:
        ds_dec = permission_resolver.resolve(db, user, "dataset", ds.id, "view")
        if not ds_dec.allowed and ds_dec.reason.startswith("explicit_deny"):
            raise HTTPException(
                status_code=403,
                detail=f"Permission denied for dataset '{ds.name}' ({ds_dec.reason})",
            )

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

    dash_filters_db = db.query(DashboardFilter).filter(DashboardFilter.dashboard_id == w.dashboard_id).all()
    df_meta = {df.id: df for df in dash_filters_db}

    effective_filters: list[dict[str, Any]] = []
    for af in active_filters:
        fid = af.get("filter_id")
        meta = df_meta.get(fid) if fid else None
        if meta:
            if meta.scope == "dashboard" and meta.page_id and meta.page_id != w.page_id:
                continue
            if meta.excluded_widget_ids and w.id in meta.excluded_widget_ids:
                continue
            if meta.target_widget_ids and w.id not in meta.target_widget_ids:
                continue
        effective_filters.append(af)

    for cf in cross_filters:
        if cf.get("source_widget_id") != w.id:
            effective_filters.append(cf)

    for wf in db.query(WidgetFilter).filter(WidgetFilter.widget_id == w.id, WidgetFilter.is_active == True).all():
        val = wf.value_json.get("value") if isinstance(wf.value_json, dict) and "value" in wf.value_json else wf.value_json
        effective_filters.append({"field_name": wf.field_name, "operator": wf.operator, "value": val, "is_active": True})

    effective_filters.extend(drill_info["drill_filters"])

    t0 = time.perf_counter()
    if wq and wq.query_mode == "sql" and wq.sql_text:
        res = execute_dataset_sql(
            db, user, wq.sql_text, dataset=ds,
            named_params=wq.parameters_json or {},
            filters=effective_filters,
            dashboard_id=w.dashboard_id, widget_id=w.id, use_cache=use_cache,
            enforce_dataset_rbac=True,
        )
    elif ds:
        sec_ctx = build_data_security_context(db, user, ds.id)
        visual_spec = dict(wq.visual_spec_json) if wq else {}
        is_bpmn_raw = drill_info["effective_chart_type"] == "bpmn"
        sec_bundle: dict[str, Any] = {}
        sql, params, _ = build_visual_query_sql(
            db, ds, visual_spec, effective_filters, sec_ctx.rls_where_clauses, sec_ctx.rls_params,
            override_dimension=drill_info["effective_dimension"],
            raw_mode=drill_info["is_raw_level"] or is_bpmn_raw,
            user=user,
            enforce_join_rbac=True,
            security_bundle_out=sec_bundle,
        )
        eff_ctx: DataSecurityContext = sec_bundle.get("merged_sec_ctx") or sec_ctx
        combined_sec_hash = sec_bundle.get("combined_context_hash") or sec_ctx.context_hash
        joined_versions = sec_bundle.get("joined_versions") or []
        perm_hash = permission_resolver.get_permission_context_hash(db, user) + ":" + combined_sec_hash
        q_hash = cache_manager.hash_dict({
            "spec": visual_spec, "dim": drill_info["effective_dimension"],
            "raw": drill_info["is_raw_level"] or is_bpmn_raw, "chart": drill_info["effective_chart_type"],
            "joined_versions": joined_versions,
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
            raw_res = warehouse.execute_query(sql, params)
            vis_cols, masked_rows = apply_cls_and_masking(raw_res["columns"], raw_res["rows"], eff_ctx)
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
    w, _ = get_widget_for_org(db, widget_id, user.organization_id)
    try:
        return _execute_single_widget(
            db, user, w, body.active_filters, body.cross_filters, body.drill_stack,
            body.use_cache, ip_address=get_client_ip(request)
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{dashboard_id}/execute-batch")
def execute_dashboard_batch(
    dashboard_id: str,
    body: BatchExecuteRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dash = get_dashboard_for_org(db, dashboard_id, user.organization_id)
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


# ---------------- DASHBOARD STATE ENGINE ----------------
@router.get("/{dashboard_id}/state")
def get_dashboard_state(dashboard_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    dash = get_dashboard_for_org(db, dashboard_id, user.organization_id)
    if not dash.is_public:
        enforce_permission(db, user, "dashboard", dash.id, "view")
    st = db.query(DashboardState).filter(
        DashboardState.dashboard_id == dash.id, DashboardState.user_id == user.id
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
    dash = get_dashboard_for_org(db, dashboard_id, user.organization_id)
    if not dash.is_public:
        enforce_permission(db, user, "dashboard", dash.id, "view")
    st = db.query(DashboardState).filter(
        DashboardState.dashboard_id == dash.id, DashboardState.user_id == user.id
    ).first()
    if not st:
        st = DashboardState(dashboard_id=dash.id, user_id=user.id)
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
    dash = get_dashboard_for_org(db, dashboard_id, user.organization_id)
    enforce_permission(db, user, "dashboard", dash.id, "view")
    vers = db.query(DashboardVersion).filter(DashboardVersion.dashboard_id == dash.id).order_by(DashboardVersion.version_number.desc()).all()
    return [{"id": v.id, "version_number": v.version_number, "label": v.label, "created_at": v.created_at} for v in vers]


@router.post("/{dashboard_id}/versions")
def create_version(
    dashboard_id: str, body: dict[str, str], user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    dash = get_dashboard_for_org(db, dashboard_id, user.organization_id)
    enforce_permission(db, user, "dashboard", dash.id, "edit")
    snap = _serialize_dashboard(db, dash, user)
    latest = db.query(DashboardVersion).filter(DashboardVersion.dashboard_id == dash.id).order_by(DashboardVersion.version_number.desc()).first()
    next_num = (latest.version_number + 1) if latest else 1
    v = DashboardVersion(
        dashboard_id=dash.id, version_number=next_num,
        label=body.get("label", f"Snapshot v{next_num}"), snapshot_json=snap, created_by=user.id,
    )
    db.add(v)
    db.commit()
    return {"id": v.id, "version_number": v.version_number, "label": v.label}


@router.post("/{dashboard_id}/versions/{version_id}/restore")
def restore_version(
    dashboard_id: str, version_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    dash = get_dashboard_for_org(db, dashboard_id, user.organization_id)
    enforce_permission(db, user, "dashboard", dash.id, "edit")
    ver = (
        db.query(DashboardVersion)
        .filter(DashboardVersion.id == version_id, DashboardVersion.dashboard_id == dash.id)
        .first()
    )
    if not ver:
        raise HTTPException(status_code=404, detail="Version snapshot not found")
    snap = ver.snapshot_json or {}
    if snap.get("title"):
        dash.title = snap["title"]
    if snap.get("description") is not None:
        dash.description = snap["description"]
    if snap.get("theme_id"):
        dash.theme_id = snap["theme_id"]

    # Restore widget layouts and configs present in snapshot
    snap_widgets = {w["id"]: w for w in (snap.get("widgets") or []) if w.get("id")}
    for w in db.query(Widget).filter(Widget.dashboard_id == dash.id).all():
        sw = snap_widgets.get(w.id)
        if not sw:
            continue
        w.title = sw.get("title", w.title)
        w.chart_type = sw.get("chart_type", w.chart_type)
        lay = sw.get("layout") or {}
        w.layout_x = int(lay.get("x", w.layout_x))
        w.layout_y = int(lay.get("y", w.layout_y))
        w.layout_w = int(lay.get("w", w.layout_w))
        w.layout_h = int(lay.get("h", w.layout_h))
        cfg = db.query(WidgetConfig).filter(WidgetConfig.widget_id == w.id).first()
        if cfg and sw.get("config"):
            cfg.style_json = sw["config"].get("style", cfg.style_json)
            cfg.visualization_json = sw["config"].get("visualization", cfg.visualization_json)
    cache_manager.invalidate_dashboard(dash.id)
    db.commit()
    return {"status": "restored", "version_number": ver.version_number}


@router.get("/{dashboard_id}/comments")
def list_comments(dashboard_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    dash = get_dashboard_for_org(db, dashboard_id, user.organization_id)
    enforce_permission(db, user, "dashboard", dash.id, "view")
    rows = db.query(DashboardComment).filter(DashboardComment.dashboard_id == dash.id).order_by(DashboardComment.created_at.desc()).all()
    return [{"id": c.id, "widget_id": c.widget_id, "author_name": c.author_name, "content": c.content, "created_at": c.created_at} for c in rows]


@router.post("/{dashboard_id}/comments")
def add_comment(
    dashboard_id: str, body: dict[str, Optional[str]], user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    dash = get_dashboard_for_org(db, dashboard_id, user.organization_id)
    enforce_permission(db, user, "dashboard", dash.id, "view")
    c = DashboardComment(
        dashboard_id=dash.id, widget_id=body.get("widget_id"),
        user_id=user.id, author_name=user.profile.full_name if user.profile else user.username,
        content=body.get("content", ""),
    )
    db.add(c)
    db.commit()
    return {"id": c.id, "author_name": c.author_name, "content": c.content, "created_at": c.created_at}


@router.post("/{dashboard_id}/share")
def generate_share_link(dashboard_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    dash = get_dashboard_for_org(db, dashboard_id, user.organization_id)
    enforce_permission(db, user, "dashboard", dash.id, "share")
    _assert_dashboard_public_safe(db, user.organization_id, dash)
    if not dash.embed_token:
        dash.embed_token = secrets.token_urlsafe(24)
    dash.is_public = True
    db.commit()
    return {"embed_token": dash.embed_token, "is_public": dash.is_public}


@router.post("/{dashboard_id}/clone-template")
def clone_from_template(dashboard_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "dashboard", "*", "add")
    src = get_dashboard_for_org(db, dashboard_id, user.organization_id)
    if not src.is_template and not src.is_public:
        enforce_permission(db, user, "dashboard", src.id, "view")
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
