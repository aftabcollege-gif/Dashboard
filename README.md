# Enterprise Intelligence Dashboard Platform (AI-Powered BI & Dynamic Dashboard Builder)

Production-grade, decoupled Business Intelligence and Dynamic Dashboard Builder built with **FastAPI**, **DuckDB OLAP Warehouse**, **PostgreSQL / SQLite Metadata DB**, **Redis / Smart Cache**, **Next.js 14**, **Apache ECharts**, and **Active Directory / Enterprise RBAC**.

---

## Live Architecture Overview (Three-Layer Separation)

1. **Layer 1 — Application Metadata DB (`backend/app/models/`, `backend/migrations/postgres_schema.sql`)**:
   - SQLAlchemy 2.x supporting **PostgreSQL 16** (with range-partitioned `audit_logs`, `widget_load_logs`, `query_execution_logs`) and zero-config local **SQLite WAL**.
   - Stores Users, Roles, Groups, Resource Permissions, AD Connections, RLS/CLS Policies, Workspaces, Dashboards, Pages, Widgets, Filters, Themes, ETL Definitions, and Telemetry.
2. **Layer 2 — Independent Analytical Data Warehouse (`backend/app/engines/warehouse.py`)**:
   - Powered by embedded **DuckDB** columnar OLAP engine.
   - Completely isolated from operational sources and application UI; updated exclusively via decoupled ETL pipelines and file ingestion with atomic staging-to-production table swaps.
3. **Layer 3 — Smart Result Cache & Pre-Aggregation (`backend/app/core/cache.py`)**:
   - Dual-backend **Redis + LRU/Disk Cache** keyed strictly by `(tenant_id, dashboard_id, widget_id, dataset_id, data_version, query_hash, filter_state_hash, permission_context_hash)` so users with differing RLS/RBAC profiles never leak cached query results.

---

## Core Capabilities Implemented

- **Data Connectivity (`backend/app/engines/connectors.py`)**: PostgreSQL, MSSQL, MySQL, MariaDB, Oracle, SQLite, MongoDB, REST API, GraphQL API (Fernet-encrypted credentials, read-only enforcement, schema/PK/FK inspection) + Drag & Drop **Excel (XLSX/XLS), CSV, JSON, XML** ingestion with sheet selection, null handling, deduplication, and type inference.
- **Data Modeling & Formulas (`backend/app/engines/data_engine.py`)**: Joins, Unions, Aggregations, Calculated Columns, Semantic Metrics, Time Intelligence (`SUM`, `AVG`, `COUNT`, `COUNT_DISTINCT`, `MIN`, `MAX`, `MEDIAN`, `RANK`, `PERCENTAGE`, `YOY`, `MOM`, `YTD`, `MTD`, `RUNNING_TOTAL`).
- **Dynamic Dashboard Builder (`frontend/src/components/dashboard/DashboardWorkspace.tsx`)**: Multi-page dashboards, responsive `react-grid-layout` drag & drop + resize, Undo/Redo, Copy/Paste/Duplicate, Edit vs View modes, persistent 6-tab Properties Panel, saved personal dashboard states, versioning, comments, and public embed links.
- **30 Interactive Visualizations (`frontend/src/components/charts/WidgetRenderer.tsx`)**: Bar, Horizontal Bar, Stacked Bar, 100% Stacked Bar, Line, Area, Stacked Area, Pie, Donut, Scatter, Bubble, Radar, Treemap, Sunburst, Heatmap, Waterfall, Funnel, Gauge, KPI Card, Progress Bar, Table, Pivot Table, Matrix, Calendar Heatmap, Histogram, Box Plot, Candlestick, Geographic Map, Sankey Diagram, and Combo Chart — with runtime chart-type switching preserving dataset, mappings, colors, and filters.
- **Smart Drill Down, Cross-Filtering & 28 Filter Operators (`backend/app/engines/drill_engine.py`, `filter_engine.py`)**: Multi-level hierarchies with clickable breadcrumbs, per-level auto chart-type switching, drill-through pages, cascading Global/Page/Widget filters, and cross-filtering.
- **Query Builder & SQL Editor (`frontend/src/app/query-builder/page.tsx`)**: Dual Visual No-Code Builder + CodeMirror SQL Editor with formatting, `:param` binding, DuckDB `EXPLAIN` plan inspection, query history, saved queries, and 1-click **Convert to Widget**.
- **Automated ETL & Scheduler (`backend/app/engines/etl_engine.py`, `scheduler.py`)**: Full, Incremental Watermark, and CDC SHA-256 Hash change detection, APScheduler cron/interval scheduling, concurrency locks, exponential backoff retries, offline resilience, and materialized summary views.
- **Enterprise Identity, AD & Granular Security (`backend/app/core/permissions.py`, `rls_cls.py`, `engines/ad_ldap.py`)**: Local + Active Directory (`ldap3` / `mock_ad`, zero AD password persistence), TOTP MFA, 12-scope Hierarchical RBAC (Default Deny, Explicit Deny overrides Allow), Role/Group/User Permission Matrix, server-side Row-Level Security, Column-Level Security, Data Masking, and exportable Audit Logs.
- **8 Built-In Themes + Bilingual EN/FA RTL Support (`frontend/src/lib/i18n.ts`, `context.tsx`)**: Minimal, Liquid, Glassmorphism, Dark Enterprise, Light Enterprise, Neon, Corporate, Material Design + custom theme editor + full RTL Persian (`Vazirmatn`) UI.
- **AI Analytics & Exports (`backend/app/engines/ai_engine.py`, `export_engine.py`)**: AI auto-dashboard builder, EN/FA NLQ-to-SQL, statistical anomaly detection & linear forecasting, PDF/Excel/CSV/PNG exports, and scheduled reports.

