import uuid
import pandas as pd
import pytest
from app.database import SessionLocal
from app.engines.warehouse import warehouse
from app.models.dashboard import Dashboard, Widget, WidgetQuery
from app.models.data import DataLineage, DataModel, Dataset, DatasetField, DatasetRelationship, SemanticMetric
from app.models.identity import AccessPolicy, Organization, ResourcePermission, Role, User, UserRole
from app.core.security import hash_password
from app.core.permissions import permission_resolver


def _cleanup_test_artifacts(
    dataset_ids: list[str],
    dashboard_ids: list[str] | None = None,
    model_ids: list[str] | None = None,
    user_ids: list[str] | None = None,
) -> None:
    with SessionLocal() as db:
        for dash_id in (dashboard_ids or []):
            db.query(Dashboard).filter(Dashboard.id == dash_id).delete()
        for mid in (model_ids or []):
            db.query(DataModel).filter(DataModel.id == mid).delete()
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
            tables = [
                r[0]
                for r in db.query(Dataset.warehouse_table).filter(Dataset.id.in_(dataset_ids)).all()
            ]
            db.query(Dataset).filter(Dataset.id.in_(dataset_ids)).delete(synchronize_session=False)
            for tbl in tables:
                try:
                    warehouse.drop_table(tbl)
                except Exception:
                    pass
        for uid in (user_ids or []):
            db.query(UserRole).filter(UserRole.user_id == uid).delete()
            db.query(User).filter(User.id == uid).delete()
        db.commit()


@pytest.fixture
def cleanup_registry():
    reg = {"dataset_ids": [], "dashboard_ids": [], "model_ids": [], "user_ids": []}
    yield reg
    _cleanup_test_artifacts(**reg)


