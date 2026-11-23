import uuid
import pandas as pd
import pytest

from app.core.permissions import permission_resolver
from app.core.security import hash_password
from app.database import SessionLocal
from app.engines.warehouse import warehouse
from app.models.dashboard import Dashboard, DashboardPage, Widget
from app.models.data import DataLineage, Dataset, DatasetField, DatasetRelationship, SemanticMetric
from app.models.identity import AccessPolicy, Organization, ResourcePermission, Role, User, UserRole


def _cleanup(dataset_ids: list[str], dashboard_ids: list[str], user_ids: list[str]) -> None:
    with SessionLocal() as db:
        for dash_id in dashboard_ids:
            db.query(Dashboard).filter(Dashboard.id == dash_id).delete()
        if dataset_ids:
            db.query(AccessPolicy).filter(AccessPolicy.dataset_id.in_(dataset_ids)).delete(synchronize_session=False)
            db.query(ResourcePermission).filter(
                ResourcePermission.resource_type == "dataset",
                ResourcePermission.resource_id.in_(dataset_ids),
            ).delete(synchronize_session=False)
            db.query(DatasetRelationship).filter(
                (DatasetRelationship.left_dataset_id.in_(dataset_ids))
                | (DatasetRelationship.right_dataset_id.in_(dataset_ids))
            ).delete(synchronize_session=False)
            db.query(SemanticMetric).filter(SemanticMetric.dataset_id.in_(dataset_ids)).delete(synchronize_session=False)
            db.query(DatasetField).filter(DatasetField.dataset_id.in_(dataset_ids)).delete(synchronize_session=False)
            db.query(DataLineage).filter(DataLineage.source_id.in_(dataset_ids)).delete(synchronize_session=False)
            tables = [r[0] for r in db.query(Dataset.warehouse_table).filter(Dataset.id.in_(dataset_ids)).all()]
            db.query(Dataset).filter(Dataset.id.in_(dataset_ids)).delete(synchronize_session=False)
            for tbl in tables:
                try:
                    warehouse.drop_table(tbl)
                except Exception:
                    pass
        for uid in user_ids:
            db.query(UserRole).filter(UserRole.user_id == uid).delete()
            db.query(User).filter(User.id == uid).delete()
        db.commit()


@pytest.fixture
def kpi_cleanup():
    reg = {"dataset_ids": [], "dashboard_ids": [], "user_ids": []}
    yield reg
    _cleanup(**reg)


def _make_dataset(db, org_id: str, name: str, df: pd.DataFrame, pks: list[str] | None = None) -> Dataset:
    tbl = f"kpi_test_{uuid.uuid4().hex[:10]}"
    warehouse.load_dataframe(tbl, df)
    ds = Dataset(
        organization_id=org_id,
        name=name,
        description=f"Test {name}",
        warehouse_table=tbl,
        schema_type="fact",
        row_count=len(df),
        freshness_status="fresh",
        data_version=1,
    )
    db.add(ds)
    db.flush()
    pk_set = set(pks or [])
    for col in df.columns:
        dt = "string"
        role = "dimension"
        if pd.api.types.is_bool_dtype(df[col]):
            dt = "boolean"
            role = "dimension"
        elif pd.api.types.is_integer_dtype(df[col]):
            dt = "integer"
            role = "id" if col.endswith("_id") or col in pk_set else "measure"
        elif pd.api.types.is_float_dtype(df[col]):
            dt = "double"
            role = "measure"
        elif "date" in col.lower() or "time" in col.lower():
            dt = "date"
            role = "time"
        db.add(
            DatasetField(
                dataset_id=ds.id,
                field_name=col,
                display_name=col,
                data_type=dt,
                semantic_role=role,
                is_primary_key=col in pk_set,
            )
        )
    db.commit()
    return ds


def test_semantic_classes_endpoint(client, admin_headers):
    res = client.get("/api/v1/ai/semantic-classes", headers=admin_headers)
    assert res.status_code == 200
    classes = res.json()
    assert len(classes) == 22
    by_id = {c["id"]: c for c in classes}
    assert by_id["Percentage"]["sum_allowed"] is False
    assert by_id["Balance"]["sum_allowed"] is False
    assert by_id["Identifier"]["sum_allowed"] is False
    assert by_id["Revenue"]["sum_allowed"] is True
    assert by_id["Profit"]["sum_allowed"] is True


