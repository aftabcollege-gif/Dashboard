from typing import Optional
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import enforce_permission, get_current_user
from app.core.tenant import get_dataset_for_org
from app.database import get_db
from app.engines.correlation_engine import correlate_process_and_security
from app.models.identity import User

router = APIRouter(prefix="/correlation", tags=["Cross-Domain Correlation"])


class CorrelationRequest(BaseModel):
    process_dataset_id: str
    correlation_key: str = "username"  # username | hostname | ip
    window_minutes: int = Field(120, ge=1, le=10080)
    process_field_override: Optional[str] = None


@router.post("/analyze")
def run_cross_domain_correlation(
    body: CorrelationRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ds = get_dataset_for_org(db, body.process_dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", ds.id, "view")
    enforce_permission(db, user, "process_intel", "*", "view")
    enforce_permission(db, user, "security_log", "*", "view")
    return correlate_process_and_security(
        db,
        user,
        ds,
        correlation_key=body.correlation_key,
        window_minutes=body.window_minutes,
        process_field_override=body.process_field_override,
    )
