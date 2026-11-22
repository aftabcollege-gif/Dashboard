import concurrent.futures
import os
import shutil
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from fastapi import HTTPException

from app.api.deps import get_client_ip, is_https_request, is_trusted_proxy_ip
from app.config import settings
from app.core.cache import cache_manager
from app.core.rate_limit import rate_limiter
from app.core.rls_cls import build_data_security_context
from app.core.security import hash_password
from app.core.sql_guard import validate_readonly_sql_ast
from app.database import SessionLocal
from app.engines.ad_ldap import ActiveDirectoryEngine, _build_ldap_server, get_mock_directory
from app.engines.data_engine import DataProfiler
from app.engines.etl_engine import recover_stale_etl_runs, run_etl_pipeline
from app.engines.filter_engine import get_cascading_filter_options
from app.engines.scheduler import (
    _acquire_or_renew_leader_lease,
    _release_leader_lease,
    start_scheduler,
    stop_scheduler,
)
from app.engines.warehouse import warehouse
from app.models.dashboard import Dashboard, DashboardPage, Widget, WidgetQuery, Workspace
from app.models.data import Dataset, DatasetField
from app.models.etl import EtlJob, EtlJobRun, EtlPipeline
from app.models.identity import (
    AccessPolicy,
    DirectoryConnection,
    ExternalIdentity,
    Organization,
    Permission,
    ResourcePermission,
    Role,
    RolePermission,
    User,
    UserProfile,
    UserRole,
    UserSession,
)

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _register_dataset_with_fields(
    db,
    org_id: str,
    name: str,
    df: pd.DataFrame,
    fields_meta: list[tuple[str, str, str]],
) -> Dataset:
    wh_table = f"wh_s3_{uuid.uuid4().hex[:8]}"
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


def _create_isolated_org(db, prefix: str = "s3org") -> tuple[Organization, Workspace, Role]:
    suffix = uuid.uuid4().hex[:6]
    org = Organization(name=f"{prefix}-{suffix}", slug=f"{prefix}-{suffix}")
    db.add(org)
    db.flush()
    admin_role = Role(
        organization_id=org.id,
        name=f"OrgAdmin {suffix}",
        slug=f"org_admin_{suffix}",
        is_super_admin=True,
    )
    db.add(admin_role)
    db.flush()
    for p in db.query(Permission).all():
        db.add(RolePermission(role_id=admin_role.id, permission_id=p.id, effect="allow"))
    admin_user = User(
        organization_id=org.id,
        username=f"{prefix}_adm_{suffix}",
        email=f"{prefix}_adm_{suffix}@corp.local",
        password_hash=hash_password("OrgAdmin!2026"),
        auth_source="local",
        is_active=True,
    )
    db.add(admin_user)
    db.flush()
    db.add(UserRole(user_id=admin_user.id, role_id=admin_role.id))
    ws = Workspace(organization_id=org.id, name=f"WS {suffix}", created_by=admin_user.id)
    db.add(ws)
    db.flush()
    return org, ws, admin_role


def test_stage3_sql_guard_catalog_and_schema_rejection():
    for bad_sql in [
        "SELECT * FROM main.wh_sales_fact",
        "SELECT * FROM duckdb_catalog.main.wh_sales_fact",
        "SELECT * FROM pg_catalog.pg_tables",
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM sqlite_master",
        'SELECT * FROM ("main"."wh_sales_fact")',
    ]:
        with pytest.raises((ValueError, PermissionError)):
            validate_readonly_sql_ast(bad_sql, allowed_tables={"wh_sales_fact"})


def test_stage3_sql_guard_cte_shadowing_and_scope():
    with pytest.raises((ValueError, PermissionError)):
        validate_readonly_sql_ast(
            "WITH wh_sales_fact AS (SELECT 1 AS a) SELECT * FROM wh_sales_fact",
            allowed_tables={"wh_sales_fact"},
        )
    with pytest.raises((ValueError, PermissionError)):
        validate_readonly_sql_ast(
            "WITH agg AS (SELECT 1 AS x) SELECT * FROM wh_secret_payroll",
            allowed_tables={"wh_sales_fact"},
        )
    refs = validate_readonly_sql_ast(
        'WITH agg AS (SELECT region FROM "wh_sales_fact") SELECT region FROM agg',
        allowed_tables={"wh_sales_fact"},
    )
    assert "wh_sales_fact" in refs


def test_stage3_sql_guard_mysql_percent_s_normalization():
    mysql_refs = validate_readonly_sql_ast(
        "SELECT id, amount FROM orders WHERE updated_at > %s AND status = %s",
        dialect="mysql",
    )
    assert "orders" in mysql_refs
    for unsafe_mysql in [
        "DELETE FROM orders WHERE id = %s",
        "INSERT INTO orders (id) VALUES (%s)",
        "CALL dangerous_proc(%s)",
        "SELECT 1; DROP TABLE orders",
    ]:
        with pytest.raises((ValueError, PermissionError)):
            validate_readonly_sql_ast(unsafe_mysql, dialect="mysql")


