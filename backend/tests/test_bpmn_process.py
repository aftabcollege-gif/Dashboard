"""End-to-end tests for BPMN workflow bottleneck discovery, cartable drill-down data, and AI BPMN builder."""
import io
import openpyxl


def test_bpmn_seeded_dashboard_and_raw_execution(client, admin_headers):
    dashes = client.get("/api/v1/dashboards", headers=admin_headers).json()
    bpmn_dash = next((d for d in dashes if "BPMN" in d["title"]), None)
    assert bpmn_dash is not None, f"Seeded BPMN dashboard missing from {[d['title'] for d in dashes]}"

    detail = client.get(f"/api/v1/dashboards/{bpmn_dash['id']}", headers=admin_headers).json()
    bpmn_widget = next((w for w in detail["widgets"] if w["chart_type"] == "bpmn"), None)
    assert bpmn_widget is not None
    assert bpmn_widget["query"]["visual_spec"]["bpmn_config"]["heatmap_enabled"] is True

    # Execute batch -> BPMN widget must return raw event rows with step, wait_hours, assignee_name, sla_hours, status
    exec_res = client.post(
        f"/api/v1/dashboards/{bpmn_dash['id']}/execute-batch",
        headers=admin_headers,
        json={"active_filters": [], "cross_filters": [], "drill_states": {}, "use_cache": False},
    )
    assert exec_res.status_code == 200, exec_res.text
    wdata = exec_res.json()["widgets"][bpmn_widget["id"]]
    assert wdata["effective_chart_type"] == "bpmn"
    assert wdata["row_count"] >= 60
    for req_col in ("step_name", "assignee_name", "wait_hours", "sla_hours", "status", "request_code"):
        assert req_col in wdata["columns"]

    # Verify bottleneck steps have clearly higher wait_hours than initial steps
    rows = wdata["rows"]
    comm_waits = [r["wait_hours"] for r in rows if r["step_name"] == "کمیسیون معاملات و مناقصه" and r["status"] != "تکمیل‌شده"]
    submit_waits = [r["wait_hours"] for r in rows if r["step_name"] == "ثبت درخواست خرید" and r["status"] != "تکمیل‌شده"]
    assert len(comm_waits) >= 10
    assert (sum(comm_waits) / len(comm_waits)) > (sum(submit_waits) / len(submit_waits)) * 5

    # Filtering by department must filter BPMN rows dynamically
    filtered_res = client.post(
        f"/api/v1/dashboards/widgets/{bpmn_widget['id']}/data",
        headers=admin_headers,
        json={
            "active_filters": [{"field_name": "department", "operator": "eq", "value": "فنی و مهندسی", "is_active": True}],
            "cross_filters": [],
            "drill_stack": [],
            "use_cache": False,
        },
    )
    assert filtered_res.status_code == 200, filtered_res.text
    f_rows = filtered_res.json()["rows"]
    assert 0 < len(f_rows) < len(rows)
    assert all(r["department"] == "فنی و مهندسی" for r in f_rows)


def test_bpmn_widget_crud_switch_and_persian_ai(client, admin_headers):
    datasets = client.get("/api/v1/datasets", headers=admin_headers).json()
    bpmn_ds = next(d for d in datasets if d["warehouse_table"] == "wh_process_bpmn_events")

    # 1. AI Assistant generates a BPMN widget from plain Persian prompt
    ai_w = client.post(
        "/api/v1/ai/assistant",
        headers=admin_headers,
        json={
            "prompt": "یک نمودار BPMN برای کشف گلوگاه فرآیندها بساز",
            "mode": "add_widget",
        },
    )
    assert ai_w.status_code == 200, ai_w.text
    ai_json = ai_w.json()
    assert ai_json["status"] == "ready"
    assert ai_json["widget_spec"]["chart_type"] == "bpmn"
    assert ai_json["widget_spec"]["visual_spec"]["bpmn_config"]["heatmap_enabled"] is True
    assert ai_json["preview_data"]["row_count"] >= 60

    # 2. AI Assistant creates a full dashboard with BPMN widget inside
    ai_dash = client.post(
        "/api/v1/ai/assistant",
        headers=admin_headers,
        json={
            "prompt": "یک داشبورد کامل کشف گلوگاه فرآیندهای BPMN با تم اداری بساز",
            "mode": "create_dashboard",
        },
    )
    assert ai_dash.status_code == 200, ai_dash.text
    dash_body = ai_dash.json()
    assert dash_body["status"] == "dashboard_created"
    new_dash_id = dash_body["created_dashboard"]["id"]
    assert any(w["chart_type"] == "bpmn" for w in dash_body["created_dashboard"]["widgets"])

    # 3. Switch a chart widget to BPMN and back
    bar_w = next(w for w in dash_body["created_dashboard"]["widgets"] if w["chart_type"] != "bpmn")
    sw = client.post(
        f"/api/v1/dashboards/widgets/{bar_w['id']}/switch-chart",
        headers=admin_headers,
        json={"target_chart_type": "bpmn"},
    )
    assert sw.status_code == 200, sw.text
    assert sw.json()["chart_type"] == "bpmn"

    # 4. Upload a custom Persian BPMN Excel file and auto-build BPMN dashboard
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["کد درخواست", "مرحله فرآیند", "مسئول کارتابل", "مدت انتظار", "سقف مجاز", "وضعیت"])
    ws.append(["REQ-1", "ثبت اولیه", "علی رضایی", "۴", "۸", "تکمیل‌شده"])
    ws.append(["REQ-2", "تأیید مدیریت", "سارا محمدی", "۴۸", "۱۲", "در انتظار اقدام"])
    ws.append(["REQ-3", "تأیید مدیریت", "رضا کریمی", "۳۶", "۱۲", "در انتظار اقدام"])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    imp = client.post(
        "/api/v1/data-sources/files/import",
        headers=admin_headers,
        files={"file": ("process_log.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"dataset_name": "فرآیند تست اکسل", "has_header": "true"},
    )
    assert imp.status_code == 200, imp.text
    new_ds_id = imp.json()["dataset_id"]

    gen = client.post(
        "/api/v1/ai/generate-dashboard",
        headers=admin_headers,
        json={"dataset_id": new_ds_id, "title": "داشبورد فرآیند اکسل", "auto_generate_kpis": True},
    )
    assert gen.status_code == 200, gen.text
    gen_dash_id = gen.json()["dashboard_id"]
    gen_det = client.get(f"/api/v1/dashboards/{gen_dash_id}", headers=admin_headers).json()
    assert any(w["chart_type"] == "bpmn" for w in gen_det["widgets"])

    # Cleanup temporary dashboards & uploaded dataset
    client.delete(f"/api/v1/dashboards/{new_dash_id}", headers=admin_headers)
    client.delete(f"/api/v1/dashboards/{gen_dash_id}", headers=admin_headers)
    client.delete(f"/api/v1/datasets/{new_ds_id}", headers=admin_headers)
