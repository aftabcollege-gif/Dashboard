import hashlib
import json
import re
import threading
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
import psutil
from sqlalchemy.orm import Session
from app.config import settings
from app.core.cache import cache_manager
from app.core.security import decrypt_secret
from app.core.sql_guard import validate_readonly_sql_ast
from app.engines.connectors import DataConnectorEngine, infer_bi_type, infer_semantic_role
from app.engines.data_engine import DataProfiler
from app.engines.warehouse import sanitize_identifier, warehouse
from app.models.data import DataLineage, DataSource, DataSourceCredential, Dataset, DatasetField
from app.models.etl import (
    EtlCheckpoint,
    EtlError,
    EtlJob,
    EtlJobRun,
    EtlMetric,
    EtlPipeline,
    EtlPipelineStep,
)
from app.models.performance import MaterializedViewMetadata

_PIPELINE_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
_REDIS_UNLOCK_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


def _get_pipeline_lock(pipeline_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        if pipeline_id not in _PIPELINE_LOCKS:
            _PIPELINE_LOCKS[pipeline_id] = threading.Lock()
        return _PIPELINE_LOCKS[pipeline_id]


def _acquire_distributed_etl_lock(pipeline_id: str, ttl_sec: int) -> tuple[bool, Optional[str], Any]:
    redis_client = getattr(cache_manager, "_redis", None)
    if redis_client is None:
        return True, None, None
    token = uuid.uuid4().hex
    try:
        ok = redis_client.set(f"etl_lock:{pipeline_id}", token, nx=True, ex=max(30, ttl_sec))
        return bool(ok), token if ok else None, redis_client
    except Exception:
        # Fall back to DB CAS lock when Redis lock is unreachable
        return True, None, None


def _release_distributed_etl_lock(pipeline_id: str, token: Optional[str], redis_client: Any) -> None:
    if redis_client is not None and token:
        try:
            redis_client.eval(_REDIS_UNLOCK_LUA, 1, f"etl_lock:{pipeline_id}", token)
        except Exception:
            pass


def recover_stale_etl_runs(
    db: Session,
    pipeline_id: Optional[str] = None,
    max_age_minutes: Optional[int] = None,
) -> int:
    """Marks abandoned running ETL pipelines/runs older than timeout as failed and resets is_running."""
    age_mins = settings.ETL_STALE_RUN_TIMEOUT_MINUTES if max_age_minutes is None else int(max_age_minutes)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=max(0, age_mins))

    q = db.query(EtlPipeline).filter(EtlPipeline.is_running == True)
    if pipeline_id:
        q = q.filter(EtlPipeline.id == pipeline_id)
    stuck_pipelines = q.all()
    recovered = 0

    for pl in stuck_pipelines:
        running_runs = (
            db.query(EtlJobRun)
            .filter(EtlJobRun.pipeline_id == pl.id, EtlJobRun.status == "running")
            .order_by(EtlJobRun.started_at.desc())
            .all()
        )
        is_stale = False
        if not running_runs:
            upd = pl.updated_at if pl.updated_at.tzinfo else pl.updated_at.replace(tzinfo=timezone.utc)
            if upd <= cutoff:
                is_stale = True
        else:
            newest = running_runs[0]
            st = newest.started_at if newest.started_at.tzinfo else newest.started_at.replace(tzinfo=timezone.utc)
            if st <= cutoff:
                is_stale = True

        if not is_stale:
            continue

        for r in running_runs:
            r.status = "failed"
            r.finished_at = now
            r.log_text = (r.log_text or "") + "\n[RECOVERY] Stale ETL run aborted after timeout/crash."
            db.query(EtlJob).filter(EtlJob.id == r.job_id, EtlJob.status.in_(("running", "retrying"))).update(
                {"status": "failed"}, synchronize_session=False
            )
            db.add(
                EtlError(
                    pipeline_id=pl.id,
                    job_run_id=r.id,
                    error_type="StaleRunRecovered",
                    message="Recovered stale crashed or timed-out ETL execution",
                    traceback="",
                )
            )
        pl.is_running = False
        recovered += 1

    if recovered > 0:
        db.commit()
    return recovered


