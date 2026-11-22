# NexusBI Enterprise Platform — Stage 1 Comprehensive Technical & Security Audit

**Audit Date:** 2026-11-16  
**Repository / Branch:** `aftabcollege-gif/Dashboard` (`arena/590acc8b-dashboard`, branched from `main@57da551a`)  
**Scope:** 100% full-stack audit across Backend (`backend/app/**`, `backend/migrations/**`), Frontend (`frontend/src/**`, `frontend/next.config.mjs`), Infrastructure (`docker-compose.yml`, `Dockerfiles`, `.github/workflows/**`), and Windows Desktop Packaging (`winsetup/**`).

---

## Executive Risk Summary

| Severity | Count | Primary Categories |
|---|---:|---|
| **CRITICAL** | 6 | Cross-Tenant IDOR Isolation Gaps, Blacklist SQL Injection / DuckDB File Exfiltration, RBAC / Public Dashboard / Privilege Escalation Flaws, Auth Session / Refresh Revocation & `localStorage` JWT, Unbounded DuckDB Query Execution / Missing Query Interrupt, Raw SQL Execution without Org/Dataset Scope |
| **HIGH** | 8 | Single-Connection DuckDB Lock + `--workers 2` Multi-Worker Conflict & Duplicate APScheduler, File Upload OOM / Zip-Bomb / XXE / Collision / Leak, AD/LDAP Injection & Missing LDAPS Cert Validation & Deleted-User Deactivation, Global `flushdb()` Cache / Non-propagating In-Memory Permission Memo, Missing Alembic Migrations & Drifted `postgres_schema.sql`, Missing Security Headers & Wildcard CORS + Credential Misconfiguration, ETL No Crash Recovery / Timeout / Schema Drift / DQ Scoring, Frontend↔Backend API Contract Bugs |
| **MEDIUM** | 5 | Dashboard Builder Missing Auto-Save / Unsaved Guard / Optimistic Concurrency / Version Restore / 50+ Widget Virtualization, Incomplete Granular UI Permission Gating, AI Engine Missing Error Diagnosis / Data Narrative / Dataset-Level Scoping, Demo vs Production Separation Leaks, Static Default Secrets in Compose & `.env.example` |
| **LOW** | 3 | Raw Exception Leakage in HTTP 400/500 Responses, Missing CI Security/Test Workflow, Unused/Duplicate Route Handlers |

---

## Detailed Findings

### 1. CRITICAL — Multi-Tenancy Isolation & Cross-Organization IDOR

#### 1.1 Unfiltered Multi-Tenant Reads & Writes in `dashboards.py`
- **Locations:**
  - `backend/app/api/dashboards.py`: `list_templates` (L444), `list_dashboards` (L450), `create_dashboard` (L465), `get_dashboard` (L501), `update_dashboard`, `delete_dashboard` (L701), `add_dashboard_page`, `save_dashboard_layout`, `create_widget`, `update_widget`, `delete_widget` (L907), `duplicate_widget`, `switch_widget_chart_type`, `add_dashboard_filter`, `delete_dashboard_filter`, `add_widget_filter`, `delete_widget_filter`, `get_widget_data`, `execute_dashboard_batch`, `get_dashboard_state`, `save_dashboard_state` (L1153), `list_versions`, `create_version`, `list_comments`, `add_comment`, `generate_share_link`, `clone_from_template` (L1234).
- **Technical Description:**
  - `list_dashboards` and `list_templates` query `Dashboard` without joining `Workspace` and filtering by `Workspace.organization_id == user.organization_id`.
  - `create_dashboard` accepts an arbitrary `body.workspace_id` without verifying the workspace belongs to `user.organization_id`.
  - Every single resource lookup (`Dashboard`, `DashboardPage`, `Widget`, `DashboardFilter`, `WidgetFilter`) resolves solely by primary key UUID (`id`) without validating workspace/organization ownership.
  - Even `get_dashboard` allows `is_public == True` dashboards across organizations.
