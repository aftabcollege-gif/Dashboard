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
    excel_bpmn_w = next(w for w in gen_det["widgets"] if w["chart_type"] == "bpmn")
    excel_exec = client.post(
        f"/api/v1/dashboards/widgets/{excel_bpmn_w['id']}/data",
        headers=admin_headers,
        json={"active_filters": [], "cross_filters": [], "drill_stack": [], "use_cache": False},
    )
    assert excel_exec.status_code == 200, excel_exec.text
    ex_ba = excel_exec.json().get("bpmn_analytics")
    assert ex_ba is not None
    assert ex_ba["total_source_rows"] == 3
    ex_drill = client.post(
        f"/api/v1/dashboards/widgets/{excel_bpmn_w['id']}/bpmn-drill",
        headers=admin_headers,
        json={"step_name": "تأیید مدیریت", "pending_only": True},
    )
    assert ex_drill.status_code == 200, ex_drill.text
    ex_names = {a["assigneeName"] for a in ex_drill.json()["assignees"]}
    assert {"سارا محمدی", "رضا کریمی"} <= ex_names

    # Cleanup temporary dashboards & uploaded dataset
    client.delete(f"/api/v1/dashboards/{new_dash_id}", headers=admin_headers)
    client.delete(f"/api/v1/dashboards/{gen_dash_id}", headers=admin_headers)
    client.delete(f"/api/v1/datasets/{new_ds_id}", headers=admin_headers)


