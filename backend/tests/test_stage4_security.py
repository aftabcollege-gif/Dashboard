import os
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.dashboards import _RESERVED_PUBLIC_OVERRIDE_SETTINGS_KEYS
from app.config import settings
from app.core.cache import cache_manager
from app.core.permissions import permission_resolver
from app.core.rate_limit import rate_limiter
from app.core.rls_cls import inspect_dataset_public_safety
from app.core.security import hash_password
from app.database import SessionLocal
from app.engines.connectors import _PinnedSSRFHTTPTransport, _validate_external_http_url
from app.engines.etl_engine import (
    _assert_pipeline_fenced,
    recover_stale_etl_runs,
    run_etl_pipeline,
)
from app.engines.scheduler import (
    _LEADER_ID,
    _REDIS_RELEASE_LEADER_LUA,
    _acquire_or_renew_leader_lease,
    _release_leader_lease,
    _scheduler_requires_redis,
)
from app.engines.warehouse import warehouse
from app.main import _verify_production_schema_at_head, app
from app.models.dashboard import Dashboard, DashboardPage, Widget, WidgetQuery, Workspace
from app.models.data import Dataset, DatasetField
from app.models.etl import EtlJob, EtlJobRun, EtlPipeline
from app.models.identity import (
    AccessPolicy,
    Organization,
    Permission,
    ResourcePermission,
    Role,
    RolePermission,
    User,
    UserProfile,
    UserRole,
)

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _register_dataset(
    db,
    org_id: str,
    name: str,
    df: pd.DataFrame,
    fields_meta: list[tuple[str, str, str]],
) -> Dataset:
    wh_table = f"wh_s4_{uuid.uuid4().hex[:8]}"
    warehouse.load_dataframe(wh_table, df, mode="replace")
    ds = Dataset(
        organization_id=org_id,
        name=name,
        warehouse_table=wh_table,
        row_count=len(df),
        freshness_status="fresh",
    )
    db.add(ds)
    db.flush()
    for fname, dtype, role in fields_meta:
        db.add(
            DatasetField(
                dataset_id=ds.id,
                field_name=fname,
                display_name=fname,
                data_type=dtype,
                semantic_role=role,
            )
        )
    db.flush()
    return ds


def _make_isolated_org_user(
    db,
    *,
    role_slug: str = "custom_role",
    is_super: bool = False,
    permissions: list[tuple[str, str]] | None = None,
) -> tuple[Organization, Workspace, Role, User, dict[str, str]]:
    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"Stage4 Org {suffix}", slug=f"stage4-org-{suffix}")
    db.add(org)
    db.flush()

    ws = Workspace(organization_id=org.id, name=f"Workspace {suffix}")
    db.add(ws)
    db.flush()

    role = Role(
        organization_id=org.id,
        name=f"Role {role_slug} {suffix}",
        slug=f"{role_slug}_{suffix}",
        is_system=False,
        is_super_admin=is_super,
    )
    db.add(role)
    db.flush()

    for rt, act in permissions or []:
        perm = (
            db.query(Permission)
            .filter(Permission.resource_type == rt, Permission.action == act)
            .first()
        )
        if perm:
            db.add(RolePermission(role_id=role.id, permission_id=perm.id, effect="allow"))

    user = User(
        organization_id=org.id,
        username=f"s4_{role_slug}_{suffix}",
        email=f"s4_{role_slug}_{suffix}@example.local",
        password_hash=hash_password("Stage4Pass!2026"),
        auth_source="local",
        is_active=True,
    )
    db.add(user)
    db.flush()
    db.add(UserProfile(user_id=user.id, full_name=user.username, region="North"))
    db.add(UserRole(user_id=user.id, role_id=role.id))
    db.commit()
    return org, ws, role, user, {"username": user.username, "password": "Stage4Pass!2026"}


