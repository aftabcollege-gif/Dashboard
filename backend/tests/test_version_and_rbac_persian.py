import json
from pathlib import Path
from app.version import APP_VERSION


def test_version_consistency_across_repo(client):
    res = client.get("/api/v1/about")
    assert res.status_code == 200
    data = res.json()
    assert data["version"] == APP_VERSION == "1.2.0"
    assert data["previous_version"] == "1.1.0"
    assert len(data["release_notes"]) >= 3

    h = client.get("/api/v1/health")
    assert h.status_code == 200
    assert h.json()["api_version"] == APP_VERSION

    repo_root = Path(__file__).resolve().parents[2]
    pkg = json.loads((repo_root / "frontend" / "package.json").read_text(encoding="utf-8"))
    assert pkg["version"] == APP_VERSION

    iss_text = (repo_root / "winsetup" / "installer" / "NexusBI.iss").read_text(encoding="utf-8")
    assert f'#define MyAppVersion "{APP_VERSION}"' in iss_text


def test_last_active_super_admin_guard_and_audit_reason(client, admin_headers):
    # Locate initial admin and super_admin role
    users = client.get("/api/v1/rbac/users", headers=admin_headers).json()
    admin_user = next(u for u in users if u["username"] == "admin")
    roles_data = client.get("/api/v1/rbac/roles", headers=admin_headers).json()
    super_role = next(r for r in roles_data["roles"] if r["slug"] == "super_admin")
    org_admin_role = next(r for r in roles_data["roles"] if r["slug"] == "org_admin")

    # If earlier session tests left other active super_admins, temporarily demote them during this test and restore in finally
    extra_supers = [
        u
        for u in users
        if u["id"] != admin_user["id"]
        and u.get("is_active")
        and not u.get("is_locked")
        and super_role["id"] in (u.get("role_ids") or [])
    ]
    for eu in extra_supers:
        client.put(
            f"/api/v1/rbac/users/{eu['id']}",
            headers=admin_headers,
            json={"role_ids": [org_admin_role["id"]], "reason": "Isolate sole super_admin for guard test"},
        )

    try:
        # Cannot strip super_admin role from the only active super_admin
        strip_res = client.put(
            f"/api/v1/rbac/users/{admin_user['id']}",
            headers=admin_headers,
            json={"role_ids": [org_admin_role["id"]], "reason": "Test strip last super admin"},
        )
        assert strip_res.status_code == 400

        # Cannot lock the only active super_admin
        lock_res = client.put(
            f"/api/v1/rbac/users/{admin_user['id']}",
            headers=admin_headers,
            json={"is_locked": True, "reason": "Test lock last super admin"},
        )
        assert lock_res.status_code == 400

        # Create a second super_admin
        u2_res = client.post(
            "/api/v1/rbac/users",
            headers=admin_headers,
            json={
                "username": "second_super",
                "email": "second_super@enterprise.local",
                "password": "StrongSuper!2026",
                "full_name": "مدیر ارشد دوم",
                "role_ids": [super_role["id"]],
                "reason": "ایجاد مدیر ارشد پشتیبان",
            },
        )
        assert u2_res.status_code == 200
        u2_id = u2_res.json()["id"]

        # Now disabling second_super works because admin is still active
        disable_u2 = client.put(
            f"/api/v1/rbac/users/{u2_id}",
            headers=admin_headers,
            json={"is_active": False, "reason": "تست غیرفعال‌سازی با وجود مدیر جایگزین"},
        )
        assert disable_u2.status_code == 200

        # Still cannot lock/strip the sole remaining active super_admin
        lock_again = client.put(
            f"/api/v1/rbac/users/{admin_user['id']}",
            headers=admin_headers,
            json={"is_locked": True},
        )
        assert lock_again.status_code == 400

        # Effective permissions check works cleanly
        eff = client.get(f"/api/v1/rbac/permissions/effective/{u2_id}", headers=admin_headers)
        assert eff.status_code == 200
        assert eff.json()["is_super_admin"] is True

        # Audit log recorded reason + before/after
        logs = client.get("/api/v1/monitoring/audit-logs?limit=20", headers=admin_headers).json()
        matched = [l for l in logs if l.get("resource_id") == u2_id and l.get("action") == "user_updated"]
        assert matched
        assert matched[0]["details"]["reason"] == "تست غیرفعال‌سازی با وجود مدیر جایگزین"
        assert matched[0]["before"]["is_active"] is True
        assert matched[0]["after"]["is_active"] is False

        # Role lifecycle: create custom role -> update with Persian description -> delete
        r_create = client.post(
            "/api/v1/rbac/roles",
            headers=admin_headers,
            json={
                "name": "ناظر کیفیت فرآیند",
                "slug": "process_auditor_fa",
                "description": "بازبینی گزارش‌ها و گلوگاه‌های فرآیندی",
                "permission_ids": [],
                "reason": "تعریف نقش سازمانی جدید",
            },
        )
        assert r_create.status_code == 200
        rid = r_create.json()["id"]

        r_del = client.delete(f"/api/v1/rbac/roles/{rid}", headers=admin_headers)
        assert r_del.status_code == 200

        # Built-in role cannot be deleted
        r_del_sys = client.delete(f"/api/v1/rbac/roles/{org_admin_role['id']}", headers=admin_headers)
        assert r_del_sys.status_code == 400
    finally:
        for eu in extra_supers:
            client.put(
                f"/api/v1/rbac/users/{eu['id']}",
                headers=admin_headers,
                json={"role_ids": list(eu.get("role_ids") or []), "reason": "Restore test super_admin"},
            )
