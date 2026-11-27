# Changelog & Release Notes — NexusBI Enterprise

All notable releases of `aftabcollege-gif/Dashboard` (`NexusBI Enterprise`) follow **Semantic Versioning (`MAJOR.MINOR.PATCH`)**.
Version numbers are synchronized across `backend/app/version.py`, `backend/app/main.py`, `frontend/package.json`, `frontend/src/lib/version.ts`, `winsetup/installer/NexusBI.iss`, `/api/v1/about`, and `/settings` (درباره سامانه).

---

## [1.2.0] - 2026-11-16

### Added & Upgraded
- **Versioning & Continuity (16.1):** Unified Semantic Versioning across backend, frontend, Windows installer (`NexusBI.iss`), `/api/v1/about`, and `/settings` About & Release Notes tab.
- **Persian User & Access Management (16.2–16.4):** Full RTL Persian UI in `/admin/security` with user/role/permission search & filters, effective-permission viewer, role editor with Persian descriptions, before/after diff confirmation modal with audit reason capture, audit history viewer, and backend protection preventing deactivation/deletion/demotion of the last active administrator.
- **Large Excel & Analytics (Section 2 & 10):** Multi-sheet inspection & batch import, explicit column selection, explicit row validity accounting (`total_rows`, `processed_rows`, `valid_rows`, `invalid_rows`, `rejected_reasons`), DuckDB SQL Outlier detection (IQR/Z-score), Pearson Correlation matrix, duplicate/incomplete profiling, period comparison, and real-progress asynchronous background jobs (`/api/v1/jobs`).
- **BPMN Engine & Process Intelligence (Sections 3 & 4):** Safe AST-validated Custom Bottleneck Formula Studio (13 metrics, arithmetic/AND/OR, warning/critical thresholds, diagnostic explanation), 11-metric analytical hover tooltips, dynamic 20+ field task drill-down with sorting/filtering/per-request Timeline/Excel export, Edit-mode Drag & Drop node reordering & layout persistence, customizable BPMN display/tooltip schema, congested cartables, imbalanced workload, delay/backlog forecasts with sample bounds, and draggable Process Intelligence cards.
- **Shared Data Source Layer (Section 5):** Cross-dashboard composite dataset builder directly from database connections (multi-table/view joins, column selection, scheduled refresh, health & error status).
- **Security Operations Tier 1 & Tier 2 (Section 6):** Configurable Persian/English/mixed-text NLP & regex status rules, 15 threat classifications + custom admin rules, asset/software version CVE & CVSS monitoring (SSRF-pinned NVD sync + local catalog + `needs_review` for missing versions), and 7-state Tier 1/Tier 2 Incident Workflow (`New → Assigned → Investigating → Escalated → Contained → Resolved → Closed`) with MTTA/MTTR and strictly advisory guardrails.
- **KPI Reliability & Widget Editor Redesign (Sections 7 & 8):** Explicit KPI formula/source/row-count/last-calculated/validity metadata & error states, Custom KPI Studio (target, thresholds, previous-period comparison, drill-down), Persian Widget Properties Editor with helper tooltips, topic grouping, collapsible Advanced, settings search, Reset to Defaults, named multi-layouts, widget show/hide, and full dashboard duplication.
- **Unified Recommendations, Smart Q&A & Action Management (Sections 9 & 13):** 9-field cross-domain recommendations separating data-grounded evidence from hypotheses, cross-domain Q&A assistant returning charts + source records, and unified action item tracking.

### Database Migrations & Backward Compatibility
- **Migration:** `0007_enterprise_platform_v12` (strictly additive; no destructive column drops or data deletion).
- **Upgrade Path from 1.1.0:** Run `alembic upgrade head`. All existing `1.0.0`/`1.1.0` dashboards, widgets, datasets, users, roles, and policies remain intact.
- **Rollback Path:** Run `alembic downgrade 0006_process_security_intel` cleanly without disturbing `1.1.0` tables.
- **Breaking Changes:** None.

---

## [1.1.0] - 2026-11-25
- 100%-row DuckDB SQL-first multi-process BPMN analytics & cartable drill-down (`400k+` row Excel scale).
- Stage 2–4 Security Hardening: AST SQL guards (`sqlglot`), pre-SQL RLS/CLS/masking, SSRF IP pinning, refresh token rotation, atomic rate limiting, Alembic migrations (`0001`–`0006`), and Windows Desktop Gateway E2E verification.
- Phase 1–4 Process Intelligence, Security Log Intelligence, and Cross-Domain Correlation engines.

## [1.0.0] - 2026-11-16
- Initial release: FastAPI + DuckDB Analytical Warehouse + PostgreSQL/SQLite metadata + Next.js + ECharts 30+ visualizations + RBAC & Active Directory sync.
