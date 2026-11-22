from datetime import datetime, timezone
from typing import Any, Optional
import ldap3
from sqlalchemy.orm import Session
from app.core.permissions import permission_resolver
from app.core.security import decrypt_secret
from app.models.identity import (
    DirectoryConnection,
    DirectorySyncJob,
    ExternalIdentity,
    Group,
    GroupMember,
    GroupRole,
    Role,
    User,
    UserProfile,
    UserRole,
)

# Built-in testable Corporate Active Directory state for 'mock_ad' mode
# Can be mutated dynamically by admin/tests to simulate user additions, group updates, and AD account disabling!
_MOCK_AD_DIRECTORIES: dict[str, dict[str, Any]] = {
    "corp.enterprise.local": {
        "bind_dn": "CN=bi-svc,OU=ServiceAccounts,DC=corp,DC=enterprise,DC=local",
        "bind_password": "AdServiceBind!2026",
        "groups": [
            {
                "cn": "AD-Executive-Board",
                "dn": "CN=AD-Executive-Board,OU=Groups,DC=corp,DC=enterprise,DC=local",
                "description": "Enterprise Executive Leadership",
                "mapped_role_slug": "executive_viewer",
            },
            {
                "cn": "AD-Sales-Analysts",
                "dn": "CN=AD-Sales-Analysts,OU=Groups,DC=corp,DC=enterprise,DC=local",
                "description": "Sales Analytics & BI Team",
                "mapped_role_slug": "data_analyst",
            },
            {
                "cn": "AD-Finance-Controllers",
                "dn": "CN=AD-Finance-Controllers,OU=Groups,DC=corp,DC=enterprise,DC=local",
                "description": "Finance Department Controllers",
                "mapped_role_slug": "dashboard_designer",
            },
        ],
        "users": [
            {
                "objectGUID": "ad-guid-1001",
                "sAMAccountName": "sara.ahmadi",
                "mail": "sara.ahmadi@corp.enterprise.local",
                "displayName": "Sara Ahmadi",
                "department": "Sales",
                "ou": "Tehran-HQ",
                "region": "Central",
                "title": "Senior Sales Analyst",
                "dn": "CN=Sara Ahmadi,OU=Sales,DC=corp,DC=enterprise,DC=local",
                "enabled": True,
                "password": "DomainUser!123",
                "memberOf": ["AD-Sales-Analysts"],
            },
            {
                "objectGUID": "ad-guid-1002",
                "sAMAccountName": "ali.rezaei",
                "mail": "ali.rezaei@corp.enterprise.local",
                "displayName": "Ali Rezaei",
                "department": "Finance",
                "ou": "Finance-HQ",
                "region": "North",
                "title": "Finance BI Lead",
                "dn": "CN=Ali Rezaei,OU=Finance,DC=corp,DC=enterprise,DC=local",
                "enabled": True,
                "password": "DomainUser!123",
                "memberOf": ["AD-Finance-Controllers"],
            },
            {
                "objectGUID": "ad-guid-1003",
                "sAMAccountName": "maryam.karimi",
                "mail": "maryam.karimi@corp.enterprise.local",
                "displayName": "Maryam Karimi",
                "department": "Executive",
                "ou": "Board",
                "region": "All",
                "title": "VP Operations",
                "dn": "CN=Maryam Karimi,OU=Exec,DC=corp,DC=enterprise,DC=local",
                "enabled": True,
                "password": "DomainUser!123",
                "memberOf": ["AD-Executive-Board"],
            },
        ],
    }
}


def get_mock_directory(domain: str) -> dict[str, Any]:
    if domain not in _MOCK_AD_DIRECTORIES:
        _MOCK_AD_DIRECTORIES[domain] = {
            "bind_dn": f"CN=bind,DC={domain.replace('.', ',DC=')}",
            "bind_password": "AdServiceBind!2026",
            "groups": [],
            "users": [],
        }
    return _MOCK_AD_DIRECTORIES[domain]


