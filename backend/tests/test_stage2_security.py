import time
import uuid
from fastapi.testclient import TestClient
import pandas as pd
import pytest

from app.config import settings
from app.core.rate_limit import rate_limiter
from app.core.security import hash_password
from app.database import SessionLocal
from app.engines.connectors import parse_file_to_preview
from app.engines.data_engine import DataProfiler
from app.engines.warehouse import warehouse
from app.main import app
from app.models.dashboard import Dashboard, DashboardPage, Workspace
from app.models.data import Dataset
from app.models.identity import Organization, Role, RolePermission, Permission, User, UserProfile, UserRole


def _seed_second_org():
    """Creates a completely separate tenant Organization B with an admin user, dataset, and dashboard."""
    with SessionLocal() as db:
        org_b = Organization(name="Tenant B Corp", slug=f"tenant-b-{uuid.uuid4().hex[:6]}")
        db.add(org_b)
        db.flush()

        role_b = Role(
            organization_id=org_b.id,
            name="Org B Admin",
            slug="org_b_admin",
            is_system=False,
            is_super_admin=True,
        )
        db.add(role_b)
        db.flush()
        for p in db.query(Permission).all():
            db.add(RolePermission(role_id=role_b.id, permission_id=p.id, effect="allow"))

        uname_b = f"orgb_admin_{uuid.uuid4().hex[:6]}"
        user_b = User(
            organization_id=org_b.id,
            username=uname_b,
            email=f"{uname_b}@tenantb.local",
            password_hash=hash_password("TenantB!2026"),
            auth_source="local",
            is_active=True,
        )
        db.add(user_b)
        db.flush()
        db.add(UserProfile(user_id=user_b.id, full_name="Org B Admin", department="Finance", region="West"))
        db.add(UserRole(user_id=user_b.id, role_id=role_b.id))

        ws_b = Workspace(organization_id=org_b.id, name="Tenant B Workspace", created_by=user_b.id)
        db.add(ws_b)
        db.flush()

        wh_table_b = f"wh_tenant_b_{uuid.uuid4().hex[:6]}"
        warehouse.load_dataframe(
            wh_table_b,
            pd.DataFrame({"region": ["West", "East"], "secret_rev": [9999, 8888]}),
            mode="replace",
        )
        ds_b = Dataset(
            organization_id=org_b.id,
            name="Tenant B Confidential",
            warehouse_table=wh_table_b,
            row_count=2,
            freshness_status="fresh",
        )
        db.add(ds_b)
        db.flush()

        dash_b = Dashboard(
            workspace_id=ws_b.id,
            title="Tenant B Secret Dashboard",
            is_public=True,  # Even if marked public in Tenant B, Tenant A must NOT see it in authenticated API
            created_by=user_b.id,
        )
        db.add(dash_b)
        db.flush()
        db.add(DashboardPage(dashboard_id=dash_b.id, title="Main", page_order=0))
        db.commit()

        return {
            "org_b_id": org_b.id,
            "username_b": uname_b,
            "password_b": "TenantB!2026",
            "ds_b_id": ds_b.id,
            "wh_table_b": wh_table_b,
            "dash_b_id": dash_b.id,
        }


