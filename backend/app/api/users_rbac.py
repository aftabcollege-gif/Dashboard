from datetime import datetime
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.api.deps import enforce_permission, get_client_ip, get_current_user
from app.core.audit import record_audit
from app.core.cache import cache_manager
from app.core.permissions import permission_resolver
from app.core.security import encrypt_secret, hash_password
from app.database import get_db
from app.engines.ad_ldap import ActiveDirectoryEngine, get_mock_directory
from app.models.dashboard import Dashboard, Widget
from app.models.identity import (
    AccessPolicy,
    DirectoryConnection,
    DirectorySyncJob,
    Group,
    GroupMember,
    GroupRole,
    Permission,
    ResourcePermission,
    Role,
    RolePermission,
    User,
    UserProfile,
    UserRole,
)

router = APIRouter(prefix="/rbac", tags=["Users, RBAC, AD & Security Policies"])


class UserCreate(BaseModel):
    username: str
    email: str
    password: str
    full_name: str = ""
    department: Optional[str] = None
    org_unit: Optional[str] = None
    region: Optional[str] = None
    job_title: Optional[str] = None
    role_ids: list[str] = []
    group_ids: list[str] = []
    expires_at: Optional[datetime] = None


class UserUpdate(BaseModel):
    email: Optional[str] = None
    full_name: Optional[str] = None
    department: Optional[str] = None
    org_unit: Optional[str] = None
    region: Optional[str] = None
    job_title: Optional[str] = None
    is_active: Optional[bool] = None
    is_locked: Optional[bool] = None
    expires_at: Optional[datetime] = None
    reset_password: Optional[str] = None
    role_ids: Optional[list[str]] = None
    group_ids: Optional[list[str]] = None


class RoleCreate(BaseModel):
    name: str
    slug: str
    description: str = ""
    permission_ids: list[str] = []


class GroupCreate(BaseModel):
    name: str
    description: str = ""
    role_ids: list[str] = []
    member_user_ids: list[str] = []


class MatrixPermissionSet(BaseModel):
    subject_type: str  # user | group | role
    subject_id: str
    resource_type: str  # dashboard | widget | drill_down | dataset | ...
    resource_id: str
    action: str
    effect: str = "allow"  # allow | deny | remove
    inherit_to_children: bool = False
    cascade_types: list[str] = []


class MatrixBulkCopy(BaseModel):
    from_subject_type: str
    from_subject_id: str
    to_subject_type: str
    to_subject_id: str


class AccessPolicyCreate(BaseModel):
    name: str
    policy_type: str  # rls | cls | masking
    dataset_id: Optional[str] = None
    target_type: str = "all"  # user | group | role | all
    target_id: Optional[str] = None
    rule_json: dict[str, Any]
    priority: int = 100
    is_active: bool = True


class DirectoryCreate(BaseModel):
    name: str
    domain_name: str
    server_url: str
    use_ldaps: bool = False
    base_dn: str
    bind_dn: str
    bind_password: str
    directory_mode: str = "mock_ad"  # ldap | mock_ad
    sync_policy: str = "manual"
    selected_groups: list[str] = []


class MockAdMutateUser(BaseModel):
    domain_name: str = "corp.enterprise.local"
    username: str
    enabled: Optional[bool] = None
    display_name: Optional[str] = None
    department: Optional[str] = None
    region: Optional[str] = None
    password: Optional[str] = None
    groups: Optional[list[str]] = None
    create_if_missing: bool = False


# ---------------- USERS ----------------
@router.get("/users")
def list_users(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "admin", "*", "view")
    users = db.query(User).filter(User.organization_id == user.organization_id).all()
    out = []
    for u in users:
        r_ids = [ur.role_id for ur in db.query(UserRole).filter(UserRole.user_id == u.id).all()]
        g_ids = [gm.group_id for gm in db.query(GroupMember).filter(GroupMember.user_id == u.id).all()]
        out.append({
            "id": u.id,
            "username": u.username,
            "email": u.email,
            "auth_source": u.auth_source,
            "is_active": u.is_active,
            "is_locked": u.is_locked,
            "mfa_enabled": u.mfa_enabled,
            "expires_at": u.expires_at,
            "last_login_at": u.last_login_at,
            "full_name": u.profile.full_name if u.profile else "",
            "department": u.profile.department if u.profile else "",
            "region": u.profile.region if u.profile else "",
            "role_ids": r_ids,
            "group_ids": g_ids,
        })
    return out


