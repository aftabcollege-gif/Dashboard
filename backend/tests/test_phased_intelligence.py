from datetime import datetime, timedelta, timezone
import io
import time
import uuid
import pandas as pd
import pytest

from app.core.security import hash_password
from app.database import SessionLocal
from app.engines.warehouse import warehouse
from app.models.data import Dataset, DatasetField, DatasetRelationship
from app.models.identity import AccessPolicy, Organization, ResourcePermission, Role, User, UserProfile, UserRole
from app.models.intelligence import (
    ProcessCorrectiveAction,
    ProcessDatasetMapping,
    SecurityAlert,
    SecurityCollector,
    SecurityEvent,
    SecurityIncident,
    SecurityUnparsedEvent,
)


def _create_ds(db, org_id: str, name: str, df: pd.DataFrame, pks: list[str] | None = None) -> Dataset:
    tbl = f"ph_test_{uuid.uuid4().hex[:10]}"
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
        elif pd.api.types.is_integer_dtype(df[col]):
            dt = "integer"
            role = "id" if col.endswith("_id") or col in pk_set else "measure"
        elif pd.api.types.is_float_dtype(df[col]):
            dt = "double"
            role = "measure"
        elif "time" in col.lower() or "date" in col.lower():
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


def _cleanup_all(dataset_ids: list[str], user_ids: list[str], collector_ids: list[str]) -> None:
    with SessionLocal() as db:
        if collector_ids:
            db.query(SecurityAlert).filter(SecurityAlert.collector_id.in_(collector_ids)).delete(synchronize_session=False)
            db.query(SecurityUnparsedEvent).filter(SecurityUnparsedEvent.collector_id.in_(collector_ids)).delete(synchronize_session=False)
            db.query(SecurityEvent).filter(SecurityEvent.collector_id.in_(collector_ids)).delete(synchronize_session=False)
            db.query(SecurityCollector).filter(SecurityCollector.id.in_(collector_ids)).delete(synchronize_session=False)
        if dataset_ids:
            db.query(ProcessCorrectiveAction).filter(ProcessCorrectiveAction.dataset_id.in_(dataset_ids)).delete(synchronize_session=False)
            db.query(ProcessDatasetMapping).filter(ProcessDatasetMapping.dataset_id.in_(dataset_ids)).delete(synchronize_session=False)
            db.query(AccessPolicy).filter(AccessPolicy.dataset_id.in_(dataset_ids)).delete(synchronize_session=False)
            db.query(DatasetRelationship).filter(
                (DatasetRelationship.left_dataset_id.in_(dataset_ids))
                | (DatasetRelationship.right_dataset_id.in_(dataset_ids))
            ).delete(synchronize_session=False)
            db.query(DatasetField).filter(DatasetField.dataset_id.in_(dataset_ids)).delete(synchronize_session=False)
            tables = [r[0] for r in db.query(Dataset.warehouse_table).filter(Dataset.id.in_(dataset_ids)).all()]
            db.query(Dataset).filter(Dataset.id.in_(dataset_ids)).delete(synchronize_session=False)
            for t in tables:
                try:
                    warehouse.drop_table(t)
                except Exception:
                    pass
        for uid in user_ids:
            db.query(UserRole).filter(UserRole.user_id == uid).delete()
            db.query(UserProfile).filter(UserProfile.user_id == uid).delete()
            db.query(User).filter(User.id == uid).delete()
        db.commit()


@pytest.fixture
def phase_cleanup():
    reg = {"dataset_ids": [], "user_ids": [], "collector_ids": []}
    yield reg
    _cleanup_all(**reg)


