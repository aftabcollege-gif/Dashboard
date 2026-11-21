# Deliverable 06 — Automated ETL, Scheduling & Resilience Architecture

Implemented in `backend/app/engines/etl_engine.py`, `backend/app/engines/scheduler.py`, and `backend/app/api/etl.py`.

---

## 1. Core Architectural Guarantees

1. **Execution Separation**: ETL extraction and loading run in background worker threads (APScheduler / async executor), completely decoupled from dashboard rendering. Users querying dashboards read directly from the Analytical Warehouse (`Layer 2`) and Result Cache (`Layer 3`), never blocking on operational source databases.
2. **Atomic Staging-to-Warehouse Swap**:
   - Data extracted from operational sources is loaded into an isolated DuckDB staging table (`_stg_{table}_{ts}`), transformed, and validated.
   - Only after 100% validation success is the warehouse table atomically swapped (`CREATE OR REPLACE TABLE {target} AS SELECT * FROM {staging}`) and `Dataset.data_version` incremented.
   - **Zero Corruption on Failure**: If an extract query fails, times out, or a source goes offline mid-run, the staging table is discarded and existing warehouse data remains intact and queryable ("offline resilience").
3. **Zero-Duplication Incremental & CDC Extraction**:
   - `full`: Complete snapshot replacement.
   - `incremental`: Uses persisted watermark in `etl_checkpoints` (`last_cursor_value` on `updated_at` or numeric `PK`) so subsequent runs only extract/apply new or modified rows (`WHERE updated_at > :cursor`).
   - `cdc_hash`: Computes row-level **SHA-256 content hashes** per primary key and stores the hash map in `etl_checkpoints.row_hashes_json`. Re-running against unchanged source tables detects `rows_unchanged = N` and skips redundant warehouse rewrites.

---

## 2. Concurrency Control, Retries & Scheduling

- **Concurrency Lock**: `EtlPipeline.is_running` acts as an atomic execution guard preventing overlapping runs of the same pipeline from corrupting checkpoints or duplicating loads (`ETLConcurrencyError`).
- **Exponential Backoff Retries**: Configurable `retry_policy_json` (`max_retries`, `initial_backoff_sec`, `backoff_multiplier`) retries transient network/source errors before marking the job failed and recording an `EtlError` entry.
- **Scheduler (`APScheduler`)**:
  - Supports intervals (`1m`, `5m`, `15m`, `30m`, `1h`), `daily`, `weekly`, `monthly`, and arbitrary `cron` expressions (`croniter` validated) with `timezone`, execution windows (`allowed_window_json`), and upstream `depends_on_pipeline_id` dependency chaining.
- **Immediate Downstream Invalidation**: Every successful warehouse load increments `dataset.data_version`, rebuilds dependent Materialized Views (`mv_sales_monthly`), and triggers cache invalidation for affected keys.