def test_stage3_pre_sql_rls_cls_masking_and_hidden_column_rejection(client):
    suffix = uuid.uuid4().hex[:6]
    uname = f"rls_viewer_{suffix}"
    with SessionLocal() as db:
        org, _, _ = _create_isolated_org(db, prefix="rlsorg")
        role = Role(organization_id=org.id, name=f"Viewer {suffix}", slug=f"viewer_{suffix}")
        db.add(role)
        db.flush()
        for perm in db.query(Permission).filter(
            Permission.resource_type.in_(["dataset", "query", "dashboard", "widget"]),
            Permission.action.in_(["view", "execute"]),
        ).all():
            db.add(RolePermission(role_id=role.id, permission_id=perm.id, effect="allow"))

        user = User(
            organization_id=org.id,
            username=uname,
            email=f"{uname}@corp.local",
            password_hash=hash_password("ViewerPass!2026"),
            auth_source="local",
            is_active=True,
        )
        db.add(user)
        db.flush()
        db.add(UserProfile(user_id=user.id, full_name="North Viewer", region="North"))
        db.add(UserRole(user_id=user.id, role_id=role.id))

        ds = _register_dataset_with_fields(
            db,
            org.id,
            f"Secure Sales {suffix}",
            pd.DataFrame(
                {
                    "region": ["North", "South", "North"],
                    "customer_email": ["alice@corp.com", "bob@corp.com", "charlie@corp.com"],
                    "secret_margin": [900, 1200, 750],
                    "revenue": [1000, 2500, 1500],
                }
            ),
            [
                ("region", "string", "dimension"),
                ("customer_email", "string", "dimension"),
                ("secret_margin", "integer", "measure"),
                ("revenue", "integer", "measure"),
            ],
        )

        db.add(
            AccessPolicy(
                organization_id=org.id,
                name=f"rls_reg_{suffix}",
                policy_type="rls",
                dataset_id=ds.id,
                target_type="user",
                target_id=user.id,
                rule_json={"column": "region", "operator": "eq", "value": "${user.region}"},
                is_active=True,
            )
        )
        db.add(
            AccessPolicy(
                organization_id=org.id,
                name=f"cls_sec_{suffix}",
                policy_type="cls",
                dataset_id=ds.id,
                target_type="user",
                target_id=user.id,
                rule_json={"hidden_columns": ["secret_margin"]},
                is_active=True,
            )
        )
        db.add(
            AccessPolicy(
                organization_id=org.id,
                name=f"mask_em_{suffix}",
                policy_type="masking",
                dataset_id=ds.id,
                target_type="user",
                target_id=user.id,
                rule_json={"column": "customer_email", "strategy": "partial"},
                is_active=True,
            )
        )
        db.commit()
        ds_id = ds.id
        wh_table = ds.warehouse_table

    login = client.post(
        "/api/v1/auth/login",
        json={"username": uname, "password": "ViewerPass!2026"},
        headers={"X-Auth-Mode": "bearer"},
    )
    assert login.status_code == 200
    u_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    vq = client.post(
        "/api/v1/queries/execute-visual",
        headers=u_headers,
        json={
            "dataset_id": ds_id,
            "visual_spec": {"columns": ["region", "customer_email", "revenue"], "raw_mode": True},
        },
    )
    assert vq.status_code == 200, vq.text
    vdata = vq.json()
    assert all(r["region"] == "North" for r in vdata["rows"])
    assert len(vdata["rows"]) == 2
    assert "secret_margin" not in vdata["columns"]
    assert all(
        "*" in str(r["customer_email"]) and r["customer_email"] not in ("alice@corp.com", "charlie@corp.com")
        for r in vdata["rows"]
    )

    sq_star = client.post(
        "/api/v1/queries/execute-sql",
        headers=u_headers,
        json={"dataset_id": ds_id, "sql_text": f'SELECT * FROM "{wh_table}"'},
    )
    assert sq_star.status_code == 200, sq_star.text
    assert "secret_margin" not in sq_star.json()["columns"]
    assert len(sq_star.json()["rows"]) == 2

    for forbidden_sql in [
        f'SELECT secret_margin FROM "{wh_table}"',
        f'SELECT region FROM "{wh_table}" WHERE secret_margin > 100',
        f'WITH "{wh_table}" AS (SELECT 1 AS x) SELECT * FROM "{wh_table}"',
    ]:
        sq_bad = client.post(
            "/api/v1/queries/execute-sql",
            headers=u_headers,
            json={"dataset_id": ds_id, "sql_text": forbidden_sql},
        )
        assert sq_bad.status_code in (400, 403)

    with SessionLocal() as db:
        u_obj = db.query(User).filter(User.username == uname).first()
        ds_obj = db.query(Dataset).filter(Dataset.id == ds_id).first()
        sc = build_data_security_context(db, u_obj, ds_obj.id)
        prof = DataProfiler.profile_table(ds_obj.warehouse_table, sec_ctx=sc)
        assert "secret_margin" not in [c["field_name"] for c in prof["fields"]]
        assert prof["row_count"] == 2
        assert get_cascading_filter_options(ds_obj.warehouse_table, "region", [], [], [], sec_ctx=sc) == ["North"]
        assert get_cascading_filter_options(ds_obj.warehouse_table, "secret_margin", [], [], [], sec_ctx=sc) == []

    rec = client.get(f"/api/v1/ai/recommend/{ds_id}", headers=u_headers)
    assert rec.status_code == 200
    assert "secret_margin" not in str(rec.json())


