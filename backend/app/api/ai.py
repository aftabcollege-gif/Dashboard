from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.api.deps import enforce_permission, get_client_ip, get_current_user
from app.core.audit import record_audit
from app.core.rls_cls import build_data_security_context
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
from app.models.identity import User

router = APIRouter(prefix="/ai", tags=["AI Analytics Engine"])


class GenerateDashboardRequest(BaseModel):
    dataset_id: str
    workspace_id: Optional[str] = None
    title: Optional[str] = None


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


@router.get("/recommend/{dataset_id}")
def recommend_dashboard(dataset_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ds = get_dataset_for_org(db, dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", ds.id, "view")
    res = AiAnalyticsEngine.recommend_widgets_for_dataset(db, ds, user=user, include_profile=False)
    sec_ctx = build_data_security_context(db, user, ds.id)
    res["profile"] = DataProfiler.profile_table(
        ds.warehouse_table,
        sec_ctx=sec_ctx,
    )
    return res


@router.post("/generate-dashboard")
def auto_generate_dashboard(
    body: GenerateDashboardRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(db, user, "dashboard", "*", "add", ip_address=get_client_ip(request))
    ds = get_dataset_for_org(db, body.dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", ds.id, "view", ip_address=get_client_ip(request))
    if body.workspace_id:
        ws = get_workspace_for_org(db, body.workspace_id, user.organization_id)
    else:
        ws = get_default_workspace_for_org(db, user.organization_id)
        if not ws:
            raise HTTPException(status_code=404, detail="Workspace not found")
    dash = AiAnalyticsEngine.generate_dashboard_from_dataset(db, user, ws.id, ds, title=body.title)
    record_audit(
        db, user.organization_id, "ai_auto_dashboard", "dashboard", resource_id=dash.id,
        user_id=user.id, username=user.username, ip_address=get_client_ip(request)
    )
    return {"dashboard_id": dash.id, "title": dash.title}


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