def _create_warehouse_dataset(db, org_id: str, name: str, df: pd.DataFrame, pks: list[str] | None = None) -> Dataset:
    tbl = f"desc_test_{uuid.uuid4().hex[:10]}"
    warehouse.load_dataframe(tbl, df)
    ds = Dataset(
        organization_id=org_id,
        name=name,
        description=f"Test dataset {name}",
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
        if pd.api.types.is_integer_dtype(df[col]):
            dt = "integer"
            role = "id" if col.endswith("_id") or col in pk_set else "measure"
        elif pd.api.types.is_float_dtype(df[col]):
            dt = "double"
            role = "measure"
        elif "date" in col.lower():
            dt = "date"
            role = "time"
        db.add(DatasetField(
            dataset_id=ds.id,
            field_name=col,
            display_name=col,
            data_type=dt,
            semantic_role=role,
            is_primary_key=col in pk_set,
        ))
    db.commit()
    return ds


def test_descriptive_builder_all_core_and_security_scenarios(client, admin_headers, cleanup_registry):
    # Setup multi-source datasets in org:
    # 1. Sales (fact) -> region, dept_id, customer_id, order_date, revenue, cost, salary_secret
    # 2. Departments (dim) -> dept_id, dept_name, budget
    # 3. Customers (dim) -> customer_id, segment
    # 4. Branches (bridge/ambiguous): Sales -> Branches AND Branches -> Departments (plus disconnected)
    # 5. ManyToManyBad (for M:N explosion check)
    # 6. DisconnectedTable (no relationship)
    with SessionLocal() as db:
        org = db.query(Organization).first()
        assert org is not None

        df_sales = pd.DataFrame({
            "sale_id": [1, 2, 3, 4, 5, 6],
            "dept_id": [10, 10, 20, 20, 30, 30],
            "customer_id": [101, 102, 101, 103, 102, 104],
            "region": ["North", "North", "South", "South", "North", "South"],
            "order_date": ["2025-03-21", "2025-04-21", "2025-05-22", "2025-06-22", "2025-07-23", "2025-08-23"],
            "revenue": [1000.0, 1500.0, 2000.0, 500.0, 2500.0, 8000.0],  # 8000 is outlier
            "cost": [400.0, 600.0, 800.0, 200.0, 1000.0, 2000.0],
            "quantity": [10, 15, 20, 0, 25, 40],  # includes 0 for NULLIF division safety!
            "salary_secret": [90000, 91000, 85000, 86000, 92000, 99000],
        })
        df_depts = pd.DataFrame({
            "dept_id": [10, 20, 30],
            "dept_name": ["فروش شمال", "بازاریابی جنوب", "عملیات"],
            "target_budget": [3000.0, 3000.0, 9000.0],
        })
        df_cust = pd.DataFrame({
            "customer_id": [101, 102, 103, 104],
            "segment": ["VIP", "Enterprise", "SMB", "VIP"],
        })
        df_bridge = pd.DataFrame({
            "dept_id": [10, 20, 30],
            "region_code": ["R1", "R2", "R3"],
        })
        df_mn = pd.DataFrame({
            "dept_id": [10, 10, 10, 20, 20, 30, 30],
            "tag": ["A", "B", "C", "A", "B", "A", "B"],
        })
        df_disc = pd.DataFrame({
            "unrelated_code": ["X1", "X2"],
            "score": [11.0, 22.0],
        })

        ds_sales = _create_warehouse_dataset(db, org.id, "SalesFactDB", df_sales, pks=["sale_id"])
        ds_depts = _create_warehouse_dataset(db, org.id, "DepartmentsDB", df_depts, pks=["dept_id"])
        ds_cust = _create_warehouse_dataset(db, org.id, "CustomersDB", df_cust, pks=["customer_id"])
        ds_bridge = _create_warehouse_dataset(db, org.id, "BridgeDB", df_bridge, pks=["dept_id"])
        ds_mn = _create_warehouse_dataset(db, org.id, "ManyToManyDB", df_mn)
        ds_disc = _create_warehouse_dataset(db, org.id, "DisconnectedDB", df_disc)

        # Register Sales -> Departments (LEFT) and Bridge -> Customers? No: Sales -> Bridge -> Departments (for ambiguity later!)
        rel_sd = DatasetRelationship(
            left_dataset_id=ds_sales.id, left_field="dept_id",
            right_dataset_id=ds_depts.id, right_field="dept_id",
            join_type="left", cardinality="many_to_one",
        )
        rel_sc = DatasetRelationship(
            left_dataset_id=ds_sales.id, left_field="customer_id",
            right_dataset_id=ds_cust.id, right_field="customer_id",
            join_type="left", cardinality="many_to_one",
        )
        rel_sb = DatasetRelationship(
            left_dataset_id=ds_sales.id, left_field="dept_id",
            right_dataset_id=ds_bridge.id, right_field="dept_id",
            join_type="left", cardinality="many_to_one",
        )
        rel_smn = DatasetRelationship(
            left_dataset_id=ds_sales.id, left_field="dept_id",
            right_dataset_id=ds_mn.id, right_field="dept_id",
            join_type="inner", cardinality="many_to_many",
        )
        db.add_all([rel_sd, rel_sc, rel_sb, rel_smn])

        # Create restricted user with:
        # - RLS: region = 'North' on SalesFactDB
        # - CLS: hidden_columns = ['salary_secret'] on SalesFactDB
        # - DENY on CustomersDB!
        analyst_role = db.query(Role).filter(Role.organization_id == org.id, Role.slug == "data_analyst").first()
        assert analyst_role is not None
        r_user = User(
            organization_id=org.id,
            username=f"north_cls_{uuid.uuid4().hex[:6]}",
            email=f"north_cls_{uuid.uuid4().hex[:6]}@test.local",
            password_hash=hash_password("User!2026"),
            is_active=True,
        )
        db.add(r_user)
        db.flush()
        if analyst_role:
            db.add(UserRole(user_id=r_user.id, role_id=analyst_role.id))
        db.add(AccessPolicy(
            organization_id=org.id,
            name="Test RLS North",
            policy_type="rls",
            target_type="user",
            target_id=r_user.id,
            dataset_id=ds_sales.id,
            rule_json={"column": "region", "operator": "eq", "value": "North"},
            is_active=True,
        ))
        db.add(AccessPolicy(
            organization_id=org.id,
            name="Test CLS Hide Salary",
            policy_type="cls",
            target_type="user",
            target_id=r_user.id,
            dataset_id=ds_sales.id,
            rule_json={"hidden_columns": ["salary_secret"]},
            is_active=True,
        ))
        # Explicit deny on CustomersDB
        from app.models.identity import ResourcePermission
        db.add(ResourcePermission(
            organization_id=org.id,
            subject_type="user",
            subject_id=r_user.id,
            resource_type="dataset",
            resource_id=ds_cust.id,
            action="view",
            effect="deny",
        ))
        db.commit()
        permission_resolver.invalidate_all(org.id)

        r_username = r_user.username
        sales_id, depts_id, cust_id, bridge_id, mn_id, disc_id = (
            ds_sales.id, ds_depts.id, ds_cust.id, ds_bridge.id, ds_mn.id, ds_disc.id
        )
        cleanup_registry["dataset_ids"].extend([sales_id, depts_id, cust_id, bridge_id, mn_id, disc_id])
        cleanup_registry["user_ids"].append(r_user.id)

    login_r = client.post(
        "/api/v1/auth/login",
        json={"username": r_username, "password": "User!2026"},
        headers={"X-Auth-Mode": "bearer"},
    )
    assert login_r.status_code == 200
    r_headers = {"Authorization": f"Bearer {login_r.json()['access_token']}"}

    # Scenario 1 & 2: Catalog + Accessible Datasets Metadata (CLS omits salary_secret, RBAC omits CustomersDB for r_user)
    cat = client.get("/api/v1/descriptive-builder/catalog", headers=admin_headers)
    assert cat.status_code == 200
    cat_j = cat.json()
    assert len(cat_j["functions"]) >= 30
    assert len(cat_j["analyses"]) == 15

    r_ds_list = client.get("/api/v1/descriptive-builder/datasets", headers=r_headers).json()["datasets"]
    r_ds_ids = {d["id"] for d in r_ds_list}
    assert sales_id in r_ds_ids
    assert cust_id not in r_ds_ids  # Denied dataset is absent!
    sales_meta_r = next(d for d in r_ds_list if d["id"] == sales_id)
    assert "salary_secret" not in {f["field_name"] for f in sales_meta_r["fields"]}  # Hidden column omitted!

    # Scenario 3: No speculative JOIN when datasets are disconnected -> returns disconnected + suggestions
    rel_disc = client.post(
        "/api/v1/descriptive-builder/relationships/analyze",
        json={"dataset_ids": [sales_id, disc_id]},
        headers=admin_headers,
    )
    assert rel_disc.status_code == 200
    assert rel_disc.json()["ready_for_join"] is False
    assert len(rel_disc.json()["disconnected_datasets"]) == 1

    # Scenario 4: M:N Row-Explosion Risk Detection
    rel_mn_chk = client.post(
        "/api/v1/descriptive-builder/relationships/analyze",
        json={"dataset_ids": [sales_id, mn_id]},
        headers=admin_headers,
    )
    assert rel_mn_chk.status_code == 200
    assert len(rel_mn_chk.json()["explosion_checks"]) == 1
    assert rel_mn_chk.json()["explosion_checks"][0]["risk"] in ("moderate", "high")
    assert rel_mn_chk.json()["explosion_checks"][0]["expansion_factor"] >= 1.5

    # Scenario 5: Ambiguity Detection (add Bridge -> Departments so Sales has 2 paths to Departments? Actually Sales -> Bridge -> Depts is 2 hops, Sales -> Depts is 1 hop; add another 1-hop Sales -> Depts on another field to trigger 2 shortest paths!)
    with SessionLocal() as db:
        rel_sd_alt = DatasetRelationship(
            left_dataset_id=sales_id, left_field="sale_id",
            right_dataset_id=depts_id, right_field="dept_id",
            join_type="left", cardinality="many_to_one",
        )
        db.add(rel_sd_alt)
        db.commit()
        alt_rel_id = rel_sd_alt.id

    amb = client.post(
        "/api/v1/descriptive-builder/relationships/analyze",
        json={"dataset_ids": [sales_id, depts_id]},
        headers=admin_headers,
    )
    assert amb.status_code == 200
    assert amb.json()["ready_for_join"] is False
    assert len(amb.json()["ambiguous_paths"]) == 1
    assert len(amb.json()["ambiguous_paths"][0]["candidate_paths"]) == 2

    # Disambiguate by choosing rel_sd.id!
    disambig = client.post(
        "/api/v1/descriptive-builder/relationships/analyze",
        json={"dataset_ids": [sales_id, depts_id], "chosen_relationship_ids": [rel_sd.id]},
        headers=admin_headers,
    )
    assert disambig.status_code == 200
    assert disambig.json()["ready_for_join"] is True
    # Remove duplicate test relationship so subsequent tests stay clean
    with SessionLocal() as db:
        db.query(DatasetRelationship).filter(DatasetRelationship.id == alt_rel_id).delete()
        db.commit()

    # Scenario 6: Multi-hop bridge join path (Bridge -> Sales -> Customers)
    multihop = client.post(
        "/api/v1/descriptive-builder/relationships/analyze",
        json={"dataset_ids": [bridge_id, cust_id]},
        headers=admin_headers,
    )
    assert multihop.status_code == 200
    assert multihop.json()["ready_for_join"] is True
    assert len(multihop.json()["resolved_joins"]) == 2  # Bridge -> Sales -> Customers!

    # Scenario 7: SQL AST Allowlist & Injection Blocks + CLS Enforcement on Formulas
    for bad_expr in [
        "read_csv('/etc/passwd')",
        "getenv('PATH')",
        "1; DROP TABLE users",
        "(SELECT MAX(revenue) FROM users)",
        "unknown_evil_udf(revenue)",
    ]:
        bad_val = client.post(
            "/api/v1/descriptive-builder/formulas/validate",
            json={"dataset_ids": [sales_id], "formula": bad_expr, "calculation_type": "calculated_column"},
            headers=admin_headers,
        )
        assert bad_val.status_code == 200
        assert bad_val.json()["valid"] is False, f"Failed to reject {bad_expr}"

    # CLS hidden column access via formula MUST fail with 403!
    cls_attack = client.post(
        "/api/v1/descriptive-builder/formulas/validate",
        json={"dataset_ids": [sales_id], "formula": "salary_secret * 2", "calculation_type": "calculated_column"},
        headers=r_headers,
    )
    assert cls_attack.status_code == 403

    # RBAC denied dataset in formula/join MUST fail with 403!
    rbac_attack = client.post(
        "/api/v1/descriptive-builder/relationships/analyze",
        json={"dataset_ids": [sales_id, cust_id]},
        headers=r_headers,
    )
    assert rbac_attack.status_code == 403

    # Scenario 8: Calculated Column vs Calculated Measure type & aggregate consistency + NULLIF division-by-zero safety + Jalali
    col_with_agg = client.post(
        "/api/v1/descriptive-builder/formulas/validate",
        json={"dataset_ids": [sales_id], "formula": "SUM(revenue)", "calculation_type": "calculated_column"},
        headers=admin_headers,
    )
    assert col_with_agg.json()["valid"] is False

    # Valid Calculated Column with division by quantity (quantity contains 0 -> NULLIF protects!)
    unit_price_prev = client.post(
        "/api/v1/descriptive-builder/formulas/preview",
        json={
            "dataset_ids": [sales_id],
            "formula": "ROUND(revenue / quantity, 2)",
            "calculation_type": "calculated_column",
            "field_name": "unit_price",
        },
        headers=admin_headers,
    )
    assert unit_price_prev.status_code == 200, unit_price_prev.text
    up_j = unit_price_prev.json()
    assert up_j["valid"] is True
    assert "NULLIF" in up_j["compiled_sql"]
    assert up_j["row_count"] == 6
    assert any(r["unit_price"] is None for r in up_j["rows"])  # row with quantity=0 returned None safely!

    # Jalali formula preview!
    jalali_prev = client.post(
        "/api/v1/descriptive-builder/formulas/preview",
        json={
            "dataset_ids": [sales_id],
            "formula": "JALALI_DATE(order_date)",
            "calculation_type": "calculated_column",
            "field_name": "jalali_order_date",
        },
        headers=admin_headers,
    )
    assert jalali_prev.status_code == 200
    assert any(r["jalali_order_date"] == "1404/01/01" for r in jalali_prev.json()["rows"])

    # Scenario 9: Save Calculated Column, Nested Dependent Calculated Column, Circular Dependency Detection, & Impact Check
    cc1 = client.post(
        "/api/v1/descriptive-builder/calculated-fields",
        json={
            "dataset_id": sales_id,
            "field_name": "gross_profit",
            "display_name": "سود ناخالص",
            "formula": "revenue - cost",
            "calculation_type": "calculated_column",
        },
        headers=admin_headers,
    )
    assert cc1.status_code == 200, cc1.text
    cc1_id = cc1.json()["id"]

    cc2 = client.post(
        "/api/v1/descriptive-builder/calculated-fields",
        json={
            "dataset_id": sales_id,
            "field_name": "margin_pct",
            "display_name": "درصد حاشیه سود",
            "formula": "ROUND(gross_profit * 100.0 / NULLIF(revenue, 0), 2)",
            "calculation_type": "calculated_column",
        },
        headers=admin_headers,
    )
    assert cc2.status_code == 200, cc2.text

    # Circular dependency check (trying to update gross_profit to reference margin_pct!)
    circ = client.put(
        f"/api/v1/descriptive-builder/calculated-fields/{cc1_id}",
        json={
            "dataset_id": sales_id,
            "field_name": "gross_profit",
            "display_name": "سود ناخالص",
            "formula": "margin_pct + 10",
            "calculation_type": "calculated_column",
            "force": True,
        },
        headers=admin_headers,
    )
    assert circ.status_code == 400
    assert "چرخه" in circ.json()["detail"]

    # Impact check prevents accidental delete without force=true!
    del_blocked = client.delete(f"/api/v1/descriptive-builder/calculated-fields/{cc1_id}", headers=admin_headers)
    assert del_blocked.status_code == 409

    # Create Reusable Analytical Measure (SemanticMetric)
    sm = client.post(
        "/api/v1/descriptive-builder/measures",
        json={
            "dataset_id": sales_id,
            "dataset_ids": [sales_id, depts_id],
            "name": "نسبت درآمد به بودجه",
            "slug": "rev_to_budget",
            "formula": "ROUND((SUM(revenue) * 100.0) / NULLIF(SUM(target_budget), 0), 2)",
            "metric_kind": "analytical_metric",
            "unit": "%",
        },
        headers=admin_headers,
    )
    assert sm.status_code == 200, sm.text

    # Scenario 10: Generate Full Descriptive Dashboard across Joined Sources (Sales + Departments + Customers) with Jalali & Analyses
    gen = client.post(
        "/api/v1/descriptive-builder/generate",
        json={
            "dataset_ids": [sales_id, depts_id, cust_id],
            "title": "داشبورد جامع فروش و مشتریان",
            "dimensions": ["dept_name", "segment"],
            "measures": [{"field": "gross_profit", "aggregation": "SUM", "label": "سود ناخالص"}],
            "analysis_types": ["kpi", "trend", "org_unit_comparison", "share", "ranking", "outlier_detection", "missing_data_audit", "target_vs_actual"],
            "calendar_mode": "jalali",
            "date_range": {"field_name": "order_date", "start": "1404/01/01", "end": "1404/06/31"},
        },
        headers=admin_headers,
    )
    assert gen.status_code == 200, gen.text
    gen_j = gen.json()
    assert gen_j["total_records"] == 6
    assert len(gen_j["widgets"]) >= 7
    for w in gen_j["widgets"]:
        assert w["narrative_fa"] and len(w["narrative_fa"]) > 15
        assert "SalesFactDB" in w["narrative_fa"]

    # Scenario 11: RLS enforced inside Descriptive Generation for r_user (only North rows = 3 rows, total revenue = 1000+1500+2500=5000!)
    gen_r = client.post(
        "/api/v1/descriptive-builder/generate",
        json={
            "dataset_ids": [sales_id, depts_id],
            "title": "داشبورد شمال",
            "dimensions": ["dept_name"],
            "measures": [{"field": "revenue", "aggregation": "SUM", "label": "فروش"}],
            "analysis_types": ["kpi"],
        },
        headers=r_headers,
    )
    assert gen_r.status_code == 200, gen_r.text
    assert gen_r.json()["total_records"] == 3  # RLS North only!
    assert gen_r.json()["widgets"][0]["data"]["rows"][0]["value"] == 5000.0

    # Scenario 12: Save Descriptive Dashboard -> Persists Dashboard, Widgets, WidgetQuery, DataModel, DataLineage -> Executes Live!
    save_res = client.post(
        "/api/v1/descriptive-builder/save",
        json={
            "title": "داشبورد توصیفی نهایی تست",
            "description": gen_j["executive_summary_fa"],
            "dataset_ids": [sales_id, depts_id, cust_id],
            "model_definition": {"dataset_ids": [sales_id, depts_id, cust_id], "calendar_mode": "jalali"},
            "filters": [{"name": "بخش مشتریان", "field_name": "segment", "dataset_id": cust_id, "filter_type": "multi_select", "operator": "in"}],
            "widgets": [
                {
                    "title": w["title"],
                    "chart_type": w["chart_type"],
                    "dataset_id": w["dataset_id"],
                    "visual_spec": w["visual_spec"],
                    "style": w["style"],
                    "visualization": w["visualization"],
                    "drill_hierarchy": w.get("drill_hierarchy", []),
                    "layout": w["layout"],
                    "narrative_fa": w["narrative_fa"],
                    "sources_used": w["sources_used"],
                    "analysis_type": w["analysis_type"],
                }
                for w in gen_j["widgets"]
            ],
        },
        headers=admin_headers,
    )
    assert save_res.status_code == 200, save_res.text
    dash_id = save_res.json()["id"]
    cleanup_registry["dashboard_ids"].append(dash_id)

    # Execute saved dashboard batch via existing dashboards endpoint!
    exec_res = client.post(f"/api/v1/dashboards/{dash_id}/execute-batch", json={}, headers=admin_headers)
    assert exec_res.status_code == 200, exec_res.text
    assert len(exec_res.json()["widgets"]) == len(gen_j["widgets"])

    # Verify r_user CANNOT execute widgets of saved dashboard that join denied CustomersDB!
    exec_r = client.post(f"/api/v1/dashboards/{dash_id}/execute-batch", json={}, headers=r_headers)
    assert exec_r.status_code == 200
    for w_res in exec_r.json()["widgets"].values():
        assert w_res.get("error") is not None or w_res.get("row_count") == 0

    # Scenario 13: Public dashboard safety blocks publishing dashboard with restricted RLS/CLS datasets without override
    pub_blocked = client.put(f"/api/v1/dashboards/{dash_id}", json={"is_public": True}, headers=admin_headers)
    assert pub_blocked.status_code == 403


def test_descriptive_builder_models_measures_jalali_and_notime_layout(client, admin_headers, cleanup_registry):
    from app.engines.filter_engine import compile_filters_to_sql

    with SessionLocal() as db:
        org = db.query(Organization).first()
        assert org is not None
        df_ops = pd.DataFrame({
            "ticket_id": [1, 2, 3, 4],
            "team": ["A", "B", "A", "C"],
            "opened_at": ["2025-03-21", "2025-04-21", "2025-06-22", "2026-11-10"],
            "tickets_closed": [12.0, 25.0, 18.0, 40.0],
            "sla_met": [10.0, 20.0, 15.0, 38.0],
        })
        ds_ops = _create_warehouse_dataset(db, org.id, "OpsNoTimeDB", df_ops, pks=["ticket_id"])
        db.commit()
        ops_id = ds_ops.id
        cleanup_registry["dataset_ids"].append(ops_id)

    # 1. Jalali filter bounds & jalali_ytd / jalali_mtd in filter_engine
    res_jalali = compile_filters_to_sql(
        [{"field": "opened_at", "operator": "between", "value": ["1404/01/01", "1404/06/31"]}],
        available_columns={"opened_at", "team", "tickets_closed", "sla_met"},
    )
    assert len(res_jalali["where_clauses"]) >= 1 and res_jalali["params"] == ["2025-03-21", "2025-09-22"], res_jalali

    res_ytd = compile_filters_to_sql(
        [{"field": "opened_at", "operator": "relative_date", "value": "jalali_ytd"}],
        available_columns={"opened_at"},
    )
    assert len(res_ytd["where_clauses"]) == 1 and len(res_ytd["params"]) == 1

    # 2. DataModel versioning CRUD + SemanticMetric CRUD
    m_create = client.post(
        "/api/v1/descriptive-builder/models",
        json={
            "name": "مدل عملیات",
            "description": "نسخه اولیه",
            "definition_json": {"dataset_ids": [ops_id], "calendar_mode": "jalali"},
        },
        headers=admin_headers,
    )
    assert m_create.status_code == 200, m_create.text
    model_id = m_create.json()["id"]
    cleanup_registry["model_ids"].append(model_id)
    assert m_create.json()["version"] == 1

    m_upd = client.put(
        f"/api/v1/descriptive-builder/models/{model_id}",
        json={"name": "مدل عملیات پیشرفته", "definition_json": {"dataset_ids": [ops_id], "calendar_mode": "gregorian"}},
        headers=admin_headers,
    )
    assert m_upd.status_code == 200 and m_upd.json()["version"] == 2

    m_list = client.get("/api/v1/descriptive-builder/models", headers=admin_headers)
    assert m_list.status_code == 200
    assert any(m["id"] == model_id and m["version"] == 2 for m in m_list.json())

    meas_create = client.post(
        "/api/v1/descriptive-builder/measures",
        json={
            "dataset_id": ops_id,
            "name": "نرخ تحقق SLA",
            "slug": f"sla_rate_{uuid.uuid4().hex[:6]}",
            "formula": "(SUM(sla_met) * 100.0) / NULLIF(SUM(tickets_closed), 0)",
            "format_type": "percentage",
            "unit": "%",
        },
        headers=admin_headers,
    )
    assert meas_create.status_code == 200, meas_create.text
    meas_id = meas_create.json()["id"]

    # 3. No-time-dimension layout check + /preview without saving
    gen_notime = client.post(
        "/api/v1/descriptive-builder/generate",
        json={
            "title": "تست بدون بعد زمانی",
            "dataset_ids": [ops_id],
            "measures": [{"field_name": "tickets_closed", "aggregation": "SUM"}, {"field_name": "sla_met", "aggregation": "SUM"}],
            "dimensions": ["team"],
            "analysis_types": ["kpi", "org_unit_comparison", "distribution", "ranking", "performance"],
            "calendar_mode": "jalali",
        },
        headers=admin_headers,
    )
    assert gen_notime.status_code == 200, gen_notime.text
    widgets = gen_notime.json()["widgets"]
    assert len(widgets) >= 4
    # Ensure non-KPI widgets do not overlap on grid coordinates (y, x)
    occupied: set[tuple[int, int]] = set()
    for w in widgets:
        ly = w["layout"]
        for row in range(ly["y"], ly["y"] + ly["h"]):
            for col in range(ly["x"], ly["x"] + ly["w"]):
                assert (row, col) not in occupied, f"Overlapping widget cell {(row, col)} in {w['title']}"
                occupied.add((row, col))

    prev_w = next(w for w in widgets if w["chart_type"] != "kpi")
    prev_res = client.post(
        "/api/v1/descriptive-builder/preview",
        json={
            "dataset_id": prev_w["dataset_id"],
            "title": prev_w["title"],
            "chart_type": prev_w["chart_type"],
            "analysis_type": prev_w["analysis_type"],
            "visual_spec": prev_w["visual_spec"],
            "filters": [],
            "calendar_mode": "jalali",
        },
        headers=admin_headers,
    )
    assert prev_res.status_code == 200, prev_res.text
    assert prev_res.json()["row_count"] >= 1
    assert prev_res.json()["narrative_fa"]

    meas_del = client.delete(f"/api/v1/descriptive-builder/measures/{meas_id}", headers=admin_headers)
    assert meas_del.status_code == 200

