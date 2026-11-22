from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.api.deps import enforce_permission, get_client_ip, get_current_user
from app.core.audit import record_audit
from app.core.sql_guard import validate_readonly_sql_ast
from app.core.tenant import (
    get_data_source_for_org,
    get_dataset_for_org,
    get_pipeline_for_org,
)
from app.database import get_db
from app.engines.etl_engine import run_etl_pipeline
from app.engines.scheduler import get_scheduler_status, sync_scheduler_jobs
from app.engines.warehouse import warehouse
from app.models.data import DataSource, Dataset
from app.models.etl import (
    EtlCheckpoint,
    EtlError,
    EtlJobRun,
    EtlPipeline,
    EtlPipelineStep,
    EtlSchedule,
)
from app.models.identity import User
from app.models.performance import MaterializedViewMetadata

router = APIRouter(prefix="/etl", tags=["Automated ETL & Data Warehouse"])


class PipelineCreate(BaseModel):
    name: str
    description: str = ""
    data_source_id: str
    target_dataset_id: str
    extract_mode: str = "incremental"  # full | incremental | cdc_hash
    incremental_column: Optional[str] = None
    primary_key_column: Optional[str] = None
    source_query: Optional[str] = None
    steps: list[dict[str, Any]] = []
    retry_policy_json: dict[str, Any] = {"max_retries": 3, "initial_backoff_sec": 1.0, "backoff_multiplier": 2.0}
    priority: int = 5


class ScheduleUpdate(BaseModel):
    schedule_type: str = "interval"  # interval | daily | weekly | monthly | cron
    interval_minutes: Optional[int] = 15
    cron_expression: Optional[str] = None
    timezone: str = "UTC"
    allowed_window_json: dict[str, Any] = {}
    depends_on_pipeline_id: Optional[str] = None
    is_enabled: bool = True


@router.get("/monitor")
def etl_monitoring_dashboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "dataset", "*", "view")
    sources = db.query(DataSource).filter(DataSource.organization_id == user.organization_id).all()
    datasets = db.query(Dataset).filter(Dataset.organization_id == user.organization_id).all()
    pipelines = db.query(EtlPipeline).filter(EtlPipeline.organization_id == user.organization_id).all()

    org_pipeline_ids = [p.id for p in pipelines]
    org_dataset_ids = [d.id for d in datasets]

    if org_pipeline_ids:
        runs = (
            db.query(EtlJobRun)
            .filter(EtlJobRun.pipeline_id.in_(org_pipeline_ids))
            .order_by(EtlJobRun.started_at.desc())
            .limit(30)
            .all()
        )
        errors = (
            db.query(EtlError)
            .filter(EtlError.pipeline_id.in_(org_pipeline_ids))
            .order_by(EtlError.created_at.desc())
            .limit(20)
            .all()
        )
        succeeded = (
            db.query(EtlJobRun)
            .filter(EtlJobRun.pipeline_id.in_(org_pipeline_ids), EtlJobRun.status == "succeeded")
            .count()
        )
        failed = (
            db.query(EtlJobRun)
            .filter(EtlJobRun.pipeline_id.in_(org_pipeline_ids), EtlJobRun.status == "failed")
            .count()
        )
    else:
        runs, errors, succeeded, failed = [], [], 0, 0

    mvs = (
        db.query(MaterializedViewMetadata)
        .filter(MaterializedViewMetadata.dataset_id.in_(org_dataset_ids))
        .all()
        if org_dataset_ids
        else []
    )
    running = (
        db.query(EtlPipeline)
        .filter(EtlPipeline.organization_id == user.organization_id, EtlPipeline.is_running == True)
        .count()
    )

    pl_list = []
    for pl in pipelines:
        sched = db.query(EtlSchedule).filter(EtlSchedule.pipeline_id == pl.id).first()
        ckpt = db.query(EtlCheckpoint).filter(EtlCheckpoint.pipeline_id == pl.id).first()
        latest_run = (
            db.query(EtlJobRun).filter(EtlJobRun.pipeline_id == pl.id).order_by(EtlJobRun.started_at.desc()).first()
        )
        pl_list.append({
            "id": pl.id,
            "name": pl.name,
            "description": pl.description,
            "data_source_id": pl.data_source_id,
            "target_dataset_id": pl.target_dataset_id,
            "extract_mode": pl.extract_mode,
            "incremental_column": pl.incremental_column,
            "primary_key_column": pl.primary_key_column,
            "source_query": pl.source_query,
            "is_active": pl.is_active,
            "is_running": pl.is_running,
            "retry_policy": pl.retry_policy_json,
            "schedule": {
                "schedule_type": sched.schedule_type,
                "interval_minutes": sched.interval_minutes,
                "cron_expression": sched.cron_expression,
                "is_enabled": sched.is_enabled,
                "next_run_at": sched.next_run_at,
            } if sched else None,
            "checkpoint": {
                "last_cursor_value": ckpt.last_cursor_value,
                "last_success_at": ckpt.last_success_at,
            } if ckpt else None,
            "last_run": {
                "status": latest_run.status,
                "rows_extracted": latest_run.rows_extracted,
                "rows_inserted": latest_run.rows_inserted,
                "rows_updated": latest_run.rows_updated,
                "rows_unchanged": latest_run.rows_unchanged,
                "duration_ms": latest_run.duration_ms,
                "finished_at": latest_run.finished_at,
            } if latest_run else None,
        })

    return {
        "kpis": {
            "data_sources_count": len(sources),
            "datasets_count": len(datasets),
            "pipelines_count": len(pipelines),
            "jobs_succeeded": succeeded,
            "jobs_failed": failed,
            "jobs_running": running,
            "warehouse_storage_bytes": warehouse.storage_size_bytes(),
        },
        "scheduler": get_scheduler_status(),
        "pipelines": pl_list,
        "recent_runs": [
            {
                "id": r.id, "pipeline_id": r.pipeline_id, "status": r.status,
                "rows_extracted": r.rows_extracted, "rows_inserted": r.rows_inserted,
                "rows_updated": r.rows_updated, "rows_unchanged": r.rows_unchanged,
                "duration_ms": r.duration_ms, "started_at": r.started_at,
                "finished_at": r.finished_at, "log_text": r.log_text,
            }
            for r in runs
        ],
        "recent_errors": [
            {
                "id": e.id, "pipeline_id": e.pipeline_id, "error_type": e.error_type,
                "message": e.message, "created_at": e.created_at,
            }
            for e in errors
        ],
        "materialized_views": [
            {
                "id": mv.id, "view_name": mv.view_name, "dataset_id": mv.dataset_id,
                "granularity": mv.granularity, "row_count": mv.row_count, "last_refreshed_at": mv.last_refreshed_at,
            }
            for mv in mvs
        ],
    }


