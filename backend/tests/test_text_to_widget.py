def test_persian_csv_and_text_to_widget_with_clarification(client, admin_headers):
    # 1. Get an existing dashboard for target placement
    dashes = client.get("/api/v1/dashboards", headers=admin_headers).json()
    assert len(dashes) >= 1
    dash_id = dashes[0]["id"]

    # 2. Ambiguous prompt before uploading custom dataset -> must ask Persian clarification on dataset_id
    amb = client.post(
        "/api/v1/ai/text-to-widget",
        headers=admin_headers,
        json={
            "prompt": "از روی منبع تعداد یک ویجت میله ای بر اساس نام پیمانکار بساز تحت عنوان عملکرد پیمانکار",
            "dashboard_id": dash_id,
            "create_widget": True,
        },
    )
    assert amb.status_code == 200, amb.text
    amb_body = amb.json()
    assert amb_body["status"] == "needs_clarification"
    assert any(q["key"] == "dataset_id" for q in amb_body["questions"])
    assert "تعداد" in amb_body["questions"][0]["question"]

    # Step 2b: User answers dataset + free text clarification in Persian
    step2 = client.post(
        "/api/v1/ai/text-to-widget",
        headers=admin_headers,
        json={
            "prompt": "از روی منبع تعداد یک ویجت میله ای بر اساس نام پیمانکار بساز تحت عنوان عملکرد پیمانکار",
            "dashboard_id": dash_id,
            "clarifications": {"dataset_id": amb_body["questions"][0]["options"][0]["value"]},
            "create_widget": True,
        },
    )
    assert step2.status_code == 200, step2.text
    s2_body = step2.json()
    assert s2_body["status"] == "needs_clarification"
    assert any(q["key"] == "x_field" for q in s2_body["questions"])

    # 3. Direct clear Persian prompt -> builds & attaches widget immediately without extra questions
    clear_res = client.post(
        "/api/v1/ai/text-to-widget",
        headers=admin_headers,
        json={
            "prompt": "از روی منبع فروش یک ویجت میله ای از مجموع فروش بر اساس منطقه بساز تحت عنوان عملکرد مناطق",
            "dashboard_id": dash_id,
            "create_widget": True,
        },
    )
    assert clear_res.status_code == 200, clear_res.text
    cj = clear_res.json()
    assert cj["status"] == "created"
    assert cj["widget"]["title"] == "عملکرد مناطق"
    assert cj["widget"]["chart_type"] == "bar"
    assert len(cj["preview_data"]["rows"]) > 0

    # Verify widget persisted on dashboard
    d_detail = client.get(f"/api/v1/dashboards/{dash_id}", headers=admin_headers).json()
    assert any(w["id"] == cj["widget"]["id"] and w["title"] == "عملکرد مناطق" for w in d_detail["widgets"])

    # Clean up test widget
    client.delete(f"/api/v1/dashboards/widgets/{cj['widget']['id']}", headers=admin_headers)

    # 4. Upload Persian CSV dataset named "تعداد" with Persian columns including "نام پیمانکار"
    csv_content = "نام پیمانکار,امتیاز عملکرد,تعداد پروژه\nپیمانکار الف,95,4\nپیمانکار ب,88,3\nپیمانکار الف,91,2\n".encode("utf-8")
    imp = client.post(
        "/api/v1/data-sources/files/import",
        headers=admin_headers,
        files={"file": ("teedad.csv", csv_content, "text/csv")},
        data={"dataset_name": "تعداد"},
    )
    assert imp.status_code == 200, imp.text

    # Prompt referencing "تعداد" and "نام پیمانکار" -> matches dataset & Persian column!
    p_res = client.post(
        "/api/v1/ai/text-to-widget",
        headers=admin_headers,
        json={
            "prompt": "از روی منبع تعداد یک ویجت میله ای بر اساس نام پیمانکار بساز تحت عنوان عملکرد پیمانکار",
            "dashboard_id": dash_id,
            "clarifications": {"y_field": "__COUNT__"},
            "create_widget": True,
        },
    )
    assert p_res.status_code == 200, p_res.text
    pj = p_res.json()
    assert pj["status"] == "created", pj
    assert pj["widget"]["title"] == "عملکرد پیمانکار"
    assert pj["widget"]["chart_type"] == "bar"
    assert len(pj["preview_data"]["rows"]) == 2
    assert {r[pj["widget_spec"]["visual_spec"]["x_field"]] for r in pj["preview_data"]["rows"]} == {"پیمانکار الف", "پیمانکار ب"}

    client.delete(f"/api/v1/dashboards/widgets/{pj['widget']['id']}", headers=admin_headers)