def test_stage4_users_and_permission_matrix_desktop_cookie_flow():
    """Verifies Windows desktop production startup, cookie/CSRF auth, and all Users & Permission Matrix flows."""
    with tempfile.TemporaryDirectory(prefix="s4_win_matrix_") as tmp:
        tmp_path = Path(tmp)
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        env = {
            **os.environ,
            "BI_DATA_DIR": str(data_dir),
            "DATABASE_URL": f"sqlite:///{data_dir / 'app_metadata.db'}",
            "WAREHOUSE_PATH": str(data_dir / "warehouse.duckdb"),
            "SAMPLE_ERP_DB_PATH": str(data_dir / "sample_erp.sqlite"),
        }
        script = f"""
import os
import sys
from pathlib import Path

os.environ["ENVIRONMENT"] = "production"
os.environ["BI_DATA_DIR"] = r"{data_dir}"
os.environ["DATABASE_URL"] = r"sqlite:///{data_dir / 'app_metadata.db'}"
os.environ["WAREHOUSE_PATH"] = r"{data_dir / 'warehouse.duckdb'}"
os.environ["SAMPLE_ERP_DB_PATH"] = r"{data_dir / 'sample_erp.sqlite'}"
os.environ["BI_SEED_DEMO_USERS"] = "0"
os.environ["BI_REQUIRE_GENERATED_SECRETS"] = "1"
os.environ["BI_AUTO_CREATE_SCHEMA"] = "0"
os.environ["BI_JWT_SECRET"] = "stage4-jwt-secret-key-very-strong-1234567890abcdef"
os.environ["BI_CRED_KEY"] = "b" * 43 + "="
os.environ["BI_INITIAL_SETUP_TOKEN"] = "desktop-setup-token-stage4-987654"
os.environ["BI_DESKTOP_CONTROL_TOKEN"] = "desktop-control-token-stage4-1234"
os.environ["RATE_LIMIT_FAIL_CLOSED_PROD"] = "1"

sys.path.insert(0, r"{BACKEND_DIR}")
from app.db_migrate import run_migrations
run_migrations()

from fastapi.testclient import TestClient
from app.main import app

with TestClient(app) as c:
    assert c.get("/openapi.json").status_code == 404
    assert c.get("/docs").status_code == 404
    st = c.get("/api/v1/auth/status")
    assert st.status_code == 200
    assert st.json()["needs_initial_setup"] is True

    # 1. Cookie bootstrap WITHOUT sample data (tests empty dashboards matrix fallback)
    boot = c.post(
        "/api/v1/auth/bootstrap-admin",
        json={{
            "username": "win_admin",
            "password": "WinStrongPass!2026",
            "email": "win_admin@company.local",
            "full_name": "Windows Admin",
            "organization_name": "Desktop Org",
            "include_sample_data": False,
            "setup_token": "desktop-setup-token-stage4-987654",
        }},
    )
    assert boot.status_code == 200, boot.text
    assert boot.json()["auth_mode"] == "cookie"
    csrf = c.cookies.get("bi_csrf_token")
    assert csrf and c.cookies.get("bi_access_token")
    csrf_h = {{"X-CSRF-Token": csrf}}

    # 2. All RBAC endpoints used by /admin/security
    u_res = c.get("/api/v1/rbac/users")
    assert u_res.status_code == 200, u_res.text
    users = u_res.json()
    assert len(users) == 1
    assert "Super Administrator" in users[0]["roles"]

    r_res = c.get("/api/v1/rbac/roles")
    assert r_res.status_code == 200, r_res.text
    r_json = r_res.json()
    assert isinstance(r_json["roles"], list) and len(r_json["roles"]) >= 10
    assert isinstance(r_json["catalog"], list) and len(r_json["catalog"]) >= 70

    g_res = c.get("/api/v1/rbac/groups")
    assert g_res.status_code == 200, g_res.text
    assert isinstance(g_res.json(), list)

    m_res = c.get("/api/v1/rbac/permissions/matrix")
    assert m_res.status_code == 200, m_res.text
    m_json = m_res.json()
    assert m_json["dashboards"] == []
    assert isinstance(m_json["rules"], list)

    # 3. Create dashboard, create viewer user, toggle & copy matrix permissions, add CLS hidden_columns
    dash = c.post("/api/v1/dashboards", headers=csrf_h, json={{"title": "Win Matrix Dash"}})
    assert dash.status_code == 200, dash.text
    dash_id = dash.json()["id"]

    viewer_role = next(r for r in r_json["roles"] if r["slug"] == "report_viewer")
    designer_role = next(r for r in r_json["roles"] if r["slug"] == "dashboard_designer")
    cu = c.post(
        "/api/v1/rbac/users",
        headers=csrf_h,
        json={{
            "username": "win_viewer",
            "email": "win_viewer@company.local",
            "full_name": "Win Viewer",
            "password": "WinViewerPass!2026",
            "role_ids": [viewer_role["id"]],
        }},
    )
    assert cu.status_code == 200, cu.text

    ms = c.put(
        "/api/v1/rbac/permissions/matrix/set",
        headers=csrf_h,
        json={{
            "subject_type": "role",
            "subject_id": viewer_role["id"],
            "resource_type": "dashboard",
            "resource_id": dash_id,
            "action": "view",
            "effect": "allow",
        }},
    )
    assert ms.status_code == 200, ms.text

    mc = c.post(
        "/api/v1/rbac/permissions/matrix/copy",
        headers=csrf_h,
        json={{
            "from_subject_type": "role",
            "from_subject_id": viewer_role["id"],
            "to_subject_type": "role",
            "to_subject_id": designer_role["id"],
        }},
    )
    assert mc.status_code == 200, mc.text
    assert mc.json()["copied"] >= 1

    cls = c.post(
        "/api/v1/rbac/policies",
        headers=csrf_h,
        json={{
            "name": "Hide email",
            "policy_type": "cls",
            "target_type": "all",
            "rule_json": {{"hidden_columns": ["email"], "column": "email", "strategy": "deny"}},
        }},
    )
    assert cls.status_code == 200, cls.text

    # 4. Non-admin viewer receives explicit 403 on Users & Matrix (never silent empty data)
    v_tok = c.post(
        "/api/v1/auth/login",
        headers={{"X-Auth-Mode": "bearer"}},
        json={{"username": "win_viewer", "password": "WinViewerPass!2026"}},
    ).json()["access_token"]
    for p in ("/api/v1/rbac/users", "/api/v1/rbac/roles", "/api/v1/rbac/permissions/matrix"):
        resp = c.get(p, headers={{"Authorization": f"Bearer {{v_tok}}"}})
        assert resp.status_code == 403, f"{{p}} expected 403 for viewer, got {{resp.status_code}}"

print("WIN_DESKTOP_MATRIX_OK")
"""
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(BACKEND_DIR),
            env=env,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        assert "WIN_DESKTOP_MATRIX_OK" in proc.stdout


