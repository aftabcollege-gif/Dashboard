from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.api.deps import enforce_permission, get_current_user
from app.database import get_db
from app.models.identity import User
from app.models.performance import BackgroundJob

router = APIRouter(prefix="/jobs", tags=["Async Background Jobs"])


def _serialize_job(j: BackgroundJob) -> dict:
    return {
        "id": j.id,
        "job_type": j.job_type,
        "title": j.title,
        "status": j.status,
        "progress_pct": round(float(j.progress_pct or 0.0), 2),
        "processed_items": int(j.processed_items or 0),
        "total_items": int(j.total_items or 0),
        "stage_label": j.stage_label,
        "result": j.result_json or {},
        "error_message": j.error_message,
        "started_at": j.started_at,
        "finished_at": j.finished_at,
        "created_at": j.created_at,
    }


@router.get("")
def list_jobs(limit: int = 30, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "dataset", "*", "view")
    rows = (
        db.query(BackgroundJob)
        .filter(BackgroundJob.organization_id == user.organization_id)
        .order_by(BackgroundJob.created_at.desc())
        .limit(max(1, min(int(limit), 100)))
        .all()
    )
    return [_serialize_job(j) for j in rows]


@router.get("/{job_id}")
def get_job(job_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "dataset", "*", "view")
    job = (
        db.query(BackgroundJob)
        .filter(BackgroundJob.id == job_id, BackgroundJob.organization_id == user.organization_id)
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _serialize_job(job)
