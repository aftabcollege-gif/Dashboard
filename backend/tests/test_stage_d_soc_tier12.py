from app.database import db_session_scope
from app.models.intelligence import SecurityAlert, SecurityAssetExposure, SecurityCollector, SecurityEvent, SecurityIncident


def test_stage_d_soc_nlp_15_categories_tier12_and_cve_exposure(client, admin_headers):
    # 1) Fetch classification rules & verify 15 threat categories exist
    cr_r = client.get("/api/v1/security-intel/classification-rules", headers=admin_headers)
    assert cr_r.status_code == 200, cr_r.text
    cats = cr_r.json()["categories"]
    assert len(cats) == 15

    # 2) Add custom Persian NLP status rule
    add_rule = client.post(
        "/api/v1/security-intel/classification-rules",
        headers=admin_headers,
        json={
            "rule_kind": "status_map",
            "name": "قانون ایزوله‌سازی فوری",
            "pattern": "قرنطینه\\s*اضطراری|ایزوله\\s*کامل",
            "target_value": "blocked_quarantined",
            "severity_override": "critical",
        },
    )
    assert add_rule.status_code == 200, add_rule.text
    rule_id = add_rule.json()["id"]

    # 3) Create active collector & ingest JSONL/Syslog events with Persian text + software versions + missing version
    col_r = client.post(
        "/api/v1/security-intel/collectors",
        headers=admin_headers,
        json={"name": "SOC Stage D Collector", "source_type": "syslog_security_jsonl"},
    )
    assert col_r.status_code == 200, col_r.text
    col_id = col_r.json()["id"]

    batch_items = [
        # Event 1-3: Brute force burst in bucket 1 (baseline) -> low count
        {
            "timestamp": "2026-11-16T01:00:00Z",
            "vendor": "SSH",
            "product": "openssh",
            "software_version": "9.3p1",
            "event_type": "auth_login",
            "action": "Failed",
            "severity": "medium",
            "username": "nima",
            "hostname": "GW-CORE-01",
            "src_ip": "198.51.100.10",
            "message": "تلاش ناموفق ورود رمزعبور در بررسی",
        },
        # Bucket 2 spike: 4 Failed/Ransomware events -> triggers alerts + CVE match for openssh 9.3p1
        {
            "timestamp": "2026-11-16T05:01:00Z",
            "vendor": "EDR",
            "product": "openssh",
            "software_version": "9.3p1",
            "event_type": "auth_login",
            "action": "Failed",
            "severity": "high",
            "username": "sara",
            "hostname": "GW-CORE-01",
            "src_ip": "198.51.100.22",
            "message": "حمله brute force و ایزوله کامل انجام شد",
        },
        {
            "timestamp": "2026-11-16T05:02:00Z",
            "vendor": "EDR",
            "product": "EndpointGuard",
            "event_type": "malware",
            "action": "Quarantine",
            "severity": "critical",
            "username": "sara",
            "hostname": "WS-UNKNOWN-VER",
            "threat_name": "Trojan.Ransomware.X",
            "message": "باج‌افزار شناسایی و پاکسازی شد",
        },
        {
            "timestamp": "2026-11-16T05:03:00Z",
            "vendor": "Auth",
            "product": "openssh",
            "software_version": "9.3p1",
            "event_type": "auth_login",
            "action": "Failed",
            "severity": "high",
            "username": "sara",
            "hostname": "GW-CORE-01",
            "src_ip": "198.51.100.22",
            "message": "ورود ناموفق پیاپی",
        },
        {
            "timestamp": "2026-11-16T05:04:00Z",
            "vendor": "Auth",
            "product": "openssh",
            "software_version": "9.3p1",
            "event_type": "auth_login",
            "action": "Failed",
            "severity": "high",
            "username": "sara",
            "hostname": "GW-CORE-01",
            "src_ip": "198.51.100.22",
            "message": "ورود ناموفق پیاپی ۲",
        },
    ]
    ing = client.post(
        "/api/v1/security-intel/ingest/batch",
        headers=admin_headers,
        json={"collector_id": col_id, "items": batch_items},
    )
    assert ing.status_code == 200, ing.text
    assert ing.json()["ingested"] == 5

    # Verify NLP categories + custom status extraction reason
    ev_r = client.get("/api/v1/security-intel/events?limit=20", headers=admin_headers)
    assert ev_r.status_code == 200
    evs = ev_r.json()["events"]
    assert any(e["threat_category"] == "malware_ransomware" and e["threat_status"] == "resolved_cleaned" for e in evs)
    assert any("ایزوله کامل" in (e["extraction_reason"] or "") and e["severity"] == "critical" for e in evs)

    # 4) Evaluate alerts twice -> verify deduplication increments duplicate_count instead of cloning
    eval1 = client.post("/api/v1/security-intel/alerts/evaluate", headers=admin_headers)
    assert eval1.status_code == 200 and eval1.json()["count"] >= 1
    created_alert_ids = [a["id"] for a in eval1.json()["created"]]
    eval2 = client.post("/api/v1/security-intel/alerts/evaluate", headers=admin_headers)
    assert eval2.status_code == 200 and eval2.json()["count"] == 0  # Deduplicated!

    alerts = client.get("/api/v1/security-intel/alerts", headers=admin_headers).json()
    dup_alert = next(a for a in alerts if (a.get("duplicate_count") or 1) >= 2)
    assert dup_alert["tier"] == 1

    # 5) Tier 1 Escalate to Tier 2 -> auto-creates Incident with Blast Radius & MTTA
    esc = client.post(
        f"/api/v1/security-intel/alerts/{dup_alert['id']}/triage",
        headers=admin_headers,
        json={"action": "escalate_tier2", "note": "ارجاع به Tier 2"},
    )
    assert esc.status_code == 200, esc.text
    assert esc.json()["tier"] == 2
    inc_id = esc.json()["escalated_incident_id"]
    assert inc_id is not None

    # Update Tier 2 Incident lifecycle -> resolve & compute MTTR
    lc = client.post(
        f"/api/v1/security-intel/incidents/{inc_id}/lifecycle",
        headers=admin_headers,
        json={
            "status": "resolved",
            "hypothesis_notes_fa": "تایید حمله brute-force روی GW-CORE-01",
            "final_report_fa": "نیاز به ارتقای OpenSSH به 9.8+",
        },
    )
    assert lc.status_code == 200, lc.text
    assert lc.json()["status"] == "resolved"
    assert lc.json()["mtta_minutes"] is not None
    assert lc.json()["mttr_minutes"] is not None

    # 6) Scan CVE/CVSS Exposures -> matches OpenSSH 9.3p1 CVE AND marks WS-UNKNOWN-VER as NEEDS_REVIEW
    scan = client.post("/api/v1/security-intel/exposures/scan", headers=admin_headers)
    assert scan.status_code == 200, scan.text
    assert scan.json()["cve_matches"] >= 1
    assert scan.json()["needs_review"] >= 1

    exps = client.get("/api/v1/security-intel/exposures", headers=admin_headers).json()
    assert any(e["asset_name"] == "GW-CORE-01" and e["cve_id"].startswith("CVE-") and e["cvss_score"] >= 8.0 for e in exps)
    assert any(e["asset_name"] == "WS-UNKNOWN-VER" and e["cve_id"] == "NEEDS_REVIEW" for e in exps)

    # 7) Run NLP dataset classifier on an uploaded CSV security report
    csv_bytes = (
        "log_id,message\n"
        "1,تزریق SQL injection شناسایی و مسدود شد\n"
        "2,فیشینگ ایمیلی نیازمند بررسی توسط کارشناس\n"
    ).encode("utf-8")
    up = client.post(
        "/api/v1/data-sources/files/import",
        headers=admin_headers,
        files={"file": ("sec_notes.csv", csv_bytes, "text/csv")},
        data={"dataset_name": "Stage_D_Sec_Notes"},
    )
    assert up.status_code == 200
    sec_ds_id = up.json()["dataset_id"]

    cls_ds = client.post(
        f"/api/v1/security-intel/classify-dataset/{sec_ds_id}",
        headers=admin_headers,
        json={"text_column": "message"},
    )
    assert cls_ds.status_code == 200, cls_ds.text
    cat_keys = {c["key"] for c in cls_ds.json()["category_breakdown"]}
    assert "web_injection_sqli" in cat_keys
    assert "phishing_social" in cat_keys

    # Cleanup test records so shared session DB stays pristine for subsequent tests
    client.delete(f"/api/v1/security-intel/classification-rules/{rule_id}", headers=admin_headers)
    client.delete(f"/api/v1/datasets/{sec_ds_id}", headers=admin_headers)
    with db_session_scope() as db:
        db.query(SecurityEvent).filter(SecurityEvent.collector_id == col_id).delete()
        if created_alert_ids:
            db.query(SecurityAlert).filter(SecurityAlert.id.in_(created_alert_ids)).delete()
        db.query(SecurityIncident).filter(SecurityIncident.id == inc_id).delete()
        db.query(SecurityAssetExposure).filter(SecurityAssetExposure.asset_name.in_(["GW-CORE-01", "WS-UNKNOWN-VER"])).delete()
        db.query(SecurityCollector).filter(SecurityCollector.id == col_id).delete()
        db.commit()