def test_stage4_public_override_governance_and_privilege_escalation(client: TestClient, admin_headers):
    db = SessionLocal()
    try:
        org, ws, designer_role, designer_user, designer_creds = _make_isolated_org_user(
            db,
            role_slug="designer_no_override",
            permissions=[
                ("workspace", "view"),
                ("dashboard", "view"),
                ("dashboard", "add"),
                ("dashboard", "edit"),
                ("dashboard", "share"),
                ("dashboard", "add_widget"),
                ("dataset", "view"),
                ("admin", "view"),
                ("admin", "add"),
                ("admin", "edit"),
            ],
        )
        df = pd.DataFrame([{"region": "North", "email": "alice@corp.local", "revenue": 100.0}])
        ds_rls = _register_dataset(
            db,
            org.id,
            "Restricted Sales",
            df,
            [("region", "string", "dimension"), ("email", "string", "dimension"), ("revenue", "float", "measure")],
        )
        db.add(
            AccessPolicy(
                organization_id=org.id,
                name="North RLS",
                policy_type="rls",
                dataset_id=ds_rls.id,
                target_type="all",
                rule_json={"column": "region", "operator": "eq", "value": "North"},
                is_active=True,
            )
        )
        db.add(
            AccessPolicy(
                organization_id=org.id,
                name="Mask Email",
                policy_type="masking",
                dataset_id=ds_rls.id,
                target_type="all",
                rule_json={"column": "email", "strategy": "partial"},
                is_active=True,
            )
        )

        # Grant super admin in same isolated org for governance verification
        sa_role = Role(
            organization_id=org.id,
            name="Org Super Admin",
            slug=f"sa_{uuid.uuid4().hex[:6]}",
            is_super_admin=True,
        )
        db.add(sa_role)
        db.flush()
        sa_user = User(
            organization_id=org.id,
            username=f"s4_sa_{uuid.uuid4().hex[:8]}",
            email=f"s4_sa_{uuid.uuid4().hex[:8]}@example.local",
            password_hash=hash_password("Stage4Super!2026"),
            auth_source="local",
            is_active=True,
        )
        db.add(sa_user)
        db.flush()
        db.add(UserProfile(user_id=sa_user.id, full_name="SA"))
        db.add(UserRole(user_id=sa_user.id, role_id=sa_role.id))
        db.commit()

        safe, reason = inspect_dataset_public_safety(db, org.id, ds_rls)
        assert safe is False
        assert "security policy" in reason

        override_perm = (
            db.query(Permission)
            .filter(Permission.resource_type == "dashboard", Permission.action == "public_restricted_override")
            .first()
        )
        assert override_perm is not None
        org_id = org.id
        ds_id = ds_rls.id
        ws_id = ws.id
        designer_role_id = designer_role.id
        override_perm_id = override_perm.id
        sa_username = sa_user.username
    finally:
        db.close()

    d_tok = client.post(
        "/api/v1/auth/login",
        headers={"X-Auth-Mode": "bearer"},
        json=designer_creds,
    ).json()["access_token"]
    d_headers = {"Authorization": f"Bearer {d_tok}"}

    sa_tok = client.post(
        "/api/v1/auth/login",
        headers={"X-Auth-Mode": "bearer"},
        json={"username": sa_username, "password": "Stage4Super!2026"},
    ).json()["access_token"]
    sa_headers = {"Authorization": f"Bearer {sa_tok}"}

    # 1. Designer cannot reserve/forge settings_json keys or pass allow_public_restricted on create
    for key in _RESERVED_PUBLIC_OVERRIDE_SETTINGS_KEYS:
        forged = client.post(
            "/api/v1/dashboards",
            headers=d_headers,
            json={"workspace_id": ws_id, "title": "Forged", "settings_json": {key: True}},
        )
        assert forged.status_code == 403

    bad_create = client.post(
        "/api/v1/dashboards",
        headers=d_headers,
        json={
            "workspace_id": ws_id,
            "title": "Override Attempt",
            "allow_public_restricted": True,
            "public_restricted_reason": "Bypass attempt",
        },
    )
    assert bad_create.status_code == 403

    # 2. Designer cannot escalate by granting public_restricted_override to roles or matrix
    esc_role = client.post(
        "/api/v1/rbac/roles",
        headers=d_headers,
        json={"name": "EscRole", "slug": f"esc_{uuid.uuid4().hex[:6]}", "permission_ids": [override_perm_id]},
    )
    assert esc_role.status_code == 403

    esc_matrix = client.put(
        "/api/v1/rbac/permissions/matrix/set",
        headers=d_headers,
        json={
            "subject_type": "role",
            "subject_id": designer_role_id,
            "resource_type": "dashboard",
            "resource_id": "*",
            "action": "public_restricted_override",
            "effect": "allow",
        },
    )
    assert esc_matrix.status_code == 403

    # 3. Create restricted dashboard & verify designer cannot share/override it
    dash_resp = client.post(
        "/api/v1/dashboards",
        headers=d_headers,
        json={"workspace_id": ws_id, "title": "Restricted Revenue"},
    )
    assert dash_resp.status_code == 200
    dash_id = dash_resp.json()["id"]
    pages = client.get(f"/api/v1/dashboards/{dash_id}", headers=d_headers).json()["pages"]
    w_resp = client.post(
        f"/api/v1/dashboards/{dash_id}/widgets",
        headers=d_headers,
        json={
            "page_id": pages[0]["id"],
            "title": "Rev KPI",
            "chart_type": "kpi",
            "dataset_id": ds_id,
            "visual_spec": {"y_field": "revenue", "aggregation": "SUM"},
        },
    )
    assert w_resp.status_code == 200

    assert client.post(f"/api/v1/dashboards/{dash_id}/share", headers=d_headers, json={}).status_code == 403
    assert (
        client.put(
            f"/api/v1/dashboards/{dash_id}/public-override",
            headers=d_headers,
            json={"allow_public_restricted": True, "reason": "Unauthorized"},
        ).status_code
        == 403
    )

    # 4. Super Admin must provide explicit reason; valid override allows publishing & unshare/revoke unpublishes
    no_reason = client.put(
        f"/api/v1/dashboards/{dash_id}/public-override",
        headers=sa_headers,
        json={"allow_public_restricted": True, "reason": "x"},
    )
    assert no_reason.status_code == 400

    ok_override = client.put(
        f"/api/v1/dashboards/{dash_id}/public-override",
        headers=sa_headers,
        json={"allow_public_restricted": True, "reason": "Board approved public KPI release"},
    )
    assert ok_override.status_code == 200
    assert ok_override.json()["allow_public_restricted"] is True

    pub = client.post(f"/api/v1/dashboards/{dash_id}/share", headers=sa_headers, json={})
    assert pub.status_code == 200
    assert pub.json()["is_public"] is True

    # Revoking override automatically unpublishes restricted dashboard
    revoked = client.put(
        f"/api/v1/dashboards/{dash_id}/public-override",
        headers=sa_headers,
        json={"allow_public_restricted": False, "reason": "Revoked after review"},
    )
    assert revoked.status_code == 200
    assert revoked.json()["allow_public_restricted"] is False
    assert revoked.json()["is_public"] is False

    # Also verify showcase template d3 is NOT public by default
    admin_dashes = client.get("/api/v1/dashboards", headers=admin_headers).json()
    d3 = next((d for d in admin_dashes if "Showcase" in d["title"]), None)
    assert d3 is not None and d3["is_public"] is False


