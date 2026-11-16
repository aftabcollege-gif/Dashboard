def _login(client, username, password):
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_b_and_c_permission_isolation_and_independent_crud(client, admin_headers):
    dashes = client.get("/api/v1/dashboards", headers=admin_headers).json()
    dash_a_id = dashes[0]["id"]
    dash_b_id = dashes[1]["id"]
    dash_a_detail = client.get(f"/api/v1/dashboards/{dash_a_id}", headers=admin_headers).json()
    # Find non-KPI widgets (with hierarchy for drill-down)
    widgets = dash_a_detail["widgets"]
    w1 = next(w for w in widgets if w["drill_rule"]["hierarchy"])
    w2 = next(w for w in widgets if w["id"] != w1["id"])
    w3 = next(w for w in widgets if w["id"] not in (w1["id"], w2["id"]))

    roles = client.get("/api/v1/rbac/roles", headers=admin_headers).json()["roles"]
    scoped_role_id = next(r["id"] for r in roles if r["slug"] == "custom_scoped")

    # Create 4 isolated users using custom_scoped role (zero blanket dashboard/widget permissions)
    created_ids = {}
    for uname in ("iso_u1", "iso_u2", "iso_u3", "iso_u4", "crud_u"):
        r = client.post(
            "/api/v1/rbac/users",
            json={
                "username": uname,
                "email": f"{uname}@enterprise.local",
                "password": "IsoUser!2026",
                "full_name": uname,
                "role_ids": [scoped_role_id],
            },
            headers=admin_headers,
        )
        assert r.status_code == 200
        created_ids[uname] = r.json()["id"]

    # Wait: scoped_role had dash_a view seeded; let's remove role-level dash_a grant so isolation is purely per-user!
    client.put(
        "/api/v1/rbac/permissions/matrix/set",
        json={
            "subject_type": "role", "subject_id": scoped_role_id,
            "resource_type": "dashboard", "resource_id": dash_a_id,
            "action": "view", "effect": "remove",
        },
        headers=admin_headers,
    )

    def grant(subj_id, rtype, rid, act, effect="allow"):
        res = client.put(
            "/api/v1/rbac/permissions/matrix/set",
            json={
                "subject_type": "user", "subject_id": subj_id,
                "resource_type": rtype, "resource_id": rid,
                "action": act, "effect": effect,
            },
            headers=admin_headers,
        )
        assert res.status_code == 200

    # User 1: ONLY Dashboard A (NOT Dashboard B)
    grant(created_ids["iso_u1"], "dashboard", dash_a_id, "view")
    # User 2: Dashboard A AND B
    grant(created_ids["iso_u2"], "dashboard", dash_a_id, "view")
    grant(created_ids["iso_u2"], "dashboard", dash_b_id, "view")
    # User 3: Dashboard A + ONLY Widget 1 & Widget 2 (NOT Widget 3)
    grant(created_ids["iso_u3"], "dashboard", dash_a_id, "view")
    grant(created_ids["iso_u3"], "widget", w1["id"], "view")
    grant(created_ids["iso_u3"], "widget", w2["id"], "view")
    # User 4: Dashboard A + Widget 1 View ONLY, NO Drill Down
    grant(created_ids["iso_u4"], "dashboard", dash_a_id, "view")
    grant(created_ids["iso_u4"], "widget", w1["id"], "view")

    # Verify Test B Isolation
    h1 = _login(client, "iso_u1", "IsoUser!2026")
    u1_dashes = [d["id"] for d in client.get("/api/v1/dashboards", headers=h1).json()]
    assert dash_a_id in u1_dashes
    assert dash_b_id not in u1_dashes
    assert client.get(f"/api/v1/dashboards/{dash_b_id}", headers=h1).status_code == 403

    h2 = _login(client, "iso_u2", "IsoUser!2026")
    u2_dashes = [d["id"] for d in client.get("/api/v1/dashboards", headers=h2).json()]
    assert dash_a_id in u2_dashes and dash_b_id in u2_dashes

    h3 = _login(client, "iso_u3", "IsoUser!2026")
    u3_detail = client.get(f"/api/v1/dashboards/{dash_a_id}", headers=h3).json()
    u3_wids = {w["id"] for w in u3_detail["widgets"]}
    assert w1["id"] in u3_wids and w2["id"] in u3_wids
    assert w3["id"] not in u3_wids
    # API bypass attempt on Widget 3 must return 403!
    assert client.post(f"/api/v1/dashboards/widgets/{w3['id']}/data", json={}, headers=h3).status_code == 403

    h4 = _login(client, "iso_u4", "IsoUser!2026")
    # View allowed:
    assert client.post(f"/api/v1/dashboards/widgets/{w1['id']}/data", json={}, headers=h4).status_code == 200
    # Drill Down attempt via API must return 403!
    drill_attempt = client.post(
        f"/api/v1/dashboards/widgets/{w1['id']}/data",
        json={"drill_stack": [{"field": "year", "value": 2025}]},
        headers=h4,
    )
    assert drill_attempt.status_code == 403

    # Verify Test C: Independent CRUD permissions
    cid = created_ids["crud_u"]
    hc = _login(client, "crud_u", "IsoUser!2026")
    grant(cid, "dashboard", dash_a_id, "view")
    # View without Edit -> PUT returns 403
    assert client.get(f"/api/v1/dashboards/{dash_a_id}", headers=hc).status_code == 200
    assert client.put(f"/api/v1/dashboards/{dash_a_id}", json={"title": "Hacked"}, headers=hc).status_code == 403

    # Edit without Delete -> PUT succeeds, DELETE returns 403
    grant(cid, "dashboard", dash_a_id, "edit")
    assert client.put(f"/api/v1/dashboards/{dash_a_id}", json={"description": "Updated"}, headers=hc).status_code == 200
    assert client.delete(f"/api/v1/dashboards/{dash_a_id}", headers=hc).status_code == 403

    # Add without Delete -> can add dashboard, cannot delete it!
    grant(cid, "dashboard", "*", "add")
    new_d = client.post("/api/v1/dashboards", json={"title": "Crud Test Dash"}, headers=hc)
    assert new_d.status_code == 200
    nd_id = new_d.json()["id"]
    assert client.delete(f"/api/v1/dashboards/{nd_id}", headers=hc).status_code == 403

    # Delete without Edit -> create second dash, remove edit/grant delete on it -> delete succeeds while edit fails
    new_d2 = client.post("/api/v1/dashboards", json={"title": "Delete Only Dash"}, headers=hc).json()["id"]
    grant(cid, "dashboard", new_d2, "edit", effect="deny")
    grant(cid, "dashboard", new_d2, "delete", effect="allow")
    assert client.put(f"/api/v1/dashboards/{new_d2}", json={"title": "No Edit"}, headers=hc).status_code == 403
    assert client.delete(f"/api/v1/dashboards/{new_d2}", headers=hc).status_code == 200