def test_400k_multiprocess_bpmn_sql_scale_and_drill(client, admin_headers):
    """
    Validates 100%-row DuckDB SQL BPMN + Process Intelligence across 400,000 rows
    with multiple Persian processes and collision-prone column names
    (نام فرآیند / مرحله فرآیند / نام فرد در کارتابل).
    """
    from app.database import SessionLocal
    from app.engines.warehouse import warehouse, _WriteGuard
    from app.models.data import Dataset, DatasetField
    from app.models import Workspace

    table_name = "wh_test_400k_bpmn"
    total_rows = 400_000

    with _WriteGuard(warehouse._rw):
        conn = warehouse._ensure_conn_unlocked()
        conn.execute(f"DROP TABLE IF EXISTS main.{table_name}")
        conn.execute(
            f"""
            CREATE TABLE main.{table_name} AS
            SELECT
                'REQ-' || ((i % 100000) + 1)::VARCHAR AS "کد درخواست",
                CASE (i % 4)
                    WHEN 0 THEN 'فرآیند خرید'
                    WHEN 1 THEN 'فرآیند خرید'
                    WHEN 2 THEN 'فرآیند مرخصی'
                    ELSE 'فرآیند تدارکات'
                END AS "نام فرآیند",
                CASE (i % 3)
                    WHEN 0 THEN 'ثبت درخواست'
                    WHEN 1 THEN 'تایید مدیر واحد'
                    ELSE 'بررسی مالی'
                END AS "مرحله فرآیند",
                CASE ((i // 4) % 4)
                    WHEN 0 THEN 'علی رضایی'
                    WHEN 1 THEN 'سارا محمدی'
                    WHEN 2 THEN 'حسین کریمی'
                    ELSE 'مریم احمدی'
                END AS "نام فرد در کارتابل",
                CASE (i % 3)
                    WHEN 0 THEN 4.0
                    WHEN 1 THEN 38.0
                    ELSE 14.0
                END AS "مدت انتظار",
                CASE (i % 2)
                    WHEN 0 THEN 'در انتظار اقدام'
                    ELSE 'تکمیل شده'
                END AS "وضعیت"
            FROM range(0, {total_rows}) t(i)
            """
        )

    db = SessionLocal()
    ds_id = None
    dash_id = None
    try:
        ws = db.query(Workspace).first()
        assert ws is not None
        ds = Dataset(
            organization_id=ws.organization_id,
            name="لاگ ۴۰۰ هزار رکوردی فرآیندها",
            warehouse_table=table_name,
            row_count=total_rows,
        )
        db.add(ds)
        db.flush()
        ds_id = ds.id
        for idx, (fname, dtype, role) in enumerate(
            [
                ("کد درخواست", "string", "dimension"),
                ("نام فرآیند", "string", "dimension"),
                ("مرحله فرآیند", "string", "dimension"),
                ("نام فرد در کارتابل", "string", "dimension"),
                ("مدت انتظار", "float", "measure"),
                ("وضعیت", "string", "dimension"),
            ]
        ):
            db.add(
                DatasetField(
                    dataset_id=ds.id,
                    field_name=fname,
                    display_name=fname,
                    data_type=dtype,
                    semantic_role=role,
                )
            )
        db.commit()

        # 1. AI generates dashboard for the 400k multi-process dataset
        gen = client.post(
            "/api/v1/ai/generate-dashboard",
            headers=admin_headers,
            json={"dataset_id": ds_id, "title": "داشبورد ۴۰۰ هزار رکوردی", "auto_generate_kpis": True},
        )
        assert gen.status_code == 200, gen.text
        dash_id = gen.json()["dashboard_id"]
        det = client.get(f"/api/v1/dashboards/{dash_id}", headers=admin_headers).json()
        bpmn_w = next((w for w in det["widgets"] if w["chart_type"] == "bpmn"), None)
        assert bpmn_w is not None, f"Expected BPMN widget, got {[w['chart_type'] for w in det['widgets']]}"

        # 2. Execute batch -> verify 100% of 400,000 rows counted, zero column collision
        batch = client.post(
            f"/api/v1/dashboards/{dash_id}/execute-batch",
            headers=admin_headers,
            json={"active_filters": [], "cross_filters": [], "drill_states": {}, "use_cache": False},
        )
        assert batch.status_code == 200, batch.text
        wdata = batch.json()["widgets"][bpmn_w["id"]]
        ba = wdata.get("bpmn_analytics")
        assert ba is not None
        assert ba["total_source_rows"] == total_rows
        assert ba["mapping"]["process_field"] == "نام فرآیند"
        assert ba["mapping"]["step_field"] == "مرحله فرآیند"
        assert ba["mapping"]["assignee_field"] == "نام فرد در کارتابل"
        assert len(ba["processes"]) == 3
        assert sum(p["total_count"] for p in ba["processes"]) == total_rows

        # 3. Switch process to فرآیند مرخصی -> verify scoped to 100,000 rows
        switched = client.post(
            f"/api/v1/dashboards/widgets/{bpmn_w['id']}/data",
            headers=admin_headers,
            json={
                "active_filters": [],
                "cross_filters": [],
                "drill_stack": [],
                "use_cache": False,
                "bpmn_process_name": "فرآیند مرخصی",
            },
        )
        assert switched.status_code == 200, switched.text
        sw_ba = switched.json()["bpmn_analytics"]
        assert sw_ba["active_process"] == "فرآیند مرخصی"
        assert sw_ba["active_source_rows"] == 100_000
        assert sum(s["total_count"] for s in sw_ba["step_summary"]) == 100_000

        # 4. Live SQL step cartable drill-down -> verify real assignees, never "نامشخص"
        drill = client.post(
            f"/api/v1/dashboards/widgets/{bpmn_w['id']}/bpmn-drill",
            headers=admin_headers,
            json={
                "step_name": "تایید مدیر واحد",
                "process_name": "فرآیند خرید",
                "pending_only": True,
                "limit": 25,
                "offset": 0,
            },
        )
        assert drill.status_code == 200, drill.text
        dr = drill.json()
        assignee_names = {a["assigneeName"] for a in dr["assignees"]}
        assert "نامشخص" not in assignee_names
        assert {"علی رضایی", "سارا محمدی", "حسین کریمی", "مریم احمدی"} <= assignee_names
        assert len(dr["items"]) == 25
        assert dr["total_items"] > 30_000

        # 5. Process Intelligence /processes + 100%-row SQL /analyze on 400k dataset
        pi_procs = client.get(
            f"/api/v1/process-intelligence/datasets/{ds_id}/processes",
            headers=admin_headers,
        )
        assert pi_procs.status_code == 200, pi_procs.text
        pi_p_json = pi_procs.json()
        assert pi_p_json["total_rows"] == total_rows
        assert len(pi_p_json["processes"]) == 3

        pi_ana = client.post(
            f"/api/v1/process-intelligence/datasets/{ds_id}/analyze",
            headers=admin_headers,
            json={"process_name": "فرآیند تدارکات"},
        )
        assert pi_ana.status_code == 200, pi_ana.text
        ana_json = pi_ana.json()
        assert ana_json["status"] == "ok"
        assert ana_json["summary"]["total_events_analyzed"] == 100_000
        assert len(ana_json["bottlenecks"]) == 3
    finally:
        if dash_id:
            client.delete(f"/api/v1/dashboards/{dash_id}", headers=admin_headers)
        if ds_id:
            client.delete(f"/api/v1/datasets/{ds_id}", headers=admin_headers)
        with _WriteGuard(warehouse._rw):
            warehouse._ensure_conn_unlocked().execute(f"DROP TABLE IF EXISTS main.{table_name}")
        db.close()