def _row_hash(row: dict[str, Any]) -> str:
    raw = json.dumps(row, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def refresh_materialized_views_for_dataset(db: Session, dataset: Dataset) -> None:
    mvs = db.query(MaterializedViewMetadata).filter(MaterializedViewMetadata.dataset_id == dataset.id).all()
    for mv in mvs:
        try:
            validate_readonly_sql_ast(mv.definition_sql, require_table_allowlist=False)
            cnt = warehouse.create_materialized_summary(mv.view_name, mv.definition_sql)
            mv.row_count = cnt
            mv.last_refreshed_at = datetime.now(timezone.utc)
        except Exception:
            pass


def run_etl_pipeline(
    db: Session,
    pipeline_id: str,
    trigger_type: str = "manual",
    simulate_retry_sync: bool = True,
) -> dict[str, Any]:
    """
    Executes an ETL Pipeline with:
    - Distributed Redis lock + atomic DB CAS claim + per-process mutex preventing duplicate concurrent runs
    - Automatic stale-run recovery after crashes/timeouts
    - Organization ownership enforcement on source & target dataset
    - AST-validated base query & parameterized incremental cursor (never string-interpolated)
    - Exponential backoff retry policy & bounded extraction timeout
    - Atomic warehouse load (failed extraction NEVER overwrites existing valid dataset)
    """
    lock = _get_pipeline_lock(pipeline_id)
    if not lock.acquire(blocking=False):
        return {
            "status": "skipped_duplicate",
            "message": "Pipeline is already running; duplicate concurrent execution prevented",
            "pipeline_id": pipeline_id,
        }

    dist_token: Optional[str] = None
    dist_redis: Any = None
    claimed_db = False

    try:
        dist_ok, dist_token, dist_redis = _acquire_distributed_etl_lock(
            pipeline_id, max(settings.ETL_RUN_TIMEOUT_SECONDS * 2, 120)
        )
        if not dist_ok:
            return {
                "status": "skipped_duplicate",
                "message": "Pipeline lock held by another worker; duplicate execution prevented",
                "pipeline_id": pipeline_id,
            }

        pipeline = db.query(EtlPipeline).filter(EtlPipeline.id == pipeline_id).first()
        if not pipeline:
            raise ValueError(f"Pipeline not found: {pipeline_id}")

        if pipeline.is_running:
            recover_stale_etl_runs(db, pipeline_id=pipeline_id)

        claimed = (
            db.query(EtlPipeline)
            .filter(EtlPipeline.id == pipeline_id, EtlPipeline.is_running == False)
            .update({"is_running": True}, synchronize_session=False)
        )
        db.commit()
        if claimed == 0:
            return {
                "status": "skipped_duplicate",
                "message": "Pipeline marked running; duplicate prevented",
                "pipeline_id": pipeline_id,
            }
        claimed_db = True
        db.refresh(pipeline)

        job = EtlJob(pipeline_id=pipeline.id, trigger_type=trigger_type, status="running", attempt_count=1)
        db.add(job)
        db.commit()

        retry_policy = pipeline.retry_policy_json or {}
        max_retries = min(int(retry_policy.get("max_retries", 2)), 5)
        backoff = min(float(retry_policy.get("initial_backoff_sec", 0.5)), 5.0)
        multiplier = min(float(retry_policy.get("backoff_multiplier", 2.0)), 4.0)

        last_exc: Optional[Exception] = None
        run_record: Optional[EtlJobRun] = None
        logs: list[str] = []

        for attempt in range(1, max_retries + 2):
            job.attempt_count = attempt
            started_at = datetime.now(timezone.utc)
            t0 = time.perf_counter()
            run_record = EtlJobRun(
                job_id=job.id,
                pipeline_id=pipeline.id,
                status="running",
                started_at=started_at,
            )
            db.add(run_record)
            db.commit()

            try:
                logs.append(f"[{started_at.isoformat()}] Attempt {attempt}: Starting ETL '{pipeline.name}' ({pipeline.extract_mode})")

                source = (
                    db.query(DataSource)
                    .filter(
                        DataSource.id == pipeline.data_source_id,
                        DataSource.organization_id == pipeline.organization_id,
                    )
                    .first()
                )
                dataset = (
                    db.query(Dataset)
                    .filter(
                        Dataset.id == pipeline.target_dataset_id,
                        Dataset.organization_id == pipeline.organization_id,
                    )
                    .first()
                )
                if not source or not dataset:
                    raise ValueError("Pipeline requires valid organization-owned data_source_id and target_dataset_id")

                cred_row = (
                    db.query(DataSourceCredential)
                    .filter(DataSourceCredential.data_source_id == source.id)
                    .first()
                )
                creds = json.loads(decrypt_secret(cred_row.encrypted_payload)) if cred_row else {}

                checkpoint = (
                    db.query(EtlCheckpoint).filter(EtlCheckpoint.pipeline_id == pipeline.id).first()
                )
                if not checkpoint:
                    checkpoint = EtlCheckpoint(pipeline_id=pipeline.id, row_hashes_json={})
                    db.add(checkpoint)
                    db.commit()

                default_tbl = sanitize_identifier(str(source.config_json.get("default_table", "erp_orders")))
                base_query = pipeline.source_query or f'SELECT * FROM "{default_tbl}"'
                clean_base = base_query.strip().rstrip(";")
                src_st = source.source_type.lower()
                src_dialect = (
                    "sqlite"
                    if src_st == "sqlite"
                    else "postgres"
                    if src_st == "postgresql"
                    else "mysql"
                    if src_st in ("mysql", "mariadb")
                    else "duckdb"
                )
                validate_readonly_sql_ast(clean_base, dialect=src_dialect, require_table_allowlist=False)

                # Parameterized incremental filter (never interpolate cursor literal into SQL)
                inc_col = pipeline.incremental_column
                exec_query = clean_base
                exec_params: Optional[list[Any]] = None
                if (
                    pipeline.extract_mode == "incremental"
                    and inc_col
                    and checkpoint.last_cursor_value
                    and warehouse.table_exists(dataset.warehouse_table)
                ):
                    clean_inc = sanitize_identifier(inc_col)
                    cursor_raw = str(checkpoint.last_cursor_value).replace("\x00", "")
                    if re.fullmatch(r"-?\d+", cursor_raw):
                        cursor_param: Any = int(cursor_raw)
                    elif re.fullmatch(r"-?\d+\.\d+", cursor_raw):
                        cursor_param = float(cursor_raw)
                    else:
                        cursor_param = cursor_raw
                    exec_query = f'SELECT * FROM ({clean_base}) AS _src WHERE "{clean_inc}" > ?'
                    exec_params = [cursor_param]
                    logs.append(f"Incremental filter active (parameterized): {clean_inc} > ?")

                raw_df = DataConnectorEngine.extract_dataframe(
                    source.source_type,
                    source.config_json,
                    creds,
                    query=exec_query,
                    read_only=True,
                    query_params=exec_params,
                    timeout_sec=settings.ETL_RUN_TIMEOUT_SECONDS,
                )
                source.health_status = "healthy"
                source.last_error = None
                source.last_tested_at = datetime.now(timezone.utc)

                rows_extracted = len(raw_df)
                logs.append(f"Extracted {rows_extracted} rows from source '{source.name}'")

                steps = (
                    db.query(EtlPipelineStep)
                    .filter(EtlPipelineStep.pipeline_id == pipeline.id)
                    .order_by(EtlPipelineStep.step_order.asc())
                    .all()
                )
                step_dicts = [
                    {"step_type": s.step_type, "transform_type": s.step_type, "config_json": s.config_json, "is_enabled": s.is_enabled}
                    for s in steps
                ]
                transformed_df = DataProfiler.apply_transformations(raw_df, step_dicts)
                transformed_df.columns = [sanitize_identifier(str(c)) for c in transformed_df.columns]

                pk_col = sanitize_identifier(pipeline.primary_key_column) if pipeline.primary_key_column else None
                prev_hashes: dict[str, str] = dict(checkpoint.row_hashes_json or {})
                rows_unchanged = 0
                rows_inserted = 0
                rows_updated = 0

                if len(transformed_df) == 0 and warehouse.table_exists(dataset.warehouse_table):
                    logs.append("No new/changed rows detected since last checkpoint.")
                else:
                    if pipeline.extract_mode == "cdc_hash" and pk_col and pk_col in transformed_df.columns and warehouse.table_exists(dataset.warehouse_table):
                        changed_indices = []
                        new_hashes = dict(prev_hashes)
                        records = transformed_df.to_dict(orient="records")
                        for idx, rec in enumerate(records):
                            pk_val = str(rec[pk_col])
                            h = _row_hash(rec)
                            if prev_hashes.get(pk_val) == h:
                                rows_unchanged += 1
                            else:
                                changed_indices.append(idx)
                                new_hashes[pk_val] = h
                        changed_df = transformed_df.iloc[changed_indices].copy()
                        if len(changed_df) > 0:
                            load_stats = warehouse.load_dataframe(
                                dataset.warehouse_table, changed_df, mode="merge", pk_column=pk_col
                            )
                            rows_inserted = load_stats["inserted"]
                            rows_updated = load_stats["updated"]
                        checkpoint.row_hashes_json = new_hashes
                    elif pipeline.extract_mode == "incremental" and pk_col and pk_col in transformed_df.columns and warehouse.table_exists(dataset.warehouse_table):
                        load_stats = warehouse.load_dataframe(
                            dataset.warehouse_table, transformed_df, mode="merge", pk_column=pk_col
                        )
                        rows_inserted = load_stats["inserted"]
                        rows_updated = load_stats["updated"]
                    elif pipeline.extract_mode == "incremental" and warehouse.table_exists(dataset.warehouse_table):
                        load_stats = warehouse.load_dataframe(dataset.warehouse_table, transformed_df, mode="append")
                        rows_inserted = load_stats["inserted"]
                    else:
                        load_stats = warehouse.load_dataframe(dataset.warehouse_table, transformed_df, mode="replace")
                        rows_inserted = load_stats["inserted"]
                        if pk_col and pk_col in transformed_df.columns:
                            checkpoint.row_hashes_json = {
                                str(rec[pk_col]): _row_hash(rec)
                                for rec in transformed_df.to_dict(orient="records")
                            }

                if inc_col:
                    s_inc = sanitize_identifier(inc_col)
                    if s_inc in transformed_df.columns and len(transformed_df) > 0:
                        max_cursor = str(transformed_df[s_inc].max())
                        checkpoint.last_cursor_value = max_cursor
                        logs.append(f"Updated checkpoint cursor {s_inc} = {max_cursor}")
                checkpoint.last_success_at = datetime.now(timezone.utc)

                total_res = warehouse.execute_query(f'SELECT COUNT(*) AS cnt FROM "{dataset.warehouse_table}"')
                total_rows = int(total_res["rows"][0]["cnt"])
                dataset.row_count = total_rows
                dataset.data_version += 1
                dataset.last_refreshed_at = datetime.now(timezone.utc)
                dataset.freshness_status = "fresh"

                existing_fields = {
                    f.field_name
                    for f in db.query(DatasetField).filter(DatasetField.dataset_id == dataset.id).all()
                }
                for c in warehouse.describe_table(dataset.warehouse_table):
                    if c["column_name"] not in existing_fields:
                        bt = infer_bi_type(c["column_type"])
                        db.add(
                            DatasetField(
                                dataset_id=dataset.id,
                                field_name=c["column_name"],
                                display_name=c["column_name"].replace("_", " ").title(),
                                data_type=bt,
                                semantic_role=infer_semantic_role(c["column_name"], bt),
                                is_primary_key=(c["column_name"] == pk_col),
                            )
                        )

                existing_lin = (
                    db.query(DataLineage)
                    .filter(
                        DataLineage.source_type == "data_source",
                        DataLineage.source_id == source.id,
                        DataLineage.target_type == "dataset",
                        DataLineage.target_id == dataset.id,
                    )
                    .first()
                )
                if not existing_lin:
                    db.add(
                        DataLineage(
                            source_type="data_source",
                            source_id=source.id,
                            target_type="dataset",
                            target_id=dataset.id,
                            metadata_json={"pipeline_id": pipeline.id},
                        )
                    )

                refresh_materialized_views_for_dataset(db, dataset)
                inv_count = cache_manager.invalidate_dataset(dataset.id)
                logs.append(f"Refreshed dataset v{dataset.data_version} ({total_rows} rows); invalidated {inv_count} cache entries")

                elapsed_ms = round((time.perf_counter() - t0) * 1000, 3)
                run_record.status = "succeeded"
                run_record.rows_extracted = rows_extracted
                run_record.rows_inserted = rows_inserted
                run_record.rows_updated = rows_updated
                run_record.rows_unchanged = rows_unchanged
                run_record.duration_ms = elapsed_ms
                run_record.finished_at = datetime.now(timezone.utc)
                run_record.log_text = "\n".join(logs)

                job.status = "succeeded"

                proc = psutil.Process()
                db.add(
                    EtlMetric(
                        pipeline_id=pipeline.id,
                        job_run_id=run_record.id,
                        cpu_percent=float(psutil.cpu_percent(interval=None)),
                        memory_mb=round(proc.memory_info().rss / (1024 * 1024), 2),
                        storage_bytes=warehouse.storage_size_bytes(),
                        throughput_rows_sec=round((rows_extracted / max(elapsed_ms / 1000.0, 0.001)), 2),
                    )
                )
                db.commit()

                return {
                    "status": "succeeded",
                    "pipeline_id": pipeline.id,
                    "job_id": job.id,
                    "run_id": run_record.id,
                    "attempts": attempt,
                    "rows_extracted": rows_extracted,
                    "rows_inserted": rows_inserted,
                    "rows_updated": rows_updated,
                    "rows_unchanged": rows_unchanged,
                    "total_dataset_rows": total_rows,
                    "data_version": dataset.data_version,
                    "duration_ms": elapsed_ms,
                    "logs": logs,
                }

            except Exception as exc:
                last_exc = exc
                elapsed_ms = round((time.perf_counter() - t0) * 1000, 3)
                logs.append(f"Attempt {attempt} FAILED: {exc}")
                run_record.status = "failed"
                run_record.duration_ms = elapsed_ms
                run_record.finished_at = datetime.now(timezone.utc)
                run_record.log_text = "\n".join(logs)

                db.add(
                    EtlError(
                        pipeline_id=pipeline.id,
                        job_run_id=run_record.id,
                        error_type=type(exc).__name__,
                        message=str(exc),
                        traceback=traceback.format_exc(),
                    )
                )
                src = db.query(DataSource).filter(DataSource.id == pipeline.data_source_id).first()
                if src:
                    src.health_status = "offline"
                    src.last_error = str(exc)
                ds = db.query(Dataset).filter(Dataset.id == pipeline.target_dataset_id).first()
                if ds:
                    ds.freshness_status = "stale"
                db.commit()

                if attempt <= max_retries:
                    job.status = "retrying"
                    db.commit()
                    if simulate_retry_sync:
                        time.sleep(min(backoff, 2.0))
                    backoff *= multiplier
                else:
                    job.status = "failed"
                    db.commit()

        return {
            "status": "failed",
            "pipeline_id": pipeline.id,
            "job_id": job.id,
            "attempts": max_retries + 1,
            "error": str(last_exc),
            "logs": logs,
        }
    finally:
        if claimed_db:
            try:
                db.query(EtlPipeline).filter(EtlPipeline.id == pipeline_id).update(
                    {"is_running": False}, synchronize_session=False
                )
                db.commit()
            except Exception:
                db.rollback()
        _release_distributed_etl_lock(pipeline_id, dist_token, dist_redis)
        lock.release()
