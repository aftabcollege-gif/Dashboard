# Deliverable 09 — Security & Acceptance Test Report

All acceptance test suites and engine verifications reside in `backend/tests/` and were executed against FastAPI, DuckDB, and the Smart Cache Manager with zero mocked security checks.

---

## 1. Automated Test Execution Summary

Command: `/home/user/Dashboard/backend/.venv/bin/pytest -v tests/`
Result: **`5 passed in 8.06s`**

| Suite | Scenarios Covered | Status |
|---|---|---|
| `tests/test_acceptance_ad.py` | **Acceptance Test A**: AD connect/test, search users & groups, full sync, domain user login (`sara.ahmadi`, `ali.rezaei`), **zero AD password stored in App DB**, AD group change -> BI role update, **AD account deactivation -> immediate login refusal (401/403)** | **PASSED** |
| `tests/test_acceptance_rbac.py` | **Acceptance Tests B & C**: `custom_scoped` user can view ONLY Dashboard 1, receives `HTTP 403` on Dashboards 2 & 3, cannot create/edit/delete widgets or drill down if denied (`HTTP 403`), Permission Matrix toggling + Copy Permissions + immediate cache invalidation | **PASSED** |
| `tests/test_acceptance_etl.py` | **Acceptance Tests D & E**: Failed/offline source query leaves prior warehouse dataset intact (atomic staging swap), Incremental watermark sync processes only delta rows, CDC SHA-256 sync reports unchanged rows on repeat runs, concurrency lock prevents overlapping runs | **PASSED** |
| `tests/test_acceptance_perf_sec.py` | **Acceptance Tests F & G**: 50,000-row analytical aggregation, cold vs warm cache hit verification, **Server-side RLS** (`north_mgr` restricted exclusively to `North` even under URL/filter tampering; `south_mgr` restricted to `South`), **CLS Partial Masking** on `wh_hr_workforce.email`, SQL write/DDL rejection, and **cache isolation** between differing RLS users | **PASSED** |
| `tests/test_bi_engines.py` | Formula Compiler (`SUM`, `AVG`, `COUNT`, `COUNT_DISTINCT`, `MIN`, `MAX`, `MEDIAN`, `RANK`, `PERCENTAGE`, `YOY`, `MOM`, `YTD`, `MTD`, `RUNNING_TOTAL`), chart-type switching preserving dataset & filters, drill hierarchy transitions & breadcrumbs, AI NLQ / Anomaly / Forecast, PDF/Excel/CSV exports, Excel file preview & import | **PASSED** |

---

## 2. Verified Security Controls Checklist

| Security Layer | Verification Outcome |
|---|---|
| Password Hashing | Local passwords hashed with `bcrypt` (`passlib`); AD passwords never persisted |
| Credential Storage | Data source passwords & AD bind passwords encrypted at rest with Fernet (`cryptography`) |
| Authentication & Session | Short-lived JWT access tokens + refresh tokens + `user_sessions` tracking + account lockout (`5` failed attempts) + TOTP MFA (`pyotp`) |
| URL & API Tampering Protection | Direct widget execution (`POST /widgets/{id}/data`) and dataset preview (`GET /datasets/{id}/preview`) enforce `PermissionResolver` and return `HTTP 403` when unauthorized |
| SQL Injection & Destructive SQL Guard | `sqlparse` + keyword validation rejects `DROP`, `DELETE`, `UPDATE`, `INSERT`, `ALTER`, `ATTACH`, `COPY` on SQL Editor and direct queries |
| Shared Cache Leakage Prevention | Cache keys incorporate `permission_context_hash` (roles, groups, user ID, RLS hash); verified `north_mgr` warm cache cannot serve `south_mgr` or `admin` data |
| Immutable Audit Trail | Auth logins, denials, AD syncs, ETL runs, RBAC changes, and file imports logged to `audit_logs` with IP & before/after diffs |