def _setup_denied_dataset_user(db):
    suffix = uuid.uuid4().hex[:6]
    uname = f"join_user_{suffix}"
    org, ws, _ = _create_isolated_org(db, prefix="joinorg")
    role = Role(organization_id=org.id, name=f"JoinRole {suffix}", slug=f"joinrole_{suffix}")
    db.add(role)
    db.flush()
    for perm in db.query(Permission).filter(
        Permission.resource_type.in_(["dataset", "query", "dashboard", "widget"]),
        Permission.action.in_(["view", "execute"]),
    ).all():
        db.add(RolePermission(role_id=role.id, permission_id=perm.id, effect="allow"))

    user = User(
        organization_id=org.id,
        username=uname,
        email=f"{uname}@corp.local",
        password_hash=hash_password("JoinPass!2026"),
        auth_source="local",
        is_active=True,
    )
    db.add(user)
    db.flush()
    db.add(UserRole(user_id=user.id, role_id=role.id))

    ds_a = _register_dataset_with_fields(
        db, org.id, f"Join A {suffix}",
        pd.DataFrame({"k": [1, 2], "val_a": [10, 20]}),
        [("k", "integer", "dimension"), ("val_a", "integer", "measure")],
    )
    ds_b = _register_dataset_with_fields(
        db, org.id, f"Join B {suffix}",
        pd.DataFrame({"k": [1, 2], "secret_b": [999, 888]}),
        [("k", "integer", "dimension"), ("secret_b", "integer", "measure")],
    )
    db.add(
        ResourcePermission(
            organization_id=org.id,
            subject_type="user",
            subject_id=user.id,
            resource_type="dataset",
            resource_id=ds_b.id,
            action="view",
            effect="deny",
        )
    )
    dash = Dashboard(workspace_id=ws.id, title=f"Join Dash {suffix}", created_by=user.id)
    db.add(dash)
    db.flush()
    page = DashboardPage(dashboard_id=dash.id, title="Main", page_order=0)
    db.add(page)
    db.flush()
    widget_b = Widget(
        dashboard_id=dash.id, page_id=page.id, title="Denied DS Widget",
        chart_type="bar", dataset_id=ds_b.id,
    )
    db.add(widget_b)
    db.flush()
    db.add(
        WidgetQuery(
            widget_id=widget_b.id,
            dataset_id=ds_b.id,
            query_mode="visual",
            visual_spec_json={"dataset_id": ds_b.id, "dimensions": ["k"], "measures": [{"field": "secret_b", "agg": "SUM"}]},
        )
    )
    db.commit()
    return uname, ds_a.id, ds_b.id, dash.id, widget_b.id


def test_stage3_visual_join_dataset_rbac(client):
    with SessionLocal() as db:
        uname, ds_a_id, ds_b_id, _, _ = _setup_denied_dataset_user(db)
    login = client.post(
        "/api/v1/auth/login",
        json={"username": uname, "password": "JoinPass!2026"},
        headers={"X-Auth-Mode": "bearer"},
    )
    assert login.status_code == 200
    u_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    res_join = client.post(
        "/api/v1/queries/execute-visual",
        headers=u_headers,
        json={
            "dataset_id": ds_a_id,
            "visual_spec": {
                "dimensions": ["k"],
                "measures": [{"field": "val_a", "agg": "SUM"}],
                "joins": [{"dataset_id": ds_b_id, "left_key": "k", "right_key": "k", "join_type": "INNER"}],
            },
        },
    )
    assert res_join.status_code == 403


