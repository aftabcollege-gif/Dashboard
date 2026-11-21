def test_persian_csv_and_text_to_widget_with_clarification(client, admin_headers):
    # 1. Verify sample dataset "تعداد" exists in sample data and get target dashboard
    datasets = client.get("/api/v1/datasets", headers=admin_headers).json()
    assert any(d["name"] == "تعداد" and d["warehouse_table"] == "wh_contractor_counts" for d in datasets)
    dashes = client.get("/api/v1/dashboards", headers=admin_headers).json()
    assert len(dashes) >= 1
    dash_id = dashes[0]["id"]

    # 2. Ambiguous unknown source/column prompt -> asks Persian clarification on dataset_id, then x_field
    amb = client.post(
        "/api/v1/ai/text-to-widget",
        headers=admin_headers,
        json={
            "prompt": "از روی منبع انبار مرکزی یک ویجت میله ای بر اساس نام شعبه بساز تحت عنوان وضعیت شعب",
            "dashboard_id": dash_id,
            "create_widget": True,
        },
    )
    assert amb.status_code == 200, amb.text
    amb_body = amb.json()
    assert amb_body["status"] == "needs_clarification"
    assert any(q["key"] == "dataset_id" for q in amb_body["questions"])
    assert "انبار مرکزی" in amb_body["questions"][0]["question"]

    step2 = client.post(
        "/api/v1/ai/text-to-widget",
        headers=admin_headers,
        json={
            "prompt": "از روی منبع انبار مرکزی یک ویجت میله ای بر اساس نام شعبه بساز تحت عنوان وضعیت شعب",
            "dashboard_id": dash_id,
            "clarifications": {"dataset_id": amb_body["questions"][0]["options"][0]["value"]},
            "create_widget": True,
        },
    )
    assert step2.status_code == 200, step2.text
    s2_body = step2.json()
    assert s2_body["status"] == "needs_clarification"
    assert any(q["key"] == "x_field" for q in s2_body["questions"])

    # 3. User's exact sample prompt against seeded sample dataset "تعداد"
    # Matches source "تعداد" + dimension "نام پیمانکار" ("contractor_name") and asks only for y_field!
    sample_prompt = "از روی منبع تعداد یک ویجت میله ای بر اساس نام پیمانکار بساز تحت عنوان عملکرد پیمانکار"
    sample_ask = client.post(
        "/api/v1/ai/text-to-widget",
        headers=admin_headers,
        json={"prompt": sample_prompt, "dashboard_id": dash_id, "create_widget": True},
    )
    assert sample_ask.status_code == 200, sample_ask.text
    sa_body = sample_ask.json()
    assert sa_body["status"] == "needs_clarification"
    assert [q["key"] for q in sa_body["questions"]] == ["y_field"]
    assert sa_body["parsed"]["x_field"] == "contractor_name"

    # Resolve y_field with AVG:performance_score (from option click) -> builds real widget!
    sample_done = client.post(
        "/api/v1/ai/text-to-widget",
        headers=admin_headers,
        json={
            "prompt": sample_prompt,
            "dashboard_id": dash_id,
            "clarifications": {
                "dataset_id": sa_body["parsed"]["dataset_id"],
                "x_field": "contractor_name",
                "y_field": "AVG:performance_score",
            },
            "create_widget": True,
        },
    )
    assert sample_done.status_code == 200, sample_done.text
    sd_body = sample_done.json()
    assert sd_body["status"] == "created"
    assert sd_body["widget"]["title"] == "عملکرد پیمانکار"
    assert sd_body["widget_spec"]["visual_spec"]["aggregation"] == "AVG"
    assert len(sd_body["preview_data"]["rows"]) == 6
    client.delete(f"/api/v1/dashboards/widgets/{sd_body['widget']['id']}", headers=admin_headers)

    # 4. Direct unambiguous Persian prompt on sample "تعداد" -> builds immediately with SUM(project_count)!
    direct_cnt = client.post(
        "/api/v1/ai/text-to-widget",
        headers=admin_headers,
        json={
            "prompt": "از روی منبع تعداد یک ویجت میله ای از مجموع تعداد پروژه بر اساس نام پیمانکار بساز تحت عنوان عملکرد پیمانکار",
            "dashboard_id": dash_id,
            "create_widget": True,
        },
    )
    assert direct_cnt.status_code == 200, direct_cnt.text
    dc_body = direct_cnt.json()
    assert dc_body["status"] == "created", dc_body
    assert dc_body["widget"]["title"] == "عملکرد پیمانکار"
    assert dc_body["widget_spec"]["visual_spec"]["y_field"] == "project_count"
    assert dc_body["widget_spec"]["visual_spec"]["aggregation"] == "SUM"
    assert len(dc_body["preview_data"]["rows"]) == 6
    client.delete(f"/api/v1/dashboards/widgets/{dc_body['widget']['id']}", headers=admin_headers)

    # 5. Direct clear Persian prompt on sales -> builds & attaches widget immediately
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
    d_detail = client.get(f"/api/v1/dashboards/{dash_id}", headers=admin_headers).json()
    assert any(w["id"] == cj["widget"]["id"] and w["title"] == "عملکرد مناطق" for w in d_detail["widgets"])
    client.delete(f"/api/v1/dashboards/widgets/{cj['widget']['id']}", headers=admin_headers)

    # 6. Upload custom Persian CSV dataset and build widget directly from its Persian headers
    csv_content = "نام نماینده,امتیاز مشتری,تعداد سفارش\nنماینده تهران,95,4\nنماینده شیراز,88,3\nنماینده تهران,91,2\n".encode("utf-8")
    imp = client.post(
        "/api/v1/data-sources/files/import",
        headers=admin_headers,
        files={"file": ("agents.csv", csv_content, "text/csv")},
        data={"dataset_name": "عملکرد نمایندگان"},
    )
    assert imp.status_code == 200, imp.text

    p_res = client.post(
        "/api/v1/ai/text-to-widget",
        headers=admin_headers,
        json={
            "prompt": "از روی منبع عملکرد نمایندگان یک ویجت میله ای از تعداد رکورد بر اساس نام نماینده بساز تحت عنوان گزارش نمایندگان",
            "dashboard_id": dash_id,
            "create_widget": True,
        },
    )
    assert p_res.status_code == 200, p_res.text
    pj = p_res.json()
    assert pj["status"] == "created", pj
    assert pj["widget"]["title"] == "گزارش نمایندگان"
    assert pj["widget"]["chart_type"] == "bar"
    assert len(pj["preview_data"]["rows"]) == 2
    assert {r[pj["widget_spec"]["visual_spec"]["x_field"]] for r in pj["preview_data"]["rows"]} == {"نماینده تهران", "نماینده شیراز"}
    client.delete(f"/api/v1/dashboards/widgets/{pj['widget']['id']}", headers=admin_headers)
