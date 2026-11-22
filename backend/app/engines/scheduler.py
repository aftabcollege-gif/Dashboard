from datetime import datetime, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from app.database import db_session_scope
from app.models.etl import EtlPipeline, EtlSchedule

_scheduler: BackgroundScheduler | None = None


def _execute_scheduled_pipeline(pipeline_id: str) -> None:
    from app.engines.etl_engine import run_etl_pipeline

    with db_session_scope() as db:
        sched = db.query(EtlSchedule).filter(EtlSchedule.pipeline_id == pipeline_id).first()
        if not sched or not sched.is_enabled:
            return
        # Dependency check: verify upstream pipeline completed if configured
        if sched.depends_on_pipeline_id:
            dep = db.query(EtlPipeline).filter(EtlPipeline.id == sched.depends_on_pipeline_id).first()
            if dep and dep.is_running:
                return
        run_etl_pipeline(db, pipeline_id, trigger_type="scheduled", simulate_retry_sync=False)


def sync_scheduler_jobs() -> None:
    if _scheduler is None:
        return
    with db_session_scope() as db:
        schedules = db.query(EtlSchedule).filter(EtlSchedule.is_enabled == True).all()
        active_ids = set()
        for s in schedules:
            job_id = f"etl_{s.pipeline_id}"
            active_ids.add(job_id)
            trigger = None
            if s.schedule_type == "cron" and s.cron_expression:
                try:
                    trigger = CronTrigger.from_crontab(s.cron_expression, timezone=s.timezone or "UTC")
                except Exception:
                    trigger = IntervalTrigger(minutes=15)
            elif s.schedule_type == "daily":
                trigger = IntervalTrigger(hours=24)
            elif s.schedule_type == "weekly":
                trigger = IntervalTrigger(days=7)
            elif s.schedule_type == "monthly":
                trigger = IntervalTrigger(days=30)
            else:
                mins = max(1, int(s.interval_minutes or 15))
                trigger = IntervalTrigger(minutes=mins)

            existing = _scheduler.get_job(job_id)
            if existing:
                _scheduler.reschedule_job(job_id, trigger=trigger)
            else:
                _scheduler.add_job(
                    _execute_scheduled_pipeline,
                    trigger=trigger,
                    id=job_id,
                    args=[s.pipeline_id],
                    replace_existing=True,
                    max_instances=1,
                    coalesce=True,
                )
            job = _scheduler.get_job(job_id)
            if job and job.next_run_time:
                s.next_run_at = job.next_run_time
        # Remove obsolete jobs
        for job in _scheduler.get_jobs():
            if job.id.startswith("etl_") and job.id not in active_ids:
                _scheduler.remove_job(job.id)
        db.commit()


def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.start()
    try:
        sync_scheduler_jobs()
    except Exception:
        pass


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:
            pass
        _scheduler = None


def get_scheduler_status() -> dict:
    if _scheduler is None:
        return {"running": False, "jobs": []}
    return {
        "running": _scheduler.running,
        "jobs": [
            {
                "id": j.id,
                "next_run_time": j.next_run_time.isoformat() if j.next_run_time else None,
            }
            for j in _scheduler.get_jobs()
        ],
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