def test_stage3_widget_explicit_dataset_deny_rbac(client):
    with SessionLocal() as db:
        uname, _, _, dash_id, widget_b_id = _setup_denied_dataset_user(db)
    login = client.post(
        "/api/v1/auth/login",
        json={"username": uname, "password": "JoinPass!2026"},
        headers={"X-Auth-Mode": "bearer"},
    )
    assert login.status_code == 200
    u_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    res_w = client.post(f"/api/v1/dashboards/widgets/{widget_b_id}/data", headers=u_headers, json={})
    assert res_w.status_code == 403

    res_batch = client.post(f"/api/v1/dashboards/{dash_id}/execute-batch", headers=u_headers, json={})
    assert res_batch.status_code == 200
    assert res_batch.json()["widgets"][widget_b_id].get("error")


def test_stage3_public_dashboard_share_rbac(client):
    suffix = uuid.uuid4().hex[:6]
    uname = f"editor_noshare_{suffix}"
    with SessionLocal() as db:
        org, ws, _ = _create_isolated_org(db, prefix="shareorg")
        role = Role(organization_id=org.id, name=f"EditorNoShare {suffix}", slug=f"ednoshare_{suffix}")
        db.add(role)
        db.flush()
        for perm in db.query(Permission).filter(
            Permission.resource_type.in_(["dashboard", "widget", "dataset", "query"]),
            Permission.action.in_(["view", "edit", "add", "execute"]),
        ).all():
            db.add(RolePermission(role_id=role.id, permission_id=perm.id, effect="allow"))

        user = User(
            organization_id=org.id,
            username=uname,
            email=f"{uname}@corp.local",
            password_hash=hash_password("EditorPass!2026"),
            auth_source="local",
            is_active=True,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        dash = Dashboard(workspace_id=ws.id, title=f"Share Dash {suffix}", is_public=False, created_by=user.id)
        db.add(dash)
        db.commit()
        dash_id = dash.id

    login = client.post(
        "/api/v1/auth/login",
        json={"username": uname, "password": "EditorPass!2026"},
        headers={"X-Auth-Mode": "bearer"},
    )
    assert login.status_code == 200
    e_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert client.put(f"/api/v1/dashboards/{dash_id}", headers=e_headers, json={"is_public": True}).status_code == 403
    assert client.post(f"/api/v1/dashboards/{dash_id}/share", headers=e_headers).status_code == 403


def test_stage3_public_dashboard_sql_cte_isolation(client):
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        org, ws, _ = _create_isolated_org(db, prefix="pubcte")
        ds_pub = _register_dataset_with_fields(
            db, org.id, f"Pub DS {suffix}",
            pd.DataFrame({"region": ["North", "South"], "sales": [100, 200]}),
            [("region", "string", "dimension"), ("sales", "integer", "measure")],
        )
        ds_priv = _register_dataset_with_fields(
            db, org.id, f"Priv DS {suffix}",
            pd.DataFrame({"secret": ["classified"]}),
            [("secret", "string", "dimension")],
        )
        embed_tok = f"pubtok_{suffix}"
        dash = Dashboard(
            workspace_id=ws.id, title=f"Pub Dash {suffix}", is_public=True, embed_token=embed_tok,
        )
        db.add(dash)
        db.flush()
        page = DashboardPage(dashboard_id=dash.id, title="Main", page_order=0)
        db.add(page)
        db.flush()
        w_bad = Widget(dashboard_id=dash.id, page_id=page.id, title="Bad Public SQL", chart_type="table", dataset_id=ds_pub.id)
        db.add(w_bad)
        db.flush()
        db.add(
            WidgetQuery(
                widget_id=w_bad.id,
                dataset_id=ds_pub.id,
                query_mode="sql",
                sql_text=f'WITH safe AS (SELECT 1 AS x) SELECT * FROM "{ds_priv.warehouse_table}"',
            )
        )
        db.commit()
        w_bad_id = w_bad.id

    r_pub = client.post(f"/api/v1/dashboards/public/{embed_tok}/execute-batch", json={})
    assert r_pub.status_code == 200
    assert r_pub.json()["widgets"][w_bad_id].get("error")


def test_stage3_connector_readonly_and_timeout(client, admin_headers, tmp_path):
    db_file = tmp_path / "ext_readonly.sqlite"
    con = sqlite3.connect(str(db_file))
    con.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
    con.execute("INSERT INTO items VALUES (1, 'alpha'), (2, 'beta')")
    con.commit()
    con.close()

    ds_res = client.post(
        "/api/v1/data-sources",
        headers=admin_headers,
        json={
            "name": f"Readonly SQLite {uuid.uuid4().hex[:6]}",
            "source_type": "sqlite",
            "config_json": {"file_path": str(db_file)},
        },
    )
    assert ds_res.status_code == 200, ds_res.text
    src_id = ds_res.json()["id"]

    ok = client.post(f"/api/v1/data-sources/{src_id}/query", headers=admin_headers, json={"query": "SELECT * FROM items"})
    assert ok.status_code == 200, ok.text
    assert len(ok.json()["rows"]) == 2

    for bad in ["DROP TABLE items", "INSERT INTO items VALUES (3, 'x')", "PRAGMA database_list"]:
        r = client.post(f"/api/v1/data-sources/{src_id}/query", headers=admin_headers, json={"query": bad})
        assert r.status_code in (400, 403)

    with patch(
        "app.engines.connectors.DataConnectorEngine.extract_dataframe",
        side_effect=TimeoutError("External query timed out"),
    ):
        t_res = client.post(
            f"/api/v1/data-sources/{src_id}/query",
            headers=admin_headers,
            json={"query": "SELECT * FROM items"},
        )
        assert t_res.status_code == 504


def test_stage3_cookie_default_no_token_leak(client):
    suffix = uuid.uuid4().hex[:6]
    uname = f"ck_user_{suffix}"
    with SessionLocal() as db:
        _, _, role = _create_isolated_org(db, prefix="ckorg")
        u = User(
            organization_id=role.organization_id,
            username=uname,
            email=f"{uname}@corp.local",
            password_hash=hash_password("CookiePass!2026"),
            auth_source="local",
            is_active=True,
        )
        db.add(u)
        db.flush()
        db.add(UserRole(user_id=u.id, role_id=role.id))
        db.commit()

    res_cookie = client.post("/api/v1/auth/login", json={"username": uname, "password": "CookiePass!2026"})
    assert res_cookie.status_code == 200
    body = res_cookie.json()
    assert "access_token" not in body
    assert "refresh_token" not in body
    assert settings.ACCESS_COOKIE_NAME in res_cookie.cookies
    assert settings.REFRESH_COOKIE_NAME in res_cookie.cookies


def test_stage3_concurrent_refresh_atomic_cas(client):
    suffix = uuid.uuid4().hex[:6]
    uname = f"cas_user_{suffix}"
    with SessionLocal() as db:
        _, _, role = _create_isolated_org(db, prefix="casorg")
        u = User(
            organization_id=role.organization_id,
            username=uname,
            email=f"{uname}@corp.local",
            password_hash=hash_password("CasPass!2026"),
            auth_source="local",
            is_active=True,
        )
        db.add(u)
        db.flush()
        db.add(UserRole(user_id=u.id, role_id=role.id))
        db.commit()

    res_bearer = client.post(
        "/api/v1/auth/login",
        json={"username": uname, "password": "CasPass!2026"},
        headers={"X-Auth-Mode": "bearer"},
    )
    assert res_bearer.status_code == 200
    rtok = res_bearer.json()["refresh_token"]

    def _do_refresh():
        return client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": rtok},
            headers={"X-Auth-Mode": "bearer"},
        ).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        statuses = list(pool.map(lambda _: _do_refresh(), range(6)))

    assert statuses.count(200) == 1, f"Expected 1 winner, got {statuses}"
    assert statuses.count(401) == 5, f"Expected 5 rejections, got {statuses}"


