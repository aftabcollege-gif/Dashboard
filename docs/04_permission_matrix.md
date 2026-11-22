# Deliverable 04 — Enterprise Permission Matrix & Access Control Model

Implemented in `backend/app/core/permissions.py`, `backend/app/core/rls_cls.py`, and `backend/app/api/users_rbac.py`.

---

## 1. Core Security Invariants

1. **Default Deny**: If no matching `allow` rule exists for a subject (and user is not `super_admin`), access is strictly denied (`HTTP 403`).
2. **Explicit Deny Overrides Allow**: An explicit `deny` rule at **User**, **Group**, or **Role** level always overrides any `allow` rule anywhere in the inheritance hierarchy.
3. **Precedence Order**: `User Direct Rule` > `Group Rule` > `Role Rule` (with `Explicit Deny` short-circuiting immediately).
4. **Hierarchical Inheritance (`inherit_to_children`)**:
   - Workspace permissions can cascade to Dashboards, Pages, and Widgets when `inherit_to_children = True`.
   - Parent denial blocks child access unless overridden carefully; widget-level isolation rules enforce fine-grained `view`, `edit`, `delete`, and `drill_down` checks independently.
5. **Server-Side Enforcement & Cache Invalidation**:
   - All endpoints check `PermissionResolver` on the server — tampering with UI state or calling `/api/v1/dashboards/{id}/execute-batch` or `/api/v1/dashboards/widgets/{id}/data` directly returns `HTTP 403` for unauthorized dashboards/widgets or disables drill-down transitions.
   - Any RBAC/Group/Policy mutation immediately calls `bump_org_versions()` and `cache_manager.invalidate_all()`, ensuring revoked privileges take effect on the very next HTTP call.

---

## 2. The 12 Permission Scopes & Actions

| Resource Scope | Supported Actions |
|---|---|
| `dashboard` | `view`, `edit`, `add`, `delete`, `export`, `share`, `add_widget`, `change_theme`, `drill_down` |
| `widget` | `view`, `edit`, `add`, `delete`, `drill_down`, `export` |
| `dataset` | `view`, `edit`, `add`, `delete`, `refresh` |
| `data_source` | `view`, `edit`, `add`, `delete`, `execute_query` |
| `query` | `view`, `edit`, `add`, `delete`, `execute_query` |
| `etl` | `view`, `edit`, `add`, `delete`, `refresh` |
| `theme` | `view`, `edit`, `add`, `delete` |
| `report` | `view`, `edit`, `add`, `delete`, `export` |
| `filter` | `view`, `edit`, `add`, `delete` |
| `drill` | `view`, `drill_down` |
| `workspace` | `view`, `edit`, `add`, `delete` |
| `admin` | `view`, `edit`, `add`, `delete` (User/Role/AD/Audit/Perf administration) |

---

## 3. Built-In Seeded Role Matrix

| Role | Dashboards (View/Edit/Add/Del) | Widgets & Drill | Data Sources & SQL | Datasets & ETL | Security Admin |
|---|---|---|---|---|---|
| `super_admin` | Full (`*`) | Full (`*`) | Full (`*`) | Full (`*`) | Full (`*`) |
| `dashboard_designer` | View, Edit, Add, Export, Theme | View, Edit, Add, Drill | View, Execute Query | View, Refresh | Denied |
| `data_analyst` | View, Export | View, Drill | View, Execute Query | View, Add, Edit | Denied |
| `report_viewer` | View, Export | View Only (Drill Denied if restricted) | Denied | Denied | Denied |
| `executive_viewer` | View, Export | View, Drill | Denied | Denied | Denied |
| `regional_manager` | View, Export | View, Drill (**RLS Region Locked**) | Denied | Denied | Denied |
| `custom_scoped` | **Isolated Dashboard 1 View Only** | Isolated | Denied | Denied | Denied |

---

## 4. Row-Level Security (RLS) & Column-Level Security (CLS)

Evaluated dynamically inside `backend/app/core/rls_cls.py` (`SecurityQueryCompiler`) before any query hits DuckDB:
- **RLS**: Compiles user profile tokens such as `${user.region}` and `${user.department}` into parameterized SQL `WHERE` predicates wrapped around the dataset query (cannot be bypassed by custom SQL filters or cached results).
- **CLS & Masking**:
  - `cls` (`deny`): Removes restricted columns from query results completely (or replaces with `"[RESTRICTED]"`).
  - `masking`: Applies deterministic transformations (`partial`: `a***@domain.com`, `redact`: `***`, `hash`: SHA-256 prefix) on sensitive attributes such as employee email or salary.