- **Impact:** Any authenticated user (e.g., an admin or dashboard editor in Org B) can read, alter, execute, or delete dashboards, widgets, comments, and layouts of Org A simply with a target UUID or wildcard role.
- **Fix Design:** Introduce a mandatory tenant-boundary helper (`get_dashboard_for_org`, `get_widget_for_org`, `get_workspace_for_org`) joining `Workspace` and enforcing `Workspace.organization_id == user.organization_id`. Scope `list_dashboards` and `list_templates` strictly to the caller's organization.
- **Proving Test:** Create Org A & Org B with separate admins and dashboards; verify Org B admin receives `404` on Org A `GET/PUT/DELETE /dashboards/{id}`, `POST /dashboards/{id}/execute-batch`, `POST /dashboards/{id}/clone-template`, and `GET /dashboards` never returns Org A dashboards.

#### 1.2 Cross-Tenant Gaps in `datasets.py`, `data_sources.py`, `etl.py`, `exports.py`, `themes.py`, `monitoring.py`, `queries.py`, `ai.py`, `users_rbac.py`
- **Locations:**
  - `backend/app/api/datasets.py`: `create_semantic_metric` (L110), `list_relationships` (L130), `create_relationship` (L144), `profile_dataset` (L162), `preview_dataset` (L171), `cascading_filter_options`, `add_calculated_field`, `get_dataset_lineage`, `add_transformation`, `delete_dataset` (L273).
  - `backend/app/api/data_sources.py`: `delete_data_source` (L110), `test_data_source` (L123), `browse_source_schema` (L139), `execute_direct_source_query` (L154).
  - `backend/app/api/etl.py`: `etl_monitoring_dashboard` (L52), `create_pipeline` (L147), `trigger_pipeline` (L182), `update_pipeline_schedule`.
  - `backend/app/api/users_rbac.py`: `create_user`, `update_user`, `delete_user`, `update_role`, `clone_role`, `update_group`, `get_permission_matrix` (L396), `set_matrix_permission` (L425), `copy_matrix_permissions`, `get_effective_permissions`, `delete_access_policy`, `test_ad_connection`, `search_ad_directory`, `sync_ad_directory`, `mutate_mock_ad_user` (L682).
  - `backend/app/api/exports.py`: `export_dashboard`, `export_widget`, `create_scheduled_report`, `run_scheduled_report_now` (L156 — additionally missing `enforce_permission`).
  - `backend/app/api/themes.py`: `create_custom_theme` (L33 — missing permission check), `export_theme` (L61 — missing org check).
  - `backend/app/api/monitoring.py`: `performance_dashboard` (L81 — global query telemetry across all orgs), `invalidate_system_cache` (L133 — clears all orgs).
  - `backend/app/api/queries.py`: `execute_sql_editor` (L64), `execute_visual_builder` (L95), `convert_query_to_widget` (L195).
  - `backend/app/api/ai.py`: `recommend_dashboard` (L51), `auto_generate_dashboard` (L60), `natural_language_query` (L76), `text_to_widget` (L90), `conversational_assistant` (L110).
- **Technical Description:** Tenant checks are absent on lookups and cross-entity links (e.g., creating a `DatasetRelationship` between Org A and Org B datasets; assigning Org A roles to Org B users; leaking Org A dashboards/widgets in `GET /rbac/permissions/matrix`; triggering Org A ETL pipelines or reading Org A `QueryExecutionLog` latencies).
- **Fix Design:** Enforce `organization_id == user.organization_id` at every single repository/ORM lookup, list query, join target, role/group/policy assignment, and monitoring aggregate. Restrict `system:invalidate_all` vs org-scoped cache clearing.
- **Proving Test:** Cross-tenant integration test suite iterating over datasets, data sources, ETL pipelines, RBAC roles/groups/matrix/policies, exports, themes, monitoring, queries, and AI endpoints confirming strict `403`/`404` isolation.

---