def test_stage3_trusted_proxy_headers_and_https_detection():
    orig_trust = settings.TRUST_PROXY_HEADERS
    orig_ips = settings.TRUSTED_PROXY_IPS
    try:
        settings.TRUST_PROXY_HEADERS = True
        settings.TRUSTED_PROXY_IPS = "10.0.0.0/8,127.0.0.1"
        untrusted_req = SimpleNamespace(
            client=SimpleNamespace(host="198.51.100.77"),
            headers={"x-forwarded-for": "1.2.3.4", "x-forwarded-proto": "https"},
            url=SimpleNamespace(scheme="http"),
        )
        assert not is_trusted_proxy_ip("198.51.100.77")
        assert get_client_ip(untrusted_req) == "198.51.100.77"
        assert is_https_request(untrusted_req) is False

        trusted_req = SimpleNamespace(
            client=SimpleNamespace(host="10.2.3.4"),
            headers={"x-forwarded-for": "203.0.113.9, 10.1.1.5", "x-forwarded-proto": "https"},
            url=SimpleNamespace(scheme="http"),
        )
        assert is_trusted_proxy_ip("10.2.3.4")
        assert get_client_ip(trusted_req) == "203.0.113.9"
        assert is_https_request(trusted_req) is True
    finally:
        settings.TRUST_PROXY_HEADERS = orig_trust
        settings.TRUSTED_PROXY_IPS = orig_ips


