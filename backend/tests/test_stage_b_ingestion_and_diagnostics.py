import io
import json
import os
import sqlite3
import tempfile
import time

import openpyxl


def _build_multisheet_xlsx_bytes() -> bytes:
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "Orders_Q1"
    ws1.append(["order_id", "order_date", "region", "amount", "cost", "secret_note"])
    rows_q1 = [
        [1, "2026-01-05", "Tehran", 100.0, 60.0, "n1"],
        [2, "2026-01-12", "Shiraz", 110.0, 65.0, "n2"],
        [3, "2026-01-20", "Tehran", 120.0, 70.0, "n3"],
        [4, "2026-02-02", "Isfahan", 105.0, 62.0, "n4"],
        [5, "2026-02-15", "Tehran", 98.0, 58.0, "n5"],
        [6, "2026-02-22", "Shiraz", 115.0, 68.0, "n6"],
        [7, "2026-03-04", "Tehran", 5500.0, 3200.0, "outlier"],  # clear IQR outlier & correlated cost
        [7, "2026-03-04", "Tehran", 5500.0, 3200.0, "outlier"],  # duplicate row
    ]
    for r in rows_q1:
        ws1.append(r)

    ws2 = wb.create_sheet("Orders_Q2")
    ws2.append(["order_id", "order_date", "region", "amount", "cost", "secret_note"])
    ws2.append([8, "2026-04-03", "Tehran", 200.0, 120.0, "q2_1"])
    ws2.append([9, "2026-04-10", "Mashhad", 220.0, 130.0, "q2_2"])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


def test_multisheet_preview_selected_columns_and_row_accounting(client, admin_headers):
    xlsx_bytes = _build_multisheet_xlsx_bytes()

    resp = client.post(
        "/api/v1/data-sources/files/preview",
        headers=admin_headers,
        files={"file": ("sales_multi.xlsx", xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={
            "sheet_name": "Orders_Q1",
            "selected_columns_json": json.dumps(["order_id", "order_date", "region", "amount", "cost"]),
            "deduplicate": "false",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["sheets"] == ["Orders_Q1", "Orders_Q2"]
    assert len(data["sheet_summaries"]) == 2
    col_names = [c["field_name"] for c in data["columns"]]
    assert "secret_note" not in col_names
    assert set(col_names) == {"order_id", "order_date", "region", "amount", "cost"}
    assert data["row_accounting"]["input_rows"] == 8
    assert data["row_accounting"]["valid_rows"] == 8


def test_merged_multisheet_import_async_job_and_diagnostics(client, admin_headers):
    xlsx_bytes = _build_multisheet_xlsx_bytes()

    resp = client.post(
        "/api/v1/data-sources/files/import",
        headers=admin_headers,
        files={"file": ("sales_multi.xlsx", xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={
            "dataset_name": "Merged_Multi_Sales",
            "sheet_names_json": json.dumps(["Orders_Q1", "Orders_Q2"]),
            "merge_sheets": "true",
            "selected_columns_json": json.dumps(["order_id", "order_date", "region", "amount", "cost"]),
            "async_mode": "true",
        },
    )
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]

    job_data = None
    for _ in range(50):
        jr = client.get(f"/api/v1/jobs/{job_id}", headers=admin_headers)
        assert jr.status_code == 200
        job_data = jr.json()
        if job_data["status"] in ("completed", "failed"):
            break
        time.sleep(0.15)
    assert job_data is not None and job_data["status"] == "completed", job_data
    ds_id = job_data["result"]["dataset_id"]
    assert job_data["result"]["row_count"] == 10  # 8 rows from Q1 + 2 from Q2

    # Run SQL-first DataDiagnosticsEngine on the merged dataset
    diag = client.get(
        f"/api/v1/datasets/{ds_id}/analytics-diagnostics",
        headers=admin_headers,
        params={"date_column": "order_date", "metric_column": "amount"},
    )
    assert diag.status_code == 200, diag.text
    d_json = diag.json()
    assert d_json["duplicate_rows"] >= 1
    outlier_cols = {o["column"] for o in d_json["outliers"]}
    assert "amount" in outlier_cols
    assert any(
        {c["column_a"], c["column_b"]} == {"amount", "cost"} and c["pearson_r"] > 0.9
        for c in d_json["correlations"]
    )
    assert d_json["period_comparison"] is not None
    assert d_json["period_comparison"]["metric_column"] == "amount"
    assert d_json["period_comparison"]["current_value"] is not None

    del_r = client.delete(f"/api/v1/datasets/{ds_id}", headers=admin_headers)
    assert del_r.status_code == 200


def test_composite_multitable_dataset_from_shared_datasource(client, admin_headers):
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tf:
        sqlite_path = tf.name

    try:
        conn = sqlite3.connect(sqlite_path)
        conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, region_code TEXT, revenue REAL)")
        conn.execute("CREATE TABLE regions (code TEXT PRIMARY KEY, region_name TEXT)")
        conn.execute("INSERT INTO regions VALUES ('THR', 'Tehran'), ('SHZ', 'Shiraz')")
        conn.execute("INSERT INTO orders VALUES (1, 'THR', 500.0), (2, 'SHZ', 300.0), (3, 'THR', 700.0)")
        conn.commit()
        conn.close()

        src_resp = client.post(
            "/api/v1/data-sources",
            headers=admin_headers,
            json={
                "name": "Composite Test SQLite",
                "source_type": "sqlite",
                "read_only": True,
                "config_json": {"file_path": sqlite_path},
                "credentials": {},
            },
        )
        assert src_resp.status_code == 200, src_resp.text
        src_id = src_resp.json()["id"]

        comp_resp = client.post(
            "/api/v1/data-sources/composite-datasets",
            headers=admin_headers,
            json={
                "data_source_id": src_id,
                "name": "Orders_With_Regions",
                "base_table": "orders",
                "joins": [
                    {
                        "table": "regions",
                        "join_type": "LEFT",
                        "left_table": "orders",
                        "left_col": "region_code",
                        "right_col": "code",
                    }
                ],
                "refresh_schedule": "daily",
            },
        )
        assert comp_resp.status_code == 200, comp_resp.text
        cj = comp_resp.json()
        assert cj["total_rows"] == 3
        ds_id = cj["dataset_id"]
        comp_id = cj["composite_id"]

        prev = client.get(f"/api/v1/datasets/{ds_id}/preview", headers=admin_headers)
        assert prev.status_code == 200
        assert prev.json()["row_count"] == 3

        ref = client.post(f"/api/v1/data-sources/composite-datasets/{comp_id}/refresh", headers=admin_headers)
        assert ref.status_code == 200
        assert ref.json()["total_rows"] == 3

        lst = client.get("/api/v1/data-sources/composite-datasets", headers=admin_headers)
        assert lst.status_code == 200
        assert any(item["id"] == comp_id for item in lst.json())

        client.delete(f"/api/v1/data-sources/composite-datasets/{comp_id}", headers=admin_headers)
        client.delete(f"/api/v1/datasets/{ds_id}", headers=admin_headers)
        client.delete(f"/api/v1/data-sources/{src_id}", headers=admin_headers)
    finally:
        if os.path.exists(sqlite_path):
            os.remove(sqlite_path)
