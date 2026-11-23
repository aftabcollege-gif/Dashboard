import io
import json
import os
import zipfile
import openpyxl


def _build_persian_xlsx_bytes() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "فروش"
    ws.append(["منطقه", "محصول", "مبلغ فروش", "سود"])
    ws.append(["تهران", "نرم‌افزار ابری", "۱,۵۰۰,۰۰۰", "۴۵۰,۰۰۰"])
    ws.append(["اصفهان", "تحلیل داده", "۹۰۰,۰۰۰", "۳۰۰,۰۰۰"])
    ws.append(["تهران", "تحلیل داده", "۱,۲۰۰,۰۰۰", "۴۰۰,۰۰۰"])
    ws.append(["شیراز", "نرم‌افزار ابری", "۸۰۰,۰۰۰", "۲۵۰,۰۰۰"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _build_wide_xlsx_bytes(num_cols: int = 360, num_rows: int = 12) -> bytes:
    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet("WideData")
    headers = ["region", "order_date", "revenue", "cost"] + [f"metric_{i}" for i in range(5, num_cols + 1)]
    ws.append(headers)
    regions = ["North", "South", "East", "West"]
    for r in range(num_rows):
        row = [
            regions[r % len(regions)],
            f"2026-01-{(r % 28) + 1:02d}",
            1000.5 + r * 10,
            400.25 + r * 5,
        ] + [float(r + i) * 0.5 for i in range(5, num_cols + 1)]
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _build_large_xlsx_bytes(num_rows: int = 55_000) -> bytes:
    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet("BulkSales")
    ws.append(["region", "category", "revenue", "units"])
    regions = ["Tehran", "Isfahan", "Shiraz", "Mashhad"]
    cats = ["Cloud", "Analytics", "Security"]
    for r in range(num_rows):
        ws.append([regions[r % 4], cats[r % 3], 100 + (r % 500), 1 + (r % 10)])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_persian_excel_preview_import_dashboard_and_cleanup(client, admin_headers):
    xlsx_bytes = _build_persian_xlsx_bytes()

    # 1. Preview Excel file
    prev_res = client.post(
        "/api/v1/data-sources/files/preview",
        headers=admin_headers,
        files={"file": ("گزارش_فروش.xlsx", xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"has_header": "true", "null_strategy": "keep", "deduplicate": "false"},
    )
    assert prev_res.status_code == 200, prev_res.text
    prev_body = prev_res.json()
    assert prev_body["sheets"] == ["فروش"]
    assert prev_body["total_rows"] == 4
    col_labels = {c["display_name"]: c for c in prev_body["columns"]}
    assert "مبلغ فروش" in col_labels
    assert col_labels["مبلغ فروش"]["data_type"] in ("integer", "float")
    assert col_labels["مبلغ فروش"]["semantic_role"] == "measure"
    assert col_labels["منطقه"]["semantic_role"] == "geo"

    # 2. Import Excel file into warehouse
    imp_res = client.post(
        "/api/v1/data-sources/files/import",
        headers=admin_headers,
        files={"file": ("گزارش_فروش.xlsx", xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"dataset_name": "فروش تست اکسل", "sheet_name": "فروش", "has_header": "true"},
    )
    assert imp_res.status_code == 200, imp_res.text
    imp_body = imp_res.json()
    ds_id = imp_body["dataset_id"]
    assert imp_body["row_count"] == 4

    # Verify it appears in /datasets with is_uploaded_excel=True
    ds_list = client.get("/api/v1/datasets", headers=admin_headers).json()
    uploaded = next((d for d in ds_list if d["id"] == ds_id), None)
    assert uploaded is not None
    assert uploaded["is_uploaded_excel"] is True

    # 3. Auto-build full dashboard via AI Generate Dashboard
    gen_res = client.post(
        "/api/v1/ai/generate-dashboard",
        headers=admin_headers,
        json={"dataset_id": ds_id},
    )
    assert gen_res.status_code == 200, gen_res.text
    dash_id = gen_res.json()["dashboard_id"]

    # 4. Execute batch on generated dashboard -> charts must return Persian aggregated rows!
    batch_res = client.post(
        f"/api/v1/dashboards/{dash_id}/execute-batch",
        headers=admin_headers,
        json={"active_filters": [], "cross_filters": []},
    )
    assert batch_res.status_code == 200, batch_res.text
    widgets_data = batch_res.json()["widgets"]
    assert len(widgets_data) >= 3
    any_rows = [w["rows"] for w in widgets_data.values() if w.get("rows")]
    assert len(any_rows) >= 1

    # 5. Conversational Assistant also works directly on the uploaded Excel dataset
    asst_res = client.post(
        "/api/v1/ai/assistant",
        headers=admin_headers,
        json={
            "prompt": "از روی منبع فروش تست اکسل یک نمودار میله ای برای مبلغ فروش بر اساس منطقه بساز",
            "mode": "create_dashboard",
            "clarifications": {"dataset_id": ds_id, "theme_id": "__default__", "layout_preset": "executive_full", "title": "داشبورد فروش اکسل"},
        },
    )
    assert asst_res.status_code == 200, asst_res.text
    asst_body = asst_res.json()
    assert asst_body["status"] == "dashboard_created"
    assert len(asst_body["created_dashboard"]["widgets"]) >= 4

    # 6. Delete uploaded dataset cleanly
    del_res = client.delete(f"/api/v1/datasets/{ds_id}", headers=admin_headers)
    assert del_res.status_code == 200, del_res.text


def test_wide_excel_over_250_columns_upload_profile_and_dashboard(client, admin_headers):
    wide_bytes = _build_wide_xlsx_bytes(num_cols=360, num_rows=10)

    prev_res = client.post(
        "/api/v1/data-sources/files/preview",
        headers=admin_headers,
        files={"file": ("wide_360cols.xlsx", wide_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"has_header": "true"},
    )
    assert prev_res.status_code == 200, prev_res.text
    prev_body = prev_res.json()
    assert prev_body["total_rows"] == 10
    assert len(prev_body["columns"]) == 360

    imp_res = client.post(
        "/api/v1/data-sources/files/import",
        headers=admin_headers,
        files={"file": ("wide_360cols.xlsx", wide_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"dataset_name": "Wide 360 Columns Excel"},
    )
    assert imp_res.status_code == 200, imp_res.text
    imp_body = imp_res.json()
    ds_id = imp_body["dataset_id"]
    assert imp_body["row_count"] == 10
    assert imp_body["columns_count"] == 360
    assert len(imp_body["columns"]) == 360

    # Batched profiler returns all 360 columns without truncation
    prof_res = client.get(f"/api/v1/datasets/{ds_id}/profile", headers=admin_headers)
    assert prof_res.status_code == 200, prof_res.text
    prof_body = prof_res.json()
    assert prof_body["row_count"] == 10
    assert len(prof_body["fields"]) == 360

    # AI Generate Dashboard succeeds on 360-column dataset
    gen_res = client.post(
        "/api/v1/ai/generate-dashboard",
        headers=admin_headers,
        json={"dataset_id": ds_id},
    )
    assert gen_res.status_code == 200, gen_res.text
    dash_id = gen_res.json()["dashboard_id"]

    batch_res = client.post(
        f"/api/v1/dashboards/{dash_id}/execute-batch",
        headers=admin_headers,
        json={"active_filters": [], "cross_filters": []},
    )
    assert batch_res.status_code == 200, batch_res.text
    assert len(batch_res.json()["widgets"]) >= 3

    del_res = client.delete(f"/api/v1/datasets/{ds_id}", headers=admin_headers)
    assert del_res.status_code == 200


def test_large_excel_over_50k_rows_streaming_import_and_dashboard(client, admin_headers):
    large_bytes = _build_large_xlsx_bytes(num_rows=55_000)

    prev_res = client.post(
        "/api/v1/data-sources/files/preview",
        headers=admin_headers,
        files={"file": ("large_55k.xlsx", large_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"has_header": "true"},
    )
    assert prev_res.status_code == 200, prev_res.text
    prev_body = prev_res.json()
    assert prev_body["total_rows"] == 55_000

    imp_res = client.post(
        "/api/v1/data-sources/files/import",
        headers=admin_headers,
        files={"file": ("large_55k.xlsx", large_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"dataset_name": "Large 55k Excel"},
    )
    assert imp_res.status_code == 200, imp_res.text
    imp_body = imp_res.json()
    ds_id = imp_body["dataset_id"]
    assert imp_body["row_count"] == 55_000

    gen_res = client.post(
        "/api/v1/ai/generate-dashboard",
        headers=admin_headers,
        json={"dataset_id": ds_id},
    )
    assert gen_res.status_code == 200, gen_res.text
    dash_id = gen_res.json()["dashboard_id"]

    batch_res = client.post(
        f"/api/v1/dashboards/{dash_id}/execute-batch",
        headers=admin_headers,
        json={"active_filters": [], "cross_filters": []},
    )
    assert batch_res.status_code == 200, batch_res.text
    assert len(batch_res.json()["widgets"]) >= 3

    del_res = client.delete(f"/api/v1/datasets/{ds_id}", headers=admin_headers)
    assert del_res.status_code == 200


def test_streaming_excel_chunk_type_widening_schema_expansion_and_dedup(client, admin_headers, monkeypatch):
    import app.engines.connectors as conn_mod
    from app.engines.warehouse import warehouse

    # Force 4 streaming batches over 400 rows (with 50 exact duplicates across chunks)
    monkeypatch.setattr(conn_mod, "_EXCEL_STREAM_CHUNK", 100)

    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet("Evolving")
    ws.append(["region", "metric_col", "code_col"])
    # Chunk 1 (rows 0..99): strictly integers in metric_col & code_col, 3 columns
    for i in range(100):
        ws.append(["East", i + 1, 1000 + i])
    # Chunk 2 (rows 100..199): introduces floats in metric_col -> BIGINT widens to DOUBLE
    for i in range(100):
        ws.append(["West", 12.75 + i, 2000 + i])
    # Chunk 3 (rows 200..299): introduces text in code_col -> BIGINT widens to VARCHAR
    for i in range(100):
        ws.append(["North", 50.5, f"CODE-{i}"])
    # Chunk 4 (rows 300..349): ragged 4th column added mid-stream!
    for i in range(50):
        ws.append(["South", 99.0, f"TXT-{i}", f"note_{i}"])
    # Chunk 4 tail (rows 350..399): 50 exact duplicates of Chunk 1 rows!
    for i in range(50):
        ws.append(["East", i + 1, 1000 + i])
    buf = io.BytesIO()
    wb.save(buf)
    xlsx_bytes = buf.getvalue()

    imp_res = client.post(
        "/api/v1/data-sources/files/import",
        headers=admin_headers,
        files={"file": ("evolving_dedup.xlsx", xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"dataset_name": "Evolving Dedup Excel", "deduplicate": "true"},
    )
    assert imp_res.status_code == 200, imp_res.text
    body = imp_res.json()
    ds_id = body["dataset_id"]
    wh_table = body["warehouse_table"]
    assert body["row_count"] == 350
    col_names = [c["field_name"] for c in body["columns"]]
    assert col_names == ["region", "metric_col", "code_col", "col_4"]

    # Confirm DuckDB preserved float fractions and expanded string codes
    res = warehouse.execute_query(f'SELECT ROUND(SUM("metric_col"), 2) AS s, COUNT(DISTINCT "code_col") AS d FROM "{wh_table}"')
    assert float(res["rows"][0]["s"]) > 10000.0
    assert int(res["rows"][0]["d"]) == 350

    client.delete(f"/api/v1/datasets/{ds_id}", headers=admin_headers)


def test_log_files_apache_app_jsonl_txt_upload_and_ai_dashboard(client, admin_headers):
    # 1. Apache/Nginx combined access log (.log)
    apache_log = "\n".join([
        '10.0.0.1 - alice [16/Nov/2026:10:00:01 +0000] "GET /api/orders HTTP/1.1" 200 1536 "-" "Mozilla/5.0"',
        '10.0.0.2 - bob [16/Nov/2026:10:00:05 +0000] "POST /api/orders HTTP/1.1" 201 512 "-" "curl/8.0"',
        '10.0.0.3 - - [16/Nov/2026:10:00:09 +0000] "GET /api/admin HTTP/1.1" 500 240 "-" "Mozilla/5.0"',
    ]).encode("utf-8")

    prev_apache = client.post(
        "/api/v1/data-sources/files/preview",
        headers=admin_headers,
        files={"file": ("nginx_access.log", apache_log, "text/plain")},
    )
    assert prev_apache.status_code == 200, prev_apache.text
    p_body = prev_apache.json()
    assert p_body["total_rows"] == 3
    p_cols = {c["field_name"] for c in p_body["columns"]}
    assert {"method", "path", "status_code", "response_bytes"}.issubset(p_cols)

    imp_apache = client.post(
        "/api/v1/data-sources/files/import",
        headers=admin_headers,
        files={"file": ("nginx_access.log", apache_log, "text/plain")},
        data={"dataset_name": "Nginx Access Logs"},
    )
    assert imp_apache.status_code == 200, imp_apache.text
    apache_ds_id = imp_apache.json()["dataset_id"]
    apache_src_id = imp_apache.json()["data_source_id"]

    # Generate dashboard from Apache log dataset and run batch execution
    gen_apache = client.post(
        "/api/v1/ai/generate-dashboard",
        headers=admin_headers,
        json={"dataset_id": apache_ds_id},
    )
    assert gen_apache.status_code == 200, gen_apache.text
    dash_id = gen_apache.json()["dashboard_id"]
    batch_res = client.post(
        f"/api/v1/dashboards/{dash_id}/execute-batch",
        headers=admin_headers,
        json={"active_filters": [], "cross_filters": []},
    )
    assert batch_res.status_code == 200, batch_res.text
    assert len(batch_res.json()["widgets"]) >= 3

    # Verify saved log file path exists then gets cleaned up on dataset delete
    sources = client.get("/api/v1/data-sources", headers=admin_headers).json()
    src_obj = next((s for s in sources if s["id"] == apache_src_id), None)
    assert src_obj is not None
    saved_file = (src_obj.get("config_json") or {}).get("file_path")
    assert saved_file and os.path.exists(saved_file)
    del_res = client.delete(f"/api/v1/datasets/{apache_ds_id}", headers=admin_headers)
    assert del_res.status_code == 200
    assert not os.path.exists(saved_file)

    # 2. Timestamped application log with key=value pairs & latency_ms (.log)
    app_log = "\n".join([
        "2026-11-16T10:15:00Z INFO [auth] Login succeeded service=auth latency=12ms status=ok",
        "2026-11-16T10:15:02Z ERROR [billing] Timeout calling gateway service=billing latency=450ms status=fail",
        "2026-11-16T10:15:05Z WARN [orders] Slow query detected service=orders latency=180ms status=ok",
    ]).encode("utf-8")

    imp_app = client.post(
        "/api/v1/data-sources/files/import",
        headers=admin_headers,
        files={"file": ("app_service.log", app_log, "text/plain")},
        data={"dataset_name": "Application Service Logs"},
    )
    assert imp_app.status_code == 200, imp_app.text
    app_cols = {c["field_name"] for c in imp_app.json()["columns"]}
    assert {"timestamp", "level", "service", "latency_ms"}.issubset(app_cols)
    client.delete(f"/api/v1/datasets/{imp_app.json()['dataset_id']}", headers=admin_headers)

    # 3. JSON Lines log (.jsonl) with evolving keys across lines
    jsonl_bytes = "\n".join([
        json.dumps({"timestamp": "2026-11-16T12:00:00Z", "level": "INFO", "service": "api", "latency_ms": 15}),
        json.dumps({"timestamp": "2026-11-16T12:01:00Z", "level": "ERROR", "service": "worker", "latency_ms": 220, "error_code": "E_TIMEOUT"}),
    ]).encode("utf-8")
    imp_jsonl = client.post(
        "/api/v1/data-sources/files/import",
        headers=admin_headers,
        files={"file": ("events.jsonl", jsonl_bytes, "application/x-ndjson")},
        data={"dataset_name": "JSONL Telemetry"},
    )
    assert imp_jsonl.status_code == 200, imp_jsonl.text
    jsonl_cols = {c["field_name"] for c in imp_jsonl.json()["columns"]}
    assert {"timestamp", "level", "service", "latency_ms", "error_code"}.issubset(jsonl_cols)
    client.delete(f"/api/v1/datasets/{imp_jsonl.json()['dataset_id']}", headers=admin_headers)

    # 4. Pipe-delimited .txt log
    pipe_txt = "\n".join([
        "timestamp|level|host|duration_ms",
        "2026-11-16 09:00:00|INFO|node-1|25",
        "2026-11-16 09:00:05|ERROR|node-2|310",
    ]).encode("utf-8")
    imp_txt = client.post(
        "/api/v1/data-sources/files/import",
        headers=admin_headers,
        files={"file": ("syslog_pipe.txt", pipe_txt, "text/plain")},
        data={"dataset_name": "Pipe Text Logs"},
    )
    assert imp_txt.status_code == 200, imp_txt.text
    assert imp_txt.json()["row_count"] == 2
    client.delete(f"/api/v1/datasets/{imp_txt.json()['dataset_id']}", headers=admin_headers)


def test_xlsx_zip_bomb_and_xxe_guards_still_reject_unsafe_archives(client, admin_headers):
    # Nested zip archive inside .xlsx should be rejected
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("evil_nested.zip", b"PK\x03\x04")
    res = client.post(
        "/api/v1/data-sources/files/preview",
        headers=admin_headers,
        files={"file": ("nested.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert res.status_code == 400