def test_stage4_duckdb_lock_readiness_and_sanitized_500():
    import asyncio
    import json
    from starlette.requests import Request as StarletteRequest
    from app.main import sanitized_unhandled_exception_handler

    readiness = warehouse.check_readiness()
    assert readiness["ok"] is True
    assert readiness["enable_external_access"] is False
    assert readiness["lock_configuration"] is True

    # Attempt to unlock or re-enable external access via SQL MUST fail
    with pytest.raises(Exception):
        warehouse.execute_query("SET enable_external_access = true")

    # Sanitized 500 handler MUST omit exception traceback/details
    fake_req = StarletteRequest({"type": "http", "method": "GET", "path": "/api/v1/boom", "headers": []})
    resp = asyncio.run(
        sanitized_unhandled_exception_handler(fake_req, RuntimeError("secret-db-password-leak"))
    )
    assert resp.status_code == 500
    body = json.loads(resp.body.decode())
    assert body["detail"] == "Internal server error"
    assert "secret-db-password-leak" not in resp.body.decode()
    assert "error_id" in body


def test_stage4_ssrf_ip_pinning_and_dns_rebinding_defense():
    with pytest.raises(ValueError):
        _validate_external_http_url("http://169.254.169.254/latest/meta-data/")

    # Simulate public DNS resolving to private IP in production -> MUST reject!
    old_env = settings.ENVIRONMENT
    try:
        settings.ENVIRONMENT = "production"
        with pytest.raises(ValueError):
            _validate_external_http_url("http://127.0.0.1:8000/api/v1/health")
        with pytest.raises(ValueError):
            _validate_external_http_url("http://localhost:8000/api/v1/health")
        with patch("app.engines.connectors.socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [
                (2, 1, 6, "", ("10.0.0.5", 443)),
            ]
            with pytest.raises(ValueError, match="Private|loopback|reserved"):
                _validate_external_http_url("https://rebind.attacker.example/data")

            # Public IP resolves cleanly and _PinnedSSRFHTTPTransport locks TCP connection to pinned IP
            mock_dns.return_value = [
                (2, 1, 6, "", ("93.184.216.34", 443)),
            ]
            _, host, port, ips = _validate_external_http_url("https://api.example.com/v1")
            assert host == "api.example.com" and port == 443 and ips == ["93.184.216.34"]
            tr = _PinnedSSRFHTTPTransport("api.example.com", "93.184.216.34", 443)
            with pytest.raises(ValueError, match="pin violation"):
                tr._pool._network_backend.connect_tcp("evil.example.com", 443)
            tr.close()
    finally:
        settings.ENVIRONMENT = old_env