@router.post("/users")
def create_user(
    body: UserCreate, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    enforce_permission(db, user, "admin", "*", "add", ip_address=get_client_ip(request))
    if db.query(User).filter((User.username == body.username) | (User.email == body.email)).first():
        raise HTTPException(status_code=400, detail="Username or email already exists")

    u = User(
        organization_id=user.organization_id,
        username=body.username,
        email=body.email,
        password_hash=hash_password(body.password),
        auth_source="local",
        expires_at=body.expires_at,
    )
    db.add(u)
    db.flush()
    db.add(
        UserProfile(
            user_id=u.id,
            full_name=body.full_name or body.username,
            department=body.department,
            org_unit=body.org_unit or body.department,
            region=body.region,
            job_title=body.job_title,
        )
    )
    for rid in body.role_ids:
        db.add(UserRole(user_id=u.id, role_id=rid))
    for gid in body.group_ids:
        db.add(GroupMember(group_id=gid, user_id=u.id))

    record_audit(
        db, user.organization_id, "user_created", "user", resource_id=u.id,
        user_id=user.id, username=user.username, ip_address=get_client_ip(request),
        after_state={"username": u.username, "roles": body.role_ids}
    )
    db.commit()
    permission_resolver.invalidate_all()
    return {"id": u.id, "username": u.username}


@router.put("/users/{target_user_id}")
def update_user(
    target_user_id: str,
    body: UserUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(db, user, "admin", "*", "edit", ip_address=get_client_ip(request))
    target = db.query(User).filter(User.id == target_user_id, User.organization_id == user.organization_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    before = {"is_active": target.is_active, "is_locked": target.is_locked, "email": target.email}
    if body.email is not None:
        target.email = body.email
    if body.is_active is not None:
        target.is_active = body.is_active
    if body.is_locked is not None:
        target.is_locked = body.is_locked
        if not body.is_locked:
            target.failed_login_attempts = 0
            target.locked_until = None
    if body.expires_at is not None:
        target.expires_at = body.expires_at
    if body.reset_password and target.auth_source == "local":
        target.password_hash = hash_password(body.reset_password)
    if target.profile:
        if body.full_name is not None:
            target.profile.full_name = body.full_name
        if body.department is not None:
            target.profile.department = body.department
        if body.org_unit is not None:
            target.profile.org_unit = body.org_unit
        if body.region is not None:
            target.profile.region = body.region
        if body.job_title is not None:
            target.profile.job_title = body.job_title

    if body.role_ids is not None:
        db.query(UserRole).filter(UserRole.user_id == target.id).delete()
        for rid in body.role_ids:
            db.add(UserRole(user_id=target.id, role_id=rid))
    if body.group_ids is not None:
        db.query(GroupMember).filter(GroupMember.user_id == target.id).delete()
        for gid in body.group_ids:
            db.add(GroupMember(group_id=gid, user_id=target.id))

    target.permission_version += 1
    record_audit(
        db, user.organization_id, "user_updated", "user", resource_id=target.id,
        user_id=user.id, username=user.username, ip_address=get_client_ip(request),
        before_state=before, after_state={"is_active": target.is_active, "is_locked": target.is_locked}
    )
    db.commit()
    permission_resolver.invalidate_all()
    cache_manager.invalidate_all()
    return {"status": "updated"}


@router.delete("/users/{target_user_id}")
def delete_user(
    target_user_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    enforce_permission(db, user, "admin", "*", "delete", ip_address=get_client_ip(request))
    if target_user_id == user.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    target = db.query(User).filter(User.id == target_user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    db.delete(target)
    record_audit(
        db, user.organization_id, "user_deleted", "user", resource_id=target_user_id,
        user_id=user.id, username=user.username, ip_address=get_client_ip(request)
    )
    db.commit()
    permission_resolver.invalidate_all()
    return {"status": "deleted"}


# ---------------- ROLES & GROUPS ----------------
@router.get("/roles")
def list_roles(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "admin", "*", "view")
    roles = db.query(Role).filter(Role.organization_id == user.organization_id).all()
    all_perms = db.query(Permission).all()
    out = []
    for r in roles:
        rp_ids = [rp.permission_id for rp in db.query(RolePermission).filter(RolePermission.role_id == r.id, RolePermission.effect == "allow").all()]
        out.append({
            "id": r.id, "name": r.name, "slug": r.slug, "description": r.description,
            "is_system": r.is_system, "is_super_admin": r.is_super_admin, "permission_ids": rp_ids
        })
    return {"roles": out, "catalog": [{"id": p.id, "resource_type": p.resource_type, "action": p.action, "description": p.description} for p in all_perms]}


@router.post("/roles")
def create_role(body: RoleCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "admin", "*", "add")
    r = Role(organization_id=user.organization_id, name=body.name, slug=body.slug, description=body.description)
    db.add(r)
    db.flush()
    for pid in body.permission_ids:
        db.add(RolePermission(role_id=r.id, permission_id=pid, effect="allow"))
    permission_resolver.bump_org_versions(db, user.organization_id)
    db.commit()
    return {"id": r.id, "name": r.name}


@router.put("/roles/{role_id}")
def update_role(role_id: str, body: RoleCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "admin", "*", "edit")
    r = db.query(Role).filter(Role.id == role_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Role not found")
    r.name = body.name
    r.description = body.description
    db.query(RolePermission).filter(RolePermission.role_id == r.id).delete()
    for pid in body.permission_ids:
        db.add(RolePermission(role_id=r.id, permission_id=pid, effect="allow"))
    permission_resolver.bump_org_versions(db, user.organization_id)
    db.commit()
    cache_manager.invalidate_all()
    return {"status": "updated"}


@router.post("/roles/{role_id}/clone")
def clone_role(role_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "admin", "*", "add")
    src = db.query(Role).filter(Role.id == role_id).first()
    if not src:
        raise HTTPException(status_code=404, detail="Role not found")
    nr = Role(
        organization_id=user.organization_id,
        name=f"{src.name} (Copy)",
        slug=f"{src.slug}_copy_{int(datetime.now().timestamp())}",
        description=src.description,
    )
    db.add(nr)
    db.flush()
    for rp in db.query(RolePermission).filter(RolePermission.role_id == src.id).all():
        db.add(RolePermission(role_id=nr.id, permission_id=rp.permission_id, effect=rp.effect))
    db.commit()
    return {"id": nr.id, "name": nr.name}


@router.get("/groups")
def list_groups(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "admin", "*", "view")
    groups = db.query(Group).filter(Group.organization_id == user.organization_id).all()
    out = []
    for g in groups:
        r_ids = [gr.role_id for gr in db.query(GroupRole).filter(GroupRole.group_id == g.id).all()]
        m_ids = [gm.user_id for gm in db.query(GroupMember).filter(GroupMember.group_id == g.id).all()]
        out.append({"id": g.id, "name": g.name, "description": g.description, "source": g.source, "role_ids": r_ids, "member_user_ids": m_ids})
    return out


@router.post("/groups")
def create_group(body: GroupCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "admin", "*", "add")
    g = Group(organization_id=user.organization_id, name=body.name, description=body.description, source="local")
    db.add(g)
    db.flush()
    for rid in body.role_ids:
        db.add(GroupRole(group_id=g.id, role_id=rid))
    for uid in body.member_user_ids:
        db.add(GroupMember(group_id=g.id, user_id=uid))
    permission_resolver.bump_org_versions(db, user.organization_id)
    db.commit()
    return {"id": g.id, "name": g.name}


@router.put("/groups/{group_id}")
def update_group(group_id: str, body: GroupCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "admin", "*", "edit")
    g = db.query(Group).filter(Group.id == group_id).first()
    if not g:
        raise HTTPException(status_code=404, detail="Group not found")
    g.name = body.name
    g.description = body.description
    db.query(GroupRole).filter(GroupRole.group_id == g.id).delete()
    db.query(GroupMember).filter(GroupMember.group_id == g.id).delete()
    for rid in body.role_ids:
        db.add(GroupRole(group_id=g.id, role_id=rid))
    for uid in body.member_user_ids:
        db.add(GroupMember(group_id=g.id, user_id=uid))
    permission_resolver.bump_org_versions(db, user.organization_id)
    db.commit()
    cache_manager.invalidate_all()
    return {"status": "updated"}


# ---------------- PERMISSION MATRIX (Section 22) ----------------
@router.get("/permissions/matrix")
def get_permission_matrix(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "admin", "*", "view")
    rps = (
        db.query(ResourcePermission)
        .filter(ResourcePermission.organization_id == user.organization_id)
        .all()
    )
    dashboards = db.query(Dashboard).all()
    widgets = db.query(Widget).all()
    return {
        "rules": [
            {
                "id": rp.id,
                "subject_type": rp.subject_type,
                "subject_id": rp.subject_id,
                "resource_type": rp.resource_type,
                "resource_id": rp.resource_id,
                "action": rp.action,
                "effect": rp.effect,
                "inherit_to_children": rp.inherit_to_children,
            }
            for rp in rps
        ],
        "dashboards": [{"id": d.id, "title": d.title} for d in dashboards],
        "widgets": [{"id": w.id, "dashboard_id": w.dashboard_id, "title": w.title} for w in widgets],
    }


@router.put("/permissions/matrix/set")
def set_matrix_permission(
    body: MatrixPermissionSet,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(db, user, "admin", "*", "edit", ip_address=get_client_ip(request))
    existing = (
        db.query(ResourcePermission)
        .filter(
            ResourcePermission.organization_id == user.organization_id,
            ResourcePermission.subject_type == body.subject_type,
            ResourcePermission.subject_id == body.subject_id,
            ResourcePermission.resource_type == body.resource_type,
            ResourcePermission.resource_id == body.resource_id,
            ResourcePermission.action == body.action,
        )
        .first()
    )
    before = {"effect": existing.effect} if existing else None

    if body.effect == "remove":
        if existing:
            db.delete(existing)
        after = None
    else:
        conds = {"cascade_types": body.cascade_types} if body.cascade_types else {}
        if existing:
            existing.effect = body.effect
            existing.inherit_to_children = body.inherit_to_children
            existing.conditions_json = conds
        else:
            db.add(
                ResourcePermission(
                    organization_id=user.organization_id,
                    subject_type=body.subject_type,
                    subject_id=body.subject_id,
                    resource_type=body.resource_type,
                    resource_id=body.resource_id,
                    action=body.action,
                    effect=body.effect,
                    inherit_to_children=body.inherit_to_children,
                    conditions_json=conds,
                )
            )
        after = {"effect": body.effect}

    permission_resolver.bump_org_versions(db, user.organization_id)
    cache_manager.invalidate_all()
    record_audit(
        db, user.organization_id, "permission_changed", body.resource_type,
        resource_id=body.resource_id, user_id=user.id, username=user.username,
        ip_address=get_client_ip(request), before_state=before, after_state=after,
        details={"subject": f"{body.subject_type}:{body.subject_id}", "action": body.action}
    )
    db.commit()
    return {"status": "saved"}


@router.post("/permissions/matrix/copy")
def copy_matrix_permissions(
    body: MatrixBulkCopy, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    enforce_permission(db, user, "admin", "*", "edit")
    src_rules = (
        db.query(ResourcePermission)
        .filter(
            ResourcePermission.organization_id == user.organization_id,
            ResourcePermission.subject_type == body.from_subject_type,
            ResourcePermission.subject_id == body.from_subject_id,
        )
        .all()
    )
    copied = 0
    for sr in src_rules:
        dup = (
            db.query(ResourcePermission)
            .filter(
                ResourcePermission.subject_type == body.to_subject_type,
                ResourcePermission.subject_id == body.to_subject_id,
                ResourcePermission.resource_type == sr.resource_type,
                ResourcePermission.resource_id == sr.resource_id,
                ResourcePermission.action == sr.action,
            )
            .first()
        )
        if dup:
            dup.effect = sr.effect
        else:
            db.add(
                ResourcePermission(
                    organization_id=user.organization_id,
                    subject_type=body.to_subject_type,
                    subject_id=body.to_subject_id,
                    resource_type=sr.resource_type,
                    resource_id=sr.resource_id,
                    action=sr.action,
                    effect=sr.effect,
                    inherit_to_children=sr.inherit_to_children,
                    conditions_json=sr.conditions_json,
                )
            )
        copied += 1
    permission_resolver.bump_org_versions(db, user.organization_id)
    cache_manager.invalidate_all()
    db.commit()
    return {"copied": copied}


@router.get("/permissions/effective/{target_user_id}")
def get_effective_permissions(
    target_user_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    enforce_permission(db, user, "admin", "*", "view")
    target = db.query(User).filter(User.id == target_user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    return permission_resolver.get_effective_permissions_summary(db, target)


# ---------------- RLS / CLS / MASKING POLICIES (Section 24) ----------------
@router.get("/policies")
def list_access_policies(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "admin", "*", "view")
    rows = db.query(AccessPolicy).filter(AccessPolicy.organization_id == user.organization_id).all()
    return [
        {
            "id": p.id, "name": p.name, "policy_type": p.policy_type, "dataset_id": p.dataset_id,
            "target_type": p.target_type, "target_id": p.target_id, "rule_json": p.rule_json,
            "priority": p.priority, "is_active": p.is_active
        }
        for p in rows
    ]


@router.post("/policies")
def create_access_policy(
    body: AccessPolicyCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    enforce_permission(db, user, "admin", "*", "add")
    pol = AccessPolicy(
        organization_id=user.organization_id,
        name=body.name,
        policy_type=body.policy_type,
        dataset_id=body.dataset_id,
        target_type=body.target_type,
        target_id=body.target_id,
        rule_json=body.rule_json,
        priority=body.priority,
        is_active=body.is_active,
    )
    db.add(pol)
    db.commit()
    cache_manager.invalidate_all()
    return {"id": pol.id, "name": pol.name}


@router.delete("/policies/{policy_id}")
def delete_access_policy(policy_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "admin", "*", "delete")
    pol = db.query(AccessPolicy).filter(AccessPolicy.id == policy_id).first()
    if not pol:
        raise HTTPException(status_code=404, detail="Policy not found")
    db.delete(pol)
    db.commit()
    cache_manager.invalidate_all()
    return {"status": "deleted"}


# ---------------- ACTIVE DIRECTORY / LDAP (Section 19B) ----------------
@router.get("/ad/connections")
def list_ad_connections(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "admin", "*", "view")
    rows = db.query(DirectoryConnection).filter(DirectoryConnection.organization_id == user.organization_id).all()
    jobs = db.query(DirectorySyncJob).order_by(DirectorySyncJob.started_at.desc()).limit(20).all()
    return {
        "connections": [
            {
                "id": c.id, "name": c.name, "domain_name": c.domain_name, "server_url": c.server_url,
                "use_ldaps": c.use_ldaps, "base_dn": c.base_dn, "bind_dn": c.bind_dn,
                "directory_mode": c.directory_mode, "sync_policy": c.sync_policy,
                "selected_groups": c.selected_groups_json, "is_active": c.is_active,
                "last_sync_at": c.last_sync_at, "last_sync_status": c.last_sync_status,
            }
            for c in rows
        ],
        "sync_jobs": [
            {
                "id": j.id, "directory_connection_id": j.directory_connection_id, "sync_type": j.sync_type,
                "status": j.status, "users_imported": j.users_imported, "users_updated": j.users_updated,
                "users_deactivated": j.users_deactivated, "groups_synced": j.groups_synced,
                "started_at": j.started_at, "finished_at": j.finished_at, "error_message": j.error_message,
            }
            for j in jobs
        ],
    }


@router.post("/ad/connections")
def create_ad_connection(body: DirectoryCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "admin", "*", "add")
    dc = DirectoryConnection(
        organization_id=user.organization_id,
        name=body.name,
        domain_name=body.domain_name,
        server_url=body.server_url,
        use_ldaps=body.use_ldaps,
        base_dn=body.base_dn,
        bind_dn=body.bind_dn,
        bind_password_encrypted=encrypt_secret(body.bind_password),
        directory_mode=body.directory_mode,
        sync_policy=body.sync_policy,
        selected_groups_json=body.selected_groups,
    )
    db.add(dc)
    db.commit()
    return {"id": dc.id, "name": dc.name}


@router.post("/ad/connections/{conn_id}/test")
def test_ad_connection(conn_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "admin", "*", "view")
    dc = db.query(DirectoryConnection).filter(DirectoryConnection.id == conn_id).first()
    if not dc:
        raise HTTPException(status_code=404, detail="AD connection not found")
    return ActiveDirectoryEngine.test_connection(dc)


@router.get("/ad/connections/{conn_id}/search")
def search_ad_directory(
    conn_id: str, q: str = "", user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    enforce_permission(db, user, "admin", "*", "view")
    dc = db.query(DirectoryConnection).filter(DirectoryConnection.id == conn_id).first()
    if not dc:
        raise HTTPException(status_code=404, detail="AD connection not found")
    return ActiveDirectoryEngine.search_directory(dc, q)


@router.post("/ad/connections/{conn_id}/sync")
def sync_ad_directory(
    conn_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    enforce_permission(db, user, "admin", "*", "edit", ip_address=get_client_ip(request))
    dc = db.query(DirectoryConnection).filter(DirectoryConnection.id == conn_id).first()
    if not dc:
        raise HTTPException(status_code=404, detail="AD connection not found")
    res = ActiveDirectoryEngine.sync_directory(db, dc, sync_type="manual")
    record_audit(
        db, user.organization_id, "ad_group_sync", "directory_connection",
        resource_id=dc.id, user_id=user.id, username=user.username,
        ip_address=get_client_ip(request), details=res
    )
    return res


@router.post("/ad/mock-directory/mutate")
def mutate_mock_ad_user(
    body: MockAdMutateUser, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Allows admin & automated tests to simulate AD user changes/disablement in the embedded AD provider."""
    enforce_permission(db, user, "admin", "*", "edit")
    ad_dir = get_mock_directory(body.domain_name)
    for u in ad_dir["users"]:
        if u["sAMAccountName"].lower() == body.username.lower():
            if body.enabled is not None:
                u["enabled"] = body.enabled
            if body.display_name is not None:
                u["displayName"] = body.display_name
            if body.department is not None:
                u["department"] = body.department
            if body.region is not None:
                u["region"] = body.region
            if body.groups is not None:
                u["memberOf"] = body.groups
            return {"status": "updated", "user": {k: v for k, v in u.items() if k != "password"}}

    if body.create_if_missing:
        new_u = {
            "objectGUID": f"ad-guid-{len(ad_dir['users']) + 1001}",
            "sAMAccountName": body.username,
            "mail": f"{body.username}@{body.domain_name}",
            "displayName": body.display_name or body.username,
            "department": body.department or "General",
            "ou": "Standard",
            "region": body.region or "All",
            "title": "Directory User",
            "dn": f"CN={body.username},OU=Users,DC=corp,DC=enterprise,DC=local",
            "enabled": True if body.enabled is None else body.enabled,
            "password": body.password or "DomainUser!123",
            "memberOf": body.groups or ["AD-Sales-Analysts"],
        }
        ad_dir["users"].append(new_u)
        return {"status": "created", "user": {k: v for k, v in new_u.items() if k != "password"}}

    raise HTTPException(status_code=404, detail="AD user not found in directory")