### 2. CRITICAL — SQL Injection, DuckDB File/Network Exfiltration & Unscoped Raw SQL

#### 2.1 Blacklist-Only `validate_readonly_sql` Bypassable via DuckDB Table Functions
- **Locations:** `backend/app/engines/connectors.py` (`validate_readonly_sql`, L60), `backend/app/engines/query_engine.py` (`execute_dataset_sql`, L181), `backend/app/api/queries.py` (`execute_sql_editor`, L64).
- **Technical Description:**
  - `validate_readonly_sql` only checks top-level keyword blacklists (`DROP`, `DELETE`, `INSERT`, etc.) via `sqlparse`.
  - In DuckDB, an attacker can run `SELECT * FROM read_csv_auto('/etc/passwd')`, `read_text('/app/.env')`, `read_parquet(...)`, `read_json(...)`, `glob('/*')`, `sqlite_scan(...)`, `duckdb_secrets()`, `duckdb_settings()`, or `https://...` via `httpfs` inside a normal `SELECT`.
  - When `execute_sql_editor` is called with `dataset_id=None`, **neither dataset RBAC nor RLS/CLS is enforced**, allowing queries against any organization's `wh_*` tables inside shared `warehouse.duckdb` or DuckDB catalog metadata.
- **Fix Design:**
  - Replace blacklist-only validation with strict **AST-based SQL inspection** (via `sqlglot` or deep `sqlparse` + token/function AST walker): permit **only** `SELECT` / `WITH ... SELECT`, allow only single statement, forbid dangerous DuckDB file/system/network/extension functions (`read_*`, `glob`, `sqlite_*`, `postgres_*`, `mysql_*`, `duckdb_*`, `pragma_*`, `iceberg_*`, `delta_*`, `httpfs`, `install`, `load`, `copy`, `attach`, `export`, `import`, `set`, `call`, `system`, `getenv`), and forbid URLs or filesystem path patterns in `FROM` / table-valued functions.
  - Enforce a strict **org-scoped table allowlist** (`Dataset.organization_id == user.organization_id` AND user has `dataset:view`) for every table referenced in the AST! Require `dataset_id` or validate every referenced table in AST against user's permitted org datasets and apply RLS/CLS to each.
- **Proving Test:** Attempt `SELECT * FROM read_text('/etc/passwd')`, `SELECT * FROM read_csv_auto('/tmp/x.csv')`, `SELECT * FROM duckdb_secrets()`, `SELECT * FROM wh_other_org_table`, and multi-statement `; DROP TABLE ...` via `/queries/execute-sql`; assert HTTP `400`/`403`.

#### 2.2 Public Dashboard Raw SQL Execution & Custom Filter / Formula / ETL SQL Injection
- **Locations:**
  - `backend/app/api/dashboards.py`: `get_public_dashboard` (L291), `execute_public_dashboard_batch` (L354).
  - `backend/app/engines/filter_engine.py`: `custom_expression` branch (L136).
  - `backend/app/engines/etl_engine.py`: incremental `last_cursor_value` interpolation (L150–156).
  - `backend/app/engines/data_engine.py`: `FormulaCompiler.compile_measure` compound fallback (L89–109), `apply_transformations` (`out.eval(formula)`, L196).
- **Technical Description:**
  - Public embed widgets with `sql_text` run directly on DuckDB without AST/org-table validation or public-safe policy checks, and `get_public_dashboard` also returns all `DashboardTheme` rows globally.
  - `filter_engine` accepts arbitrary `custom_expression` if it lacks `;`, `--`, `/*`, `DROP`, allowing boolean/subquery/function injection like `1=1 OR length(read_text('/etc/passwd')) > 0`.
  - `etl_engine` interpolates `last_cursor_value` directly into SQL string rather than parameterized query or validated typed literal.
  - `FormulaCompiler` uses weak regex blacklist for compound formulas.
  - `DataProfiler.apply_transformations` uses `pandas.DataFrame.eval()`, which supports arbitrary local variable access (`@`) and method calls in non-sandboxed modes.