def test_stage4_atomic_rate_limiter_and_cookie_refresh_no_token_leak(client: TestClient):
    # Test atomic Redis Lua branch via mocked Redis client
    mock_redis = MagicMock()
    mock_redis.eval.side_effect = [[1, 1, 0], [0, 1, 15]]
    old_redis = cache_manager._redis
    try:
        cache_manager._redis = mock_redis
        rate_limiter.check("test:lua", limit=1, window_seconds=60)
        with pytest.raises(HTTPException) as exc_info:
            rate_limiter.check("test:lua", limit=1, window_seconds=60)
        assert exc_info.value.status_code == 429
        assert exc_info.value.headers.get("Retry-After") == "15"
    finally:
        cache_manager._redis = old_redis

    # Cookie login + refresh MUST NOT leak access_token/refresh_token in JSON body
    login_r = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "Admin!2026"},
    )
    assert login_r.status_code == 200
    assert "access_token" not in login_r.json()
    csrf = client.cookies.get(settings.CSRF_COOKIE_NAME)
    ref_r = client.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": csrf})
    assert ref_r.status_code == 200
    assert "access_token" not in ref_r.json()
    assert "refresh_token" not in ref_r.json()
    assert ref_r.json()["auth_mode"] == "cookie"


def test_stage4_prod_schema_head_verification_and_etl_fencing():
    # _verify_production_schema_at_head succeeds when DB is at head (0003) and rejects behind head
    with tempfile.TemporaryDirectory(prefix="s4_alembic_") as tmp:
        tmp_path = Path(tmp)
        db_file = tmp_path / "m.db"
        env = {
            **os.environ,
            "BI_DATA_DIR": str(tmp_path),
            "DATABASE_URL": f"sqlite:///{db_file}",
            "WAREHOUSE_PATH": str(tmp_path / "warehouse.duckdb"),
            "SAMPLE_ERP_DB_PATH": str(tmp_path / "sample_erp.sqlite"),
        }
        res = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0002_stage3_external_identity_scope"],
            cwd=str(BACKEND_DIR),
            env=env,
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0, res.stderr

        check_behind = subprocess.run(
            [
                sys.executable,
                "-c",
                "from app.main import _verify_production_schema_at_head; _verify_production_schema_at_head()",
            ],
            cwd=str(BACKEND_DIR),
            env=env,
            capture_output=True,
            text=True,
        )
        assert check_behind.returncode != 0
        assert "revision mismatch" in (check_behind.stderr + check_behind.stdout)

        res_head = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=str(BACKEND_DIR),
            env=env,
            capture_output=True,
            text=True,
        )
        assert res_head.returncode == 0, res_head.stderr
        check_head = subprocess.run(
            [
                sys.executable,
                "-c",
                "from app.main import _verify_production_schema_at_head; _verify_production_schema_at_head(); print('HEAD_OK')",
            ],
            cwd=str(BACKEND_DIR),
            env=env,
            capture_output=True,
            text=True,
        )
        assert check_head.returncode == 0, check_head.stderr
        assert "HEAD_OK" in check_head.stdout

        # Tamper a pre-Alembic legacy DB (drop alembic_version & alter table) -> validate_legacy_baseline_schema MUST fail
        conn = sqlite3.connect(db_file)
        conn.execute("DROP TABLE alembic_version")
        conn.execute("ALTER TABLE dashboards DROP COLUMN allow_public_restricted")
        conn.commit()
        conn.close()
        from sqlalchemy import create_engine as _ce
        from app.db_migrate import validate_legacy_baseline_schema

        tampered_engine = _ce(f"sqlite:///{db_file}")
        try:
            mismatches = validate_legacy_baseline_schema(tampered_engine, backend_dir=BACKEND_DIR)
            assert len(mismatches) >= 1
        finally:
            tampered_engine.dispose()

    # ETL fencing & lease recovery
    db = SessionLocal()
    try:
        pl = db.query(EtlPipeline).first()
        assert pl is not None
        pl.is_running = True
        pl.fence_token = 5
        pl.active_worker_id = "other-worker"
        pl.lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        db.commit()

        # Expired lease is safely recovered
        recovered = recover_stale_etl_runs(db)
        assert recovered >= 1
        db.refresh(pl)
        assert pl.is_running is False

        # Fencing rejects superseded worker token
        pl.is_running = True
        pl.fence_token = 9
        pl.active_worker_id = "new-worker"
        db.commit()
        with pytest.raises(RuntimeError, match="fenced/superseded"):
            _assert_pipeline_fenced(db, pl.id, expected_fence_token=8, expected_worker_id="old-worker")
        pl.is_running = False
        pl.active_worker_id = None
        db.commit()
    finally:
        db.close()

    # Scheduler Lua atomic release only deletes matching owner token
    mock_redis = MagicMock()
    mock_redis.eval.return_value = 1
    old_redis = cache_manager._redis
    try:
        cache_manager._redis = mock_redis
        assert _acquire_or_renew_leader_lease() is True
        _release_leader_lease()
        assert any(_REDIS_RELEASE_LEADER_LUA in str(c.args[0]) for c in mock_redis.eval.call_args_list)
        assert any(_LEADER_ID in str(c.args) for c in mock_redis.eval.call_args_list)
    finally:
        cache_manager._redis = old_redis
