# Deliverable 02 — Application & Analytical Database Schema

The Enterprise BI Platform strictly separates transactional application metadata (Layer 1) from columnar OLAP warehouse storage (Layer 2) and result caching (Layer 3).

- **ORM Models**: `backend/app/models/` (`identity.py`, `dashboard.py`, `data.py`, `etl.py`, `performance.py`)
- **Production PostgreSQL DDL**: `backend/migrations/postgres_schema.sql`

---

## 1. Schema Domains & Core Tables

### Domain A — Identity, Active Directory & RBAC (`identity.py`)
| Table | Purpose | Key Indexes / Notes |
|---|---|---|
| `organizations` | Multi-tenant root entity | `slug` unique |
| `users` | Local & AD-synced user records | `username`, `email`, `auth_source`, `failed_login_attempts`, `locked_until`, `mfa_secret_encrypted`; **never stores AD passwords** |
| `user_profiles` | Extended user attributes (`region`, `department`, `title`) | Used dynamically by RLS `${user.region}` / `${user.department}` templates |
| `roles` & `user_roles` | RBAC roles with `is_system`, `priority` | Supports cloning & multi-role assignment |
| `groups`, `group_members`, `group_roles` | Security groups & role bindings | Supports AD group mapping |
| `resource_permissions` | Granular ACL grants (`subject_type`, `subject_id`, `resource_type`, `resource_id`, `action`, `effect`, `inherit_to_children`) | Indexed on `(subject_type, subject_id, resource_type, resource_id)` |
| `access_policies` | Row-Level Security (`rls`), Column-Level Security (`cls`), and Data Masking (`masking`) rules | Evaluated server-side on every dataset/widget/SQL call |
| `directory_connections` & `directory_sync_jobs` | LDAP / LDAPS / Mock AD config (encrypted bind password) & sync audit runs | Supports `on_login` and scheduled directory sync |
| `external_identities` | Mapping between internal `user_id` and AD `objectGUID` / `DN` | Indexed on `external_id` |
| `user_sessions` | Active refresh/access sessions (`jti`, `ip_address`, `revoked_at`) | Supports remote session revocation |
| `audit_logs` | Immutable security & CRUD trail with `before_json` / `after_json` diffs | **Range-partitioned by `created_at` (monthly) in PostgreSQL** |

### Domain B — Workspaces, Dashboards, Widgets, Filters & Themes (`dashboard.py`)
| Table | Purpose |
|---|---|
| `themes` | 8 built-in design systems + custom theme JSON tokens |
| `workspaces` | Logical BI folders / tenant workspaces |
| `dashboards` | Dashboard metadata, `theme_id`, `is_public`, `is_template`, `embed_token` |
| `dashboard_pages` | Multi-page tabs (`page_order`, `is_drill_through`, `layout_settings_json`) |
| `widgets` | `chart_type` (30 types), `layout_x/y/w/h`, `config_json` (style/viz/interactions/advanced) |
| `widget_queries` | `dataset_id`, `query_mode` (`visual`/`sql`), `sql_text`, `visual_spec_json`, `parameters_json` |
| `widget_drill_rules` | Drill hierarchy levels, per-level `chart_type` overrides, `drill_through_page_id` |
| `dashboard_filters` & `widget_filters` | Global/Page/Widget filters with `parent_filter_id` (cascading), `target_widget_ids`, `excluded_widget_ids`, `is_locked` |
| `dashboard_user_states` | Persisted user personal filter/page/drill states across refreshes |
| `dashboard_versions` & `dashboard_comments` | Snapshot versioning and collaborative comments |

### Domain C — Data Connectivity, Semantic Layer & Lineage (`data.py`)
| Table | Purpose |
|---|---|
| `data_sources` & `data_source_credentials` | DB/API/File connections + Fernet-encrypted credential payloads |
| `datasets` & `dataset_fields` | Warehouse table mapping, `data_version`, `freshness_status`, semantic roles & calculated field formulas |
| `dataset_relationships` | Join cardinality (`many_to_one`, `one_to_many`, `one_to_one`) & join keys |
| `semantic_metrics` | Shared KPI definitions (`SUM(profit)/SUM(revenue)`) |
| `saved_queries` & `query_history` | SQL/Visual Editor persisted queries & execution plans |
| `data_lineage` | Upstream `data_source` -> `dataset` -> downstream `dashboard`/`widget` graph |

### Domain D — Automated ETL & Scheduling (`etl.py`)
| Table | Purpose |
|---|---|
| `etl_pipelines` & `etl_pipeline_steps` | Extract mode (`full`, `incremental`, `cdc_hash`), watermark column, PK, retry backoff policy, `is_running` lock |
| `etl_schedules` | `interval`, `daily`, `weekly`, `monthly`, `cron`, timezone, allowed window, `depends_on_pipeline_id` |
| `etl_checkpoints` | Persisted cursor & row hash maps for zero-duplication incremental runs |
| `etl_job_runs`, `etl_errors`, `etl_metrics` | Full run logs, row counts (`extracted`, `inserted`, `updated`, `unchanged`), error tracking |

### Domain E — Performance, Cache & Materialized Views (`performance.py`)
| Table | Purpose |
|---|---|
| `materialized_view_metadata` | Pre-aggregated summary tables (`mv_sales_monthly`) & freshness metadata |
| `cache_entries` | Cache registry & TTL tracking |
| `widget_load_logs` & `query_execution_logs` | Per-widget/query latency telemetry (**Range-partitioned by `created_at` in PostgreSQL**) |
| `scheduled_reports` | Cron schedules for automated PDF/XLSX/CSV distribution |

---

## 2. Partitioning & Composite Indexing Strategy

In `backend/migrations/postgres_schema.sql`:
- `audit_logs`, `widget_load_logs`, and `query_execution_logs` are defined as PostgreSQL `PARTITION BY RANGE (created_at)` tables with monthly partition creation so multi-million-row operational audit & telemetry histories stay fast without degrading transactional lookup speed.
- Covering/composite indexes cover:
  - `idx_rp_lookup (organization_id, subject_type, subject_id, resource_type, resource_id, action)`
  - `idx_audit_org_time (organization_id, created_at DESC)`
  - `idx_widget_page_order (dashboard_id, page_id)`
  - `idx_query_exec_slow (execution_ms DESC, created_at DESC)`