---

## Quick Start (Local Development)

### Backend (FastAPI + DuckDB + APScheduler)
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Frontend (Next.js 14)
```bash
cd frontend
npm install
npm run build
npm run start
```

Open `http://localhost:3000`.

### Seeded Demo Accounts
| Account | Password | Auth Mode | Role / Behavior |
|---|---|---|---|
| `admin` | `Admin!2026` | Local | Super Admin (full access) |
| `designer` | `Designer!2026` | Local | Dashboard Designer |
| `analyst` | `Analyst!2026` | Local | Data Analyst (CLS masked emails) |
| `viewer` | `Viewer!2026` | Local | Report Viewer (read-only) |
| `north_mgr` | `Manager!2026` | Local | Regional Manager — **RLS locked to North** |
| `south_mgr` | `Manager!2026` | Local | Regional Manager — **RLS locked to South** |
| `sara.ahmadi` | `DomainUser!123` | Active Directory (`corp.enterprise.local`) | Sales Analyst (AD synced) |
| `ali.rezaei` | `DomainUser!123` | Active Directory (`corp.enterprise.local`) | Finance BI Lead (AD synced) |

---

## Complete Deliverable Index (`docs/`)

| # | Deliverable | Path |
|---|---|---|
| 1 | Complete Source Code | `backend/` & `frontend/` |
| 2 | Database Schema | [`docs/02_database_schema.md`](docs/02_database_schema.md) & [`backend/migrations/postgres_schema.sql`](backend/migrations/postgres_schema.sql) |
| 3 | Entity Relationship Diagram (ERD) | [`docs/03_erd_diagram.md`](docs/03_erd_diagram.md) |
| 4 | Permission Matrix Documentation | [`docs/04_permission_matrix.md`](docs/04_permission_matrix.md) |
| 5 | Active Directory Integration Guide | [`docs/05_ad_integration_guide.md`](docs/05_ad_integration_guide.md) |
| 6 | ETL Architecture Documentation | [`docs/06_etl_architecture.md`](docs/06_etl_architecture.md) |
| 7 | Data Warehouse Schema Documentation | [`docs/07_data_warehouse_schema.md`](docs/07_data_warehouse_schema.md) |
| 8 | Performance Benchmark Report | [`docs/08_performance_benchmark_report.md`](docs/08_performance_benchmark_report.md) |
| 9 | Security Test Report | [`docs/09_security_test_report.md`](docs/09_security_test_report.md) |
| 10 | Deployment Guide | [`docs/10_deployment_guide.md`](docs/10_deployment_guide.md) |
| 11 | API Documentation | [`docs/11_api_documentation.md`](docs/11_api_documentation.md) |
| 12 | User Manual | [`docs/12_user_manual.md`](docs/12_user_manual.md) |
