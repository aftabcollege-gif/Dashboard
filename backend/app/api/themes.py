from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.api.deps import get_current_user
from app.database import get_db
from app.models.dashboard import DashboardTheme
from app.models.identity import User

router = APIRouter(prefix="/themes", tags=["Theme Engine & Customization"])


class ThemeCreate(BaseModel):
    name: str
    slug: str
    mode: str = "light"
    tokens_json: dict[str, Any]


@router.get("")
def list_themes(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(DashboardTheme).filter(DashboardTheme.organization_id == user.organization_id).all()
    return [
        {
            "id": t.id, "name": t.name, "slug": t.slug, "is_builtin": t.is_builtin,
            "mode": t.mode, "tokens": t.tokens_json,
        }
        for t in rows
    ]


@router.post("")
def create_custom_theme(body: ThemeCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    existing = (
        db.query(DashboardTheme)
        .filter(DashboardTheme.organization_id == user.organization_id, DashboardTheme.slug == body.slug)
        .first()
    )
    if existing:
        existing.name = body.name
        existing.mode = body.mode
        existing.tokens_json = body.tokens_json
        db.commit()
        return {"id": existing.id, "slug": existing.slug}

    dt = DashboardTheme(
        organization_id=user.organization_id,
        name=body.name,
        slug=body.slug,
        is_builtin=False,
        mode=body.mode,
        tokens_json=body.tokens_json,
        created_by=user.id,
    )
    db.add(dt)
    db.commit()
    return {"id": dt.id, "slug": dt.slug}


@router.get("/{theme_id}/export")
def export_theme(theme_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    t = db.query(DashboardTheme).filter(DashboardTheme.id == theme_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Theme not found")
    return {"name": t.name, "slug": t.slug, "mode": t.mode, "tokens_json": t.tokens_json}
