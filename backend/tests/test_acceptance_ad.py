from app.database import SessionLocal
from app.models.identity import User


def test_a_active_directory_full_lifecycle(client, admin_headers):
    # 1. Fetch configured AD connection & test connectivity
    res = client.get("/api/v1/rbac/ad/connections", headers=admin_headers)
    assert res.status_code == 200
    conns = res.json()["connections"]
    assert len(conns) >= 1
    ad_id = conns[0]["id"]

    test_res = client.post(f"/api/v1/rbac/ad/connections/{ad_id}/test", headers=admin_headers)
    assert test_res.status_code == 200
    assert test_res.json()["ok"] is True

    # 2. Search AD users and groups
    search_res = client.get(f"/api/v1/rbac/ad/connections/{ad_id}/search?q=sara", headers=admin_headers)
    assert search_res.status_code == 200
    assert any(u["sAMAccountName"] == "sara.ahmadi" for u in search_res.json()["users"])

    # 3. Sync AD users & groups into local BI platform
    sync_res = client.post(f"/api/v1/rbac/ad/connections/{ad_id}/sync", headers=admin_headers)
    assert sync_res.status_code == 200
    assert sync_res.json()["users_imported"] >= 3
    assert sync_res.json()["groups_synced"] >= 3

    # Verify AD password is NEVER saved in application DB
    with SessionLocal() as db:
        sara = db.query(User).filter(User.username == "sara.ahmadi").first()
        assert sara is not None
        assert sara.auth_source == "active_directory"
        assert sara.password_hash is None

    # 4. Login with AD Domain credentials
    domain_login = client.post(
        "/api/v1/auth/login",
        json={
            "username": "sara.ahmadi",
            "password": "DomainUser!123",
            "auth_mode": "active_directory",
            "domain": "corp.enterprise.local",
        },
    )
    assert domain_login.status_code == 200
    sara_token = domain_login.json()["access_token"]
    sara_headers = {"Authorization": f"Bearer {sara_token}"}
    assert client.get("/api/v1/auth/me", headers=sara_headers).status_code == 200

    # 5. Mutate user profile in AD & re-sync
    mut = client.post(
        "/api/v1/rbac/ad/mock-directory/mutate",
        json={"username": "sara.ahmadi", "department": "Enterprise Sales"},
        headers=admin_headers,
    )
    assert mut.status_code == 200
    client.post(f"/api/v1/rbac/ad/connections/{ad_id}/sync", headers=admin_headers)
    me_after = client.get("/api/v1/auth/me", headers=sara_headers).json()
    assert me_after["profile"]["department"] == "Enterprise Sales"

    # 6. Disable user in Active Directory -> Sync -> User deactivated & blocked
    dis = client.post(
        "/api/v1/rbac/ad/mock-directory/mutate",
        json={"username": "sara.ahmadi", "enabled": False},
        headers=admin_headers,
    )
    assert dis.status_code == 200
    sync2 = client.post(f"/api/v1/rbac/ad/connections/{ad_id}/sync", headers=admin_headers)
    assert sync2.json()["users_deactivated"] >= 1

    # Deactivated user cannot login or use previous token
    assert client.get("/api/v1/auth/me", headers=sara_headers).status_code == 403
    relogin = client.post(
        "/api/v1/auth/login",
        json={
            "username": "sara.ahmadi",
            "password": "DomainUser!123",
            "auth_mode": "active_directory",
            "domain": "corp.enterprise.local",
        },
    )
    assert relogin.status_code in (401, 403)
