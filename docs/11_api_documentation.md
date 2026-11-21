# Deliverable 11 — REST API Documentation

Base Prefix: `/api/v1` (Interactive Swagger/OpenAPI spec available live at `GET /docs` and `GET /openapi.json` on the backend server).

All authenticated endpoints require header: `Authorization: Bearer <access_token>`.

---

## 1. Authentication & MFA (`/api/v1/auth`)
| Method | Path | Description |
|---|---|---|
| `POST` | `/auth/login` | Authenticate via `local` or `active_directory` (`ad`), optional `mfa_code` |
| `POST` | `/auth/refresh` | Exchange refresh token for new access token |
| `POST` | `/auth/logout` | Revoke current JWT session |
| `GET` | `/auth/me` | Return authenticated profile, roles, groups & effective permissions |
| `POST` | `/auth/change-password` | Update local user password |
| `POST` | `/auth/mfa/setup` | Generate TOTP secret & `otpauth://` provisioning URI |
| `POST` | `/auth/mfa/enable` | Verify 6-digit TOTP code and enable MFA |
| `POST` | `/auth/mfa/disable` | Disable MFA |

## 2. Enterprise Security, RBAC, Active Directory & RLS/CLS (`/api/v1/rbac`)
| Method | Path | Description |
|---|---|---|
| `GET/POST` | `/rbac/users` | List users / create local user |
| `PUT/DELETE` | `/rbac/users/{user_id}` | Update user status/roles / delete user |
| `GET/POST` | `/rbac/roles` & `/rbac/roles/{id}/clone` | List, create, clone roles |
| `GET/POST/PUT` | `/rbac/groups` & `/rbac/groups/{id}` | Manage groups & memberships |
| `GET` | `/rbac/permissions/matrix` | Retrieve complete Role/Group/User × Dashboard/Widget ACL matrix |
| `PUT` | `/rbac/permissions/matrix/set` | Grant or explicitly Deny permission (`view/edit/add/delete/drill_down`) + clear cache |
| `POST` | `/rbac/permissions/matrix/copy` | Copy permissions from one subject to another |
| `GET` | `/rbac/permissions/effective/{user_id}` | Compute effective permissions for any user |
| `GET/POST/DELETE` | `/rbac/policies` & `/rbac/policies/{id}` | Manage Row-Level Security (`rls`) & Column-Level Security/Masking (`cls`, `masking`) |
| `GET/POST` | `/rbac/ad/connections` | List / create AD/LDAP/LDAPS connection |
| `POST` | `/rbac/ad/connections/{id}/test` | Test LDAP/LDAPS bind & reachability |
| `GET` | `/rbac/ad/connections/{id}/search?q=` | Search live AD users & security groups |
| `POST` | `/rbac/ad/connections/{id}/sync` | Execute AD sync, group-to-role mapping & disabled account deactivation |

## 3. Data Sources & File Ingestion (`/api/v1/data-sources`)
| Method | Path | Description |
|---|---|---|
| `GET` | `/data-sources/supported-types` | List supported DB/API (`postgresql`, `mssql`, `mysql`, `mariadb`, `oracle`, `sqlite`, `mongodb`, `rest_api`, `graphql`) & file types |
| `GET/POST/DELETE` | `/data-sources` | CRUD encrypted data source connections |
| `POST` | `/data-sources/{id}/test` | Live connection test |
| `GET` | `/data-sources/{id}/schema` | Inspect tables, views, columns, data types, PKs |
| `POST` | `/data-sources/{id}/query` | Execute direct read-only query against source |
| `POST` | `/data-sources/files/preview` | Upload `.xlsx/.xls/.csv/.json/.xml`, select sheet, inspect nulls/dupes & preview |
| `POST` | `/data-sources/files/import` | Load parsed file directly into Layer 2 DuckDB Analytical Warehouse |

