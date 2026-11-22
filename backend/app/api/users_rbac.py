from datetime import datetime
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.api.deps import enforce_permission, get_client_ip, get_current_user
from app.config import settings
from app.core.audit import record_audit
from app.core.cache import cache_manager
from app.core.permissions import permission_resolver
from app.core.security import encrypt_secret, hash_password, validate_password_strength
from app.core.tenant import (
    get_dashboard_for_org,
    get_dataset_for_org,
    get_group_for_org,
    get_role_for_org,
    get_user_for_org,
    get_widget_for_org,
)
from app.database import get_db
from app.engines.ad_ldap import ActiveDirectoryEngine, get_mock_directory
from app.models.dashboard import Dashboard, Widget, Workspace
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
    UserSession,
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


def _actor_is_super_admin(db: Session, user: User) -> bool:
    return (
        db.query(Role)
        .join(UserRole, UserRole.role_id == Role.id)
        .filter(UserRole.user_id == user.id, Role.is_super_admin == True)
        .first()
        is not None
    )


def _validate_org_roles(db: Session, actor: User, role_ids: list[str]) -> list[Role]:
    is_super = _actor_is_super_admin(db, actor)
    out: list[Role] = []
    for rid in role_ids:
        r = get_role_for_org(db, rid, actor.organization_id)
        if r.is_super_admin and not is_super:
            raise HTTPException(status_code=403, detail="Only a super_admin can assign a super_admin role")
        out.append(r)
    return out


def _validate_org_groups(db: Session, actor: User, group_ids: list[str]) -> list[Group]:
    return [get_group_for_org(db, gid, actor.organization_id) for gid in group_ids]


def _revoke_user_sessions(db: Session, user_id: str) -> None:
    db.query(UserSession).filter(UserSession.user_id == user_id, UserSession.is_revoked == False).update(
        {"is_revoked": True}
    )


def _invalidate_org_caches(db: Session, org_id: str) -> None:
    permission_resolver.bump_org_versions(db, org_id)
    cache_manager.invalidate_org(org_id)


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
    validate_password_strength(body.password)

    if db.query(User).filter((User.username == body.username) | (User.email == body.email)).first():
        raise HTTPException(status_code=400, detail="Username or email already exists")

    valid_roles = _validate_org_roles(db, user, body.role_ids)
    valid_groups = _validate_org_groups(db, user, body.group_ids)

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
    for r in valid_roles:
        db.add(UserRole(user_id=u.id, role_id=r.id))
    for g in valid_groups:
        db.add(GroupMember(group_id=g.id, user_id=u.id))

    record_audit(
        db, user.organization_id, "user_created", "user", resource_id=u.id,
        user_id=user.id, username=user.username, ip_address=get_client_ip(request),
        after_state={"username": u.username, "roles": [r.id for r in valid_roles]}
    )
    _invalidate_org_caches(db, user.organization_id)
    db.commit()
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
    target = get_user_for_org(db, target_user_id, user.organization_id)
    if _actor_is_super_admin(db, target) and not _actor_is_super_admin(db, user):
        raise HTTPException(status_code=403, detail="Only a super_admin can modify a super_admin user")
    if target.id == user.id and body.is_active is False:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")

    valid_roles = _validate_org_roles(db, user, body.role_ids) if body.role_ids is not None else None
    valid_groups = _validate_org_groups(db, user, body.group_ids) if body.group_ids is not None else None

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
        validate_password_strength(body.reset_password)
        target.password_hash = hash_password(body.reset_password)
        _revoke_user_sessions(db, target.id)
    if target.is_active is False or target.is_locked is True:
        _revoke_user_sessions(db, target.id)

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

    if valid_roles is not None:
        db.query(UserRole).filter(UserRole.user_id == target.id).delete()
        for r in valid_roles:
            db.add(UserRole(user_id=target.id, role_id=r.id))
    if valid_groups is not None:
        db.query(GroupMember).filter(GroupMember.user_id == target.id).delete()
        for g in valid_groups:
            db.add(GroupMember(group_id=g.id, user_id=target.id))

    target.permission_version += 1
    record_audit(
        db, user.organization_id, "user_updated", "user", resource_id=target.id,
        user_id=user.id, username=user.username, ip_address=get_client_ip(request),
        before_state=before, after_state={"is_active": target.is_active, "is_locked": target.is_locked}
    )
    _invalidate_org_caches(db, user.organization_id)
    db.commit()
    return {"status": "updated"}


