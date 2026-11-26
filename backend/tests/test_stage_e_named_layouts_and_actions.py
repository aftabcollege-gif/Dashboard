def test_stage_e_named_layouts_live_preview_and_actions(client, admin_headers):
    # 1) Create dashboard + widget
    dash_r = client.post("/api/v1/dashboards", headers=admin_headers, json={"title": "Stage E Layout Dash"})
    assert dash_r.status_code == 200, dash_r.text
    dash_id = dash_r.json()["id"]
    detail = client.get(f"/api/v1/dashboards/{dash_id}", headers=admin_headers).json()
    page_id = detail["pages"][0]["id"]

    datasets = client.get("/api/v1/datasets", headers=admin_headers).json()
    ds = datasets[0]

    w_r = client.post(
        f"/api/v1/dashboards/{dash_id}/widgets",
        headers=admin_headers,
        json={
            "page_id": page_id,
            "title": "Revenue Preview",
            "chart_type": "bar",
            "dataset_id": ds["id"],
            "visual_spec": {"aggregation": "COUNT", "y_field": "*"},
            "layout": {"x": 0, "y": 0, "w": 6, "h": 5},
        },
    )
    assert w_r.status_code == 200, w_r.text
    w_id = w_r.json()["id"]

    # 2) Live Preview via /queries/execute-visual
    prev = client.post(
        "/api/v1/queries/execute-visual",
        headers=admin_headers,
        json={"dataset_id": ds["id"], "visual_spec": {"aggregation": "COUNT", "y_field": "*"}, "filters": []},
    )
    assert prev.status_code == 200, prev.text
    assert prev.json()["row_count"] >= 1

    # 3) Save Named Layout 1 (6 cols wide), modify widget width to 12, then re-apply Named Layout 1
    nl1 = client.post(
        f"/api/v1/dashboards/{dash_id}/named-layouts",
        headers=admin_headers,
        json={"name": "چیدمان مدیریتی دو ستونه", "is_default": True},
    )
    assert nl1.status_code == 200, nl1.text
    nl1_id = nl1.json()["id"]

    client.put(
        f"/api/v1/dashboards/{dash_id}/pages/{page_id}/layout",
        headers=admin_headers,
        json={"columns_count": 12, "items": [{"i": w_id, "x": 0, "y": 0, "w": 12, "h": 6}]},
    )

    app_r = client.post(
        f"/api/v1/dashboards/{dash_id}/named-layouts/{nl1_id}/apply",
        headers=admin_headers,
    )
    assert app_r.status_code == 200, app_r.text
    reloaded = client.get(f"/api/v1/dashboards/{dash_id}", headers=admin_headers).json()
    w_reloaded = next(w for w in reloaded["widgets"] if w["id"] == w_id)
    assert w_reloaded["layout"]["w"] == 6

    # 4) Unified Action Management CRUD
    act_r = client.post(
        "/api/v1/actions",
        headers=admin_headers,
        json={
            "domain": "kpi",
            "source_ref": w_id,
            "title": "بررسی افت فروش منطقه غرب",
            "priority": "high",
            "assignee_name": "مدیر فروش",
            "evidence_before_json": {"metric": 1200},
        },
    )
    assert act_r.status_code == 200, act_r.text
    act_id = act_r.json()["id"]

    upd = client.patch(
        f"/api/v1/actions/{act_id}",
        headers=admin_headers,
        json={"status": "verified", "impact_summary": "+18% بهبود", "evidence_after_json": {"metric": 1416}},
    )
    assert upd.status_code == 200 and upd.json()["status"] == "verified"

    act_list = client.get("/api/v1/actions?domain=kpi", headers=admin_headers).json()
    assert any(a["id"] == act_id for a in act_list)

    # Cleanup
    client.delete(f"/api/v1/dashboards/{dash_id}/named-layouts/{nl1_id}", headers=admin_headers)
    client.delete(f"/api/v1/dashboards/{dash_id}", headers=admin_headers)