class ActiveDirectoryEngine:
    """
    Enterprise Active Directory & LDAP/LDAPS Engine (Section 19B).
    - Never stores AD passwords in the application database!
    - Supports 'ldap'/'ldaps' via real ldap3 protocol AND 'mock_ad' for deterministic enterprise AD testing.
    - Supports user/group discovery, import, sync, selected group filtering, and disabled-user propagation.
    """

    @staticmethod
    def test_connection(conn_row: DirectoryConnection) -> dict[str, Any]:
        bind_pw = decrypt_secret(conn_row.bind_password_encrypted)
        if conn_row.directory_mode == "mock_ad":
            ad_dir = get_mock_directory(conn_row.domain_name)
            if conn_row.bind_dn != ad_dir["bind_dn"] or bind_pw != ad_dir["bind_password"]:
                return {"ok": False, "error": "Invalid AD Bind DN or credentials"}
            return {
                "ok": True,
                "mode": "mock_ad",
                "domain": conn_row.domain_name,
                "users_visible": len(ad_dir["users"]),
                "groups_visible": len(ad_dir["groups"]),
            }

        try:
            server = ldap3.Server(conn_row.server_url, use_ssl=conn_row.use_ldaps, connect_timeout=5)
            ldap_conn = ldap3.Connection(server, user=conn_row.bind_dn, password=bind_pw, auto_bind=True)
            ldap_conn.unbind()
            return {"ok": True, "mode": "ldap3", "domain": conn_row.domain_name}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @staticmethod
    def search_directory(
        conn_row: DirectoryConnection, query_text: str = ""
    ) -> dict[str, Any]:
        if conn_row.directory_mode == "mock_ad":
            ad_dir = get_mock_directory(conn_row.domain_name)
            q = query_text.lower().strip()
            users = [
                {k: v for k, v in u.items() if k != "password"}
                for u in ad_dir["users"]
                if not q
                or q in u["sAMAccountName"].lower()
                or q in u["displayName"].lower()
                or q in u["department"].lower()
            ]
            groups = [
                g
                for g in ad_dir["groups"]
                if not q or q in g["cn"].lower() or q in g["description"].lower()
            ]
            return {"users": users, "groups": groups}

        bind_pw = decrypt_secret(conn_row.bind_password_encrypted)
        server = ldap3.Server(conn_row.server_url, use_ssl=conn_row.use_ldaps, connect_timeout=5)
        ldap_conn = ldap3.Connection(server, user=conn_row.bind_dn, password=bind_pw, auto_bind=True)
        try:
            u_filter = f"(&(objectClass=user)(|(sAMAccountName=*{query_text}*)(displayName=*{query_text}*)))"
            ldap_conn.search(
                conn_row.base_dn,
                u_filter,
                attributes=["objectGUID", "sAMAccountName", "mail", "displayName", "department", "memberOf", "userAccountControl"],
            )
            users = []
            for e in ldap_conn.entries:
                uac = int(str(e.userAccountControl)) if hasattr(e, "userAccountControl") and e.userAccountControl else 512
                users.append(
                    {
                        "objectGUID": str(e.objectGUID),
                        "sAMAccountName": str(e.sAMAccountName),
                        "mail": str(e.mail) if hasattr(e, "mail") else f"{e.sAMAccountName}@{conn_row.domain_name}",
                        "displayName": str(e.displayName),
                        "department": str(e.department) if hasattr(e, "department") else "",
                        "dn": e.entry_dn,
                        "enabled": not bool(uac & 2),
                        "memberOf": [str(m) for m in e.memberOf] if hasattr(e, "memberOf") else [],
                    }
                )
            ldap_conn.search(conn_row.base_dn, "(objectClass=group)", attributes=["cn", "description"])
            groups = [
                {"cn": str(g.cn), "dn": g.entry_dn, "description": str(g.description) if hasattr(g, "description") else ""}
                for g in ldap_conn.entries
            ]
            return {"users": users, "groups": groups}
        finally:
            ldap_conn.unbind()

    @staticmethod
    def authenticate_domain_user(
        conn_row: DirectoryConnection, username: str, password: str
    ) -> Optional[dict[str, Any]]:
        """Verifies domain credentials directly against AD without ever saving password in App DB."""
        clean_user = username.split("\\")[-1].split("@")[0].lower()
        if conn_row.directory_mode == "mock_ad":
            ad_dir = get_mock_directory(conn_row.domain_name)
            for u in ad_dir["users"]:
                if u["sAMAccountName"].lower() == clean_user:
                    if not u.get("enabled", True):
                        return None
                    if u.get("password") == password:
                        return {k: v for k, v in u.items() if k != "password"}
                    return None
            return None

        # Real LDAP bind authentication
        bind_pw = decrypt_secret(conn_row.bind_password_encrypted)
        server = ldap3.Server(conn_row.server_url, use_ssl=conn_row.use_ldaps, connect_timeout=5)
        try:
            admin_conn = ldap3.Connection(server, user=conn_row.bind_dn, password=bind_pw, auto_bind=True)
            admin_conn.search(
                conn_row.base_dn,
                f"(&(objectClass=user)(sAMAccountName={clean_user}))",
                attributes=["objectGUID", "sAMAccountName", "mail", "displayName", "department", "memberOf", "userAccountControl"],
            )
            if not admin_conn.entries:
                admin_conn.unbind()
                return None
            entry = admin_conn.entries[0]
            user_dn = entry.entry_dn
            uac = int(str(entry.userAccountControl)) if hasattr(entry, "userAccountControl") else 512
            admin_conn.unbind()
            if uac & 2:
                return None

            user_conn = ldap3.Connection(server, user=user_dn, password=password, auto_bind=True)
            user_conn.unbind()
            return {
                "objectGUID": str(entry.objectGUID),
                "sAMAccountName": str(entry.sAMAccountName),
                "mail": str(entry.mail),
                "displayName": str(entry.displayName),
                "department": str(entry.department) if hasattr(entry, "department") else "",
                "dn": user_dn,
                "enabled": True,
                "memberOf": [str(m) for m in entry.memberOf] if hasattr(entry, "memberOf") else [],
            }
        except Exception:
            return None

    @staticmethod
    def sync_directory(
        db: Session,
        conn_row: DirectoryConnection,
        sync_type: str = "full",
    ) -> dict[str, Any]:
        job = DirectorySyncJob(
            directory_connection_id=conn_row.id,
            sync_type=sync_type,
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        db.add(job)
        db.commit()

        try:
            dir_data = ActiveDirectoryEngine.search_directory(conn_row)
            selected_groups = set(conn_row.selected_groups_json or [])

            groups_synced = 0
            group_map: dict[str, Group] = {}
            for g in dir_data["groups"]:
                if selected_groups and g["cn"] not in selected_groups:
                    continue
                grp = (
                    db.query(Group)
                    .filter(Group.organization_id == conn_row.organization_id, Group.name == g["cn"])
                    .first()
                )
                if not grp:
                    grp = Group(
                        organization_id=conn_row.organization_id,
                        name=g["cn"],
                        description=g.get("description", ""),
                        source="active_directory",
                        external_dn=g.get("dn"),
                        directory_connection_id=conn_row.id,
                    )
                    db.add(grp)
                    db.flush()
                else:
                    grp.external_dn = g.get("dn")
                    grp.source = "active_directory"
                group_map[g["cn"]] = grp
                groups_synced += 1

                # Auto-map AD group to BI role if specified
                mapped_slug = g.get("mapped_role_slug")
                if mapped_slug:
                    role = (
                        db.query(Role)
                        .filter(Role.organization_id == conn_row.organization_id, Role.slug == mapped_slug)
                        .first()
                    )
                    if role:
                        existing_gr = (
                            db.query(GroupRole)
                            .filter(GroupRole.group_id == grp.id, GroupRole.role_id == role.id)
                            .first()
                        )
                        if not existing_gr:
                            db.add(GroupRole(group_id=grp.id, role_id=role.id))

            imported = 0
            updated = 0
            deactivated = 0
            seen_ext_ids: set[str] = set()

            for ad_u in dir_data["users"]:
                u_groups = set(ad_u.get("memberOf", []))
                if selected_groups and not (u_groups & selected_groups):
                    continue

                ext_id = ad_u["objectGUID"]
                seen_ext_ids.add(ext_id)
                ext = (
                    db.query(ExternalIdentity)
                    .filter(
                        ExternalIdentity.provider_type == "active_directory",
                        ExternalIdentity.external_id == ext_id,
                    )
                    .first()
                )

                is_enabled = bool(ad_u.get("enabled", True))
                if not ext:
                    user = (
                        db.query(User).filter(User.username == ad_u["sAMAccountName"]).first()
                    )
                    if not user:
                        user = User(
                            organization_id=conn_row.organization_id,
                            username=ad_u["sAMAccountName"],
                            email=ad_u.get("mail") or f"{ad_u['sAMAccountName']}@{conn_row.domain_name}",
                            password_hash=None,  # AD passwords are NEVER stored in App DB!
                            auth_source="active_directory",
                            is_active=is_enabled,
                        )
                        db.add(user)
                        db.flush()
                        db.add(
                            UserProfile(
                                user_id=user.id,
                                full_name=ad_u.get("displayName", ""),
                                department=ad_u.get("department"),
                                org_unit=ad_u.get("ou"),
                                region=ad_u.get("region"),
                                job_title=ad_u.get("title"),
                            )
                        )
                        imported += 1
                    ext = ExternalIdentity(
                        user_id=user.id,
                        directory_connection_id=conn_row.id,
                        provider_type="active_directory",
                        external_id=ext_id,
                        distinguished_name=ad_u.get("dn"),
                        domain_name=conn_row.domain_name,
                        raw_attributes_json=ad_u,
                    )
                    db.add(ext)
                else:
                    user = db.query(User).filter(User.id == ext.user_id).first()
                    if user:
                        was_active = user.is_active
                        user.is_active = is_enabled
                        user.email = ad_u.get("mail") or user.email
                        if was_active and not is_enabled:
                            deactivated += 1
                        else:
                            updated += 1
                        if user.profile:
                            user.profile.full_name = ad_u.get("displayName", user.profile.full_name)
                            user.profile.department = ad_u.get("department", user.profile.department)
                            user.profile.region = ad_u.get("region", user.profile.region)
                    ext.raw_attributes_json = ad_u
                    ext.synced_at = datetime.now(timezone.utc)

                # Sync Group Memberships
                if user:
                    current_ad_gids = {
                        gm.group_id
                        for gm in db.query(GroupMember)
                        .join(Group, Group.id == GroupMember.group_id)
                        .filter(GroupMember.user_id == user.id, Group.source == "active_directory")
                        .all()
                    }
                    target_gids = {group_map[cn].id for cn in u_groups if cn in group_map}
                    for gid_add in target_gids - current_ad_gids:
                        db.add(GroupMember(group_id=gid_add, user_id=user.id))
                    for gid_rem in current_ad_gids - target_gids:
                        db.query(GroupMember).filter(
                            GroupMember.group_id == gid_rem, GroupMember.user_id == user.id
                        ).delete()

                    # Ensure at least viewer role if user has no roles
                    has_role = db.query(UserRole).filter(UserRole.user_id == user.id).first()
                    has_grp_role = (
                        db.query(GroupRole).filter(GroupRole.group_id.in_(list(target_gids))).first()
                        if target_gids
                        else None
                    )
                    if not has_role and not has_grp_role:
                        viewer = (
                            db.query(Role)
                            .filter(Role.organization_id == conn_row.organization_id, Role.slug == "report_viewer")
                            .first()
                        )
                        if viewer:
                            db.add(UserRole(user_id=user.id, role_id=viewer.id))

            conn_row.last_sync_at = datetime.now(timezone.utc)
            conn_row.last_sync_status = "succeeded"

            job.status = "succeeded"
            job.users_imported = imported
            job.users_updated = updated
            job.users_deactivated = deactivated
            job.groups_synced = groups_synced
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
            permission_resolver.invalidate_all()

            return {
                "status": "succeeded",
                "job_id": job.id,
                "users_imported": imported,
                "users_updated": updated,
                "users_deactivated": deactivated,
                "groups_synced": groups_synced,
            }
        except Exception as exc:
            job.status = "failed"
            job.error_message = str(exc)
            job.finished_at = datetime.now(timezone.utc)
            conn_row.last_sync_status = "failed"
            db.commit()
            raise
