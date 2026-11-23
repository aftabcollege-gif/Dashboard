# Deliverable 08 — Performance Benchmark Report (Stage 4 Verified)

All measurements below were reproduced directly by `.venv/bin/python backend/scripts/benchmark_stage4.py` on this runtime environment (`Python 3.11.2`, `DuckDB 2.0.1` with `enable_external_access = false` & `lock_configuration = true`, `Next.js 16.4.6` standalone gateway -> `FastAPI`).

---

## 1. Analytical Warehouse Scale Benchmarks (100,000 Rows — Locked DuckDB 2.0.1)

| Workload | Dataset Size | Measured Latency | Notes |
|---|---|---|---|
| Bulk DataFrame Ingestion (`bench_fact_100k` load) | 100,000 rows | **91.37 ms** | Columnar vectorized ingestion |
| Multi-Dimension Aggregation (`GROUP BY region, category`, 20 groups) | 100,000 rows | **9.58 ms** | `SUM(revenue)`, `SUM(revenue - cost)`, `COUNT(*)` |
| Time-Series Running Total Window (`SUM(...) OVER (ORDER BY month)`) | 100,000 rows | **6.35 ms** | 12 monthly rolling buckets |
| RLS-Filtered Regional Aggregation (`WHERE region = 'North'`, 5 rows) | 100,000 rows | **6.15 ms** | Predicate pushdown on columnar scan |

*(Additionally verified inside automated pytest `test_acceptance_perf_sec.py`: 50,000-row DuckDB ETL batch load + group aggregation + RLS/CLS cache isolation).*

---

## 2. Live API, Dashboard Batch & RBAC Matrix Benchmarks

Measured against the 9-widget **Enterprise Revenue & Executive Intelligence** dashboard (`POST /api/v1/dashboards/{id}/execute-batch`) and RBAC Security Center endpoints:

| Benchmark Case | Measured Result | Details |
|---|---|---|
| Cold 9-Widget Batch Load (Cache Cleared) | **217.71 ms** | RBAC + RLS compilation + 9 DuckDB widget queries |
| Warm 9-Widget Batch Load (Result Cache Hit) | **121.60 ms** | Served from Layer 3 permission-keyed cache (**1.79x** faster) |
| 10 Concurrent Dashboard Requests (90 Widget Executions) | **1,686.39 ms wall clock** (**1,622.67 ms avg**) | Zero SQL errors, 100% HTTP 200 |
| Live Monitoring Snapshot (`GET /api/v1/monitoring/performance`) | **92.86% cache hit ratio** | `289.96 MB` process RSS |
| RBAC Security Center Bundle (`/rbac/users` + `/rbac/roles` + `/rbac/permissions/matrix`) | **52.62 ms** | Normalized `{roles, catalog}` + org-scoped rules |

---

## 3. Document & Workbook Export Benchmarks

| Export Format | Scope | Measured Latency | Output Size |
|---|---|---|---|
| PDF Multi-Widget Executive Report (`ReportLab`) | Full 9-widget Dashboard | **173.37 ms** | `11,587 bytes` (valid `%PDF`) |
| Excel Multi-Sheet Workbook (`OpenPyXL`) | Full 9-widget Dashboard | **179.90 ms** | `22,000 bytes` (`.xlsx`) |

---

## 4. Built-In Architectural & Security Optimizations

1. **Pre-Aggregated Materialized Views**: `mv_sales_monthly` reduces repeated monthly rollup queries from raw transaction scans to pre-computed monthly buckets.
2. **Permission-Scoped Smart Caching**: Keys include `(tenant_id, dashboard_id, widget_id, dataset_id, data_version, query_hash, filter_state_hash, permission_context_hash)` — maximizing hit ratios without cross-user leakage or global `flushdb()`.
3. **Locked DuckDB Engine + Query Guardrails (`backend/app/engines/warehouse.py`, `backend/app/config.py`)**: Locks DuckDB configuration (`enable_external_access = false`, `lock_configuration = true`), checks readiness before traffic, enforces `QUERY_TIMEOUT_SECONDS` (`conn.interrupt()` watchdog), row limits (`MAX_QUERY_ROWS`), and read-only SQL AST validation (`SELECT`/`WITH` only).