def test_multi_domain_classification_kpis_pages_and_live_execution(client, admin_headers, kpi_cleanup):
    # 1. Sales / E-commerce dataset with daily dates, targets, percentages (sum banned!), revenue, cost, profit
    dates = pd.date_range("2025-01-01", periods=30, freq="D").strftime("%Y-%m-%d").tolist()
    df_sales = pd.DataFrame({
        "order_id": list(range(1001, 1031)),
        "customer_id": [f"C{i % 10}" for i in range(30)],
        "order_date": dates,
        "region": ["تهران", "اصفهان", "شیراز", "مشهد", "تبریز"] * 6,
        "category": ["الکترونیک", "پوشاک", "خانه"] * 10,
        "revenue": [10000 + i * 350 for i in range(30)],
        "cost": [6000 + i * 200 for i in range(30)],
        "profit": [4000 + i * 150 for i in range(30)],
        "target_revenue": [12000 for _ in range(30)],
        "quantity": [5 + (i % 7) for i in range(30)],
        "margin_pct": [round((4000 + i * 150) / (10000 + i * 350) * 100, 2) for i in range(30)],
        "unit_price": [2000 + i * 20 for i in range(30)],
    })

    # 2. HR dataset
    df_hr = pd.DataFrame({
        "employee_id": [1, 2, 3, 4, 5, 6, 7, 8],
        "department": ["فنی", "فنی", "فروش", "فروش", "مالی", "منابع انسانی", "مالی", "فنی"],
        "hire_date": ["2023-01-10", "2023-06-15", "2024-02-01", "2024-05-12", "2024-07-01", "2024-09-10", "2025-01-02", "2025-02-15"],
        "salary": [45000000, 52000000, 38000000, 40000000, 42000000, 35000000, 43000000, 55000000],
        "absence_days": [1, 0, 3, 2, 1, 0, 4, 1],
        "performance_score": [92.5, 95.0, 81.0, 84.5, 88.0, 90.0, 79.5, 96.0],
        "attrition_flag": [0, 0, 1, 0, 0, 0, 1, 0],
    })

    # 3. Banking / Finance dataset (balance is semi-additive -> SUM forbidden!)
    df_bank = pd.DataFrame({
        "tx_id": [1, 2, 3, 4, 5, 6],
        "tx_date": ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-04", "2025-01-05", "2025-01-06"],
        "branch": ["مرکزی", "ونک", "تجریش", "مرکزی", "ونک", "تجریش"],
        "account_balance": [100000, 120000, 95000, 110000, 130000, 102000],
        "loan_amount": [50000, 80000, 0, 60000, 90000, 20000],
        "interest_rate": [18.0, 21.5, 0.0, 19.0, 22.0, 18.5],
        "default_flag": [0, 1, 0, 0, 1, 0],
    })

    # 4. Sparse / dirty dataset with nulls & zero denominator edge cases
    df_sparse = pd.DataFrame({
        "item_id": [1, 2, 3, 4],
        "note": ["a", "b", None, "d"],
        "revenue": [100.0, None, 0.0, 200.0],
        "cost": [0.0, 0.0, 0.0, 0.0],
    })

    with SessionLocal() as db:
        org = db.query(Organization).first()
        assert org is not None
        ds_sales = _make_dataset(db, org.id, "فروش آزمایشی KPI", df_sales, pks=["order_id"])
        ds_hr = _make_dataset(db, org.id, "منابع انسانی KPI", df_hr, pks=["employee_id"])
        ds_bank = _make_dataset(db, org.id, "بانکداری KPI", df_bank, pks=["tx_id"])
        ds_sparse = _make_dataset(db, org.id, "داده پراکنده KPI", df_sparse, pks=["item_id"])
        kpi_cleanup["dataset_ids"].extend([ds_sales.id, ds_hr.id, ds_bank.id, ds_sparse.id])

    # A. Sales recommendation checks
    rec_sales = client.get(f"/api/v1/ai/recommend/{ds_sales.id}", headers=admin_headers)
    assert rec_sales.status_code == 200
    sdata = rec_sales.json()
    assert sdata["domain_detection"]["primary_domain"] == "sales_marketing"
    assert sdata["domain_detection"]["confidence"] > 0.5
    assert sdata["deep_profile"]["row_count"] == 30
    assert sdata["deep_profile"]["completeness_pct"] == 100.0

    cls_by_name = {c["field_name"]: c for c in sdata["field_classifications"]}
    assert cls_by_name["margin_pct"]["semantic_class"] == "Percentage"
    assert cls_by_name["margin_pct"]["sum_allowed"] is False
    assert cls_by_name["unit_price"]["sum_allowed"] is False
    assert cls_by_name["order_id"]["sum_allowed"] is False
    assert cls_by_name["revenue"]["sum_allowed"] is True

    # Ensure NO valid/selected business KPI ever attempts SUM("margin_pct") or SUM("unit_price") or SUM("order_id")
    # AND confirm those invalid aggregations ARE transparently moved to `uncomputable` with Persian explanations!
    all_biz = sdata["kpis"]["all_business"]
    for k in all_biz:
        f_upper = k["formula"].upper()
        assert 'SUM("MARGIN_PCT")' not in f_upper
        assert 'SUM("UNIT_PRICE")' not in f_upper
        assert 'SUM("ORDER_ID")' not in f_upper

    uncomp_ids = {u["id"] for u in sdata["kpis"]["uncomputable"]}
    assert "blocked_sum_order_id" in uncomp_ids
    assert "blocked_sum_margin_pct" in uncomp_ids

    # Verify DQ KPIs are cleanly separated & live SQL works
    dq_kpis = sdata["kpis"]["data_quality"]
    assert len(dq_kpis) >= 1
    assert any("completeness" in d["id"] for d in dq_kpis)

    # Verify smart-kpis descriptive builder endpoint
    dk = client.get(f"/api/v1/descriptive-builder/smart-kpis/{ds_sales.id}", headers=admin_headers)
    assert dk.status_code == 200
    assert len(dk.json()["kpis"]) >= 1

    # B. HR & Banking domains
    rec_hr = client.get(f"/api/v1/ai/recommend/{ds_hr.id}", headers=admin_headers)
    assert rec_hr.status_code == 200
    assert rec_hr.json()["domain_detection"]["primary_domain"] == "hr"

    rec_bank = client.get(f"/api/v1/ai/recommend/{ds_bank.id}", headers=admin_headers)
    assert rec_bank.status_code == 200
    assert rec_bank.json()["domain_detection"]["primary_domain"] == "finance_accounting"
    bank_cls = {c["field_name"]: c for c in rec_bank.json()["field_classifications"]}
    assert bank_cls["account_balance"]["semantic_class"] == "Balance"
    assert bank_cls["account_balance"]["sum_allowed"] is False
    assert bank_cls["interest_rate"]["sum_allowed"] is False

    # C. Sparse dataset with NULLs & all-zero cost (NULLIF safety!)
    rec_sparse = client.get(f"/api/v1/ai/recommend/{ds_sparse.id}", headers=admin_headers)
    assert rec_sparse.status_code == 200
    sp_data = rec_sparse.json()
    assert sp_data["deep_profile"]["completeness_pct"] < 100.0
    # Zero division KPI should fail gracefully or compute null without throwing 500
    assert len(sp_data["kpis"]["data_quality"]) >= 2

    # D. Generate full multi-page dashboard in analytical mode + jalali calendar + verify every widget runs cleanly!
    gen_res = client.post(
        "/api/v1/ai/generate-dashboard",
        headers=admin_headers,
        json={
            "dataset_id": ds_sales.id,
            "title": "داشبورد هوشمند تست جامع",
            "template_mode": "analytical",
            "detail_level": "comprehensive",
            "calendar_mode": "jalali",
            "max_pages": 7,
        },
    )
    assert gen_res.status_code == 200
    gen_json = gen_res.json()
    dash_id = gen_json["dashboard_id"]
    kpi_cleanup["dashboard_ids"].append(dash_id)
    assert gen_json["pages_count"] >= 4
    assert gen_json["widgets_count"] >= 8

    dash_res = client.get(f"/api/v1/dashboards/{dash_id}", headers=admin_headers)
    assert dash_res.status_code == 200
    dash = dash_res.json()
    pages = dash["pages"]
    assert len(pages) >= 4
    # In analytical mode, "تحلیل آماری و توزیع" comes right after executive summary
    assert "آماری" in pages[1]["title"] or "روند" in pages[1]["title"]

    chart_types = {w["chart_type"] for w in dash["widgets"]}
    assert "kpi" in chart_types
    assert "bullet" in chart_types
    assert "calendar_heatmap" in chart_types
    assert "grouped_bar" in chart_types
    assert "histogram" in chart_types

    # Verify every page has collision-free 12-col widget grid coordinates
    for p in pages:
        p_widgets = [w for w in dash["widgets"] if w["page_id"] == p["id"]]
        for i, wi in enumerate(p_widgets):
            li = wi["layout"]
            assert li["x"] >= 0 and li["y"] >= 0 and li["w"] >= 2 and li["h"] >= 2
            assert li["x"] + li["w"] <= 12, f"Widget {wi['title']} overflows 12 cols: {li}"
            for wj in p_widgets[i + 1 :]:
                lj = wj["layout"]
                overlap_x = li["x"] < lj["x"] + lj["w"] and lj["x"] < li["x"] + li["w"]
                overlap_y = li["y"] < lj["y"] + lj["h"] and lj["y"] < li["y"] + li["h"]
                assert not (overlap_x and overlap_y), (
                    f"Page {p['title']} layout overlap between {wi['title']} {li} and {wj['title']} {lj}"
                )

    # Execute EVERY generated widget query across ALL pages via execute-batch against DuckDB and confirm zero errors!
    batch_res = client.post(f"/api/v1/dashboards/{dash_id}/execute-batch", headers=admin_headers, json={})
    assert batch_res.status_code == 200, batch_res.text
    results = batch_res.json().get("widgets", {})
    assert len(results) == len(dash["widgets"])
    for w in dash["widgets"]:
        wres = results[w["id"]]
        assert not wres.get("error"), f"Widget {w['title']} ({w['chart_type']}) failed SQL: {wres.get('error')}"
        assert wres.get("row_count", 0) >= 0


