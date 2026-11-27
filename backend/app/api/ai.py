from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.api.deps import enforce_permission, get_client_ip, get_current_user
from app.core.audit import record_audit
from app.core.rls_cls import build_data_security_context, build_security_projection_and_where
from app.core.tenant import (
    get_dashboard_for_org,
    get_dataset_for_org,
    get_default_workspace_for_org,
    get_page_for_org,
    get_widget_for_org,
    get_workspace_for_org,
)
from app.database import get_db
from app.engines.ai_engine import AiAnalyticsEngine
from app.engines.data_engine import DataProfiler
from app.engines.formula_engine import validate_and_compile_formula
from app.engines.kpi_engine import IntelligentKpiEngine, SEMANTIC_CLASS_META, _format_kpi_value, _safe_float
from app.engines.query_engine import build_visual_query_sql
from app.engines.warehouse import warehouse
from app.models.identity import User

router = APIRouter(prefix="/ai", tags=["AI Analytics Engine"])


class GenerateDashboardRequest(BaseModel):
    dataset_id: str
    workspace_id: Optional[str] = None
    dashboard_id: Optional[str] = None
    title: Optional[str] = None
    template_mode: str = "executive"
    detail_level: str = "standard"
    calendar_mode: str = "jalali"
    date_range: Optional[dict[str, Any]] = None
    max_pages: int = 7
    theme_id: Optional[str] = None
    selected_kpi_ids: Optional[list[str]] = None
    confirmed_kpis: Optional[list[dict[str, Any]]] = None
    auto_generate_kpis: bool = False
    custom_kpis: Optional[list[dict[str, Any]]] = None
    target_values: Optional[dict[str, float]] = None
    related_dataset_ids: Optional[list[str]] = None
    domain_override: Optional[str] = None


class DomainOverrideRequest(BaseModel):
    domain: Optional[str] = None


class FieldOverrideItem(BaseModel):
    field_name: str
    semantic_class: Optional[str] = None
    aggregation_policy: Optional[str] = None
    display_name: Optional[str] = None
    target_value: Optional[float] = None


class FieldOverridesRequest(BaseModel):
    overrides: list[FieldOverrideItem] = Field(default_factory=list)


class KpiPreviewRequest(BaseModel):
    formula: str
    name: str = "شاخص سفارشی"
    format_type: str = "number"
    unit: str = ""
    target_value: Optional[float] = None
    calendar_mode: str = "jalali"
    related_dataset_ids: Optional[list[str]] = None
    multi_source_agg: Optional[dict[str, Any]] = None


class KpiRegisterRequest(BaseModel):
    kpis: list[dict[str, Any]] = Field(default_factory=list)
    related_dataset_ids: Optional[list[str]] = None


class NlqRequest(BaseModel):
    dataset_id: str
    question: str


class AnomalyForecastRequest(BaseModel):
    values: list[float]
    labels: list[str]
    forecast_periods: int = 3


class TextToWidgetRequest(BaseModel):
    prompt: str
    dashboard_id: Optional[str] = None
    page_id: Optional[str] = None
    dataset_id: Optional[str] = None
    clarifications: dict = {}
    create_widget: bool = True


class ConversationalAssistantRequest(BaseModel):
    prompt: str
    dashboard_id: Optional[str] = None
    page_id: Optional[str] = None
    widget_id: Optional[str] = None
    mode: Optional[str] = None  # auto | create_dashboard | add_widget | edit
    clarifications: dict = {}


@router.get("/semantic-classes")
def list_semantic_classes(user: User = Depends(get_current_user)):
    return [
        {
            "id": k,
            "name_fa": v["fa"],
            "policy": v["policy"],
            "default_aggregation": v["default_agg"],
            "allowed_aggregations": v["allowed"],
            "sum_allowed": v["sum_allowed"],
        }
        for k, v in SEMANTIC_CLASS_META.items()
    ]