def _login_role_user(client, org_id: str, role_slug: str, region: str = "All", reg_list: list[str] | None = None) -> dict[str, str]:
    uname = f"{role_slug}_{uuid.uuid4().hex[:6]}"
    pw = "Strong!Test2026"
    with SessionLocal() as db:
        role = db.query(Role).filter(Role.organization_id == org_id, Role.slug == role_slug).first()
        assert role is not None, f"Missing seeded role {role_slug}"
        u = User(
            organization_id=org_id,
            username=uname,
            email=f"{uname}@test.local",
            password_hash=hash_password(pw),
            auth_source="local",
            is_active=True,
        )
        db.add(u)
        db.flush()
        db.add(UserProfile(user_id=u.id, full_name=uname, department="IT", region=region))
        db.add(UserRole(user_id=u.id, role_id=role.id))
        db.commit()
        if reg_list is not None:
            reg_list.append(u.id)
    r = client.post("/api/v1/auth/login", json={"username": uname, "password": pw}, headers={"X-Auth-Mode": "bearer"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_phase1_domains_disambiguation_joins_and_formulas(client, admin_headers, phase_cleanup):
    with SessionLocal() as db:
        org = db.query(Organization).first()
        # 1. Mixed / Ambiguous dataset combining Sales + Security clues
        df_mixed = pd.DataFrame([
            {"order_id": "O1", "revenue": 1200.0, "action": "Block", "severity": "high", "src_ip": "10.0.0.1", "event_time": "2026-10-01"},
            {"order_id": "O2", "revenue": 800.0, "action": "Detect", "severity": "medium", "src_ip": "10.0.0.2", "event_time": "2026-10-02"},
            {"order_id": "O3", "revenue": 1500.0, "action": "Quarantine", "severity": "critical", "src_ip": "10.0.0.3", "event_time": "2026-10-03"},
        ])
        ds_mixed = _create_ds(db, org.id, "Hybrid Sales Security Events", df_mixed, pks=["order_id"])
        phase_cleanup["dataset_ids"].append(ds_mixed.id)

        # 2. Pure Security dataset + Network dataset for domain + fan-out join check
        df_sec = pd.DataFrame([
            {"event_id": "E1", "vendor": "Syslog", "action": "Block", "severity": "high", "src_ip": "192.168.1.5", "hostname": "SRV-1", "wait_hours": 12.0, "sla_hours": 8.0},
            {"event_id": "E2", "vendor": "Syslog", "action": "Failed", "severity": "critical", "src_ip": "192.168.1.5", "hostname": "SRV-1", "wait_hours": 4.0, "sla_hours": 8.0},
            {"event_id": "E3", "vendor": "Syslog", "action": "Quarantine", "severity": "medium", "src_ip": "192.168.1.9", "hostname": "SRV-2", "wait_hours": 10.0, "sla_hours": 8.0},
            {"event_id": "E4", "vendor": "Syslog", "action": "Detect", "severity": "low", "src_ip": "192.168.1.9", "hostname": "SRV-2", "wait_hours": 2.0, "sla_hours": 8.0},
        ])
        ds_sec = _create_ds(db, org.id, "Security Threat Telemetry", df_sec, pks=["event_id"])
        phase_cleanup["dataset_ids"].append(ds_sec.id)

        df_net = pd.DataFrame([
            {"hostname": "SRV-1", "bytes_in": 50000, "latency_ms": 14.2},
            {"hostname": "SRV-1", "bytes_in": 60000, "latency_ms": 18.5},
            {"hostname": "SRV-2", "bytes_in": 20000, "latency_ms": 9.1},
        ])
        ds_net = _create_ds(db, org.id, "Network Interface Metrics", df_net)
        phase_cleanup["dataset_ids"].append(ds_net.id)

    # Test ambiguity & disambiguation on mixed dataset
    rec_mix = client.get(f"/api/v1/ai/recommend/{ds_mixed.id}", headers=admin_headers)
    assert rec_mix.status_code == 200
    dom_mix = rec_mix.json()["domain_detection"]
    assert dom_mix["domain_family"] in ("Mixed", "Security", "Business")
    assert dom_mix["is_ambiguous"] is True
    assert dom_mix["clarification_question"] is not None

    # Override domain to Security and verify confirmation
    ov_res = client.post(
        f"/api/v1/ai/datasets/{ds_mixed.id}/domain-override",
        json={"domain": "security"},
        headers=admin_headers,
    )
    assert ov_res.status_code == 200
    rec_after = client.get(f"/api/v1/ai/recommend/{ds_mixed.id}", headers=admin_headers)
    assert rec_after.json()["domain_detection"]["primary_domain"] == "security"
    assert rec_after.json()["domain_detection"]["user_confirmed_domain"] is True

    # Test pure Security domain + enriched KPI metadata
    rec_sec = client.get(f"/api/v1/ai/recommend/{ds_sec.id}", headers=admin_headers)
    assert rec_sec.status_code == 200
    sec_j = rec_sec.json()
    assert sec_j["domain_detection"]["primary_domain"] == "security"
    assert sec_j["domain_detection"]["domain_family"] == "Security"
    all_kpis = sec_j["kpis"]["all_business"] + sec_j["kpis"]["standard"]
    assert any(k["id"] == "sec_blocked_quarantined_pct" for k in all_kpis)
    for k in all_kpis[:5]:
        assert "domain_scope" in k
        assert "dataset_names" in k
        assert "computability_status" in k
        assert "formula" in k
        assert "evidence" in k

    # Test Network domain detection
    rec_net = client.get(f"/api/v1/ai/recommend/{ds_net.id}", headers=admin_headers)
    assert rec_net.status_code == 200
    assert rec_net.json()["domain_detection"]["primary_domain"] == "network"

    # Test M:N join fan-out inspection & confirmation workflow
    cand = client.post(
        "/api/v1/datasets/relationships/analyze-candidate",
        json={
            "left_dataset_id": ds_sec.id,
            "left_field": "hostname",
            "right_dataset_id": ds_net.id,
            "right_field": "hostname",
            "join_type": "left",
        },
        headers=admin_headers,
    )
    assert cand.status_code == 200
    cj = cand.json()
    assert cj["explosion_report"]["inferred_cardinality"] == "many_to_many"
    assert cj["pk_fk_analysis"]["requires_pre_aggregation"] is True

    rel_create = client.post(
        "/api/v1/datasets/relationships",
        json={
            "left_dataset_id": ds_sec.id,
            "left_field": "hostname",
            "right_dataset_id": ds_net.id,
            "right_field": "hostname",
            "join_type": "left",
        },
        headers=admin_headers,
    )
    assert rel_create.status_code == 200
    rel_id = rel_create.json()["id"]
    assert rel_create.json()["user_confirmed"] is False

    conf_res = client.post(
        f"/api/v1/datasets/relationships/{rel_id}/confirm",
        json={"confirmed": True, "note": "Reviewed fan-out risk; will use pre-agg"},
        headers=admin_headers,
    )
    assert conf_res.status_code == 200
    assert conf_res.json()["user_confirmed"] is True

    # Test Phase 1 AST-safe Formula Builder functions (live DuckDB execution)
    for expr, expected in [
        ("SAFE_DIVIDE(SUM(wait_hours), 0)", None),
        ("ROUND(SLA_BREACH_RATE(wait_hours, sla_hours), 1)", 50.0),
        ("ROUND(SECURITY_ACTION_RATE(action, 'Block'), 1)", 25.0),
        ("AVG(CLAMP(wait_hours, 3.0, 9.0))", 6.25),
        ("PERCENTILE_DISC(wait_hours, 0.5)", 4.0),
    ]:
        pr = client.post(
            f"/api/v1/ai/datasets/{ds_sec.id}/kpis/preview",
            json={"formula": expr, "name": "Formula Test"},
            headers=admin_headers,
        )
        assert pr.status_code == 200, f"Formula {expr} failed: {pr.text}"
        val = pr.json()["value"]
        if expected is None:
            assert val is None
        else:
            assert abs(float(val) - expected) < 0.2

    # Reject unsafe DuckDB system function inside formula
    bad_pr = client.post(
        f"/api/v1/ai/datasets/{ds_sec.id}/kpis/preview",
        json={"formula": "read_csv('/etc/passwd')", "name": "Evil"},
        headers=admin_headers,
    )
    assert bad_pr.status_code == 400


def test_phase2_process_intelligence_end_to_end_and_rbac_rls(client, admin_headers, phase_cleanup):
    with SessionLocal() as db:
        org = db.query(Organization).first()
        # Out-of-order timestamps + rework (Submit -> Review -> Submit -> Approve -> Close) + skip + self-loop + SLA breach + two departments
        df_proc = pd.DataFrame([
            {"case_id": "C-1", "step_name": "Submit", "event_time": "2026-10-01T08:00:00Z", "start_time": "2026-10-01T08:00:00Z", "end_time": "2026-10-01T09:00:00Z", "wait_hours": 1.0, "sla_hours": 4.0, "actor": "ali", "department": "Finance", "region": "North"},
            {"case_id": "C-1", "step_name": "Approve", "event_time": "2026-10-01T15:00:00Z", "start_time": "2026-10-01T14:00:00Z", "end_time": "2026-10-01T15:00:00Z", "wait_hours": 5.0, "sla_hours": 4.0, "actor": "sara", "department": "Finance", "region": "North"},
            # Out of chronological order in raw input
            {"case_id": "C-1", "step_name": "Review", "event_time": "2026-10-01T10:00:00Z", "start_time": "2026-10-01T09:30:00Z", "end_time": "2026-10-01T10:00:00Z", "wait_hours": 2.0, "sla_hours": 4.0, "actor": "reza", "department": "Finance", "region": "North"},
            {"case_id": "C-1", "step_name": "Review", "event_time": "2026-10-01T12:00:00Z", "start_time": "2026-10-01T11:30:00Z", "end_time": "2026-10-01T12:00:00Z", "wait_hours": 3.0, "sla_hours": 4.0, "actor": "reza", "department": "Finance", "region": "North"},

                # C-2 skips Approve & belongs to IT + South
                {"case_id": "C-2", "step_name": "Submit", "event_time": "2026-10-02T08:00:00Z", "start_time": "2026-10-02T08:00:00Z", "end_time": "2026-10-02T08:30:00Z", "wait_hours": 1.0, "sla_hours": 4.0, "actor": "mina", "department": "IT", "region": "South"},
                {"case_id": "C-2", "step_name": "Review", "event_time": "2026-10-03T12:00:00Z", "start_time": "2026-10-03T11:00:00Z", "end_time": "2026-10-03T12:00:00Z", "wait_hours": 24.0, "sla_hours": 4.0, "actor": "mina", "department": "IT", "region": "South"},

                # Additional IT cases for baseline vs post corrective action comparison & hypothesis sample n>=3
                {"case_id": "C-3", "step_name": "Submit", "event_time": "2026-10-03T08:00:00Z", "start_time": "2026-10-03T08:00:00Z", "end_time": "2026-10-03T08:30:00Z", "wait_hours": 1.0, "sla_hours": 4.0, "actor": "mina", "department": "IT", "region": "North"},
                {"case_id": "C-3", "step_name": "Review", "event_time": "2026-10-04T14:00:00Z", "start_time": "2026-10-04T13:00:00Z", "end_time": "2026-10-04T14:00:00Z", "wait_hours": 28.0, "sla_hours": 4.0, "actor": "mina", "department": "IT", "region": "North"},
                {"case_id": "C-4", "step_name": "Submit", "event_time": "2026-10-04T08:00:00Z", "start_time": "2026-10-04T08:00:00Z", "end_time": "2026-10-04T08:30:00Z", "wait_hours": 1.0, "sla_hours": 4.0, "actor": "mina", "department": "IT", "region": "North"},
                {"case_id": "C-4", "step_name": "Review", "event_time": "2026-10-05T12:00:00Z", "start_time": "2026-10-05T11:00:00Z", "end_time": "2026-10-05T12:00:00Z", "wait_hours": 26.0, "sla_hours": 4.0, "actor": "mina", "department": "IT", "region": "North"},
                # Post-improvement fast Finance cases
                {"case_id": "C-5", "step_name": "Submit", "event_time": "2026-10-20T08:00:00Z", "start_time": "2026-10-20T08:00:00Z", "end_time": "2026-10-20T08:30:00Z", "wait_hours": 0.5, "sla_hours": 4.0, "actor": "ali", "department": "Finance", "region": "North"},
                {"case_id": "C-5", "step_name": "Review", "event_time": "2026-10-20T09:30:00Z", "start_time": "2026-10-20T09:00:00Z", "end_time": "2026-10-20T09:30:00Z", "wait_hours": 0.5, "sla_hours": 4.0, "actor": "reza", "department": "Finance", "region": "North"},
                {"case_id": "C-5", "step_name": "Approve", "event_time": "2026-10-20T10:30:00Z", "start_time": "2026-10-20T10:00:00Z", "end_time": "2026-10-20T10:30:00Z", "wait_hours": 0.5, "sla_hours": 4.0, "actor": "sara", "department": "Finance", "region": "North"},
                {"case_id": "C-6", "step_name": "Submit", "event_time": "2026-10-21T08:00:00Z", "start_time": "2026-10-21T08:00:00Z", "end_time": "2026-10-21T08:30:00Z", "wait_hours": 0.5, "sla_hours": 4.0, "actor": "ali", "department": "Finance", "region": "North"},
                {"case_id": "C-6", "step_name": "Review", "event_time": "2026-10-21T09:30:00Z", "start_time": "2026-10-21T09:00:00Z", "end_time": "2026-10-21T09:30:00Z", "wait_hours": 0.5, "sla_hours": 4.0, "actor": "reza", "department": "Finance", "region": "North"},
                {"case_id": "C-6", "step_name": "Approve", "event_time": "2026-10-21T10:30:00Z", "start_time": "2026-10-21T10:00:00Z", "end_time": "2026-10-21T10:30:00Z", "wait_hours": 0.5, "sla_hours": 4.0, "actor": "sara", "department": "Finance", "region": "North"},
                {"case_id": "C-7", "step_name": "Submit", "event_time": "2026-10-22T08:00:00Z", "start_time": "2026-10-22T08:00:00Z", "end_time": "2026-10-22T08:30:00Z", "wait_hours": 0.5, "sla_hours": 4.0, "actor": "ali", "department": "Finance", "region": "North"},
                {"case_id": "C-7", "step_name": "Review", "event_time": "2026-10-22T09:30:00Z", "start_time": "2026-10-22T09:00:00Z", "end_time": "2026-10-22T09:30:00Z", "wait_hours": 0.5, "sla_hours": 4.0, "actor": "reza", "department": "Finance", "region": "North"},
                {"case_id": "C-7", "step_name": "Approve", "event_time": "2026-10-22T10:30:00Z", "start_time": "2026-10-22T10:00:00Z", "end_time": "2026-10-22T10:30:00Z", "wait_hours": 0.5, "sla_hours": 4.0, "actor": "sara", "department": "Finance", "region": "North"},
            ])
        ds_proc = _create_ds(db, org.id, "Procurement Workflow Log", df_proc)
        phase_cleanup["dataset_ids"].append(ds_proc.id)

        # Add regional RLS policy for North vs South
        db.add(
            AccessPolicy(
                organization_id=org.id,
                name="Process Regional RLS",
                policy_type="rls",
                dataset_id=ds_proc.id,
                target_type="all",
                rule_json={"column": "region", "operator": "eq", "value": "${user.region}"},
                is_active=True,
            )
        )
        db.commit()
        org_id = org.id

    # Save mapping with reference sequence ["Submit", "Review", "Approve"]
    map_res = client.post(
        f"/api/v1/process-intelligence/datasets/{ds_proc.id}/mapping",
        json={
            "mapping": {
                "case_id": "case_id",
                "activity": "step_name",
                "timestamp": "event_time",
                "start_time": "start_time",
                "end_time": "end_time",
                "wait_hours": "wait_hours",
                "sla_hours": "sla_hours",
                "actor": "actor",
                "department": "department",
            },
            "reference_sequence": ["Submit", "Review", "Approve"],
            "confirmed": True,
        },
        headers=admin_headers,
    )
    assert map_res.status_code == 200
    assert map_res.json()["user_confirmed"] is True

    ana = client.post(f"/api/v1/process-intelligence/datasets/{ds_proc.id}/analyze", json={}, headers=admin_headers)
    assert ana.status_code == 200
    aj = ana.json()
    assert aj["status"] == "ok"
    assert aj["summary"]["total_cases"] == 7
    assert aj["summary"]["rework_case_count"] == 1
    assert aj["summary"]["self_loop_case_count"] == 1
    assert aj["summary"]["skip_case_count"] == 3  # C-2, C-3, C-4 skipped Approve
    assert aj["summary"]["processing_time_status"] == "computed"
    assert aj["data_quality"]["out_of_order_cases"] == 1
    assert aj["bottlenecks"][0]["activity"] == "Review"
    assert "factor_breakdown" in aj["bottlenecks"][0]
    assert any("غیرعلّی" in h["label_fa"] for h in aj["hypotheses"])

    # Drill down single case C-1 and confirm chronological sort fixed out-of-order steps
    c1 = client.get(f"/api/v1/process-intelligence/datasets/{ds_proc.id}/cases/C-1", headers=admin_headers)
    assert c1.status_code == 200
    assert [e["activity"] for e in c1.json()["events"]] == ["Submit", "Review", "Review", "Approve"]

    # Corrective action before vs after comparison
    act_res = client.post(
        f"/api/v1/process-intelligence/datasets/{ds_proc.id}/actions",
        json={
            "title": "Streamline Review step",
            "target_activity": "Review",
            "kpi_metric": "avg_wait_hours",
            "baseline_start": "2026-10-01T00:00:00Z",
            "baseline_end": "2026-10-10T00:00:00Z",
            "post_start": "2026-10-15T00:00:00Z",
            "post_end": "2026-10-30T00:00:00Z",
        },
        headers=admin_headers,
    )
    assert act_res.status_code == 200
    actj = act_res.json()
    assert actj["measured_before"] > actj["measured_after"]
    assert actj["delta_pct"] < 0

    # RBAC + RLS verification:
    # 1. report_viewer lacks process_intel:view -> 403
    viewer_hdrs = _login_role_user(client, org_id, "report_viewer", "North", phase_cleanup["user_ids"])
    v_ana = client.post(f"/api/v1/process-intelligence/datasets/{ds_proc.id}/analyze", json={}, headers=viewer_hdrs)
    assert v_ana.status_code == 403

    # 2. executive_viewer has process_intel:view BUT lacks view_case_details -> 403 on case drill-down
    exec_hdrs = _login_role_user(client, org_id, "executive_viewer", "North", phase_cleanup["user_ids"])
    e_cases = client.get(f"/api/v1/process-intelligence/datasets/{ds_proc.id}/cases", headers=exec_hdrs)
    assert e_cases.status_code == 403

    # 3. data_analyst in North gets RLS-filtered 6 cases (never sees South C-2!)
    analyst_hdrs = _login_role_user(client, org_id, "data_analyst", "North", phase_cleanup["user_ids"])
    a_ana = client.post(f"/api/v1/process-intelligence/datasets/{ds_proc.id}/analyze", json={}, headers=analyst_hdrs)
    assert a_ana.status_code == 200
    assert a_ana.json()["summary"]["total_cases"] == 6

    # 4. Explicit DENY on drill_down:view_raw_records blocks case trace drill-down even for data_analyst
    analyst_uid = phase_cleanup["user_ids"][-1]
    with SessionLocal() as db:
        db.add(
            ResourcePermission(
                organization_id=org_id,
                subject_type="user",
                subject_id=analyst_uid,
                resource_type="drill_down",
                resource_id=ds_proc.id,
                action="view_raw_records",
                effect="deny",
                inherit_to_children=True,
            )
        )
        db.commit()
    from app.core.permissions import permission_resolver
    with SessionLocal() as db:
        permission_resolver.bump_user_version(db, analyst_uid)
        db.commit()
    denied_case = client.get(f"/api/v1/process-intelligence/datasets/{ds_proc.id}/cases/C-1", headers=analyst_hdrs)
    assert denied_case.status_code == 403
    with SessionLocal() as db:
        db.query(ResourcePermission).filter(ResourcePermission.subject_id == analyst_uid).delete()
        db.commit()


def test_phase3_and_phase4_security_logs_rbac_and_correlation(client, admin_headers, phase_cleanup):
    with SessionLocal() as db:
        org = db.query(Organization).first()
        org_id = org.id
        # Process dataset for correlation
        df_p = pd.DataFrame([
            {"case_id": "PROC-101", "step_name": "Access Grant", "event_time": "2026-11-10T09:00:00Z", "actor": "alice.sec", "hostname": "WKSTN-01", "wait_hours": 14.0, "sla_hours": 4.0},
            {"case_id": "PROC-102", "step_name": "Review", "event_time": "2026-11-10T11:00:00Z", "actor": "bob.ops", "hostname": "WKSTN-02", "wait_hours": 2.0, "sla_hours": 4.0},
        ])
        ds_p = _create_ds(db, org_id, "IT Access Workflow", df_p)
        phase_cleanup["dataset_ids"].append(ds_p.id)

    # 1. Verify Connector Matrix Honesty & reject unverified connector creation
    conn_res = client.get("/api/v1/security-intel/connectors", headers=admin_headers)
    assert conn_res.status_code == 200
    conns = {c["id"]: c for c in conn_res.json()["connectors"]}
    assert conns["syslog_security_jsonl"]["tested"] is True
    assert conns["defender_edr"]["tested"] is False
    assert "planned" in conns["defender_edr"]["status"]

    bad_col = client.post(
        "/api/v1/security-intel/collectors",
        json={"name": "Fake Defender", "source_type": "defender_edr"},
        headers=admin_headers,
    )
    assert bad_col.status_code == 400

    # 2. Create real Syslog/JSONL collector & ingest batch with:
    #    - valid JSONL (+03:30 timezone conversion!)
    #    - valid Syslog KV
    #    - duplicate line (must be deduplicated!)
    #    - malformed line (must be quarantined in unparsed DLQ!)
    #    - historical baseline + sudden Failed spike for statistical alerting!
    col_res = client.post(
        "/api/v1/security-intel/collectors",
        json={"name": "SOC Syslog Test", "source_type": "syslog_security_jsonl", "retention_days": 30, "default_timezone": "Asia/Tehran"},
        headers=admin_headers,
    )
    assert col_res.status_code == 200
    col_id = col_res.json()["id"]
    phase_cleanup["collector_ids"].append(col_id)

    line_dup = '{"timestamp":"2026-11-10T09:15:00Z","vendor":"NexusSOC","product":"AuthGW","event_type":"auth_fail","severity":"high","action":"Failed","username":"alice.sec","hostname":"WKSTN-01","src_ip":"10.1.2.3","rule_name":"AUTH_FAIL"}'
    items = [
        # Baseline historical buckets (1 failed per hour across 3 earlier hours)
        {"timestamp": "2026-11-10T05:10:00Z", "vendor": "NexusSOC", "event_type": "auth_fail", "severity": "medium", "action": "Failed", "username": "u1", "hostname": "H1"},
        {"timestamp": "2026-11-10T06:10:00Z", "vendor": "NexusSOC", "event_type": "auth_fail", "severity": "medium", "action": "Failed", "username": "u1", "hostname": "H1"},
        {"timestamp": "2026-11-10T07:10:00Z", "vendor": "NexusSOC", "event_type": "auth_fail", "severity": "medium", "action": "Failed", "username": "u1", "hostname": "H1"},
        # Spike hour 09:xx UTC (5 failed/block/quarantine events including duplicate + KV + naive Tehran time)
        line_dup,
        line_dup,  # duplicate -> deduplicated!
        "2026-11-10T12:50:00 WKSTN-01 vendor=EDR product=AV event_type=malware action=Quarantine severity=critical user=alice.sec threat_name=EICAR_TEST file_hash=abc123",
        {"timestamp": "2026-11-10T09:25:00Z", "vendor": "NexusSOC", "event_type": "auth_fail", "severity": "high", "action": "Failed", "username": "alice.sec", "hostname": "WKSTN-01", "src_ip": "10.1.2.4"},
        {"timestamp": "2026-11-10T09:30:00Z", "vendor": "NexusSOC", "event_type": "auth_fail", "severity": "high", "action": "Failed", "username": "alice.sec", "hostname": "WKSTN-01", "src_ip": "10.1.2.5"},
        {"timestamp": "2026-11-10T09:35:00Z", "vendor": "NexusSOC", "event_type": "auth_fail", "severity": "high", "action": "Failed", "username": "alice.sec", "hostname": "WKSTN-01", "src_ip": "10.1.2.6"},
        {"timestamp": "2026-11-10T09:40:00Z", "vendor": "Firewall", "event_type": "net_block", "severity": "high", "action": "Block", "username": "alice.sec", "hostname": "WKSTN-01", "src_ip": "10.1.2.6"},
        "MALFORMED RAW GARBAGE LINE WITHOUT TIMESTAMP OR KV",
    ]
    ing = client.post(
        "/api/v1/security-intel/ingest/batch",
        json={"collector_id": col_id, "source_name": "test_batch", "items": items},
        headers=admin_headers,
    )
    assert ing.status_code == 200
    ij = ing.json()
    assert ij["ingested"] == 9
    assert ij["deduplicated"] == 1
    assert ij["unparsed_quarantined"] == 1

    # Also test multipart file upload ingestion
    file_ing = client.post(
        "/api/v1/security-intel/ingest/file",
        data={"collector_id": col_id},
        files={"file": ("soc.jsonl", io.BytesIO(b'{"timestamp":"2026-11-10T09:45:00Z","event_type":"av_clean","action":"Clean","severity":"low","hostname":"WKSTN-03"}\n'), "text/plain")},
        headers=admin_headers,
    )
    assert file_ing.status_code == 200
    assert file_ing.json()["ingested"] == 1

    # Verify overview separates actions cleanly & carries interpretation warning
    ov = client.get("/api/v1/security-intel/overview?hours=8760", headers=admin_headers)
    assert ov.status_code == 200
    ab = ov.json()["action_breakdown"]
    assert ab["Failed"] >= 6
    assert ab["Block"] >= 1
    assert ab["Quarantine"] >= 1
    assert ab["Clean"] >= 1

    # Independent RBAC: data_analyst can view normalized events WITHOUT raw_message, and CANNOT access /unparsed
    analyst_hdrs = _login_role_user(client, org_id, "data_analyst", "All", phase_cleanup["user_ids"])
    ev_analyst = client.get("/api/v1/security-intel/events", headers=analyst_hdrs)
    assert ev_analyst.status_code == 200
    assert ev_analyst.json()["can_view_raw"] is False
    assert all(e["raw_message"] is None for e in ev_analyst.json()["events"])

    unp_analyst = client.get("/api/v1/security-intel/unparsed", headers=analyst_hdrs)
    assert unp_analyst.status_code == 403

    # Admin CAN view raw messages & quarantined lines
    ev_admin = client.get("/api/v1/security-intel/events", headers=admin_headers)
    assert ev_admin.json()["can_view_raw"] is True
    assert any(e["raw_message"] for e in ev_admin.json()["events"])
    unp_admin = client.get("/api/v1/security-intel/unparsed", headers=admin_headers)
    assert unp_admin.status_code == 200
    assert len(unp_admin.json()) >= 1

    # Statistical baseline alert + advisory Incident lifecycle with human approval
    al_res = client.post("/api/v1/security-intel/alerts/evaluate?zscore_threshold=1.5", headers=admin_headers)
    assert al_res.status_code == 200
    assert al_res.json()["count"] >= 1
    al_id = al_res.json()["created"][0]["id"]

    # Analyst lacks security_log:manage_incidents -> 403
    bad_inc = client.post("/api/v1/security-intel/incidents", json={"title": "Test", "alert_ids": [al_id]}, headers=analyst_hdrs)
    assert bad_inc.status_code == 403

    inc_res = client.post(
        "/api/v1/security-intel/incidents",
        json={"title": "Auth Failure Spike on WKSTN-01", "severity": "high", "alert_ids": [al_id]},
        headers=admin_headers,
    )
    assert inc_res.status_code == 200
    inc_id = inc_res.json()["id"]
    assert inc_res.json()["human_approved"] is False

    app_res = client.post(
        f"/api/v1/security-intel/incidents/{inc_id}/approve",
        json={"human_approved": True, "approval_note": "Approved advisory containment review", "new_status": "contained_proposal"},
        headers=admin_headers,
    )
    assert app_res.status_code == 200
    assert app_res.json()["human_approved"] is True
    assert app_res.json()["status"] == "contained_proposal"

    # Phase 4: Cross-Domain Correlation (Process + Security on username + time window)
    corr_res = client.post(
        "/api/v1/correlation/analyze",
        json={"process_dataset_id": ds_p.id, "correlation_key": "username", "window_minutes": 90},
        headers=admin_headers,
    )
    assert corr_res.status_code == 200
    cj = corr_res.json()
    assert cj["status"] == "ok"
    assert cj["match_quality"]["matched_process_cases"] == 1  # alice.sec matched, bob.ops unmatched
    assert cj["match_quality"]["unmatched_process_cases"] == 1
    assert cj["match_quality"]["matched_security_events"] >= 4
    assert "علت و معلولی" in cj["causation_disclaimer_fa"]


def test_phase4_process_benchmark_2000_events(client, admin_headers, phase_cleanup):
    """Repeatable benchmark: reconstruct & score 2,000 process events across 400 cases within 2.5s."""
    with SessionLocal() as db:
        org = db.query(Organization).first()
        base = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
        rows = []
        steps = ["Create", "Validate", "Approve", "Execute", "Close"]
        for c in range(400):
            for s_idx, st in enumerate(steps):
                t = base + timedelta(hours=c * 2 + s_idx)
                rows.append({
                    "case_id": f"BM-{c:04d}",
                    "step_name": st,
                    "event_time": t.isoformat(),
                    "wait_hours": float((c + s_idx) % 8),
                    "sla_hours": 5.0,
                    "actor": f"user_{c % 12}",
                    "department": f"dept_{c % 4}",
                })
        ds_bm = _create_ds(db, org.id, "Benchmark 2K Process Log", pd.DataFrame(rows))
        phase_cleanup["dataset_ids"].append(ds_bm.id)

    t0 = time.perf_counter()
    res = client.post(f"/api/v1/process-intelligence/datasets/{ds_bm.id}/analyze", json={}, headers=admin_headers)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    assert res.status_code == 200
    assert res.json()["summary"]["total_cases"] == 400
    assert res.json()["summary"]["total_events_analyzed"] == 2000
    assert elapsed_ms < 2500.0, f"Process mining benchmark took {elapsed_ms:.1f}ms (>2500ms)"