@router.post("/pipelines")
def create_pipeline(
    body: PipelineCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    enforce_permission(db, user, "dataset", "*", "edit")
    src = get_data_source_for_org(db, body.data_source_id, user.organization_id)
    ds = get_dataset_for_org(db, body.target_dataset_id, user.organization_id)
    if body.source_query and body.source_query.strip():
        try:
            validate_readonly_sql_ast(body.source_query, require_table_allowlist=False)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid source_query: {exc}")

    pl = EtlPipeline(
        organization_id=user.organization_id,
        name=body.name,
        description=body.description,
        data_source_id=src.id,
        target_dataset_id=ds.id,
        extract_mode=body.extract_mode,
        incremental_column=body.incremental_column,
        primary_key_column=body.primary_key_column,
        source_query=body.source_query,
        retry_policy_json=body.retry_policy_json,
        priority=body.priority,
    )
    db.add(pl)
    db.flush()
    for idx, st in enumerate(body.steps, start=1):
        db.add(
            EtlPipelineStep(
                pipeline_id=pl.id,
                step_order=idx,
                step_type=st.get("step_type", "clean"),
                config_json=st.get("config_json", {}),
            )
        )
    db.add(EtlSchedule(pipeline_id=pl.id, schedule_type="interval", interval_minutes=30, is_enabled=True))
    db.commit()
    sync_scheduler_jobs()
    return {"id": pl.id, "name": pl.name}


@router.post("/pipelines/{pipeline_id}/run")
def trigger_pipeline(
    pipeline_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    pl = get_pipeline_for_org(db, pipeline_id, user.organization_id)
    enforce_permission(db, user, "dataset", pl.target_dataset_id, "refresh", ip_address=get_client_ip(request))
    try:
        res = run_etl_pipeline(db, pl.id, trigger_type="manual")
        record_audit(
            db, user.organization_id, "etl_pipeline_run", "etl_pipeline",
            resource_id=pl.id, user_id=user.id, username=user.username,
            ip_address=get_client_ip(request), details={"status": res.get("status")}
        )
        return res
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.put("/pipelines/{pipeline_id}/schedule")
def update_pipeline_schedule(
    pipeline_id: str, body: ScheduleUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    pl = get_pipeline_for_org(db, pipeline_id, user.organization_id)
    enforce_permission(db, user, "dataset", pl.target_dataset_id, "edit")
    if body.depends_on_pipeline_id:
        get_pipeline_for_org(db, body.depends_on_pipeline_id, user.organization_id)
    sched = db.query(EtlSchedule).filter(EtlSchedule.pipeline_id == pl.id).first()
    if not sched:
        sched = EtlSchedule(pipeline_id=pl.id)
        db.add(sched)
    sched.schedule_type = body.schedule_type
    sched.interval_minutes = body.interval_minutes
    sched.cron_expression = body.cron_expression
    sched.timezone = body.timezone
    sched.allowed_window_json = body.allowed_window_json
    sched.depends_on_pipeline_id = body.depends_on_pipeline_id
    sched.is_enabled = body.is_enabled
    db.commit()
    sync_scheduler_jobs()
    return {"status": "schedule_updated"}
