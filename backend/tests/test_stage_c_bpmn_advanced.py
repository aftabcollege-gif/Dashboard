import io
import openpyxl


def _build_bpmn_test_csv() -> bytes:
    lines = [
        "process_name,request_id,step_name,entered_at,wait_hours,sla_hours,assignee_name,status",
        "خرید تجهیزات,REQ-1,ثبت درخواست,2026-01-05 10:00:00,2.0,12,علی رضایی,تکمیل‌شده",
        "خرید تجهیزات,REQ-1,بررسی فنی,2026-01-06 12:00:00,40.0,16,سارا محمدی,در انتظار اقدام",
        "خرید تجهیزات,REQ-2,ثبت درخواست,2026-01-10 09:00:00,1.5,12,علی رضایی,تکمیل‌شده",
        "خرید تجهیزات,REQ-2,بررسی فنی,2026-02-04 11:00:00,55.0,16,سارا محمدی,در انتظار اقدام",
        "خرید تجهیزات,REQ-2,بررسی فنی,2026-02-05 11:00:00,62.0,16,سارا محمدی,دارای تاخیر",
        "خرید تجهیزات,REQ-3,تایید مالی,2026-02-12 08:00:00,8.0,24,امیرحسین کریمی,در انتظار اقدام",
    ]
    return "\n".join(lines).encode("utf-8")


def test_stage_c_bpmn_11_kpis_rules_layout_timeline_and_excel_export(client, admin_headers):
    # 1) Upload BPMN CSV dataset
    up = client.post(
        "/api/v1/data-sources/files/import",
        headers=admin_headers,
        files={"file": ("bpmn_stage_c.csv", _build_bpmn_test_csv(), "text/csv")},
        data={"dataset_name": "Stage_C_BPMN_DS"},
    )
    assert up.status_code == 200, up.text
    ds_id = up.json()["dataset_id"]

    # 2) Create dashboard + BPMN widget
    dash_r = client.post("/api/v1/dashboards", headers=admin_headers, json={"title": "BPMN Stage C Dash"})
    assert dash_r.status_code == 200, dash_r.text
    dash_id = dash_r.json()["id"]
    pages_r = client.get(f"/api/v1/dashboards/{dash_id}", headers=admin_headers)
    page_id = pages_r.json()["pages"][0]["id"]

    w_r = client.post(
        f"/api/v1/dashboards/{dash_id}/widgets",
        headers=admin_headers,
        json={
            "page_id": page_id,
            "title": "BPMN Bottleneck Widget",
            "chart_type": "bpmn",
            "dataset_id": ds_id,
            "visual_spec": {
                "aggregation": "RAW",
                "bpmn_config": {
                    "process_field": "process_name",
                    "step_field": "step_name",
                    "wait_field": "wait_hours",
                    "sla_field": "sla_hours",
                    "assignee_field": "assignee_name",
                    "request_id_field": "request_id",
                    "entered_at_field": "entered_at",
                    "status_field": "status",
                },
            },
        },
    )
    assert w_r.status_code == 200, w_r.text
    widget_id = w_r.json()["id"]

    # 3) Create custom Bottleneck Rule
    rule_r = client.post(
        f"/api/v1/process-intelligence/datasets/{ds_id}/bottleneck-rules",
        headers=admin_headers,
        json={
            "name": "سیاست سخت‌گیرانه SLA",
            "process_name": "خرید تجهیزات",
            "weights": {"avg_wait": 0.4, "overdue_ratio": 0.4, "rework_rate": 0.2},
            "threshold_score": 45.0,
            "severity": "critical",
            "is_active": True,
        },
    )
    assert rule_r.status_code == 200, rule_r.text
    rule_id = rule_r.json()["id"]

    # 4) Save Drag-and-Drop Node Layout Coordinates
    lay_r = client.put(
        "/api/v1/process-intelligence/bpmn-layout",
        headers=admin_headers,
        json={
            "dataset_id": ds_id,
            "widget_id": widget_id,
            "process_name": "خرید تجهیزات",
            "node_positions": {"بررسی فنی": {"x": 380, "y": 210}},
            "zoom": 1.15,
        },
    )
    assert lay_r.status_code == 200, lay_r.text
    assert lay_r.json()["node_positions"]["بررسی فنی"] == {"x": 380, "y": 210}

    # 5) Fetch widget data & confirm 11 KPIs + active_rule + saved_layout
    wd_r = client.post(
        f"/api/v1/dashboards/widgets/{widget_id}/data",
        headers=admin_headers,
        json={"use_cache": False},
    )
    assert wd_r.status_code == 200, wd_r.text
    ba = wd_r.json()["bpmn_analytics"]
    assert ba["active_rule"]["id"] == rule_id
    assert ba["saved_layout"]["node_positions"]["بررسی فنی"] == {"x": 380, "y": 210}

    tech_step = next(s for s in ba["step_summary"] if s["step_name"] == "بررسی فنی")
    for required_kpi in (
        "total_count",
        "pending_count",
        "completed_count",
        "avg_wait_hours",
        "min_wait_hours",
        "max_wait_hours",
        "sla_hours",
        "overdue_count",
        "sla_breach_pct",
        "rework_count",
        "assignee_count",
        "bottleneck_score",
    ):
        assert required_kpi in tech_step, f"Missing {required_kpi}"
    assert tech_step["is_bottleneck"] is True
    assert tech_step["rework_count"] >= 1
    assert tech_step["severity"] == "critical"

    # 6) Drill-down into step with timeline + p95 + median
    drill_r = client.post(
        f"/api/v1/dashboards/widgets/{widget_id}/bpmn-drill",
        headers=admin_headers,
        json={"step_name": "بررسی فنی", "process_name": "خرید تجهیزات", "pending_only": False},
    )
    assert drill_r.status_code == 200, drill_r.text
    dr = drill_r.json()
    assert dr["total_items"] == 3
    assert "median_wait_hours" in dr["step_summary"]
    assert "p95_wait_hours" in dr["step_summary"]
    assert len(dr["timeline"]) >= 2  # 2026-01 & 2026-02

    # 7) Export BPMN Drill Excel workbook
    exp_r = client.post(
        f"/api/v1/dashboards/widgets/{widget_id}/bpmn-drill/export",
        headers=admin_headers,
        json={"step_name": "بررسی فنی", "process_name": "خرید تجهیزات", "pending_only": False},
    )
    assert exp_r.status_code == 200, exp_r.text
    assert "spreadsheetml.sheet" in exp_r.headers["content-type"]
    wb = openpyxl.load_workbook(io.BytesIO(exp_r.content))
    assert set(["Step_Summary", "Assignees", "Requests"]).issubset(set(wb.sheetnames))
    assert wb["Requests"].max_row >= 5

    # 8) Reset layout + cleanup
    client.put(
        "/api/v1/process-intelligence/bpmn-layout",
        headers=admin_headers,
        json={"dataset_id": ds_id, "widget_id": widget_id, "process_name": "خرید تجهیزات", "reset": True},
    )
    client.delete(f"/api/v1/process-intelligence/datasets/{ds_id}/bottleneck-rules/{rule_id}", headers=admin_headers)
    client.delete(f"/api/v1/dashboards/{dash_id}", headers=admin_headers)
    client.delete(f"/api/v1/datasets/{ds_id}", headers=admin_headers)
