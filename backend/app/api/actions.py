from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import enforce_permission, get_client_ip, get_current_user
from app.core.audit import record_audit
from app.database import get_db
from app.models.identity import User
from app.models.intelligence import ActionItem

router = APIRouter(prefix="/actions", tags=["Unified Action Management"])


class ActionCreateBody(BaseModel):
    domain: str = "process"  # process | security | kpi
    source_ref: str = ""
    title: str
    description: str = ""
    priority: str = "medium"
    assignee_name: str = ""
    due_date: Optional[str] = None
    evidence_before_json: dict[str, Any] = Field(default_factory=dict)


class ActionUpdateBody(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None
    assignee_name: Optional[str] = None
    evidence_after_json: Optional[dict[str, Any]] = None
    impact_summary: Optional[str] = None


@router.get("")
def list_actions(
    domain: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(db, user, "dashboard", "*", "view")
    q = db.query(ActionItem).filter(ActionItem.organization_id == user.organization_id)
    if domain:
        q = q.filter(ActionItem.domain == domain)
    if status:
        q = q.filter(ActionItem.status == status)
    rows = q.order_by(ActionItem.created_at.desc()).limit(200).all()
    return [
        {
            "id": r.id,
            "domain": r.domain,
            "source_ref": r.source_ref,
            "title": r.title,
            "description": r.description,
            "priority": r.priority,
            "status": r.status,
            "assignee_name": r.assignee_name,
            "due_date": r.due_date,
            "evidence_before_json": r.evidence_before_json,
            "evidence_after_json": r.evidence_after_json,
            "impact_summary": r.impact_summary,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.post("")
def create_action(
    body: ActionCreateBody,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(db, user, "dashboard", "*", "edit", ip_address=get_client_ip(request))
    item = ActionItem(
        organization_id=user.organization_id,
        domain=body.domain if body.domain in ("process", "security", "kpi") else "process",
        source_ref=body.source_ref,
        title=body.title.strip(),
        description=body.description,
        priority=body.priority if body.priority in ("critical", "high", "medium", "low") else "medium",
        status="open",
        assignee_name=body.assignee_name,
        due_date=body.due_date,
        evidence_before_json=body.evidence_before_json,
        created_by=user.id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    record_audit(
        db, user.organization_id, "action_item_created", "action_item",
        resource_id=item.id, user_id=user.id, username=user.username, ip_address=get_client_ip(request),
    )
    return {"id": item.id, "domain": item.domain, "title": item.title, "status": item.status}


@router.patch("/{action_id}")
def update_action(
    action_id: str,
    body: ActionUpdateBody,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(db, user, "dashboard", "*", "edit", ip_address=get_client_ip(request))
    item = (
        db.query(ActionItem)
        .filter(ActionItem.id == action_id, ActionItem.organization_id == user.organization_id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Action item not found")
    if body.status and body.status in ("open", "in_progress", "completed", "verified", "cancelled"):
        item.status = body.status
    if body.priority and body.priority in ("critical", "high", "medium", "low"):
        item.priority = body.priority
    if body.assignee_name is not None:
        item.assignee_name = body.assignee_name
    if body.evidence_after_json is not None:
        item.evidence_after_json = body.evidence_after_json
    if body.impact_summary is not None:
        item.impact_summary = body.impact_summary
    db.commit()
    db.refresh(item)
    return {"id": item.id, "status": item.status, "impact_summary": item.impact_summary}