@router.delete("/users/{target_user_id}")
def delete_user(
    target_user_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    enforce_permission(db, user, "admin", "*", "delete", ip_address=get_client_ip(request))
    if target_user_id == user.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    target = get_user_for_org(db, target_user_id, user.organization_id)
    if _actor_is_super_admin(db, target) and not _actor_is_super_admin(db, user):
        raise HTTPException(status_code=403, detail="Only a super_admin can delete a super_admin user")
    _revoke_user_sessions(db, target.id)
    db.delete(target)
    record_audit(
        db, user.organization_id, "user_deleted", "user", resource_id=target_user_id,
        user_id=user.id, username=user.username, ip_address=get_client_ip(request)
    )
    _invalidate_org_caches(db, user.organization_id)
    db.commit()
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
    if body.slug.strip().lower() == "super_admin":
        raise HTTPException(status_code=403, detail="Cannot create a super_admin role")
    if db.query(Role).filter(Role.organization_id == user.organization_id, Role.slug == body.slug).first():
        raise HTTPException(status_code=400, detail="Role slug already exists in organization")
    r = Role(organization_id=user.organization_id, name=body.name, slug=body.slug, description=body.description)
    db.add(r)
    db.flush()
    for pid in body.permission_ids:
        if db.query(Permission).filter(Permission.id == pid).first():
            db.add(RolePermission(role_id=r.id, permission_id=pid, effect="allow"))
    _invalidate_org_caches(db, user.organization_id)
    db.commit()
    return {"id": r.id, "name": r.name}


@router.put("/roles/{role_id}")
def update_role(role_id: str, body: RoleCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "admin", "*", "edit")
    r = get_role_for_org(db, role_id, user.organization_id)
    if r.is_super_admin and not _actor_is_super_admin(db, user):
        raise HTTPException(status_code=403, detail="Only a super_admin can edit a super_admin role")
    r.name = body.name
    r.description = body.description
    db.query(RolePermission).filter(RolePermission.role_id == r.id).delete()
    for pid in body.permission_ids:
        if db.query(Permission).filter(Permission.id == pid).first():
            db.add(RolePermission(role_id=r.id, permission_id=pid, effect="allow"))
    _invalidate_org_caches(db, user.organization_id)
    db.commit()
    return {"status": "updated"}


@router.post("/roles/{role_id}/clone")
def clone_role(role_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "admin", "*", "add")
    src = get_role_for_org(db, role_id, user.organization_id)
    if src.is_super_admin and not _actor_is_super_admin(db, user):
        raise HTTPException(status_code=403, detail="Only a super_admin can clone a super_admin role")
    nr = Role(
        organization_id=user.organization_id,
        name=f"{src.name} (Copy)",
        slug=f"{src.slug}_copy_{int(datetime.now().timestamp())}",
        description=src.description,
        is_super_admin=False,
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
    valid_roles = _validate_org_roles(db, user, body.role_ids)
    valid_users = [get_user_for_org(db, uid, user.organization_id) for uid in body.member_user_ids]
    g = Group(organization_id=user.organization_id, name=body.name, description=body.description, source="local")
    db.add(g)
    db.flush()
    for r in valid_roles:
        db.add(GroupRole(group_id=g.id, role_id=r.id))
    for u in valid_users:
        db.add(GroupMember(group_id=g.id, user_id=u.id))
    _invalidate_org_caches(db, user.organization_id)
    db.commit()
    return {"id": g.id, "name": g.name}


@router.put("/groups/{group_id}")
def update_group(group_id: str, body: GroupCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "admin", "*", "edit")
    g = get_group_for_org(db, group_id, user.organization_id)
    valid_roles = _validate_org_roles(db, user, body.role_ids)
    valid_users = [get_user_for_org(db, uid, user.organization_id) for uid in body.member_user_ids]
    g.name = body.name
    g.description = body.description
    db.query(GroupRole).filter(GroupRole.group_id == g.id).delete()
    db.query(GroupMember).filter(GroupMember.group_id == g.id).delete()
    for r in valid_roles:
        db.add(GroupRole(group_id=g.id, role_id=r.id))
    for u in valid_users:
        db.add(GroupMember(group_id=g.id, user_id=u.id))
    _invalidate_org_caches(db, user.organization_id)
    db.commit()
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
    dashboards = (
        db.query(Dashboard)
        .join(Workspace, Workspace.id == Dashboard.workspace_id)
        .filter(Workspace.organization_id == user.organization_id)
        .all()
    )
    dash_ids = [d.id for d in dashboards]
    widgets = (
        db.query(Widget).filter(Widget.dashboard_id.in_(dash_ids)).all()
        if dash_ids
        else []
    )
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


def _validate_matrix_subject(db: Session, actor: User, subject_type: str, subject_id: str) -> None:
    if subject_type == "user":
        get_user_for_org(db, subject_id, actor.organization_id)
    elif subject_type == "group":
        get_group_for_org(db, subject_id, actor.organization_id)
    elif subject_type == "role":
        r = get_role_for_org(db, subject_id, actor.organization_id)
        if r.is_super_admin and not _actor_is_super_admin(db, actor):
            raise HTTPException(status_code=403, detail="Only a super_admin can edit super_admin rules")
    else:
        raise HTTPException(status_code=400, detail="Invalid subject_type")


@router.put("/permissions/matrix/set")
def set_matrix_permission(
    body: MatrixPermissionSet,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(db, user, "admin", "*", "edit", ip_address=get_client_ip(request))
    _validate_matrix_subject(db, user, body.subject_type, body.subject_id)
    if body.resource_id != "*":
        if body.resource_type == "dashboard":
            get_dashboard_for_org(db, body.resource_id, user.organization_id)
        elif body.resource_type in ("widget", "drill_down"):
            get_widget_for_org(db, body.resource_id, user.organization_id)
        elif body.resource_type == "dataset":
            get_dataset_for_org(db, body.resource_id, user.organization_id)

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

    _invalidate_org_caches(db, user.organization_id)
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
    _validate_matrix_subject(db, user, body.from_subject_type, body.from_subject_id)
    _validate_matrix_subject(db, user, body.to_subject_type, body.to_subject_id)

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
                ResourcePermission.organization_id == user.organization_id,
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
    _invalidate_org_caches(db, user.organization_id)
    db.commit()
    return {"copied": copied}


@router.get("/permissions/effective/{target_user_id}")
def get_effective_permissions(
    target_user_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    enforce_permission(db, user, "admin", "*", "view")
    target = get_user_for_org(db, target_user_id, user.organization_id)
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
    if body.dataset_id:
        get_dataset_for_org(db, body.dataset_id, user.organization_id)
    if body.target_type != "all" and body.target_id:
        _validate_matrix_subject(db, user, body.target_type, body.target_id)
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
    _invalidate_org_caches(db, user.organization_id)
    db.commit()
    return {"id": pol.id, "name": pol.name}


@router.delete("/policies/{policy_id}")
def delete_access_policy(policy_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "admin", "*", "delete")
    pol = (
        db.query(AccessPolicy)
        .filter(AccessPolicy.id == policy_id, AccessPolicy.organization_id == user.organization_id)
        .first()
    )
    if not pol:
        raise HTTPException(status_code=404, detail="Policy not found")
    db.delete(pol)
    _invalidate_org_caches(db, user.organization_id)
    db.commit()
    return {"status": "deleted"}


# ---------------- ACTIVE DIRECTORY / LDAP (Section 19B) ----------------
@router.get("/ad/connections")
def list_ad_connections(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "admin", "*", "view")
    rows = db.query(DirectoryConnection).filter(DirectoryConnection.organization_id == user.organization_id).all()
    conn_ids = [c.id for c in rows]
    jobs = (
        db.query(DirectorySyncJob)
        .filter(DirectorySyncJob.directory_connection_id.in_(conn_ids))
        .order_by(DirectorySyncJob.started_at.desc())
        .limit(20)
        .all()
        if conn_ids
        else []
    )
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
    dc = (
        db.query(DirectoryConnection)
        .filter(DirectoryConnection.id == conn_id, DirectoryConnection.organization_id == user.organization_id)
        .first()
    )
    if not dc:
        raise HTTPException(status_code=404, detail="AD connection not found")
    return ActiveDirectoryEngine.test_connection(dc)


@router.get("/ad/connections/{conn_id}/search")
def search_ad_directory(
    conn_id: str, q: str = "", user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    enforce_permission(db, user, "admin", "*", "view")
    dc = (
        db.query(DirectoryConnection)
        .filter(DirectoryConnection.id == conn_id, DirectoryConnection.organization_id == user.organization_id)
        .first()
    )
    if not dc:
        raise HTTPException(status_code=404, detail="AD connection not found")
    return ActiveDirectoryEngine.search_directory(dc, q)


@router.post("/ad/connections/{conn_id}/sync")
def sync_ad_directory(
    conn_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    enforce_permission(db, user, "admin", "*", "edit", ip_address=get_client_ip(request))
    dc = (
        db.query(DirectoryConnection)
        .filter(DirectoryConnection.id == conn_id, DirectoryConnection.organization_id == user.organization_id)
        .first()
    )
    if not dc:
        raise HTTPException(status_code=404, detail="AD connection not found")
    res = ActiveDirectoryEngine.sync_directory(db, dc, sync_type="manual")
    _invalidate_org_caches(db, user.organization_id)
    record_audit(
        db, user.organization_id, "ad_group_sync", "directory_connection",
        resource_id=dc.id, user_id=user.id, username=user.username,
        ip_address=get_client_ip(request), details=res
    )
    db.commit()
    return res


@router.post("/ad/mock-directory/mutate")
def mutate_mock_ad_user(
    body: MockAdMutateUser, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Allows admin & automated tests to simulate AD user changes/disablement in dev/test only."""
    if settings.ENVIRONMENT not in ("development", "test"):
        raise HTTPException(status_code=404, detail="Endpoint not found")
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
