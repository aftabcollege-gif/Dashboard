from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.api.deps import enforce_permission, get_current_user
from app.database import get_db
from app.engines.ai_engine import AiAnalyticsEngine
from app.models.dashboard import Workspace
from app.models.data import Dataset
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
    enforce_permission(db, user, "dataset", dataset_id, "view")
    ds = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not ds:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return AiAnalyticsEngine.recommend_widgets_for_dataset(db, ds)


@router.post("/generate-dashboard")
def auto_generate_dashboard(
    body: GenerateDashboardRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    enforce_permission(db, user, "dashboard", "*", "add")
    ds = db.query(Dataset).filter(Dataset.id == body.dataset_id).first()
    if not ds:
        raise HTTPException(status_code=404, detail="Dataset not found")
    ws_id = body.workspace_id
    if not ws_id:
        ws = db.query(Workspace).filter(Workspace.organization_id == user.organization_id).first()
        ws_id = ws.id
    dash = AiAnalyticsEngine.generate_dashboard_from_dataset(db, user, ws_id, ds, title=body.title)
    return {"dashboard_id": dash.id, "title": dash.title}


@router.post("/nlq")
def natural_language_query(body: NlqRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "query", "*", "execute")
    ds = db.query(Dataset).filter(Dataset.id == body.dataset_id).first()
    if not ds:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return AiAnalyticsEngine.natural_language_query(db, user, body.question, ds)


@router.post("/insights")
def anomalies_and_forecast(body: AnomalyForecastRequest, user: User = Depends(get_current_user)):
    return AiAnalyticsEngine.detect_anomalies_and_forecast(body.values, body.labels, body.forecast_periods)


@router.post("/text-to-widget")
def text_to_widget(
    body: TextToWidgetRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if body.dataset_id:
        enforce_permission(db, user, "dataset", body.dataset_id, "view")
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