- **Fix Design:**
  - Validate public widget `sql_text` against AST safe readonly allowlist + owning org's datasets only; scope public themes to org/system.
  - Replace `custom_expression` raw SQL passthrough with a strict expression AST/whitelist (only identifiers present in dataset schema + safe comparison/boolean/arithmetic operators).
  - Validate/parameterize ETL cursor values by column type or pass bind parameters.
  - Restrict `FormulaCompiler` compound expressions to tokenized whitelist of aggregate calls, schema identifiers, numeric literals, and `+ - * / ( )`. Replace `df.eval()` with a safe AST arithmetic evaluator (Python `ast.parse` supporting only `BinOp`, `UnaryOp`, `Name`, `Constant` over dataframe Series).
- **Proving Test:** Unit tests injecting subqueries/functions into `filter_engine.custom_expression`, `FormulaCompiler`, `apply_transformations`, and `etl_engine` cursor values; assert rejection.

---

### 3. CRITICAL — RBAC, Permission Engine, Public Bypass & Privilege Escalation

#### 3.1 Privilege Escalation in Role/Permission Assignment & Public/Clone Bypasses
- **Locations:**
  - `backend/app/api/users_rbac.py`: `create_user`, `update_user`, `set_matrix_permission` (L425), `update_role`, `clone_role`.
  - `backend/app/api/dashboards.py`: `clone_from_template` (L1234), `save_dashboard_state` (L1153), `get_dashboard` (L501), `delete_widget` (L907), `switch_widget_chart_type`, `add_dashboard_filter`, `delete_dashboard_filter`.
  - `backend/app/core/permissions.py`: `PermissionResolver._evaluate` (L262), `cascade_targets` (L312–313).
- **Technical Description:**
  - A user with `admin:edit` (e.g. an organization admin) can call `set_matrix_permission` or assign `super_admin` role to themselves/others or edit `super_admin` permissions (`is_system` / higher-privilege role escalation).
  - `clone_from_template` checks only `dashboard:add`, allowing cloning of non-template private dashboards across orgs or without `dashboard:view` on the source dashboard.
  - `save_dashboard_state` lacks any `enforce_permission(db, user, "dashboard", dash.id, "view")`.
  - `PermissionResolver._evaluate` only cascades permissions when `conditions_json.cascade_targets` matches specific pairs; when `inherit_to_children=True` is set cleanly on a workspace/dashboard/dataset rule without custom JSON, hierarchical inheritance (`organization -> workspace -> dashboard -> page -> widget` and `dataset -> field`) does not cascade consistently.
  - Granular permissions (`dashboard:add_widget`, `dashboard:delete_widget`, `dashboard:manage_filters`, `dashboard:change_layout`, `dashboard:add_page`, `dashboard:delete_page`, `widget:change_chart_type`, `filter:add`, `filter:delete`, `dataset:view_schema` vs `dataset:view`) are inconsistently enforced and insufficiently serialized to the frontend.
- **Fix Design:**
  - Block non-`super_admin` users from assigning/modifying `super_admin` role or granting permissions they do not possess. Protect `is_system` core roles from destructive mutation.
  - Enforce `dashboard:view` (and `is_template == True` or ownership + same org) on `clone_from_template`; enforce `dashboard:view` on `save_dashboard_state`.
  - Implement clean hierarchical parent-chain inheritance in `PermissionResolver` respecting `inherit_to_children` + explicit `deny` overrides at every level (`user > group > role` and child resource explicit deny overrides parent allow).
  - Enforce matching granular action pairs on every mutation endpoint and expose full `permissions` map in dashboard serialization.
- **Proving Test:** RBAC test matrix proving explicit deny beats group/role allow, inherited dashboard deny blocks child widget view, non-super-admin cannot elevate to `super_admin`, `clone_from_template` blocked without source `view`, and granular `change_chart_type`/`delete_widget`/`manage_filters` constraints hold.

