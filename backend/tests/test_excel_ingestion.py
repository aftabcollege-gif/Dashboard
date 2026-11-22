import io
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
