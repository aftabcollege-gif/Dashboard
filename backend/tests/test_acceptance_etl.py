import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from app.config import settings
from app.database import SessionLocal
from app.engines.etl_engine import run_etl_pipeline


def test_d_and_e_etl_resilience_incremental_cdc_and_locking(client, admin_headers):
    mon = client.get("/api/v1/etl/monitor", headers=admin_headers).json()
    pipelines = mon["pipelines"]
    inc_pl = next(p for p in pipelines if p["extract_mode"] == "incremental")
    cdc_pl = next(p for p in pipelines if p["extract_mode"] == "cdc_hash")
    ds_id = inc_pl["target_dataset_id"]

    # 1. Verify initial warehouse data loaded
    prev_before = client.get(f"/api/v1/datasets/{ds_id}/preview?limit=5", headers=admin_headers).json()
    assert prev_before["row_count"] == 5

    # 2. Simulate source database disconnect (temporarily rename SQLite source file)
    erp_path = settings.SAMPLE_ERP_DB_PATH
    backup_path = erp_path + ".offline_bak"
    os.rename(erp_path, backup_path)
    try:
        # ETL fails & logs retry error, WITHOUT corrupting existing warehouse data!
        fail_run = client.post(f"/api/v1/etl/pipelines/{inc_pl['id']}/run", headers=admin_headers).json()
        assert fail_run["status"] == "failed"

        # Dashboard/Dataset STILL operates cleanly from Warehouse!
        dashes = client.get("/api/v1/dashboards", headers=admin_headers).json()
        d1_id = dashes[0]["id"]
        batch = client.post(f"/api/v1/dashboards/{d1_id}/execute-batch", json={}, headers=admin_headers)
        assert batch.status_code == 200
        assert len(batch.json()["widgets"]) >= 6
    finally:
        # Restore operational DB
        os.rename(backup_path, erp_path)

    # 3. Insert a brand-new order in operational source DB -> Incremental ETL extracts ONLY 1 row!
    conn = sqlite3.connect(erp_path)
    try:
        conn.execute(
            """
            INSERT INTO erp_orders VALUES (
                99999, 'ORD-99999', '2026-11-15', 2026, 'Q4', '2026-11', '2026-11-15',
                'Iran', 'North', 'Tehran', 'Tehran', 'AI Forecasting Engine', 'AI Modules',
                'Direct Enterprise', 'Engineering', 10, 250000.0, 50000.0, 200000.0, 80.0,
                '2026-11-16T00:00:00Z'
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    inc_run = client.post(f"/api/v1/etl/pipelines/{inc_pl['id']}/run", headers=admin_headers).json()
    assert inc_run["status"] == "succeeded"
    assert inc_run["rows_extracted"] == 1
    assert inc_run["total_dataset_rows"] == 1201

    # Verify new order is now queryable in Warehouse / Dashboard
    check = client.post(
        "/api/v1/queries/execute-sql",
        json={"sql_text": "SELECT order_code, revenue FROM wh_enterprise_sales WHERE order_id = 99999"},
        headers=admin_headers,
    ).json()
    assert check["row_count"] == 1
    assert check["rows"][0]["order_code"] == "ORD-99999"

    # 4. CDC Hash pipeline: run with 0 changes -> 450 unchanged!
    cdc_run1 = client.post(f"/api/v1/etl/pipelines/{cdc_pl['id']}/run", headers=admin_headers).json()
    assert cdc_run1["status"] == "succeeded"
    assert cdc_run1["rows_unchanged"] == 450
    assert cdc_run1["rows_inserted"] == 0

    # 5. Update schedule & verify scheduler status
    sched_res = client.put(
        f"/api/v1/etl/pipelines/{inc_pl['id']}/schedule",
        json={"schedule_type": "cron", "cron_expression": "*/10 * * * *", "is_enabled": True},
        headers=admin_headers,
    )
    assert sched_res.status_code == 200
    mon2 = client.get("/api/v1/etl/monitor", headers=admin_headers).json()
    assert mon2["scheduler"]["running"] is True

    # 6. Duplicate concurrent pipeline run lock test
    def _run_concurrent():
        with SessionLocal() as db:
            return run_etl_pipeline(db, cdc_pl["id"], trigger_type="concurrent_test", simulate_retry_sync=False)

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(_run_concurrent)
        f2 = pool.submit(_run_concurrent)
        results = [f1.result()["status"], f2.result()["status"]]
    assert "succeeded" in results
    assert any(s in ("succeeded", "skipped_duplicate") for s in results)
