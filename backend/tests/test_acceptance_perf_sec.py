import time
import pandas as pd
from app.engines.warehouse import warehouse


def _login(client, username, password):
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_f_and_g_performance_rls_cls_and_cache_security(client, admin_headers):
    # 1. Row-Level Security (RLS) & Cache Isolation Test (Test G)
    # north_mgr has profile.region='North'; south_mgr has profile.region='South'.
    h_north = _login(client, "north_mgr", "Manager!2026")
    h_south = _login(client, "south_mgr", "Manager!2026")

    datasets = client.get("/api/v1/datasets", headers=admin_headers).json()
    ds_sales_id = next(d["id"] for d in datasets if d["warehouse_table"] == "wh_enterprise_sales")
    ds_hr_id = next(d["id"] for d in datasets if d["warehouse_table"] == "wh_hr_workforce")

    dashes = client.get("/api/v1/dashboards", headers=admin_headers).json()
    d1 = client.get(f"/api/v1/dashboards/{dashes[0]['id']}", headers=admin_headers).json()
    reg_widget = next(w for w in d1["widgets"] if "Region" in w["title"])

    # Execute same widget for north_mgr and south_mgr -> must NEVER share cached results!
    res_n = client.post(f"/api/v1/dashboards/widgets/{reg_widget['id']}/data", json={}, headers=h_north).json()
    res_s = client.post(f"/api/v1/dashboards/widgets/{reg_widget['id']}/data", json={}, headers=h_south).json()
    assert [r["region"] for r in res_n["rows"]] == ["North"]
    assert [r["region"] for r in res_s["rows"]] == ["South"]

    # Column-Level Masking on HR email (report_viewer cannot preview raw dataset; analyst can and sees masked emails!)
    assert client.get(f"/api/v1/datasets/{ds_hr_id}/preview?limit=5", headers=h_north).status_code == 403
    h_analyst = _login(client, "analyst", "Analyst!2026")
    hr_prev = client.get(f"/api/v1/datasets/{ds_hr_id}/preview?limit=5", headers=h_analyst).json()
    assert len(hr_prev["rows"]) > 0
    assert all("***@" in r["email"] for r in hr_prev["rows"])

    # 2. Permission change during active session immediately blocks access & invalidates permission cache!
    # Grant admin temporarily or verify viewer permissions revocation
    roles = client.get("/api/v1/rbac/roles", headers=admin_headers).json()["roles"]
    scoped_role_id = next(r["id"] for r in roles if r["slug"] == "custom_scoped")
    u_sess = client.post(
        "/api/v1/rbac/users",
        json={"username": "sess_test_u", "email": "sesstest@enterprise.local", "password": "SessUser!2026", "role_ids": [scoped_role_id]},
        headers=admin_headers,
    ).json()["id"]
    client.put(
        "/api/v1/rbac/permissions/matrix/set",
        json={"subject_type": "user", "subject_id": u_sess, "resource_type": "dashboard", "resource_id": d1["id"], "action": "view", "effect": "allow"},
        headers=admin_headers,
    )
    h_sess = _login(client, "sess_test_u", "SessUser!2026")
    assert client.get(f"/api/v1/dashboards/{d1['id']}", headers=h_sess).status_code == 200

    # Revoke via Explicit Deny while session token is active
    client.put(
        "/api/v1/rbac/permissions/matrix/set",
        json={"subject_type": "user", "subject_id": u_sess, "resource_type": "dashboard", "resource_id": d1["id"], "action": "view", "effect": "deny"},
        headers=admin_headers,
    )
    # Immediate next API request with same token must return 403!
    assert client.get(f"/api/v1/dashboards/{d1['id']}", headers=h_sess).status_code == 403

    # 3. Large Dataset Benchmark (50,000 analytical rows)
    big_df = pd.DataFrame({
        "id": range(1, 50001),
        "region": ["North", "South", "East", "West"] * 12500,
        "category": ["Software", "AI Modules", "Security", "Cloud", "Licensing"] * 10000,
        "amount": [float((i % 500) + 100) for i in range(50000)],
    })
    warehouse.load_dataframe("wh_bench_50k", big_df, mode="replace")
    from app.database import db_session_scope
    from app.models.data import Dataset
    from app.models.identity import Organization
    with db_session_scope() as sdb:
        org_row = sdb.query(Organization).first()
        if not sdb.query(Dataset).filter(Dataset.warehouse_table == "wh_bench_50k").first():
            sdb.add(
                Dataset(
                    organization_id=org_row.id,
                    name="Benchmark 50k",
                    warehouse_table="wh_bench_50k",
                    row_count=50000,
                    freshness_status="fresh",
                )
            )
            sdb.commit()
    t0 = time.perf_counter()
    agg_res = client.post(
        "/api/v1/queries/execute-sql",
        json={"sql_text": "SELECT region, category, SUM(amount) AS total, COUNT(*) AS cnt FROM wh_bench_50k GROUP BY 1, 2"},
        headers=admin_headers,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert agg_res.status_code == 200
    assert agg_res.json()["row_count"] == 20
    assert elapsed_ms < 2000.0  # Should run in well under 200ms

    # Verify warm widget cache hit & performance metrics
    cold_w = client.post(f"/api/v1/dashboards/widgets/{reg_widget['id']}/data", json={}, headers=admin_headers).json()
    warm_w = client.post(f"/api/v1/dashboards/widgets/{reg_widget['id']}/data", json={}, headers=admin_headers).json()
    assert cold_w["cache_hit"] is False
    assert warm_w["cache_hit"] is True

    perf = client.get("/api/v1/monitoring/performance", headers=admin_headers).json()
    assert perf["cache"]["hits"] >= 1
    assert perf["system"]["memory_mb"] > 0
