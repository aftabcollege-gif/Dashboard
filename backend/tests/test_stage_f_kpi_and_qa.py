def test_stage_f_custom_kpi_preview_and_honest_qa(client, admin_headers):
    datasets = client.get("/api/v1/datasets", headers=admin_headers).json()
    orders_ds = next(
        d for d in datasets
        if any(tok in d["name"].lower() or tok in d["warehouse_table"].lower() for tok in ("order", "sales", "erp"))
    )

    # 1) Custom KPI formula preview + semantic registration
    prev = client.post(
        f"/api/v1/ai/datasets/{orders_ds['id']}/kpis/preview",
        headers=admin_headers,
        json={
            "name": "نسبت سود به فروش",
            "formula": "ROUND((SUM(profit) / NULLIF(SUM(revenue), 0)) * 100, 2)",
            "format_type": "percentage",
            "unit": "%",
        },
    )
    assert prev.status_code == 200, prev.text
    pj = prev.json()
    assert pj["valid"] is True
    assert pj["value"] is not None

    reg = client.post(
        f"/api/v1/ai/datasets/{orders_ds['id']}/kpis/register",
        headers=admin_headers,
        json={
            "kpis": [
                {
                    "id": "custom_margin_stage_f",
                    "name": "حاشیه سود سفارشی F",
                    "formula": "ROUND((SUM(profit) / NULLIF(SUM(revenue), 0)) * 100, 2)",
                    "format_type": "percentage",
                    "unit": "%",
                }
            ]
        },
    )
    assert reg.status_code == 200, reg.text
    assert reg.json()["count"] == 1

    # 2) Honest SQL-backed Conversational Q&A (real answer from orders dataset)
    qa_ok = client.post(
        "/api/v1/ai/assistant",
        headers=admin_headers,
        json={
            "mode": "qa",
            "prompt": "مجموع فروش بر اساس منطقه چقدر است؟",
            "clarifications": {"dataset_id": orders_ds["id"]},
        },
    )
    assert qa_ok.status_code == 200, qa_ok.text
    qj = qa_ok.json()
    assert qj["status"] == "qa_answered", qj
    assert qj["generated_sql"]
    assert qj["preview_data"]["row_count"] >= 1
    assert orders_ds["name"] in qj["source_datasets"]

    # 3) Anti-hallucination Q&A check: ask about a metric NOT in orders dataset ("میانگین حقوق چقدر است؟")
    qa_missing = client.post(
        "/api/v1/ai/assistant",
        headers=admin_headers,
        json={
            "mode": "qa",
            "prompt": "میانگین حقوق پرسنل چقدر است؟",
            "clarifications": {"dataset_id": orders_ds["id"]},
        },
    )
    assert qa_missing.status_code == 200, qa_missing.text
    mj = qa_missing.json()
    assert mj["status"] == "needs_data", mj
    assert "حقوق" in mj["message_fa"]
    assert len(mj["available_measures"]) >= 1