## 4. Datasets, Semantic Layer & Lineage (`/api/v1/datasets`)
| Method | Path | Description |
|---|---|---|
| `GET` | `/datasets` | List analytical warehouse datasets |
| `GET` | `/datasets/{id}/preview` | Server-side RLS/CLS enforced data preview |
| `GET` | `/datasets/{id}/profile` | Column min/max/avg/median/distinct/null statistics |
| `GET` | `/datasets/{id}/lineage` | Upstream source -> dataset -> downstream dashboards/widgets |
| `POST` | `/datasets/{id}/filter-options` | Cascading distinct values with parent filters & RLS enforced |
| `POST` | `/datasets/{id}/calculated-fields` | Create calculated column/expression |
| `POST` | `/datasets/{id}/transformations` | Apply server-side transformation (cast, filter, rename, group) |
| `GET/POST` | `/datasets/relationships` & `/datasets/semantic-metrics` | Manage join models & shared KPI metrics |

## 5. Query Builder & SQL Editor (`/api/v1/queries`)
| Method | Path | Description |
|---|---|---|
| `POST` | `/queries/execute-sql` | Execute read-only SQL with `:param` substitution, RLS/CLS, & `EXPLAIN` plan |
| `POST` | `/queries/execute-visual` | Execute no-code visual aggregation spec |
| `POST` | `/queries/format-sql` | Format SQL using `sqlparse` |
| `GET/POST` | `/queries/saved` & `/queries/history` | Saved query repository & execution telemetry |
| `POST` | `/queries/convert-to-widget` | Convert SQL/Visual query straight into a live dashboard widget |

## 6. Dashboards, Widgets, Filters & Drill Engine (`/api/v1/dashboards`)
| Method | Path | Description |
|---|---|---|
| `GET/POST/PUT/DELETE` | `/dashboards` & `/dashboards/{id}` | Manage dashboards (RBAC filtered) |
| `GET` | `/dashboards/public/{embed_token}` | Unauthenticated read-only embed view for shared dashboards |
| `POST` | `/dashboards/{id}/execute-batch` | Progressive parallel execution of all page widgets with Global/Cross/Drill filters & cache |
| `POST` | `/dashboards/widgets/{id}/data` | Execute single widget (supports drill stack & cache bypass) |
| `POST` | `/dashboards/widgets/{id}/switch-chart` | Convert between all 30 chart types preserving dataset, styling & filters |
| `PUT` | `/dashboards/{id}/layout` | Persist responsive grid positions (`x, y, w, h`) |
| `POST/PUT/DELETE` | `/dashboards/{id}/widgets` & `/dashboards/widgets/{id}` | Widget CRUD & `/duplicate` |
| `POST/DELETE` | `/dashboards/{id}/filters` & `/dashboards/widgets/{id}/filters` | Global, Page & Widget-isolated filters |
| `GET/PUT` | `/dashboards/{id}/state` | Persist & restore personal filter/page/drill state |
| `GET/POST` | `/dashboards/{id}/versions`, `/comments`, `/share`, `/clone-template` | Versioning, collaboration, embedding & templates |

## 7. Automated ETL, Themes, AI, Exports & Monitoring
- **ETL (`/api/v1/etl`)**: `GET /etl/monitor`, `POST /etl/pipelines`, `POST /etl/pipelines/{id}/run`, `PUT /etl/pipelines/{id}/schedule`.
- **Themes (`/api/v1/themes`)**: `GET /themes`, `POST /themes`, `GET /themes/{id}/export`.
- **AI (`/api/v1/ai`)**: `POST /ai/generate-dashboard`, `POST /ai/nlq`, `POST /ai/insights`, `GET /ai/recommend/{dataset_id}`.
- **Exports (`/api/v1/exports`)**: `GET /exports/dashboards/{id}/{pdf|xlsx|csv}`, `GET /exports/widgets/{id}/{pdf|xlsx|csv}`, `GET/POST /exports/scheduled-reports`.
- **Monitoring (`/api/v1/monitoring`)**: `GET /monitoring/audit-logs`, `GET /monitoring/audit-logs/export`, `GET /monitoring/performance`, `POST /monitoring/cache/invalidate`.
