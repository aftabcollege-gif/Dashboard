import threading
import traceback
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from sqlalchemy.orm import Session
from app.database import db_session_scope
from app.models.performance import BackgroundJob


def create_job(
    db: Session,
    *,
    organization_id: str,
    job_type: str,
    title: str,
    total_items: int = 0,
    created_by: Optional[str] = None,
) -> BackgroundJob:
    job = BackgroundJob(
        organization_id=organization_id,
        job_type=job_type,
        title=title,
        status="queued",
        progress_pct=0.0,
        processed_items=0,
        total_items=max(0, int(total_items)),
        stage_label="queued",
        result_json={},
        created_by=created_by,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def update_job_progress(
    job_id: str,
    *,
    processed_items: Optional[int] = None,
    total_items: Optional[int] = None,
    progress_pct: Optional[float] = None,
    stage_label: Optional[str] = None,
    status: Optional[str] = None,
) -> None:
    try:
        with db_session_scope() as db:
            job = db.query(BackgroundJob).filter(BackgroundJob.id == job_id).first()
            if not job:
                return
            if status is not None:
                job.status = status
            if processed_items is not None:
                job.processed_items = max(0, int(processed_items))
            if total_items is not None:
                job.total_items = max(0, int(total_items))
            if progress_pct is not None:
                job.progress_pct = round(max(0.0, min(100.0, float(progress_pct))), 2)
            elif job.total_items > 0:
                job.progress_pct = round(min(99.5, (job.processed_items / job.total_items) * 100.0), 2)
            if stage_label is not None:
                job.stage_label = stage_label[:160]
            db.commit()
    except Exception:
        # Progress ticks are best-effort and must never abort the worker job
        pass


def launch_background_job(
    job_id: str,
    worker_fn: Callable[[str, Callable[[int, int, str], None]], dict[str, Any]],
) -> None:
    def _runner() -> None:
        with db_session_scope() as db:
            job = db.query(BackgroundJob).filter(BackgroundJob.id == job_id).first()
            if not job:
                return
            job.status = "running"
            job.started_at = datetime.now(timezone.utc)
            job.stage_label = "processing"
            db.commit()

        def _progress_cb(processed: int, total: int, stage: str) -> None:
            pct = (processed / total * 100.0) if total > 0 else None
            update_job_progress(
                job_id,
                processed_items=processed,
                total_items=total if total > 0 else None,
                progress_pct=min(99.0, pct) if pct is not None else None,
                stage_label=stage,
            )

        try:
            result = worker_fn(job_id, _progress_cb) or {}
            with db_session_scope() as db:
                job = db.query(BackgroundJob).filter(BackgroundJob.id == job_id).first()
                if job:
                    job.status = "completed"
                    job.progress_pct = 100.0
                    if result.get("total_rows") is not None:
                        job.processed_items = int(result["total_rows"])
                        job.total_items = max(int(job.total_items or 0), int(result["total_rows"]))
                    job.stage_label = "completed"
                    job.result_json = result
                    job.finished_at = datetime.now(timezone.utc)
                    db.commit()
        except Exception as exc:
            with db_session_scope() as db:
                job = db.query(BackgroundJob).filter(BackgroundJob.id == job_id).first()
                if job:
                    job.status = "failed"
                    job.stage_label = "failed"
                    job.error_message = f"{exc}"[:2000]
                    job.result_json = {"trace_hint": traceback.format_exc().splitlines()[-1] if traceback.format_exc() else ""}
                    job.finished_at = datetime.now(timezone.utc)
                    db.commit()

    t = threading.Thread(target=_runner, daemon=True, name=f"bg-job-{job_id[:8]}")
    t.start()
