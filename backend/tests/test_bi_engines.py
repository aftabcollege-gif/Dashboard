import io
import pandas as pd
from app.engines.data_engine import FormulaCompiler


def test_formula_compiler_and_bi_engines(client, admin_headers):
    # 1. Formula compiler functions (Section 3)
    for expr in [
        "SUM(revenue)", "AVG(revenue)", "COUNT(*)", "COUNT DISTINCT(order_id)",
        "MIN(cost)", "MAX(revenue)", "MEDIAN(profit)", "RANK(revenue)",
        "PERCENTAGE(revenue)", "YOY(revenue)", "MOM(revenue)",
        "YTD(revenue)", "MTD(revenue)", "RUNNING_TOTAL(revenue)",
    ]:
        sql = FormulaCompiler.compile_measure(expr, time_col="order_date")
        assert len(sql) > 3

    # 2. Excel File Upload -> Auto AI Dashboard Generation -> Edit -> Runtime Chart Switch -> Drill Down
    df = pd.DataFrame({
        "year": [2024, 2024, 2025, 2025, 2026, 2026],
        "region": ["North", "South", "North", "South", "North", "South"],
        "department": ["Sales", "Marketing", "Sales", "Marketing", "Sales", "Marketing"],
        "sales": [12000, 9500, 18000, 14000, 24000, 17500],
        "cost": [4000, 3500, 5500, 4800, 6200, 5100],
    })
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="SalesSheet", index=False)
    buf.seek(0)

    prev = client.post(
        "/api/v1/data-sources/files/preview",
        files={"file": ("quarterly.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=admin_headers,
    )
    assert prev.status_code == 200
    assert prev.json()["sheets"] == ["SalesSheet"]
    assert prev.json()["total_rows"] == 6

    imp = client.post(
        "/api/v1/data-sources/files/import",
        files={"file": ("quarterly.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"dataset_name": "Quarterly Excel Upload", "sheet_name": "SalesSheet"},
        headers=admin_headers,
    )
    assert imp.status_code == 200
    ds_id = imp.json()["dataset_id"]

    # AI Auto Generate Dashboard from imported Excel
    ai_dash = client.post("/api/v1/ai/generate-dashboard", json={"dataset_id": ds_id}, headers=admin_headers)
    assert ai_dash.status_code == 200
    dash_id = ai_dash.json()["dashboard_id"]

    detail = client.get(f"/api/v1/dashboards/{dash_id}", headers=admin_headers).json()
    assert len(detail["widgets"]) >= 4
    bar_w = next(w for w in detail["widgets"] if w["chart_type"] in ("bar", "area"))

    # Add a widget-level filter, then switch chart Bar -> Line -> Donut -> Gauge -> Table
    wf = client.post(
        f"/api/v1/dashboards/widgets/{bar_w['id']}/filters",
        json={"field_name": "region", "operator": "eq", "value": "North"},
        headers=admin_headers,
    )
    assert wf.status_code == 200

    for target_type in ("line", "area", "donut", "pie", "gauge", "table", "bar"):
        sw = client.post(
            f"/api/v1/dashboards/widgets/{bar_w['id']}/switch-chart",
            json={"target_chart_type": target_type},
            headers=admin_headers,
        )
        assert sw.status_code == 200, sw.text
        assert sw.json()["chart_type"] == target_type

    # Verify dataset & widget filter remained intact after chart switching!
    detail_after = client.get(f"/api/v1/dashboards/{dash_id}", headers=admin_headers).json()
    switched_w = next(w for w in detail_after["widgets"] if w["id"] == bar_w["id"])
    assert switched_w["dataset_id"] == ds_id
    assert len(switched_w["widget_filters"]) == 1

    # 3. SQL Injection / DDL Protection in SQL Editor
    bad_sql = client.post(
        "/api/v1/queries/execute-sql",
        json={"sql_text": "DROP TABLE wh_enterprise_sales"},
        headers=admin_headers,
    )
    assert bad_sql.status_code == 400

    # Parameterized SQL Query
    good_sql2 = client.post(
        "/api/v1/queries/execute-sql",
        json={
            "sql_text": "SELECT region, SUM(revenue) AS total FROM wh_enterprise_sales WHERE year >= :min_year GROUP BY region",
            "parameters": {"min_year": 2024},
        },
        headers=admin_headers,
    )
    assert good_sql2.status_code == 200
    assert good_sql2.json()["row_count"] >= 4

    # 4. NLQ (English + Persian)
    nlq_en = client.post("/api/v1/ai/nlq", json={"dataset_id": ds_id, "question": "total sales by region"}, headers=admin_headers)
    assert nlq_en.status_code == 200
    assert nlq_en.json()["result"]["row_count"] >= 2

    nlq_fa = client.post("/api/v1/ai/nlq", json={"dataset_id": ds_id, "question": "مجموع فروش بر اساس منطقه"}, headers=admin_headers)
    assert nlq_fa.status_code == 200
    assert nlq_fa.json()["result"]["row_count"] >= 2

    # 5. Exports (PDF, Excel, CSV)
    for fmt in ("pdf", "xlsx", "csv"):
        exp = client.get(f"/api/v1/exports/dashboards/{dash_id}/{fmt}", headers=admin_headers)
        assert exp.status_code == 200
        assert len(exp.content) > 50

    # 6. Drill-Down Auto Chart Switch on Seeded Dashboard 1
    seeded_dashes = client.get("/api/v1/dashboards", headers=admin_headers).json()
    exec_dash = next(d for d in seeded_dashes if "Enterprise Revenue" in d["title"])
    exec_detail = client.get(f"/api/v1/dashboards/{exec_dash['id']}", headers=admin_headers).json()
    trend_w = next(w for w in exec_detail["widgets"] if "Revenue Performance" in w["title"])

    lvl0 = client.post(f"/api/v1/dashboards/widgets/{trend_w['id']}/data", json={"drill_stack": []}, headers=admin_headers).json()
    assert lvl0["effective_chart_type"] == "bar"

    lvl1 = client.post(
        f"/api/v1/dashboards/widgets/{trend_w['id']}/data",
        json={"drill_stack": [{"field": "year", "value": 2025}]},
        headers=admin_headers,
    ).json()
    assert lvl1["effective_chart_type"] == "line"
    assert lvl1["drill"]["level"] == 1

    lvl2 = client.post(
        f"/api/v1/dashboards/widgets/{trend_w['id']}/data",
        json={"drill_stack": [{"field": "year", "value": 2025}, {"field": "quarter", "value": "Q1"}]},
        headers=admin_headers,
    ).json()
    assert lvl2["effective_chart_type"] == "area"
    assert lvl2["drill"]["level"] == 2