@router.get("/recommend/{dataset_id}")
def recommend_dashboard(
    dataset_id: str,
    refresh: bool = Query(False),
    related_dataset_ids: Optional[str] = Query(None),
    calendar_mode: str = Query("jalali"),
    domain_override: Optional[str] = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ds = get_dataset_for_org(db, dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", ds.id, "view")
    rel_list = [x.strip() for x in (related_dataset_ids or "").split(",") if x.strip()]
    res = AiAnalyticsEngine.recommend_widgets_for_dataset(
        db,
        ds,
        user=user,
        include_profile=False,
        related_dataset_ids=rel_list or None,
        calendar_mode=calendar_mode,
        domain_override=domain_override,
        force_refresh=refresh,
    )
    sec_ctx = build_data_security_context(db, user, ds.id)
    res["profile"] = DataProfiler.profile_table(
        ds.warehouse_table,
        sec_ctx=sec_ctx,
    )
    return res


@router.post("/datasets/{dataset_id}/domain-override")
def set_dataset_domain_override(
    dataset_id: str,
    body: DomainOverrideRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.engines.kpi_engine import DOMAIN_DEFINITIONS
    ds = get_dataset_for_org(db, dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", ds.id, "edit", ip_address=get_client_ip(request))
    prof = dict(ds.profile_json or {})
    if body.domain:
        if body.domain not in DOMAIN_DEFINITIONS:
            raise HTTPException(status_code=400, detail=f"Unknown domain: {body.domain}")
        prof["domain_override"] = body.domain
    else:
        prof.pop("domain_override", None)
    ds.profile_json = prof
    db.commit()
    record_audit(
        db, user.organization_id, "dataset_domain_override", "dataset",
        resource_id=ds.id, user_id=user.id, username=user.username,
        ip_address=get_client_ip(request), details={"domain": body.domain},
    )
    return {"dataset_id": ds.id, "domain_override": prof.get("domain_override")}


@router.post("/datasets/{dataset_id}/field-overrides")
def update_field_overrides(
    dataset_id: str,
    body: FieldOverridesRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ds = get_dataset_for_org(db, dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", ds.id, "edit", ip_address=get_client_ip(request))
    res = IntelligentKpiEngine.save_field_overrides(
        db, user, ds, [ov.model_dump(exclude_none=True) for ov in body.overrides]
    )
    record_audit(
        db, user.organization_id, "kpi_field_overrides_updated", "dataset",
        resource_id=ds.id, user_id=user.id, username=user.username, ip_address=get_client_ip(request),
    )
    return res


@router.post("/datasets/{dataset_id}/kpis/preview")
def preview_custom_kpi(
    dataset_id: str,
    body: KpiPreviewRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ds = get_dataset_for_org(db, dataset_id, user.organization_id)
    enforce_permission(db, user, "query", "*", "execute", ip_address=get_client_ip(request))
    enforce_permission(db, user, "dataset", ds.id, "view", ip_address=get_client_ip(request))
    try:
        ds_ids = [ds.id, *(body.related_dataset_ids or [])]
        if body.multi_source_agg:
            psql, pparams, _ = build_visual_query_sql(
                db,
                ds,
                {"multi_source_agg": body.multi_source_agg, "calendar_mode": body.calendar_mode, "limit": 1},
                [], [], [], user=user, enforce_join_rbac=True,
            )
            rows = warehouse.execute_query(psql, pparams, limit=1)["rows"]
            val = _safe_float(rows[0].get("value")) if rows else None
            return {
                "valid": True,
                "name": body.name,
                "formula": body.formula,
                "compiled_sql": body.formula,
                "value": round(val, 4) if val is not None else None,
                "formatted_value": _format_kpi_value(val, body.format_type, body.unit),
                "target_value": body.target_value,
                "warnings": [],
                "output_data_type": "double",
                "base_fields": [],
            }

        comp = validate_and_compile_formula(
            db,
            user,
            dataset_ids=ds_ids,
            formula=body.formula,
            calculation_type="analytical_metric",
            qualify_with_alias=False,
            auto_safe_divide=False,
        )
        if comp.get("is_aov_macro") or comp.get("is_mom_calendar_macro") or comp.get("is_window_macro"):
            vspec: dict[str, Any] = {"formula": body.formula, "calendar_mode": body.calendar_mode, "limit": 1}
            if comp.get("time_field"):
                vspec["time_field"] = comp["time_field"]
            if comp.get("is_aov_macro"):
                vspec["pre_agg_grain"] = {
                    "grain_field": comp["order_field"],
                    "measure_field": comp["amount_field"],
                    "grain_agg": "SUM",
                }
            psql, pparams, _ = build_visual_query_sql(db, ds, vspec, [], [], [], user=user)
            rows = warehouse.execute_query(psql, pparams, limit=1)["rows"]
            val = _safe_float(rows[0].get("value")) if rows else None
        else:
            sc = build_data_security_context(db, user, ds.id)
            sub_sql, sub_p, _ = build_security_projection_and_where(
                ds.warehouse_table, warehouse.describe_table(ds.warehouse_table), sc
            )
            rows = warehouse.execute_query(f'SELECT ({comp["compiled_sql"]}) AS v FROM ({sub_sql}) AS _kv', sub_p, limit=1)["rows"]
            val = _safe_float(rows[0]["v"]) if rows else None

        return {
            "valid": True,
            "name": body.name,
            "formula": body.formula,
            "compiled_sql": comp["compiled_sql"],
            "value": round(val, 4) if val is not None else None,
            "formatted_value": _format_kpi_value(val, body.format_type, body.unit),
            "target_value": body.target_value,
            "warnings": comp.get("warnings", []),
            "output_data_type": comp["output_data_type"],
            "base_fields": [f["field_name"] for f in comp.get("referenced_fields", [])],
        }
    except PermissionError as pe:
        raise HTTPException(status_code=403, detail=str(pe))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/datasets/{dataset_id}/kpis/register")
def register_kpis_to_semantic_layer(
    dataset_id: str,
    body: KpiRegisterRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ds = get_dataset_for_org(db, dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", ds.id, "edit", ip_address=get_client_ip(request))
    try:
        reg = IntelligentKpiEngine.register_kpis_to_semantic_layer(
            db, user, ds, body.kpis, related_dataset_ids=body.related_dataset_ids
        )
        record_audit(
            db, user.organization_id, "kpi_registered_semantic_layer", "dataset",
            resource_id=ds.id, user_id=user.id, username=user.username, ip_address=get_client_ip(request),
        )
        return {"registered": reg, "count": len(reg)}
    except PermissionError as pe:
        raise HTTPException(status_code=403, detail=str(pe))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/generate-dashboard")
def auto_generate_dashboard(
    body: GenerateDashboardRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if body.dashboard_id:
        existing_dash = get_dashboard_for_org(db, body.dashboard_id, user.organization_id)
        enforce_permission(db, user, "dashboard", existing_dash.id, "edit", ip_address=get_client_ip(request))
    else:
        enforce_permission(db, user, "dashboard", "*", "add", ip_address=get_client_ip(request))
    ds = get_dataset_for_org(db, body.dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", ds.id, "view", ip_address=get_client_ip(request))
    if body.workspace_id:
        ws = get_workspace_for_org(db, body.workspace_id, user.organization_id)
    else:
        ws = get_default_workspace_for_org(db, user.organization_id)
        if not ws:
            raise HTTPException(status_code=404, detail="Workspace not found")
    try:
        dash, meta = IntelligentKpiEngine.generate_and_persist_dashboard(
            db,
            user,
            ws.id,
            ds,
            title=body.title,
            template_mode=body.template_mode,
            detail_level=body.detail_level,
            calendar_mode=body.calendar_mode,
            date_range=body.date_range,
            max_pages=max(1, min(int(body.max_pages or 7), 10)),
            theme_id=body.theme_id,
            selected_kpi_ids=body.selected_kpi_ids,
            confirmed_kpis=body.confirmed_kpis,
            auto_generate_kpis=body.auto_generate_kpis,
            custom_kpis=body.custom_kpis,
            target_values=body.target_values,
            related_dataset_ids=body.related_dataset_ids,
            domain_override=body.domain_override,
            dashboard_id=body.dashboard_id,
        )
    except PermissionError as pe:
        raise HTTPException(status_code=403, detail=str(pe))
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    record_audit(
        db, user.organization_id,
        "ai_dashboard_kpis_updated" if body.dashboard_id else "ai_auto_dashboard",
        "dashboard", resource_id=dash.id,
        user_id=user.id, username=user.username, ip_address=get_client_ip(request),
    )
    return {
        "dashboard_id": dash.id,
        "title": dash.title,
        **meta,
    }


@router.post("/nlq")
def natural_language_query(
    body: NlqRequest, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    enforce_permission(db, user, "query", "*", "execute", ip_address=get_client_ip(request))
    ds = get_dataset_for_org(db, body.dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", ds.id, "view", ip_address=get_client_ip(request))
    return AiAnalyticsEngine.natural_language_query(db, user, body.question, ds)


@router.post("/insights")
def anomalies_and_forecast(body: AnomalyForecastRequest, user: User = Depends(get_current_user)):
    return AiAnalyticsEngine.detect_anomalies_and_forecast(body.values, body.labels, body.forecast_periods)


@router.post("/text-to-widget")
def text_to_widget(
    body: TextToWidgetRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if body.dataset_id:
        ds = get_dataset_for_org(db, body.dataset_id, user.organization_id)
        enforce_permission(db, user, "dataset", ds.id, "view", ip_address=get_client_ip(request))
    if body.dashboard_id:
        dash = get_dashboard_for_org(db, body.dashboard_id, user.organization_id)
        enforce_permission(db, user, "dashboard", dash.id, "add_widget", ip_address=get_client_ip(request))
    if body.page_id:
        _, pdash = get_page_for_org(db, body.page_id, user.organization_id)
        if body.dashboard_id and pdash.id != body.dashboard_id:
            raise HTTPException(status_code=400, detail="Page does not belong to target dashboard")
    return AiAnalyticsEngine.plan_or_create_widget_from_text(
        db=db,
        user=user,
        prompt=body.prompt,
        dashboard_id=body.dashboard_id,
        page_id=body.page_id,
        dataset_id=body.dataset_id,
        clarifications=body.clarifications,
        create_widget=body.create_widget,
    )


@router.post("/assistant")
def conversational_assistant(
    body: ConversationalAssistantRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if body.dashboard_id:
        get_dashboard_for_org(db, body.dashboard_id, user.organization_id)
    if body.page_id:
        get_page_for_org(db, body.page_id, user.organization_id)
    if body.widget_id:
        get_widget_for_org(db, body.widget_id, user.organization_id)
    return AiAnalyticsEngine.handle_conversational_request(
        db=db,
        user=user,
        prompt=body.prompt,
        dashboard_id=body.dashboard_id,
        page_id=body.page_id,
        widget_id=body.widget_id,
        mode=body.mode,
        clarifications=body.clarifications,
    )