---

### 4. CRITICAL — Auth, Session Revocation, Token Storage, Rate Limiting & Headers

#### 4.1 Broken Session/Refresh Revocation, Missing Rotation & `localStorage` Token Exposure
- **Locations:**
  - `backend/app/api/auth.py`: `_BOOTSTRAP_LOCK` (L40, L101), `bootstrap_admin` (L97), `login` (L213), `refresh_token` (L337), `change_password` (L400).
  - `backend/app/api/deps.py`: `get_current_user` (L22).
  - `frontend/src/lib/api.ts` (L17, L22–23).
- **Technical Description:**
  - `login` issues access and refresh tokens sharing the same `jti`.
  - `refresh_token` does **not** verify `UserSession.is_revoked` or `UserSession.expires_at`, does **not** verify user `is_active`, and does **not** rotate the refresh token (logged-out or revoked sessions can continuously mint fresh access tokens!).
  - `get_current_user` uses `if sess and sess.is_revoked:` — failing **open** if the `UserSession` row is absent (when `jti` is present) and ignoring `permission_version` mismatches.
  - No `GET /auth/sessions`, `DELETE /auth/sessions/{id}`, or `POST /auth/logout-all` endpoints exist.
  - `bootstrap_admin` uses an in-process `threading.Lock` (`_BOOTSTRAP_LOCK`) which is ineffective across processes/workers.
  - `change_password` and `create_user` do not enforce password complexity policy (`validate_password_strength`), unlike `bootstrap_admin`.
  - Frontend stores access & refresh JWTs in `localStorage`, exposing tokens to XSS.
- **Fix Design:**
  - Store separate hashed/unique `jti` (`refresh_jti`) per session, rotate refresh token on every `/auth/refresh`, reject revoked/missing/expired sessions fail-closed in both `get_current_user` and `refresh_token`, and validate `perm_ver` or immediately bust user caches.
  - Add Active Sessions API (list, revoke one, revoke all) + enforce password strength on `change_password` and `create_user` + atomic DB uniqueness/transaction lock for `bootstrap-admin`.
  - Issue `HttpOnly`, `SameSite=Lax`, environment-aware `Secure` cookies (`BI_COOKIE_SECURE=auto|true|false` so HTTPS prod uses `Secure=True` while Windows desktop `http://127.0.0.1` still works cleanly!) plus CSRF double-submit header validation (`X-CSRF-Token`) for cookie-authenticated mutations, while maintaining clean Bearer compatibility for explicit API/test clients. Migrate frontend `api.ts` off `localStorage` JWT storage.
- **Proving Test:** Test that after `/auth/logout`, both `access_token` and `refresh_token` return `401`; test refresh token reuse detection revokes session; test weak password rejected on `change_password`; test missing CSRF rejected on cookie-backed state mutation.

#### 4.2 Missing Rate Limiting, Wildcard CORS & Security Headers
- **Locations:** `backend/app/main.py` (L55), `frontend/next.config.mjs` (L28), `backend/app/api/auth.py`, `queries.py`, `dashboards.py`.
- **Technical Description:**
  - `main.py` sets `allow_origins=["*"]` with `allow_credentials=True` (unsafe and invalid with cookies in modern browsers).
  - `next.config.mjs` emits `Access-Control-Allow-Origin: *` across `/(.*)`.
  - Missing `CSP`, `Strict-Transport-Security`, `Permissions-Policy`, `X-Frame-Options` / `frame-ancestors`, and `Cache-Control: no-store` on `/api/v1/auth/*` and `/api/v1/rbac/*`.
  - No IP/route rate limiting on `/auth/login`, `/auth/bootstrap-admin`, `/auth/refresh`, `/queries/execute-sql`, or `/public/{token}`.
