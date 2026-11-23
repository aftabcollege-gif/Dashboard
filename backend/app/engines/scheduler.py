import logging
import os
import socket
import threading
import uuid
from datetime import datetime, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from app.config import settings
from app.core.cache import cache_manager
from app.database import db_session_scope, engine
from app.models.etl import EtlPipeline, EtlSchedule

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None
_scheduler_lock = threading.RLock()
_lease_thread: threading.Thread | None = None
_lease_stop_event = threading.Event()
_is_leader = False
_LEADER_ID = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
_pg_leader_conn = None
_LEADER_LEASE_TTL_SEC = 60
_LEASE_RENEW_INTERVAL_SEC = 15

_REDIS_LEADER_LUA = """
local current = redis.call('GET', KEYS[1])
if (not current) or (current == ARGV[1]) then
    redis.call('SET', KEYS[1], ARGV[1], 'EX', tonumber(ARGV[2]))
    return 1
end
return 0
"""

_REDIS_RELEASE_LEADER_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


def _scheduler_requires_redis() -> bool:
    if os.environ.get("BI_SCHEDULER_REQUIRE_REDIS", "").strip().lower() in ("1", "true", "yes"):
        return True
    if settings.ENVIRONMENT == "production" and not settings.is_desktop_mode() and bool(settings.REDIS_URL):
        return True
    return False


def _acquire_or_renew_leader_lease() -> bool:
    global _pg_leader_conn
    redis_client = getattr(cache_manager, "_redis", None)
    if redis_client is not None:
        try:
            res = redis_client.eval(
                _REDIS_LEADER_LUA, 1, "bi:scheduler:leader", _LEADER_ID, _LEADER_LEASE_TTL_SEC
            )
            return int(res or 0) == 1
        except Exception as exc:
            logger.warning("Scheduler Redis leader lease check failed (fail closed): %s", exc)
            return False

    if _scheduler_requires_redis():
        logger.error("Scheduler requires Redis leader coordination in production, but Redis client is unavailable.")
        return False

    if engine.dialect.name == "postgresql":
        try:
            if _pg_leader_conn is None:
                _pg_leader_conn = engine.raw_connection()
            cur = _pg_leader_conn.cursor()
            cur.execute("SELECT pg_try_advisory_lock(83920144)")
            row = cur.fetchone()
            cur.close()
            if row and bool(row[0]):
                return True
            _pg_leader_conn.close()
            _pg_leader_conn = None
            return False
        except Exception:
            if _pg_leader_conn is not None:
                try:
                    _pg_leader_conn.close()
                except Exception:
                    pass
                _pg_leader_conn = None
            return False

    return True


def _release_leader_lease() -> None:
    global _pg_leader_conn, _is_leader
    _is_leader = False
    redis_client = getattr(cache_manager, "_redis", None)
    if redis_client is not None:
        try:
            redis_client.eval(_REDIS_RELEASE_LEADER_LUA, 1, "bi:scheduler:leader", _LEADER_ID)
        except Exception:
            pass
    if _pg_leader_conn is not None:
        try:
            _pg_leader_conn.close()
        except Exception:
            pass
        _pg_leader_conn = None


def _shutdown_apscheduler_unlocked() -> None:
    global _scheduler, _is_leader
    _is_leader = False
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:
            pass
        _scheduler = None


def _start_apscheduler_unlocked() -> None:
    global _scheduler, _is_leader
    if _scheduler is not None and _scheduler.running:
        _is_leader = True
        return
    try:
        from app.engines.etl_engine import recover_stale_etl_runs

        with db_session_scope() as db:
            recover_stale_etl_runs(db)
    except Exception:
        pass
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.start()
    _is_leader = True
    try:
        sync_scheduler_jobs()
    except Exception:
        pass


