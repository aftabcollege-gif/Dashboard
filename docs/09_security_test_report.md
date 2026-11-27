# Deliverable 09 — Security & Acceptance Test Report (Stage 4 Verified)

All acceptance, BPMN/Excel/AI, and Stage 2–4 security suites reside in `backend/tests/` and `winsetup/tests/` and were executed against FastAPI, locked DuckDB 2.0.1, Next.js 16.4.6 standalone, and the Windows desktop Node gateway (`web_gateway.mjs`).

---

## 1. Automated Test Execution Summary

- **Backend Test Suite**: `.venv/bin/pytest backend/tests -v` → **39 passed, 0 failed**
- **Static & Dependency Security Gates**:
  - `bandit -r backend/app -lll -q` & `bandit -r backend/app -ll -s B608 -q` → **0 findings**
  - `pip-audit -r backend/requirements.txt` → **No known vulnerabilities found**
  - `npm audit --omit=dev` (`frontend/`) → **0 vulnerabilities**
- **Schema & Migration Verification**:
  - `alembic upgrade head && alembic check` → **No new upgrade operations detected** (across `0001` → `0002` → `0003`)
  - `postgres_schema.sql` vs SQLAlchemy ORM → **100% table & column parity**
- **Desktop Security & UI Gateway E2E**: `.venv/bin/python winsetup/tests/test_desktop_security_e2e.py` → **All 9 E2E checks passed**

| Suite | Scenarios Covered | Status |
|---|---|---|
| `test_acceptance_ad.py` | AD connect/test, search users/groups, full sync, domain user login, zero AD password persistence, role change & deactivation | **PASSED** |
| `test_acceptance_rbac.py` | Scoped dashboard isolation, Permission Matrix toggling + copy + cache invalidation | **PASSED** |
| `test_acceptance_etl.py` | Atomic staging swap on failure, incremental watermark sync, CDC SHA-256 sync, overlap locking | **PASSED** |
| `test_acceptance_perf_sec.py` | 50k-row analytical aggregation, cold/warm cache isolation, Server-side RLS & CLS masking, SQL guardrails | **PASSED** |
| `test_bi_engines.py` | Formula compiler (`SUM`, `MEDIAN`, `YOY`, `MTD`, `RUNNING_TOTAL`, etc.), chart switching, drilldown, AI NLQ, PDF/XLSX/CSV export | **PASSED** |
| `test_bpmn_process.py` & `test_excel_ingestion.py` & `test_text_to_widget.py` | BPMN process mining bottleneck execution, Persian `.xlsx`/`.csv` import, conversational BI assistant | **PASSED** |
| `test_stage2_security.py` | Cross-tenant IDOR isolation, SQL AST/XXE/Zip-bomb/DuckDB hardening, HttpOnly cookie/CSRF + refresh rotation & reuse detection, privilege escalation | **PASSED** |
| `test_stage3_security.py` | CTE/catalog SQL guard, pre-SQL RLS/CLS enforcement, atomic CAS token refresh, trusted proxy header validation, fail-closed rate limiter, LDAPS strict TLS & filter escaping, ETL fencing, scheduler leader election, Alembic prod gating | **PASSED** |
| `test_stage4_security.py` | Desktop cookie/CSRF RBAC Users & Matrix flow (`needs_initial_setup`, `/rbac/{users,roles,groups,permissions/matrix}`), public-restricted override & role delegation block, DuckDB lock/readiness & sanitized 500 handler, SSRF IP pinning / DNS rebinding defense, atomic Redis Lua rate limiter & cookie refresh, Alembic head verification & legacy schema mismatch rejection + ETL lease fencing | **PASSED** |

---

## 2. Verified Stage 4 Security Controls

| Security Layer | Verification Outcome |
|---|---|
| Password Hashing & Credentials | Local passwords hashed with `bcrypt`; AD passwords never persisted; source & bind secrets encrypted at rest with Fernet (`BI_CRED_KEY`) |
| Desktop First-Run & Auth Cookies | Production startup refuses weak/fallback secrets or `BI_SEED_DEMO_USERS=true`; `HttpOnly` + `SameSite=Lax` cookies + `X-CSRF-Token` validation; `SystemBootstrapState` DB singleton + setup token lock; refresh responses omit JWTs unless `X-Auth-Mode: bearer` |
| RBAC & Public Dashboard Governance | Non-delegable role safeguards (`super_admin`, `admin:*`), `dashboard:public_restricted_override` gate for RLS/CLS/sensitive datasets, showcase dashboard `is_public=False` by default |
| SQL & DuckDB Warehouse Hardening | `sqlglot` AST allowlist + org-scoped table validation, `enable_external_access = false`, `lock_configuration = true`, `conn.interrupt()` query timeout watchdog, readiness probe |
| SSRF & Connector Isolation | Pre-validated literal IP pinning in `_PinnedSSRFHTTPTransport` + redirect validation + `urllib3`/socket private-CIDR rejection |
| Rate Limiting & Trusted Proxies | Atomic Redis Lua sliding-window rate limiter + fail-closed prod mode; `X-Forwarded-For` honored only from `TRUSTED_PROXY_IPS` CIDRs |
| Schema & ETL Reliability | Alembic `0001`–`0003` verified; full baseline schema validation on legacy DBs; ETL lease heartbeat + fencing token updates; single-worker + leader lock scheduler |