def test_overrides_custom_preview_register_and_cls_security(client, admin_headers, kpi_cleanup):
    df = pd.DataFrame({
        "order_id": [1, 2, 3, 4],
        "region": ["North", "South", "North", "West"],
        "revenue": [5000, 7000, 6000, 8000],
        "cost": [3000, 4000, 3500, 5000],
        "secret_margin": [2000, 3000, 2500, 3000],
    })
    with SessionLocal() as db:
        org = db.query(Organization).first()
        assert org is not None
        ds = _make_dataset(db, org.id, "CLS KPI Dataset", df, pks=["order_id"])
        kpi_cleanup["dataset_ids"].append(ds.id)

        # Create restricted user with CLS hiding secret_margin
        role = db.query(Role).filter(Role.slug == "analyst").first() or db.query(Role).first()
        r_user = User(
            organization_id=org.id,
            username=f"cls_kpi_user_{uuid.uuid4().hex[:6]}",
            email=f"cls_kpi_{uuid.uuid4().hex[:6]}@example.com",
            password_hash=hash_password("Pass1234!"),
            is_active=True,
        )
        db.add(r_user)
        db.flush()
        db.add(UserRole(user_id=r_user.id, role_id=role.id))
        db.add(
            ResourcePermission(
                organization_id=org.id,
                subject_type="user",
                subject_id=r_user.id,
                resource_type="dataset",
                resource_id=ds.id,
                action="view",
                effect="allow",
            )
        )
        db.add(
            ResourcePermission(
                organization_id=org.id,
                subject_type="user",
                subject_id=r_user.id,
                resource_type="dataset",
                resource_id=ds.id,
                action="edit",
                effect="allow",
            )
        )
        db.add(
            AccessPolicy(
                organization_id=org.id,
                name="Test CLS Hide Secret Margin",
                policy_type="cls",
                target_type="user",
                target_id=r_user.id,
                dataset_id=ds.id,
                rule_json={"hidden_columns": ["secret_margin"]},
                is_active=True,
            )
        )
        db.commit()
        permission_resolver.invalidate_all(org.id)
        kpi_cleanup["user_ids"].append(r_user.id)
        restricted_uname = r_user.username

    # Login via /auth/login cleanly for restricted user
    login_res = client.post(
        "/api/v1/auth/login",
        json={"username": restricted_uname, "password": "Pass1234!"},
        headers={"X-Auth-Mode": "bearer"},
    )
    assert login_res.status_code == 200
    restricted_headers = {"Authorization": f"Bearer {login_res.json()['access_token']}"}

    # 1. Admin saves field override -> persists on DatasetField.user_override_json
    ov_res = client.post(
        f"/api/v1/ai/datasets/{ds.id}/field-overrides",
        headers=admin_headers,
        json={
            "overrides": [
                {
                    "field_name": "revenue",
                    "semantic_class": "Revenue",
                    "display_name": "فروش کل ویژه",
                    "target_value": 30000,
                }
            ]
        },
    )
    assert ov_res.status_code == 200
    assert ov_res.json()["updated_fields"] == ["revenue"]

    rec_after = client.get(f"/api/v1/ai/recommend/{ds.id}?refresh=true", headers=admin_headers)
    assert rec_after.status_code == 200
    rev_cls = next(c for c in rec_after.json()["field_classifications"] if c["field_name"] == "revenue")
    assert rev_cls["display_name"] == "فروش کل ویژه"
    assert rev_cls["target_value"] == 30000

    # 2. Live Custom KPI formula preview (valid + unsafe injection rejected)
    good_prev = client.post(
        f"/api/v1/ai/datasets/{ds.id}/kpis/preview",
        headers=admin_headers,
        json={"name": "نسبت درآمد به هزینه", "formula": 'SUM("revenue") / NULLIF(SUM("cost"), 0)'},
    )
    assert good_prev.status_code == 200
    assert good_prev.json()["value"] > 1.5

    bad_prev = client.post(
        f"/api/v1/ai/datasets/{ds.id}/kpis/preview",
        headers=admin_headers,
        json={"name": "Unsafe", "formula": 'DROP TABLE datasets; SUM("revenue")'},
    )
    assert bad_prev.status_code == 400

    # 3. Register KPIs to Semantic Layer -> persists into SemanticMetric
    reg_res = client.post(
        f"/api/v1/ai/datasets/{ds.id}/kpis/register",
        headers=admin_headers,
        json={
            "kpis": [
                {
                    "name": "نسبت درآمد به هزینه",
                    "slug": "rev_to_cost_ratio",
                    "formula": 'SUM("revenue") / NULLIF(SUM("cost"), 0)',
                    "format_type": "number",
                    "category": "ratio",
                    "confidence": 0.96,
                    "target_value": 1.8,
                    "is_favorite": True,
                }
            ]
        },
    )
    assert reg_res.status_code == 200
    assert reg_res.json()["count"] == 1

    with SessionLocal() as db:
        sm = (
            db.query(SemanticMetric)
            .filter(
                SemanticMetric.dataset_id == ds.id,
                SemanticMetric.slug == f"{ds.id[:6]}_rev_to_cost_ratio",
            )
            .first()
        )
        assert sm is not None
        assert sm.category == "ratio"
        assert float(sm.target_value) == 1.8

    # 4. Restricted CLS User cannot see or query denied column `secret_margin`
    r_rec = client.get(f"/api/v1/ai/recommend/{ds.id}", headers=restricted_headers)
    assert r_rec.status_code == 200
    r_fields = {c["field_name"] for c in r_rec.json()["field_classifications"]}
    assert "secret_margin" not in r_fields
    for k in r_rec.json()["kpis"]["all_business"]:
        assert "secret_margin" not in k["formula"].lower()

    r_prev_secret = client.post(
        f"/api/v1/ai/datasets/{ds.id}/kpis/preview",
        headers=restricted_headers,
        json={"name": "Secret", "formula": 'SUM("secret_margin")'},
    )
    assert r_prev_secret.status_code in (400, 403)
