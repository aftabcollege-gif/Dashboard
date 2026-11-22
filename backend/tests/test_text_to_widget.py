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


def test_conversational_assistant_create_and_edit_dashboard(client, admin_headers):
    # 1. Ambiguous non-technical request to build a dashboard -> asks friendly Persian questions
    step1 = client.post(
        "/api/v1/ai/assistant",
        headers=admin_headers,
        json={"prompt": "می‌خوام یک داشبورد جدید بسازم"},
    )
    assert step1.status_code == 200, step1.text
    s1 = step1.json()
    assert s1["status"] == "needs_clarification"
    assert s1["action"] == "create_dashboard"
    q_keys = {q["key"] for q in s1["questions"]}
    assert {"dataset_id", "layout_preset", "theme_id"}.issubset(q_keys)

    ds_q = next(q for q in s1["questions"] if q["key"] == "dataset_id")
    contractor_opt = next(o for o in ds_q["options"] if "پیمانکار" in o["label"])
    theme_q = next(q for q in s1["questions"] if q["key"] == "theme_id")
    first_theme_id = theme_q["options"][0]["value"]

    # 2. Complete Q&A -> builds a full multi-widget dashboard with filter and theme
    step2 = client.post(
        "/api/v1/ai/assistant",
        headers=admin_headers,
        json={
            "prompt": "می‌خوام یک داشبورد جدید بسازم",
            "mode": "create_dashboard",
            "clarifications": {
                "dataset_id": contractor_opt["value"],
                "layout_preset": "executive_full",
                "theme_id": first_theme_id,
                "title": "داشبورد تست غیرفنی",
            },
        },
    )
    assert step2.status_code == 200, step2.text
    s2 = step2.json()
    assert s2["status"] == "dashboard_created", s2
    new_dash_id = s2["created_dashboard"]["id"]
    assert s2["created_dashboard"]["title"] == "داشبورد تست غیرفنی"
    assert s2["created_dashboard"]["widget_count"] >= 4

    # Verify newly created dashboard loads and executes properly
    d_det = client.get(f"/api/v1/dashboards/{new_dash_id}", headers=admin_headers)
    assert d_det.status_code == 200
    det_json = d_det.json()
    assert det_json["title"] == "داشبورد تست غیرفنی"
    assert len(det_json["widgets"]) >= 4
    assert len(det_json["filters"]) >= 1

    # 3. Edit dashboard theme & title via plain Persian
    ed_theme = client.post(
        "/api/v1/ai/assistant",
        headers=admin_headers,
        json={
            "prompt": "ظاهر داشبورد را شیشه‌ای کن",
            "dashboard_id": new_dash_id,
            "mode": "edit",
        },
    )
    assert ed_theme.status_code == 200, ed_theme.text
    et = ed_theme.json()
    assert et["status"] == "dashboard_updated"
    assert "Glass" in et["updated_dashboard"]["theme_name"]

    ed_title = client.post(
        "/api/v1/ai/assistant",
        headers=admin_headers,
        json={
            "prompt": "نام داشبورد را بگذار گزارش جامع پیمانکاران",
            "dashboard_id": new_dash_id,
            "mode": "edit",
        },
    )
    assert ed_title.status_code == 200, ed_title.text
    assert ed_title.json()["updated_dashboard"]["title"] == "گزارش جامع پیمانکاران"

    # 4. Ambiguous edit prompt -> asks which widget/scope to edit, then converts chart type & renames widget
    ed_ask = client.post(
        "/api/v1/ai/assistant",
        headers=admin_headers,
        json={
            "prompt": "می‌خواهم این داشبورد را ویرایش کنم",
            "dashboard_id": new_dash_id,
            "mode": "edit",
        },
    )
    assert ed_ask.status_code == 200, ed_ask.text
    ea = ed_ask.json()
    assert ea["status"] == "needs_clarification"
    assert ea["questions"][0]["key"] == "target_widget_id"
    bar_widget = next(w for w in det_json["widgets"] if w["chart_type"] == "bar")

    ed_w = client.post(
        "/api/v1/ai/assistant",
        headers=admin_headers,
        json={
            "prompt": "می‌خواهم این داشبورد را ویرایش کنم",
            "dashboard_id": new_dash_id,
            "mode": "edit",
            "clarifications": {
                "target_widget_id": bar_widget["id"],
                "free_text": "تبدیل به نمودار دایره‌ای کن و عنوانش را بگذار سهم پیمانکاران",
            },
        },
    )
    assert ed_w.status_code == 200, ed_w.text
    ew = ed_w.json()
    assert ew["status"] == "widget_updated", ew
    assert ew["widget"]["chart_type"] == "pie"
    assert ew["widget"]["title"] == "سهم پیمانکاران"
    assert ew["preview_data"] is not None

    # 5. Delete a widget via plain Persian
    del_w = client.post(
        "/api/v1/ai/assistant",
        headers=admin_headers,
        json={
            "prompt": "این نمودار را حذف کن",
            "dashboard_id": new_dash_id,
            "widget_id": bar_widget["id"],
            "mode": "edit",
        },
    )
    assert del_w.status_code == 200, del_w.text
    assert del_w.json()["status"] == "widget_deleted"

    # Clean up test dashboard
    del_d = client.delete(f"/api/v1/dashboards/{new_dash_id}", headers=admin_headers)
    assert del_d.status_code in (200, 204)