- **Fix Design:** Configure explicit CORS origin allowlist (defaulting cleanly for dev/desktop/configured domains), add FastAPI & Next.js security headers middleware (strict `frame-ancestors 'none'` except `/public/*`), `Cache-Control: no-store` on auth/security APIs, and distributed/memory sliding-window rate limiter returning `429` + `Retry-After`.
- **Proving Test:** Burst login attempts trigger `429`; security header assertions on backend & Next responses; verify unknown CORS `Origin` is not echoed with credentials.

---

### 5. CRITICAL / HIGH — DuckDB Warehouse Concurrency, Unbounded Fetch & Missing Timeout Interrupt

- **Locations:** `backend/app/engines/warehouse.py` (`execute_query`, L60), `backend/app/api/queries.py` (L77), `backend/Dockerfile` (`--workers 2`, L22), `backend/app/engines/scheduler.py` (`start_scheduler`, L74).
- **Technical Description:**
  - `warehouse.py` executes `cur.fetchall()` whenever `max_rows <= 0` — `queries.py` passes raw `body.limit` without checking `<= 0` or upper cap, enabling OOM crashes.
  - `warehouse.execute_query` holds a single global `RLock` around a single DuckDB connection and has **no query timeout interruption** (`conn.interrupt()` via timer thread). A single slow query locks out all tenants indefinitely.
  - `backend/Dockerfile` runs `--workers 2`, causing **two catastrophic multi-worker conflicts**: (a) DuckDB single-writer file lock crashes worker #2; (b) APScheduler starts inside every worker, executing scheduled ETL/report jobs twice without distributed locking.
  - `PermissionResolver._memo` is process-local memory without cross-worker synchronization, and `SmartCacheManager.invalidate_all` calls `flushdb()` on Redis globally.
- **Fix Design:**
  - Enforce hard `max_rows = min(max(1, limit), settings.MAX_QUERY_ROWS)` in `warehouse.py` and `queries.py` (never `fetchall()` unbounded).
  - Add real active query timeout using a watchdog `threading.Timer` calling `conn.interrupt()` after `timeout_s`, and use bounded DuckDB settings (`SET memory_limit`, `SET threads`). Use separate read cursors / serialized write-only lock or read-pool pattern with per-dataset/org metrics & backpressure queue timeout (`503` when saturated).
  - Fix `backend/Dockerfile` to `--workers 1` with async concurrency or explicit worker-leader scheduler lock (DB lease/Redis lock), scope cache invalidation to `org:{org_id}:*` (never `flushdb()`), and invalidate `PermissionResolver._memo` via `user.permission_version` + `org` epoch check.
- **Proving Test:** Test `limit=0` / `limit=999999` is capped and never calls unbounded fetch; test long-running recursive DuckDB query is actively interrupted via `conn.interrupt()` within timeout; test 50 concurrent dashboard requests succeed without lock errors.

---

### 6. HIGH — File Upload Security, Table Collisions, Orphan Cleanup, Data Quality & Storage Quotas

- **Locations:** `backend/app/api/data_sources.py` (L179, L217, L271), `backend/app/engines/connectors.py` (L6, L133+), `backend/app/api/datasets.py` (`delete_dataset`, L273).
- **Technical Description:**
  - `await file.read()` buffers arbitrarily large uploads into RAM with no size limit.
  - XML parsing uses stdlib `xml.etree.ElementTree.fromstring` without XXE / XML-bomb protection (`defusedxml` is not in `requirements.txt`).
  - `.xlsx` (ZIP container) is parsed without Zip-bomb uncompressed-size / compression-ratio check or max row/column guard.
  - `wh_table = f"wh_file_{clean_slug}_{int(datetime.now().timestamp()) % 100000}"` risks cross-org table collisions; files are stored flat in `UPLOADS_DIR` without org subdirs, leaked on parse failure, and neither DuckDB tables nor disk files are cleaned up on `delete_dataset`.
  - No per-organization storage quota check, Data Quality (DQ) score, Schema Drift detection, or Row-Count Anomaly alerting.
