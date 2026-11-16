import hashlib
import json
import threading
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Optional
import pandas as pd
import psutil
from sqlalchemy.orm import Session
from app.core.cache import cache_manager
from app.core.security import decrypt_secret
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


def _get_pipeline_lock(pipeline_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        if pipeline_id not in _PIPELINE_LOCKS:
            _PIPELINE_LOCKS[pipeline_id] = threading.Lock()
        return _PIPELINE_LOCKS[pipeline_id]


def _row_hash(row: dict[str, Any]) -> str:
    raw = json.dumps(row, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def refresh_materialized_views_for_dataset(db: Session, dataset: Dataset) -> None:
    mvs = db.query(MaterializedViewMetadata).filter(MaterializedViewMetadata.dataset_id == dataset.id).all()
    for mv in mvs:
        try:
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
    - Per-pipeline mutex lock preventing duplicate concurrent runs
    - Exponential backoff retry policy
    - Intelligent incremental / CDC hash extraction
    - Atomic warehouse load (failed extraction NEVER overwrites existing valid dataset)
    - Checkpoint persistence, Lineage tracking, Cache invalidation, and Pre-Aggregation refresh
    """
    lock = _get_pipeline_lock(pipeline_id)
    if not lock.acquire(blocking=False):
        return {
            "status": "skipped_duplicate",
            "message": "Pipeline is already running; duplicate concurrent execution prevented",
            "pipeline_id": pipeline_id,
        }

    pipeline = db.query(EtlPipeline).filter(EtlPipeline.id == pipeline_id).first()
    if not pipeline:
        lock.release()
        raise ValueError(f"Pipeline not found: {pipeline_id}")

    if pipeline.is_running:
        lock.release()
        return {
            "status": "skipped_duplicate",
            "message": "Pipeline marked running; duplicate prevented",
            "pipeline_id": pipeline_id,
        }

    pipeline.is_running = True
    job = EtlJob(pipeline_id=pipeline.id, trigger_type=trigger_type, status="running", attempt_count=1)
    db.add(job)
    db.commit()

    retry_policy = pipeline.retry_policy_json or {}
    max_retries = int(retry_policy.get("max_retries", 2))
    backoff = float(retry_policy.get("initial_backoff_sec", 0.5))
    multiplier = float(retry_policy.get("backoff_multiplier", 2.0))

    last_exc: Optional[Exception] = None
    run_record: Optional[EtlJobRun] = None
    logs: list[str] = []

    try:
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

                # 1. EXTRACT
                source = db.query(DataSource).filter(DataSource.id == pipeline.data_source_id).first()
                dataset = db.query(Dataset).filter(Dataset.id == pipeline.target_dataset_id).first()
                if not source or not dataset:
                    raise ValueError("Pipeline requires valid data_source_id and target_dataset_id")

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

                base_query = pipeline.source_query or f'SELECT * FROM "{source.config_json.get("default_table", "erp_orders")}"'

                # Build incremental filter when cursor exists and warehouse table already populated
                inc_col = pipeline.incremental_column
                exec_query = base_query
                if (
                    pipeline.extract_mode == "incremental"
                    and inc_col
                    and checkpoint.last_cursor_value
                    and warehouse.table_exists(dataset.warehouse_table)
                ):
                    clean_base = base_query.strip().rstrip(";")
                    cursor_val = checkpoint.last_cursor_value.replace("'", "''")
                    exec_query = f'SELECT * FROM ({clean_base}) AS _src WHERE "{inc_col}" > \'{cursor_val}\''
                    logs.append(f"Incremental filter active: {inc_col} > {checkpoint.last_cursor_value}")

                raw_df = DataConnectorEngine.extract_dataframe(
                    source.source_type,
                    source.config_json,
                    creds,
                    query=exec_query,
                    read_only=source.read_only,
                )
                source.health_status = "healthy"
                source.last_error = None
                source.last_tested_at = datetime.now(timezone.utc)

                rows_extracted = len(raw_df)
                logs.append(f"Extracted {rows_extracted} rows from source '{source.name}'")

                # 2. TRANSFORM
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

                # 3. INTELLIGENT CHANGE DETECTION & LOAD
                pk_col = sanitize_identifier(pipeline.primary_key_column) if pipeline.primary_key_column else None
                prev_hashes: dict[str, str] = dict(checkpoint.row_hashes_json or {})
                rows_unchanged = 0
                rows_inserted = 0
                rows_updated = 0

                if len(transformed_df) == 0 and warehouse.table_exists(dataset.warehouse_table):
                    logs.append("No new/changed rows detected since last checkpoint.")
                else:
                    if pipeline.extract_mode == "cdc_hash" and pk_col and pk_col in transformed_df.columns and warehouse.table_exists(dataset.warehouse_table):
                        # Detect only changed or new rows by SHA-256 content hash
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
                        # Populate hashes for future CDC runs if PK exists
                        if pk_col and pk_col in transformed_df.columns:
                            checkpoint.row_hashes_json = {
                                str(rec[pk_col]): _row_hash(rec)
                                for rec in transformed_df.to_dict(orient="records")
                            }

                # Update incremental cursor if available
                if inc_col:
                    s_inc = sanitize_identifier(inc_col)
                    if s_inc in transformed_df.columns and len(transformed_df) > 0:
                        max_cursor = str(transformed_df[s_inc].max())
                        checkpoint.last_cursor_value = max_cursor
                        logs.append(f"Updated checkpoint cursor {s_inc} = {max_cursor}")
                checkpoint.last_success_at = datetime.now(timezone.utc)

                # Sync Dataset schema metadata & bump version
                total_res = warehouse.execute_query(f'SELECT COUNT(*) AS cnt FROM "{dataset.warehouse_table}"')
                total_rows = int(total_res["rows"][0]["cnt"])
                dataset.row_count = total_rows
                dataset.data_version += 1
                dataset.last_refreshed_at = datetime.now(timezone.utc)
                dataset.freshness_status = "fresh"

                # Sync DatasetFields if newly created or missing
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

                # Record Data Lineage
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

                # Refresh Pre-Aggregated Materialized Views & Invalidate Smart Cache
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
                # Update source health if connection error
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
        pipeline.is_running = False
        db.commit()
        lock.release()
