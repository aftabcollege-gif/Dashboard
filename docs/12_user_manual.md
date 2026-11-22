# Deliverable 12 — Enterprise BI Platform User Manual

Welcome to the **Enterprise Intelligence BI Platform**. This guide covers daily workflows for Analysts, Dashboard Designers, Executives, and Administrators in both **English (LTR)** and **Persian / فارسی (RTL)**.

---

## 1. Signing In & Language Selection

1. Open the portal (`http://localhost:3000`).
2. Use the top-right toggle (`فارسی` / `EN`) at any time to switch interface language and RTL/LTR layout direction.
3. Choose **Local Account** or **Active Directory** (`corp.enterprise.local`), enter your credentials (or click a quick-demo profile on test environments), and enter your 6-digit **TOTP MFA** code if enabled.

---

## 2. Navigating Dashboards, Filtering & Drilling Down

1. Open **Dashboards** and select a dashboard (e.g., *Enterprise Revenue & Executive Intelligence*).
2. **Global & Page Filters**: Use the filter bar at the top to slice across **Region**, **Category**, **Date Range**, **Text Search**, or **Top N**. Selecting a parent filter automatically cascades child dropdown values. Click **Reset Filters** anytime to restore defaults.
3. **Cross-Filtering**: Click any bar, slice, or point inside a chart to cross-filter connected widgets on the page. Click an active pill in the filter bar to clear it.
4. **Smart Drill Down & Breadcrumbs**:
   - Click on hierarchical charts (e.g., `Year → Quarter → Month` or `Category → Product`) to drill into finer detail.
   - Watch the widget automatically switch chart types if per-level rules are configured (e.g., Bar at Year level → Line at Month level).
   - Use the clickable **Breadcrumbs** or the **Drill Up (`↑`)** arrow in the widget header to step back up.
5. **Save Personal State**: Click the bookmark icon (`Save My State`) in the toolbar to remember your active page, filter selections, and drill path across refreshes.

---

## 3. Building & Customizing Dashboards (Edit Mode)

1. Click **Edit Dashboard** in the top toolbar (requires `dashboard:edit` permission).
2. **Drag, Drop & Resize**: Drag widget headers to rearrange rows/columns or resize corners on the 12-column responsive grid. Use **Undo (`↶`) / Redo (`↷`)**, **Copy / Duplicate**, and click **Save Layout**.
3. **Add Widget**: Click **+ Add Widget**, pick any of the **30 supported visualizations** (Bar, Horizontal Bar, Stacked, Line, Area, Pie, Donut, Scatter, Bubble, Radar, Treemap, Sunburst, Heatmap, Waterfall, Funnel, Gauge, KPI Card, Progress Bar, Table, Pivot Table, Matrix, Calendar Heatmap, Histogram, Box Plot, Candlestick, Geographic Map, Sankey, Combo), choose a dataset, and map dimensions/measures.
4. **6-Tab Widget Properties Panel**: Click any widget in Edit Mode to open its right-side inspector:
   - **Data**: Switch dataset, Visual vs SQL mode, X/Series/Y/Secondary Y fields, or Time Intelligence / Window formulas (`SUM`, `AVG`, `MEDIAN`, `YOY`, `MOM`, `YTD`, `MTD`, `RUNNING_TOTAL`).
   - **Visualization**: Click any of the 30 chart types to convert the chart **live** while keeping its dataset, colors, and filters intact.
   - **Style**: Customize background/text colors, fonts, line thickness, series palette, number format (`Currency`, `Percentage`, `Decimals`, `K/M abbreviation`), border radius, legend, tooltip, and data labels.
   - **Filters**: Add widget-isolated filters that only apply to that specific widget.
   - **Interactions & Drill**: Configure Drill hierarchies, per-level chart switching rules, Cross-Filter toggle, and Drill-Through target pages.
   - **Advanced**: Set KPI target/previous comparison values and export single-widget CSV/Excel/PDF files.
5. **Themes**: Pick from **8 built-in themes** (*Minimal*, *Liquid*, *Glassmorphism*, *Dark Enterprise*, *Light Enterprise*, *Neon*, *Corporate*, *Material Design*) or design custom themes under **Settings**.

---

## 4. Uploading Excel/CSV Files & Connecting Databases

1. Navigate to **Data Sources**.
2. **Drag & Drop File Upload**: Drop `.xlsx`, `.xls`, `.csv`, `.json`, or `.xml` files directly into the dropzone. Select the Excel worksheet, choose Null handling (`Keep`, `Drop Rows`, `Fill Zero`, `Fill Mean`), toggle duplicate removal, inspect auto-inferred column types, and click **Import**. The file loads immediately into the DuckDB Analytical Warehouse.
3. **Database & API Connections**: Add PostgreSQL, SQL Server, MySQL, MariaDB, Oracle, SQLite, MongoDB, REST, or GraphQL endpoints. Click **Test** and **Schema & Query** to inspect tables, views, primary keys, and run direct read-only queries.

---

## 5. Visual Query Builder, SQL Editor & AI Copilot

1. Open **Query Builder**:
   - **Visual No-Code**: Pick dimensions, measures, aggregations, and row limit.
   - **SQL Editor**: Write DuckDB SQL with syntax highlighting, `:param` bindings, **Format SQL**, and **Explain Plan**.
   - Click **Convert to Widget** to publish your query directly onto any dashboard!
2. **AI Analytics Copilot**: Click **AI** inside any dashboard to ask natural-language questions in English or Persian (e.g., *"total revenue by region"* / *"مجموع فروش بر اساس منطقه"*), or run statistical **Anomaly Detection & Forecasting** on any chart.

---

## 6. Automated ETL, Exports & Administration

- **ETL Pipelines (`/etl`)**: Configure Full, Incremental Watermark, or CDC Hash pipelines with minute/hour/daily/cron schedules, inspect materialized views, or trigger manual **Run Now**.
- **Exports & Scheduled Reports (`/reports`)**: Export PDF, Excel, CSV, or PNG directly from any dashboard toolbar, clone templates, or schedule automated recurring reports.
- **Security & Audit (`/admin/security`, `/admin/monitoring`)**: Administrators manage Local/AD users, toggle the Role × Dashboard Permission Matrix, bind AD groups to BI roles, define Row/Column-Level Security policies, monitor cache/query performance, and export immutable audit logs.