- **Fix Design:** Stream-check upload size (`MAX_UPLOAD_BYTES`), verify magic bytes/ZIP compression ratio & max rows/cols, adopt `defusedxml` (with strict fallback blocking `<!DOCTYPE`/`<!ENTITY`), isolate paths `uploads/{org_id}/` and unique org-scoped `wh_org_{short}_{uuid}` DuckDB tables, delete orphan files on failure and drop DuckDB table + file on `delete_dataset`, enforce per-org upload/warehouse quota, and compute DQ Score + Schema Drift + Row Anomaly in ETL/import.
- **Proving Test:** Reject oversized file (`413`), Zip-bomb / XXE XML payload (`400`), clean up DuckDB table & disk file on `DELETE /datasets/{id}`, detect schema drift & compute DQ metrics on upload/ETL.

---

### 7. HIGH — Active Directory / LDAP Injection, TLS Validation & Sync Deactivation

- **Locations:** `backend/app/engines/ad_ldap.py` (L123–215, L421–432), `backend/app/api/auth.py` (L216), `backend/app/api/users_rbac.py` (L682).
- **Technical Description:**
  - `ldap3.Server` omits `Tls(validate=ssl.CERT_REQUIRED, ...)` for LDAPS.
  - Username and search filters interpolate raw user input without `escape_filter_chars` -> **LDAP Injection**.
  - `sync_directory` tracks `seen_ext_ids` only loosely for mock mode and fails to deactivate existing AD-linked users who were removed from AD or removed from `selected_groups`.
  - `login` AD path fetches first active `DirectoryConnection` without domain/org disambiguation.
  - Mock AD mutation endpoint is exposed without strict org/dev guards.
- **Fix Design:** Enforce `escape_filter_chars` on all LDAP filters, configure strict `Tls` validation on LDAPS with optional custom CA path, implement full differential deactivation for removed AD users/group members, support customizable `group_role_mappings`, bind AD login by domain/org safely, and restrict mock AD mutation endpoint by org ownership & environment setting.
- **Proving Test:** LDAP injection payload in username (`*)(uid=*))(|(uid=*`) is escaped; removed user in AD sync is deactivated (`is_active=False`) and sessions revoked.

---

### 8. HIGH — Database Migrations, Pooling, Health Checks & Production Config

- **Locations:** `backend/app/main.py` (L33), `backend/app/database.py`, `backend/migrations/postgres_schema.sql`, `docker-compose.yml`, `.env.example`, `winsetup/launcher/Program.cs`.
- **Technical Description:**
  - `postgres_schema.sql` is missing 5 ORM tables: `user_sessions`, `dashboard_comments`, `dashboard_states`, `semantic_metrics`, `scheduled_reports`.
  - No Alembic migration infrastructure exists (`Base.metadata.create_all` is run unconditionally on startup).
  - Postgres engine lacks tuned pool settings (`pool_size`, `max_overflow`, `pool_recycle`).
  - `docker-compose.yml` and `.env.example` embed static default secrets; healthcheck is shallow (`SELECT 1` missing deep DuckDB/pool checks).
- **Fix Design:** Set up Alembic (`alembic.ini` + `migrations/env.py` + baseline migration matching all ORM models), sync `postgres_schema.sql` with all tables/indexes, configure Postgres pooling + `/health/deep` readiness endpoint, and ensure production checks warn/fail on default secrets while keeping local/Windows offline desktop seamless.
- **Proving Test:** Verify `alembic upgrade head` against clean SQLite, verify all ORM tables exist in `postgres_schema.sql`, and verify `/api/v1/monitoring/health` / `/health` deep check.

---

### 9. HIGH / MEDIUM — Frontend↔Backend Bugs, Builder UX, AI Assistant & Demo Separation