def test_stage3_rate_limiter_fail_closed_in_production():
    orig_env = settings.ENVIRONMENT
    orig_redis_url = settings.REDIS_URL
    orig_fail_closed = settings.RATE_LIMIT_FAIL_CLOSED_PROD
    orig_redis_client = cache_manager._redis
    try:
        settings.ENVIRONMENT = "production"
        settings.REDIS_URL = "redis://127.0.0.1:6399/0"
        settings.RATE_LIMIT_FAIL_CLOSED_PROD = True
        cache_manager._redis = None

        with pytest.raises(HTTPException) as exc_503:
            rate_limiter.check("prod_fail_closed_test", limit=5, window_seconds=60)
        assert exc_503.value.status_code == 503

        broken_redis = MagicMock()
        broken_redis.eval.side_effect = ConnectionError("Redis unreachable")
        cache_manager._redis = broken_redis
        with pytest.raises(HTTPException) as exc_503_err:
            rate_limiter.check("prod_fail_closed_err", limit=5, window_seconds=60)
        assert exc_503_err.value.status_code == 503
    finally:
        settings.ENVIRONMENT = orig_env
        settings.REDIS_URL = orig_redis_url
        settings.RATE_LIMIT_FAIL_CLOSED_PROD = orig_fail_closed
        cache_manager._redis = orig_redis_client


def test_stage3_ad_ldap_strict_tls_and_filter_escaping():
    conn_row = DirectoryConnection(
        organization_id="org1",
        name="LDAPS Prod",
        domain_name="corp.local",
        server_url="ldaps://dc01.corp.local:636",
        use_ldaps=True,
        base_dn="DC=corp,DC=local",
        bind_dn="CN=svc,DC=corp,DC=local",
        bind_password_encrypted="x",
        directory_mode="ldap",
    )
    srv = _build_ldap_server(conn_row)
    assert srv.ssl is True
    assert srv.tls.validate == ssl.CERT_REQUIRED

    fake_conn = MagicMock()
    fake_conn.entries = []
    with patch("app.engines.ad_ldap.decrypt_secret", return_value="pw"), patch(
        "app.engines.ad_ldap.ldap3.Connection", return_value=fake_conn
    ):
        ActiveDirectoryEngine.search_directory(conn_row, "alice*)(objectClass=*")
    user_filter = fake_conn.search.call_args_list[0][0][1]
    assert "\\2a" in user_filter and "\\29" in user_filter


def test_stage3_ad_sync_cross_org_conflict_and_session_revocation():
    with SessionLocal() as db:
        org_a = db.query(Organization).first()
        org_b, _, _ = _create_isolated_org(db, prefix="orgb_ad")
        conn_b = DirectoryConnection(
            organization_id=org_b.id,
            name="Org B Mock AD",
            domain_name=f"orgb-{uuid.uuid4().hex[:5]}.local",
            server_url="ldap://mock",
            use_ldaps=False,
            base_dn="DC=orgb,DC=local",
            bind_dn="CN=svc,DC=orgb,DC=local",
            bind_password_encrypted="dummy",
            directory_mode="mock_ad",
            sync_policy="manual",
            selected_groups_json=[],
        )
        db.add(conn_b)
        db.flush()
        ad_dir = get_mock_directory(conn_b.domain_name)
        ad_dir["users"] = [
            {
                "objectGUID": "guid-collision-admin",
                "sAMAccountName": "admin",
                "mail": "admin@corp.enterprise.local",
                "displayName": "Hijack Attempt",
                "department": "IT",
                "enabled": True,
                "password": "AdPass!2026",
                "groups": ["BI_Analysts"],
            },
            {
                "objectGUID": "guid-b-legit",
                "sAMAccountName": f"b_user_{uuid.uuid4().hex[:5]}",
                "mail": f"b_user_{uuid.uuid4().hex[:5]}@orgb.local",
                "displayName": "Legit B User",
                "department": "Sales",
                "enabled": True,
                "password": "AdPass!2026",
                "groups": ["BI_Analysts"],
            },
        ]
        ad_dir["groups"] = [{"cn": "BI_Analysts", "dn": "CN=BI_Analysts,DC=orgb,DC=local", "description": "Analysts"}]
        db.commit()
        org_a_id, conn_b_id, domain_b = org_a.id, conn_b.id, conn_b.domain_name
        legit_uname = ad_dir["users"][1]["sAMAccountName"]

    with SessionLocal() as db:
        conn_b = db.query(DirectoryConnection).filter(DirectoryConnection.id == conn_b_id).first()
        with pytest.raises(ValueError, match="conflicts with another organization"):
            ActiveDirectoryEngine.sync_directory(db, conn_b, sync_type="full")
        org_a_admin = db.query(User).filter(User.username == "admin").first()
        assert org_a_admin.organization_id == org_a_id
        assert org_a_admin.auth_source == "local"

    get_mock_directory(domain_b)["users"] = [get_mock_directory(domain_b)["users"][1]]
    with SessionLocal() as db:
        conn_b = db.query(DirectoryConnection).filter(DirectoryConnection.id == conn_b_id).first()
        ActiveDirectoryEngine.sync_directory(db, conn_b, sync_type="full")
        legit_u = db.query(User).filter(User.username == legit_uname).first()
        assert legit_u is not None and legit_u.organization_id == conn_b.organization_id
        assert db.query(ExternalIdentity).filter(ExternalIdentity.user_id == legit_u.id).count() == 1
        sess = UserSession(
            user_id=legit_u.id,
            jti=f"ad-sess-{uuid.uuid4().hex}",
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            is_revoked=False,
        )
        db.add(sess)
        db.commit()
        sess_id, legit_id = sess.id, legit_u.id

    get_mock_directory(domain_b)["users"][0]["enabled"] = False
    with SessionLocal() as db:
        conn_b = db.query(DirectoryConnection).filter(DirectoryConnection.id == conn_b_id).first()
        ActiveDirectoryEngine.sync_directory(db, conn_b, sync_type="full")
        assert db.query(User).filter(User.id == legit_id).first().is_active is False
        assert db.query(UserSession).filter(UserSession.id == sess_id).first().is_revoked is True