def _lease_renewal_loop() -> None:
    while not _lease_stop_event.wait(timeout=_LEASE_RENEW_INTERVAL_SEC):
        if not settings.BI_ENABLE_SCHEDULER:
            continue
        acquired = _acquire_or_renew_leader_lease()
        with _scheduler_lock:
            if acquired:
                if _scheduler is None or not _scheduler.running:
                    _start_apscheduler_unlocked()
            else:
                if _scheduler is not None:
                    logger.warning("Scheduler lost leader lease; stopping active jobs to prevent split-brain.")
                    _shutdown_apscheduler_unlocked()


def _execute_scheduled_pipeline(pipeline_id: str) -> None:
    if not _acquire_or_renew_leader_lease():
        with _scheduler_lock:
            _shutdown_apscheduler_unlocked()
        return
    from app.engines.etl_engine import run_etl_pipeline

    with db_session_scope() as db:
        sched = db.query(EtlSchedule).filter(EtlSchedule.pipeline_id == pipeline_id).first()
        if not sched or not sched.is_enabled:
            return
        if sched.depends_on_pipeline_id:
            dep = db.query(EtlPipeline).filter(EtlPipeline.id == sched.depends_on_pipeline_id).first()
            if dep and dep.is_running:
                return
        run_etl_pipeline(db, pipeline_id, trigger_type="scheduled", simulate_retry_sync=False)


def sync_scheduler_jobs() -> None:
    with _scheduler_lock:
        sched_inst = _scheduler
        if sched_inst is None:
            return
        with db_session_scope() as db:
            schedules = db.query(EtlSchedule).filter(EtlSchedule.is_enabled == True).all()
            active_ids = set()
            for s in schedules:
                job_id = f"etl_{s.pipeline_id}"
                active_ids.add(job_id)
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

                existing = sched_inst.get_job(job_id)
                if existing:
                    sched_inst.reschedule_job(job_id, trigger=trigger)
                else:
                    sched_inst.add_job(
                        _execute_scheduled_pipeline,
                        trigger=trigger,
                        id=job_id,
                        args=[s.pipeline_id],
                        replace_existing=True,
                        max_instances=1,
                        coalesce=True,
                    )
                job = sched_inst.get_job(job_id)
                if job and job.next_run_time:
                    s.next_run_at = job.next_run_time
            for job in sched_inst.get_jobs():
                if job.id.startswith("etl_") and job.id not in active_ids:
                    sched_inst.remove_job(job.id)
            db.commit()


def start_scheduler() -> None:
    global _lease_thread
    if not settings.BI_ENABLE_SCHEDULER:
        return
    with _scheduler_lock:
        if _scheduler is not None and _scheduler.running:
            return
        _lease_stop_event.clear()
        if _acquire_or_renew_leader_lease():
            _start_apscheduler_unlocked()
        if _lease_thread is None or not _lease_thread.is_alive():
            _lease_thread = threading.Thread(
                target=_lease_renewal_loop, name="bi-scheduler-lease-renewal", daemon=True
            )
            _lease_thread.start()


def stop_scheduler() -> None:
    global _lease_thread
    _lease_stop_event.set()
    with _scheduler_lock:
        _shutdown_apscheduler_unlocked()
    if _lease_thread is not None and _lease_thread.is_alive():
        _lease_thread.join(timeout=2.0)
    _lease_thread = None
    _release_leader_lease()


def get_scheduler_status() -> dict:
    with _scheduler_lock:
        if _scheduler is None:
            return {
                "enabled": settings.BI_ENABLE_SCHEDULER,
                "running": False,
                "leader": _is_leader,
                "leader_id": _LEADER_ID,
                "jobs": [],
            }
        return {
            "enabled": settings.BI_ENABLE_SCHEDULER,
            "running": _scheduler.running,
            "leader": _is_leader,
            "leader_id": _LEADER_ID,
            "jobs": [
                {
                    "id": j.id,
                    "next_run_time": j.next_run_time.isoformat() if j.next_run_time else None,
                }
                for j in _scheduler.get_jobs()
            ],
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