- **API Contract Mismatches Discovered During Code Read:**
  1. `frontend/src/app/admin/security/page.tsx`: parses `GET /rbac/roles` as array instead of `{roles, catalog}` and reads `res.copied_rules` instead of `res.copied`.
  2. `frontend/src/app/reports/page.tsx`: reads `res.size_bytes` and `res.format` instead of `res.bytes` on `/exports/scheduled-reports/{id}/run`.
  3. `frontend/src/app/admin/monitoring/page.tsx`: passes `?search=...` instead of `?action=...` / `?username=...`.
  4. `frontend/src/app/query-builder/page.tsx`: reads `res.explain_plan` instead of `res.query_plan` and `h.sql_text` instead of `h.query_hash`.
  5. `frontend/src/app/settings/page.tsx`: omits required `slug` field in `POST /themes`.
- **Builder UX (`DashboardWorkspace.tsx`, `dashboards.py`):** Missing debounced auto-save, `beforeunload` unsaved-change protection, save-status pill, optimistic concurrency (`version` mismatch `409`), `POST /dashboards/{id}/versions/{ver_id}/restore`, IntersectionObserver lazy widget loading for 50–100+ widgets, and full granular permission flags in UI.
- **AI Engine (`ai_engine.py`):** Missing SQL/widget error diagnosis & fix recommendation mode, missing data-grounded chart explanation insights endpoint/action, and inconsistent dataset-level RBAC/CLS validation.
- **Demo vs Production:** Clear separation of demo fallback (`vercelDemoApi` / `demoStore`) from production runtime (`localStorage` theme/token leakage removed from prod auth path), plus explicit demo notice banners on data-mutating views when running in `vercel-preview-demo`.
- **CI/CD:** Add `.github/workflows/ci.yml` running backend pytest + security tests, Bandit/pip-audit checks, and frontend `tsc --noEmit` + Next build.

---

## Phased Implementation Roadmap (Stages 2–10)

1. **Stage 2 (Critical Security & RBAC/Tenant Isolation):** Multi-tenant boundaries on all endpoints, AST SQL validator + org-table allowlist, RBAC privilege escalation & inheritance fixes, refresh token rotation + fail-closed sessions + HttpOnly cookie/CSRF + rate limiting + strict CORS & security headers.
2. **Stage 3 (Warehouse Concurrency, Upload Hardening & Data Quality):** Bounded `max_rows`, real DuckDB `conn.interrupt()` timeout, upload byte/zip-bomb/XXE limits, org-scoped table/file isolation & cleanup, storage quota, DQ score & schema drift.
3. **Stage 4 (Scalability, Cache Isolation & Resilient ETL/Scheduler):** Worker-safe cache invalidation (no `flushdb()`), `permission_version` memo sync, ETL crash recovery (`is_running` reset), ETL timeout/cancel, single-leader scheduler locking, Dockerfile `--workers 1` correctness.
4. **Stage 5 (Active Directory Hardening):** LDAP filter escaping, strict LDAPS TLS validation, removed-user deactivation, group-to-role mapping config, org-bound domain login.
5. **Stage 6 (Dashboard Builder UX & API Contract Fixes):** Auto-save, dirty-state prompt, optimistic concurrency `409`, version restore endpoint, 50+ widget lazy viewport loading, granular permission controls, fix all 5 UI↔API contract bugs.
6. **Stage 7 (AI Assistant Enhancements):** Query/widget error diagnostics & fix suggestions, grounded analytical chart explanations, dataset RBAC enforcement.
7. **Stage 8 (Demo vs Production Hygiene):** Clean auth cookie client flow in `api.ts`, explicit demo-mode notices, isolate `demoStore` strictly to preview demo mode.
8. **Stage 9 (Database Migrations, Health & CI):** Alembic setup, update `postgres_schema.sql`, pooling & deep healthcheck, CI workflow + SAST checks.
9. **Stage 10 (Final E2E Verification & Production Packaging Check):** Full pytest security/concurrency/BI/BPMN/AI suites, frontend TypeScript + Next build (`VERCEL=1` & standard), live server E2E, Windows installer build script & security E2E validation, and clean branch readiness.