def test_stage2_multi_tenant_isolation(client, admin_headers):
    tenant_b = _seed_second_org()

    # Login as Tenant B admin using an isolated TestClient (separate cookie jar, no lifespan shutdown)
    client_b = TestClient(app)
    r_login = client_b.post(
        "/api/v1/auth/login",
        json={"username": tenant_b["username_b"], "password": tenant_b["password_b"]},
    )
    assert r_login.status_code == 200
    h_b = {"Authorization": f"Bearer {r_login.json()['access_token']}"}

    # Tenant B must NOT see Tenant A dashboards or datasets
    b_dashes = client_b.get("/api/v1/dashboards", headers=h_b).json()
    assert all(d["id"] == tenant_b["dash_b_id"] for d in b_dashes)

    b_datasets = client_b.get("/api/v1/datasets", headers=h_b).json()
    assert all(d["id"] == tenant_b["ds_b_id"] for d in b_datasets)

    # Tenant A must NOT access Tenant B's dashboard or dataset (even though Tenant B dash is_public=True)
    assert client.get(f"/api/v1/dashboards/{tenant_b['dash_b_id']}", headers=admin_headers).status_code == 404
    assert client.get(f"/api/v1/datasets/{tenant_b['ds_b_id']}/preview", headers=admin_headers).status_code == 404

    # Cross-tenant raw SQL table access in SQL Editor must be blocked (403)
    sql_cross_a_to_b = client.post(
        "/api/v1/queries/execute-sql",
        json={"sql_text": f'SELECT * FROM "{tenant_b["wh_table_b"]}"'},
        headers=admin_headers,
    )
    assert sql_cross_a_to_b.status_code == 403

    sql_cross_b_to_a = client_b.post(
        "/api/v1/queries/execute-sql",
        json={"sql_text": "SELECT * FROM wh_enterprise_sales"},
        headers=h_b,
    )
    assert sql_cross_b_to_a.status_code == 403


def test_stage2_sql_ast_xxe_formula_and_duckdb_hardening(client, admin_headers):
    attack_queries = [
        "SELECT * FROM read_csv_auto('/etc/passwd')",
        "SELECT * FROM read_parquet('https://evil.example/data.parquet')",
        "SELECT * FROM glob('/etc/*')",
        "SELECT getenv('JWT_SECRET') AS s",
        "SELECT * FROM duckdb_secrets()",
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM sqlite_master",
        "SELECT * FROM '/etc/passwd'",
        "SELECT 1; DROP TABLE wh_enterprise_sales",
        "PRAGMA version",
        "COPY wh_enterprise_sales TO '/tmp/leak.csv'",
        "ATTACH '/tmp/other.db' AS ext",
    ]
    for q in attack_queries:
        resp = client.post("/api/v1/queries/execute-sql", json={"sql_text": q}, headers=admin_headers)
        assert resp.status_code in (400, 403), f"Attack query not blocked: {q} -> {resp.status_code} {resp.text}"

    # Malicious custom_expression in visual filter must be blocked
    ds_id = client.get("/api/v1/datasets", headers=admin_headers).json()[0]["id"]
    bad_filter = client.post(
        "/api/v1/queries/execute-visual",
        json={
            "dataset_id": ds_id,
            "visual_spec": {"x_field": "region", "y_field": "revenue", "aggregation": "SUM"},
            "filters": [{"field_name": "region", "operator": "custom_expression", "value": "1=1 OR EXISTS(SELECT * FROM duckdb_secrets())", "is_active": True}],
        },
        headers=admin_headers,
    )
    assert bad_filter.status_code == 400

    # Safe formula eval must reject code execution attempts
    df = pd.DataFrame({"a": [10, 20], "b": [2, 4]})
    with pytest.raises(ValueError):
        DataProfiler.apply_transformations(
            df,
            [{"transform_type": "calculated", "is_enabled": True, "config_json": {"target_column": "evil", "formula": "__import__('os').system('id')"}}],
        )

    # Valid arithmetic formula succeeds
    out_df = DataProfiler.apply_transformations(
        df,
        [{"transform_type": "calculated", "is_enabled": True, "config_json": {"target_column": "ratio", "formula": "(a + b) / 2"}}],
    )
    assert list(out_df["ratio"]) == [6.0, 12.0]

    # Warehouse bounds & timeout cancellation
    res_clamp = warehouse.execute_query("SELECT * FROM wh_enterprise_sales", limit=0)
    assert res_clamp["row_count"] == 1

    with pytest.raises(TimeoutError):
        warehouse.execute_query(
            "WITH RECURSIVE t(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM t WHERE x < 100000000) SELECT COUNT(*) FROM t",
            timeout_sec=0.15,
        )
    # DuckDB remains healthy immediately after query interruption
    healthy = warehouse.execute_query("SELECT COUNT(*) AS cnt FROM wh_enterprise_sales")
    assert healthy["rows"][0]["cnt"] > 0

    # XXE payload inside XML upload must be neutralized/rejected by defusedxml
    xxe_bytes = b"""<?xml version="1.0"?>
<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<rows><row><name>&xxe;</name></row></rows>"""
    with pytest.raises(Exception):
        parse_file_to_preview(xxe_bytes, "xxe.xml")


