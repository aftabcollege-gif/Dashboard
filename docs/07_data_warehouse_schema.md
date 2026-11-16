# Deliverable 07 — Analytical Data Warehouse Schema Documentation

Implemented in `backend/app/engines/warehouse.py` and seeded via `backend/app/seed/bootstrap.py`.

---

## 1. Warehouse Engine Design

- **Storage Engine**: Embedded **DuckDB** (`backend/data/warehouse.duckdb`), offering vectorized columnar execution, parallel aggregation, full SQL window functions (`OVER`, `PARTITION BY`, `DATE_TRUNC`), and zero external JVM/cluster overhead.
- **Schema Styles Supported**: Star Schema (Fact + Dimension tables), Snowflake Schema, and Flat Analytical Tables (including auto-ingested Excel/CSV/JSON/XML datasets prefixed with `wh_file_*`).

---

## 2. Seeded Enterprise Warehouse Tables

### `wh_enterprise_sales` (Fact Table — 1,200 rows)
| Column | DuckDB Type | Semantic Role | Description |
|---|---|---|---|
| `order_id` | `BIGINT` | Dimension (PK) | Unique order identifier |
| `order_code` | `VARCHAR` | Dimension | Order reference (`ORD-10001` .. `ORD-11200`) |
| `order_date` | `VARCHAR/DATE` | Time Dimension | Order timestamp |
| `year` | `BIGINT` | Time Hierarchy Lvl 0 | Fiscal Year (2024–2026) |
| `quarter` | `VARCHAR` | Time Hierarchy Lvl 1 | Quarter (`Q1`–`Q4`) |
| `month` | `VARCHAR` | Time Hierarchy Lvl 2 | Month (`2024-01` .. `2026-10`) |
| `region` | `VARCHAR` | RLS Dimension | `North`, `South`, `East`, `West` |
| `country` | `VARCHAR` | Geo Dimension | Country name (for Geographic Map) |
| `category` | `VARCHAR` | Product Hierarchy Lvl 0 | `Cloud`, `Security`, `Analytics`, `Enterprise`, `Hardware` |
| `product_name` | `VARCHAR` | Product Hierarchy Lvl 1 | Specific SKU name |
| `sales_channel` | `VARCHAR` | Dimension | `Direct`, `Partner`, `Online`, `Enterprise` |
| `quantity` | `BIGINT` | Measure | Units sold |
| `revenue` | `DOUBLE` | Measure | Gross revenue USD |
| `cost` | `DOUBLE` | Measure | COGS USD |
| `profit` | `DOUBLE` | Measure | Net profit USD |
| `updated_at` | `VARCHAR` | ETL Watermark | Incremental sync cursor |

### `wh_enterprise_expenses` (Fact Table — 450 rows)
| Column | DuckDB Type | Semantic Role |
|---|---|---|
| `expense_id` | `BIGINT` | PK |
| `voucher_no` | `VARCHAR` | Dimension |
| `expense_date`, `month` | `VARCHAR` | Time Dimensions |
| `department` | `VARCHAR` | RLS/Group Dimension (`Engineering`, `Sales`, `Marketing`, `Operations`, `HR`, `Finance`) |
| `cost_center`, `expense_type`, `approval_status` | `VARCHAR` | Category Dimensions |
| `budget_amount`, `actual_amount`, `variance` | `DOUBLE` | Financial Measures |

### `wh_hr_workforce` (Dimension Table — 8 rows, CLS-Protected)
| Column | DuckDB Type | Security Note |
|---|---|---|
| `employee_id`, `full_name`, `department`, `region`, `role_title` | `BIGINT / VARCHAR` | Workforce attributes |
| `email` | `VARCHAR` | **Protected by CLS Partial Masking Policy** (`s***@enterprise.local` for non-admins) |
| `base_salary` | `DOUBLE` | Compensation measure |

---

## 3. Pre-Aggregated Materialized Summary Views

### `mv_sales_monthly` (549 rows)
Pre-aggregated monthly summary built automatically after ETL loads (`year`, `quarter`, `month`, `region`, `category`, `total_revenue`, `total_profit`, `total_cost`, `order_count`) enabling sub-5ms dashboard chart rendering for executive time-series and regional breakdowns.