def test_stage3_etl_ast_validation_stale_recovery_and_duplicate_claim(client, admin_headers, tmp_path):
    db_file = tmp_path / "etl_test.sqlite"
    con = sqlite3.connect(str(db_file))
    con.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, updated_at TEXT, total REAL)")
    con.execute("INSERT INTO orders VALUES (1, '2026-01-01', 100.0), (2, '2026-01-02', 200.0)")
    con.commit()
    con.close()

    src_res = client.post(
        "/api/v1/data-sources",
        headers=admin_headers,
        json={"name": f"ETL Src {uuid.uuid4().hex[:5]}", "source_type": "sqlite", "config_json": {"file_path": str(db_file)}},
    )
    assert src_res.status_code == 200, src_res.text
    src = src_res.json()

    with SessionLocal() as db:
        org = db.query(Organization).first()
        target_ds = _register_dataset_with_fields(
            db, org.id, f"ETL Target {uuid.uuid4().hex[:5]}",
            pd.DataFrame({"id": [0], "updated_at": ["2025-01-01"], "total": [0.0]}),
            [("id", "integer", "dimension"), ("updated_at", "string", "time"), ("total", "float", "measure")],
        )
        db.commit()
        target_ds_id = target_ds.id

    bad_pipe = client.post(
        "/api/v1/etl/pipelines",
        headers=admin_headers,
        json={
            "name": "Bad ETL",
            "data_source_id": src["id"],
            "target_dataset_id": target_ds_id,
            "source_query": "SELECT * FROM orders; DROP TABLE orders",
            "extract_mode": "full",
        },
    )
    assert bad_pipe.status_code == 400

    good_pipe = client.post(
        "/api/v1/etl/pipelines",
        headers=admin_headers,
        json={
            "name": f"Safe ETL {uuid.uuid4().hex[:5]}",
            "data_source_id": src["id"],
            "target_dataset_id": target_ds_id,
            "source_query": "SELECT id, updated_at, total FROM orders",
            "extract_mode": "incremental",
            "incremental_column": "updated_at",
            "primary_key_column": "id",
        },
    )
    assert good_pipe.status_code == 200, good_pipe.text
    pipe_id = good_pipe.json()["id"]

    with SessionLocal() as db:
        pipe = db.query(EtlPipeline).filter(EtlPipeline.id == pipe_id).first()
        job = EtlJob(pipeline_id=pipe_id, trigger_type="manual", status="running")
        db.add(job)
        db.flush()
        stale_run = EtlJobRun(
            job_id=job.id,
            pipeline_id=pipe_id,
            status="running",
            started_at=datetime.now(timezone.utc) - timedelta(minutes=settings.ETL_STALE_RUN_TIMEOUT_MINUTES + 10),
        )
        db.add(stale_run)
        pipe.is_running = True
        db.commit()

        assert recover_stale_etl_runs(db, pipe.id) >= 1
        db.refresh(stale_run)
        db.refresh(pipe)
        assert stale_run.status == "failed"
        assert pipe.is_running is False

        run_res = run_etl_pipeline(db, pipe.id, trigger_type="manual")
        assert run_res["status"] == "succeeded"
        assert run_res["rows_extracted"] == 2
        assert run_res["rows_inserted"] == 2

        db.refresh(pipe)
        active_job = EtlJob(pipeline_id=pipe_id, trigger_type="manual", status="running")
        db.add(active_job)
        db.flush()
        active_run = EtlJobRun(
            job_id=active_job.id,
            pipeline_id=pipe_id,
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        db.add(active_run)
        pipe.is_running = True
        pipe.updated_at = datetime.now(timezone.utc)
        db.commit()

        dup_res = run_etl_pipeline(db, pipe.id, trigger_type="manual")
        assert dup_res["status"] == "skipped_duplicate"

        pipe.is_running = False
        active_run.status = "succeeded"
        active_job.status = "succeeded"
        db.commit()


def test_stage3_scheduler_leader_election_and_disabled_gate():
    orig_sched = settings.BI_ENABLE_SCHEDULER
    try:
        settings.BI_ENABLE_SCHEDULER = False
        stop_scheduler()
        start_scheduler()
        assert _acquire_or_renew_leader_lease() is True
        _release_leader_lease()
    finally:
        settings.BI_ENABLE_SCHEDULER = orig_sched


def test_stage3_alembic_migrations_and_prod_no_create_all():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        db_path = tmp_dir / "alembic_stage3.db"
        env = {
            **os.environ,
            "DATABASE_URL": f"sqlite:///{db_path}",
            "WAREHOUSE_PATH": str(tmp_dir / "alembic_wh.duckdb"),
            "ENVIRONMENT": "development",
        }
        alembic_bin = str(Path(sys.executable).parent / "alembic")
        if not Path(alembic_bin).exists():
            alembic_bin = shutil.which("alembic") or "alembic"

        up = subprocess.run([alembic_bin, "upgrade", "head"], cwd=str(BACKEND_DIR), env=env, capture_output=True, text=True)
        assert up.returncode == 0, up.stderr

        chk = subprocess.run([alembic_bin, "check"], cwd=str(BACKEND_DIR), env=env, capture_output=True, text=True)
        assert chk.returncode == 0, f"Alembic drift detected:\n{chk.stdout}\n{chk.stderr}"

        down = subprocess.run(
            [alembic_bin, "downgrade", "0001_initial_baseline"],
            cwd=str(BACKEND_DIR),
            env=env,
            capture_output=True,
            text=True,
        )
        assert down.returncode == 0, down.stderr

        up2 = subprocess.run([alembic_bin, "upgrade", "head"], cwd=str(BACKEND_DIR), env=env, capture_output=True, text=True)
        assert up2.returncode == 0, up2.stderr

        empty_db = tmp_dir / "empty_prod.db"
        prod_env = {
            **os.environ,
            "DATABASE_URL": f"sqlite:///{empty_db}",
            "WAREHOUSE_PATH": str(tmp_dir / "prod_wh.duckdb"),
            "SAMPLE_ERP_DB_PATH": str(tmp_dir / "prod_erp.sqlite"),
            "ENVIRONMENT": "production",
            "BI_AUTO_CREATE_SCHEMA": "0",
            "BI_REQUIRE_GENERATED_SECRETS": "1",
            "BI_JWT_SECRET": "a" * 64,
            "BI_CRED_KEY": "b" * 44,
        }
        boot_fail = subprocess.run(
            [
                sys.executable,
                "-c",
                "import asyncio; from app.main import app, lifespan\n"
                "async def run():\n"
                "  async with lifespan(app):\n"
                "    pass\n"
                "asyncio.run(run())\n",
            ],
            cwd=str(BACKEND_DIR),
            env=prod_env,
            capture_output=True,
            text=True,
        )
        assert boot_fail.returncode != 0
        assert "alembic upgrade head" in (boot_fail.stdout + boot_fail.stderr)


def test_stage3_admin_disable_user_revokes_active_sessions(client, admin_headers):
    suffix = uuid.uuid4().hex[:6]
    uname = f"dis_target_{suffix}"
    with SessionLocal() as db:
        org = db.query(Organization).first()
        role = db.query(Role).filter(Role.organization_id == org.id, Role.slug == "data_analyst").first()
        u = User(
            organization_id=org.id,
            username=uname,
            email=f"{uname}@corp.local",
            password_hash=hash_password("TargetPass!2026"),
            auth_source="local",
            is_active=True,
        )
        db.add(u)
        db.flush()
        db.add(UserRole(user_id=u.id, role_id=role.id))
        db.commit()
        uid = u.id

    login = client.post(
        "/api/v1/auth/login",
        json={"username": uname, "password": "TargetPass!2026"},
        headers={"X-Auth-Mode": "bearer"},
    )
    assert login.status_code == 200
    rtok = login.json()["refresh_token"]

    upd = client.put(f"/api/v1/rbac/users/{uid}", headers=admin_headers, json={"is_active": False})
    assert upd.status_code == 200

    ref = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": rtok},
        headers={"X-Auth-Mode": "bearer"},
    )
    assert ref.status_code == 401