def test_stage2_cookies_csrf_session_rotation_and_reuse_detection(client, admin_headers):
    # Use a dedicated test user so refresh-reuse detection revokes ONLY this user's sessions,
    # leaving the session-scoped admin_headers fixture intact for subsequent tests.
    roles_data = client.get("/api/v1/rbac/roles", headers=admin_headers).json()
    super_role_id = next(r["id"] for r in roles_data["roles"] if r["is_super_admin"])
    uname = f"cookie_u_{uuid.uuid4().hex[:6]}"
    create_res = client.post(
        "/api/v1/rbac/users",
        json={
            "username": uname,
            "email": f"{uname}@enterprise.local",
            "password": "CookieTest!2026",
            "role_ids": [super_role_id],
        },
        headers=admin_headers,
    )
    assert create_res.status_code == 200

    cookie_client = TestClient(app)
    login_res = cookie_client.post(
        "/api/v1/auth/login",
        json={"username": uname, "password": "CookieTest!2026"},
    )
    assert login_res.status_code == 200
    # Security headers verified
    assert login_res.headers.get("X-Content-Type-Options") == "nosniff"
    assert login_res.headers.get("X-Frame-Options") == "DENY"
    assert "no-store" in (login_res.headers.get("Cache-Control") or "")

    # Verify HttpOnly cookies + CSRF cookie were set
    set_cookies = login_res.headers.get_list("set-cookie")
    access_cookie_hdr = next(c for c in set_cookies if f"{settings.ACCESS_COOKIE_NAME}=" in c)
    refresh_cookie_hdr = next(c for c in set_cookies if f"{settings.REFRESH_COOKIE_NAME}=" in c)
    csrf_cookie_hdr = next(c for c in set_cookies if f"{settings.CSRF_COOKIE_NAME}=" in c)
    assert "HttpOnly" in access_cookie_hdr
    assert "HttpOnly" in refresh_cookie_hdr
    assert "HttpOnly" not in csrf_cookie_hdr

    csrf_val = cookie_client.cookies.get(settings.CSRF_COOKIE_NAME)
    old_refresh_cookie = cookie_client.cookies.get(settings.REFRESH_COOKIE_NAME)
    assert csrf_val and old_refresh_cookie

    # Cookie-only GET succeeds
    me_res = cookie_client.get("/api/v1/auth/me")
    assert me_res.status_code == 200

    # Cookie-only state-changing POST WITHOUT X-CSRF-Token is blocked with 403!
    no_csrf = cookie_client.post("/api/v1/dashboards", json={"title": "CSRF Attack Dash"})
    assert no_csrf.status_code == 403

    # With valid X-CSRF-Token it succeeds!
    with_csrf = cookie_client.post(
        "/api/v1/dashboards",
        json={"title": "Legit Cookie Dash"},
        headers={"X-CSRF-Token": csrf_val},
    )
    assert with_csrf.status_code == 200
    created_id = with_csrf.json()["id"]

    # Refresh token rotation works and updates CSRF & refresh cookie
    ref_res = cookie_client.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": csrf_val})
    assert ref_res.status_code == 200
    new_csrf = cookie_client.cookies.get(settings.CSRF_COOKIE_NAME)
    assert cookie_client.cookies.get(settings.REFRESH_COOKIE_NAME) != old_refresh_cookie

    # Session list works
    sess_res = cookie_client.get("/api/v1/auth/sessions")
    assert sess_res.status_code == 200
    assert len(sess_res.json()) >= 1

    # Refresh token REUSE DETECTION: replaying old_refresh_cookie revokes session family!
    reuse_res = cookie_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": old_refresh_cookie},
        headers={"X-CSRF-Token": new_csrf},
    )
    assert reuse_res.status_code == 401

    # Subsequent /auth/me with the compromised session must also fail (401)
    assert cookie_client.get("/api/v1/auth/me").status_code == 401

    # Clean up created dashboard using fresh client login
    relogin = cookie_client.post(
        "/api/v1/auth/login",
        json={"username": uname, "password": "CookieTest!2026"},
    )
    assert relogin.status_code == 200
    csrf2 = cookie_client.cookies.get(settings.CSRF_COOKIE_NAME)
    assert cookie_client.delete(
        f"/api/v1/dashboards/{created_id}", headers={"X-CSRF-Token": csrf2}
    ).status_code == 200


def test_stage2_privilege_escalation_password_policy_and_concurrency(client, admin_headers):
    # 1. Weak password rejected
    weak = client.post(
        "/api/v1/rbac/users",
        json={"username": "weak_u", "email": "weak@enterprise.local", "password": "123"},
        headers=admin_headers,
    )
    assert weak.status_code == 400

    # 2. Create a delegated non-super-admin role that has admin:add/edit
    roles_data = client.get("/api/v1/rbac/roles", headers=admin_headers).json()
    admin_perms = [p["id"] for p in roles_data["catalog"] if p["resource_type"] == "admin"]
    super_role_id = next(r["id"] for r in roles_data["roles"] if r["is_super_admin"])

    delegated_role = client.post(
        "/api/v1/rbac/roles",
        json={"name": "SubAdmin", "slug": f"subadmin_{uuid.uuid4().hex[:6]}", "permission_ids": admin_perms},
        headers=admin_headers,
    )
    assert delegated_role.status_code == 200
    sub_role_id = delegated_role.json()["id"]

    sub_uname = f"subadmin_{uuid.uuid4().hex[:6]}"
    sub_u = client.post(
        "/api/v1/rbac/users",
        json={
            "username": sub_uname,
            "email": f"{sub_uname}@enterprise.local",
            "password": "SubAdmin!2026",
            "role_ids": [sub_role_id],
        },
        headers=admin_headers,
    )
    assert sub_u.status_code == 200
    sub_uid = sub_u.json()["id"]

    sub_tok = client.post("/api/v1/auth/login", json={"username": sub_uname, "password": "SubAdmin!2026"}).json()["access_token"]
    sub_hdrs = {"Authorization": f"Bearer {sub_tok}"}

    # SubAdmin must NOT escalate themselves or others to super_admin (403)
    esc = client.put(
        f"/api/v1/rbac/users/{sub_uid}",
        json={"role_ids": [super_role_id]},
        headers=sub_hdrs,
    )
    assert esc.status_code == 403

    esc_matrix = client.put(
        "/api/v1/rbac/permissions/matrix/set",
        json={
            "subject_type": "role",
            "subject_id": super_role_id,
            "resource_type": "dashboard",
            "resource_id": "*",
            "action": "delete",
            "effect": "deny",
        },
        headers=sub_hdrs,
    )
    assert esc_matrix.status_code == 403

    # 3. Optimistic concurrency check on Dashboard update (409 Conflict on stale timestamp)
    dashes = client.get("/api/v1/dashboards", headers=admin_headers).json()
    d0 = client.get(f"/api/v1/dashboards/{dashes[0]['id']}", headers=admin_headers).json()
    stale = client.put(
        f"/api/v1/dashboards/{d0['id']}",
        json={"description": "Stale Edit", "expected_updated_at": "2000-01-01T00:00:00"},
        headers=admin_headers,
    )
    assert stale.status_code == 409

    # 4. Rate limiter raises 429 when limit exceeded
    rate_limiter.reset()
    test_key = f"test_rl_{uuid.uuid4().hex[:6]}"
    rate_limiter.check(test_key, limit=2, window_seconds=60)
    rate_limiter.check(test_key, limit=2, window_seconds=60)
    with pytest.raises(Exception) as exc_info:
        rate_limiter.check(test_key, limit=2, window_seconds=60)
    assert getattr(exc_info.value, "status_code", None) == 429
    rate_limiter.reset()
