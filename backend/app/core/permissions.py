import hashlib
import json
import threading
from dataclasses import dataclass
from typing import Optional
from sqlalchemy.orm import Session
from app.core.cache import cache_manager
from app.models.identity import (
    GroupMember,
    GroupRole,
    Permission,
    ResourcePermission,
    Role,
    RolePermission,
    User,
    UserRole,
)

# All 12 Hierarchical Scopes (Section 20)
RESOURCE_SCOPES = [
    "organization",
    "workspace",
    "dashboard",
    "dashboard_page",
    "widget",
    "drill_down",
    "dataset",
    "data_source",
    "query",
    "filter",
    "export",
    "admin",
]

# Full Catalog of Independent Granular Actions
PERMISSION_CATALOG: list[tuple[str, str, str]] = [
    # Organization & Admin
    ("organization", "view", "View organization settings"),
    ("organization", "edit", "Edit organization settings"),
    ("admin", "view", "View admin panels and users"),
    ("admin", "edit", "Edit users, roles, groups, and AD settings"),
    ("admin", "add", "Create users, roles, groups, and policies"),
    ("admin", "delete", "Delete users, roles, groups, and policies"),
    # Workspace
    ("workspace", "view", "View workspace"),
    ("workspace", "edit", "Edit workspace"),
    ("workspace", "add", "Create workspace"),
    ("workspace", "delete", "Delete workspace"),
    # Dashboard (Independent CRUD + specialized actions)
    ("dashboard", "view", "View dashboard"),
    ("dashboard", "edit", "Edit dashboard settings"),
    ("dashboard", "add", "Create dashboard"),
    ("dashboard", "delete", "Delete dashboard"),
    ("dashboard", "add_widget", "Add widget to dashboard"),
    ("dashboard", "delete_widget", "Remove widget from dashboard"),
    ("dashboard", "add_page", "Add page to dashboard"),
    ("dashboard", "delete_page", "Delete page from dashboard"),
    ("dashboard", "change_layout", "Change dashboard grid layout"),
    ("dashboard", "change_theme", "Change dashboard theme"),
    ("dashboard", "manage_filters", "Manage dashboard and global filters"),
    ("dashboard", "refresh", "Refresh dashboard data"),
    ("dashboard", "export", "Export dashboard"),
    ("dashboard", "share", "Share or embed dashboard"),
    ("dashboard", "manage_permissions", "Manage dashboard permissions"),
    ("dashboard", "public_restricted_override", "Authorize publishing dashboards containing restricted datasets"),
    # Dashboard Page
    ("dashboard_page", "view", "View dashboard page"),
    ("dashboard_page", "edit", "Edit dashboard page"),
    ("dashboard_page", "add", "Add dashboard page"),
    ("dashboard_page", "delete", "Delete dashboard page"),
    # Widget (Independent CRUD + specialized actions)
    ("widget", "view", "View widget"),
    ("widget", "edit", "Edit widget"),
    ("widget", "add", "Add widget"),
    ("widget", "delete", "Delete widget"),
    ("widget", "change_chart_type", "Switch widget visualization type"),
    ("widget", "change_style", "Change widget colors and appearance"),
    ("widget", "change_query", "Modify widget query"),
    ("widget", "change_dataset", "Change widget dataset"),
    ("widget", "change_filter", "Modify widget-level filters"),
    ("widget", "refresh", "Refresh widget data"),
    ("widget", "view_details", "View widget detailed breakdown"),
    ("widget", "export", "Export widget data"),
    # Drill Down (Completely independent from View!)
    ("drill_down", "drill_down", "Drill down into deeper hierarchy levels"),
    ("drill_down", "drill_up", "Drill up to parent hierarchy level"),
    ("drill_down", "drill_through", "Drill through to target detail page"),
    ("drill_down", "drill_level_1", "Access drill level 1"),
    ("drill_down", "drill_level_2", "Access drill level 2"),
    ("drill_down", "drill_level_3", "Access drill level 3"),
    ("drill_down", "view_raw_records", "Access raw underlying records"),
    ("drill_down", "drill_through_target", "Access drill-through destination pages"),
    # Dataset (with independent schema vs data views)
    ("dataset", "view", "View dataset metadata and preview"),
    ("dataset", "view_schema", "View dataset schema and profiler"),
    ("dataset", "view_data", "Preview dataset row data"),
    ("dataset", "edit", "Edit dataset transforms and schema"),
    ("dataset", "add", "Create or import dataset"),
    ("dataset", "delete", "Delete dataset"),
    ("dataset", "refresh", "Refresh dataset ETL"),
    # Data Source
    ("data_source", "view", "View data source definitions"),
    ("data_source", "edit", "Edit data sources"),
    ("data_source", "add", "Create data source connections"),
    ("data_source", "delete", "Delete data sources"),
    ("data_source", "execute_query", "Execute direct query on data source"),
    # Query
    ("query", "view", "View saved queries"),
    ("query", "edit", "Edit queries"),
    ("query", "add", "Create queries"),
    ("query", "delete", "Delete queries"),
    ("query", "execute", "Execute custom SQL or visual queries"),
    # Filter
    ("filter", "view", "View filters"),
    ("filter", "edit", "Edit filter values or rules"),
    ("filter", "add", "Create filters"),
    ("filter", "delete", "Delete filters"),
    # Export
    ("export", "view", "View exports"),
    ("export", "export_pdf", "Export to PDF"),
    ("export", "export_excel", "Export to Excel"),
    ("export", "export_csv", "Export to CSV"),
    ("export", "export_png", "Export to PNG"),
]


@dataclass
class PermissionDecision:
    allowed: bool
    reason: str
    source: str


class PermissionResolver:
    """
    Hierarchical Permission Resolution Engine (Section 23).
    Rules:
    1. Default is DENY.
    2. Explicit DENY overrides any ALLOW (including role/group/parent allows).
    3. CRUD actions (view, edit, add, delete) and drill_down NEVER imply one another.
    4. Resolution aggregates Direct User + Groups + Roles + Group Roles + Resource Hierarchy.
    5. Instant invalidation across workers via DB permission_version + Redis epoch key.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._memo: dict[str, PermissionDecision] = {}

    def _get_org_epoch(self, organization_id: str) -> str:
        r = getattr(cache_manager, "_redis", None)
        if r is not None:
            try:
                val = r.get(f"perm_epoch:{organization_id}")
                if val is not None:
                    return str(val)
            except Exception:
                pass
        return "0"

    def _bump_org_epoch(self, organization_id: str) -> None:
        r = getattr(cache_manager, "_redis", None)
        if r is not None:
            try:
                r.incr(f"perm_epoch:{organization_id}")
            except Exception:
                pass

    def invalidate_all(self, organization_id: Optional[str] = None) -> None:
        with self._lock:
            self._memo.clear()
        if organization_id:
            self._bump_org_epoch(organization_id)
            cache_manager.invalidate_org(organization_id)

    def bump_user_version(self, db: Session, user_id: str) -> None:
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            user.permission_version += 1
            self.invalidate_all(user.organization_id)
        else:
            self.invalidate_all()

    def bump_org_versions(self, db: Session, organization_id: str) -> None:
        users = db.query(User).filter(User.organization_id == organization_id).all()
        for u in users:
            u.permission_version += 1
        self.invalidate_all(organization_id)

    def _get_user_context(self, db: Session, user: User) -> dict:
        group_ids = [
            gm.group_id
            for gm in db.query(GroupMember).filter(GroupMember.user_id == user.id).all()
        ]
        direct_role_ids = [
            ur.role_id
            for ur in db.query(UserRole).filter(UserRole.user_id == user.id).all()
        ]
        group_role_ids = []
        if group_ids:
            group_role_ids = [
                gr.role_id
                for gr in db.query(GroupRole).filter(GroupRole.group_id.in_(group_ids)).all()
            ]
        all_role_ids = list(set(direct_role_ids + group_role_ids))
        roles = (
            db.query(Role)
            .filter(Role.id.in_(all_role_ids), Role.organization_id == user.organization_id)
            .all()
            if all_role_ids
            else []
        )
        valid_role_ids = [r.id for r in roles]
        is_super_admin = any(r.is_super_admin for r in roles)

        role_perms: list[tuple[str, str, str]] = []
        if valid_role_ids:
            rows = (
                db.query(Permission.resource_type, Permission.action, RolePermission.effect)
                .join(RolePermission, RolePermission.permission_id == Permission.id)
                .filter(RolePermission.role_id.in_(valid_role_ids))
                .all()
            )
            role_perms = [(r[0], r[1], r[2]) for r in rows]

        subjects = [("user", user.id)]
        subjects.extend(("group", gid) for gid in group_ids)
        subjects.extend(("role", rid) for rid in valid_role_ids)

        rps: list[ResourcePermission] = []
        all_rps = (
            db.query(ResourcePermission)
            .filter(ResourcePermission.organization_id == user.organization_id)
            .all()
        )
        subj_set = set(subjects)
        for rp in all_rps:
            if (rp.subject_type, rp.subject_id) in subj_set:
                rps.append(rp)

        return {
            "group_ids": group_ids,
            "role_ids": valid_role_ids,
            "roles": [r.slug for r in roles],
            "is_super_admin": is_super_admin,
            "role_perms": role_perms,
            "resource_perms": rps,
        }

    def get_permission_context_hash(self, db: Session, user: User) -> str:
        ctx = self._get_user_context(db, user)
        summary = {
            "user_id": user.id,
            "org_id": user.organization_id,
            "perm_ver": user.permission_version,
            "epoch": self._get_org_epoch(user.organization_id),
            "roles": sorted(ctx["roles"]),
            "groups": sorted(ctx["group_ids"]),
            "rp": sorted(
                f"{rp.resource_type}:{rp.resource_id}:{rp.action}:{rp.effect}"
                for rp in ctx["resource_perms"]
            ),
        }
        return hashlib.sha256(json.dumps(summary, sort_keys=True).encode()).hexdigest()[:16]

    def resolve(
        self,
        db: Session,
        user: User,
        resource_type: str,
        resource_id: str,
        action: str,
        parent_chain: Optional[list[tuple[str, str]]] = None,
    ) -> PermissionDecision:
        if not user.is_active or user.is_locked:
            return PermissionDecision(False, "user_inactive_or_locked", "user_status")

        org_epoch = self._get_org_epoch(user.organization_id)
        parent_key = ",".join(f"{pt}:{pi}" for pt, pi in (parent_chain or []))
        cache_key = (
            f"{user.organization_id}:{org_epoch}:{user.id}:{user.permission_version}:"
            f"{resource_type}:{resource_id}:{action}:{parent_key}"
        )
        with self._lock:
            cached = self._memo.get(cache_key)
            if cached is not None:
                return cached

        try:
            decision = self._evaluate(db, user, resource_type, resource_id, action, parent_chain)
        except Exception as exc:
            return PermissionDecision(False, f"policy_evaluation_failed:{type(exc).__name__}", "fail_closed")
        if not isinstance(decision, PermissionDecision):
            return PermissionDecision(False, "invalid_policy_decision", "fail_closed")
        with self._lock:
            if len(self._memo) > 10000:
                self._memo.clear()
            self._memo[cache_key] = decision
        return decision

    def _matches_rp(self, rp: ResourcePermission, r_type: str, r_id: str, action: str) -> bool:
        type_match = rp.resource_type == r_type or rp.resource_type == "*"
        id_match = rp.resource_id == r_id or rp.resource_id == "*"
        act_match = rp.action == action or rp.action == "*"
        return type_match and id_match and act_match

    def _evaluate(
        self,
        db: Session,
        user: User,
        resource_type: str,
        resource_id: str,
        action: str,
        parent_chain: Optional[list[tuple[str, str]]],
    ) -> PermissionDecision:
        ctx = self._get_user_context(db, user)
        rps: list[ResourcePermission] = ctx["resource_perms"]
        role_perms: list[tuple[str, str, str]] = ctx["role_perms"]

        # 1. Explicit DENY on exact resource or wildcard on that resource_type
        for rp in rps:
            if rp.effect == "deny" and self._matches_rp(rp, resource_type, resource_id, action):
                return PermissionDecision(False, "explicit_deny_resource", f"{rp.subject_type}:{rp.subject_id}")

        # 2. Explicit DENY inherited from parent resource (e.g., dashboard deny blocks its widgets)
        if parent_chain:
            for p_type, p_id in parent_chain:
                for rp in rps:
                    if rp.effect == "deny" and rp.inherit_to_children:
                        if rp.resource_type == p_type and (rp.resource_id == p_id or rp.resource_id == "*"):
                            if rp.action in (action, "view", "*"):
                                return PermissionDecision(
                                    False, "explicit_deny_inherited", f"{p_type}:{p_id}"
                                )

        # 3. Role-level explicit DENY
        for r_type, r_act, effect in role_perms:
            if effect == "deny" and r_type in (resource_type, "*") and r_act in (action, "*"):
                return PermissionDecision(False, "explicit_deny_role", "role_permission")

        # 4. Super Admin Allow (after explicit denies checked!)
        if ctx["is_super_admin"]:
            return PermissionDecision(True, "super_admin", "role:super_admin")

        # 5. Explicit ALLOW on exact resource or wildcard
        for rp in rps:
            if rp.effect == "allow" and self._matches_rp(rp, resource_type, resource_id, action):
                return PermissionDecision(True, "explicit_allow_resource", f"{rp.subject_type}:{rp.subject_id}")

        # 6. Inherited ALLOW from parent resource ONLY when inherit_to_children=True AND
        #    rule explicitly lists target resource_type in cascade_types / cascade_targets
        if parent_chain:
            for p_type, p_id in parent_chain:
                for rp in rps:
                    if rp.effect == "allow" and rp.inherit_to_children:
                        if rp.resource_type == p_type and (rp.resource_id == p_id or rp.resource_id == "*"):
                            cond = rp.conditions_json or {}
                            cascade_targets = cond.get("cascade_types") or cond.get("cascade_targets") or []
                            if resource_type in cascade_targets and rp.action == action:
                                return PermissionDecision(
                                    True, "inherited_allow_parent", f"{p_type}:{p_id}"
                                )

        # 7. Role-level ALLOW
        for r_type, r_act, effect in role_perms:
            if effect == "allow" and r_type in (resource_type, "*") and r_act in (action, "*"):
                return PermissionDecision(True, "role_permission_allow", "role_permission")

        # 8. Default DENY
        return PermissionDecision(False, "default_deny", "default")

    def require(
        self,
        db: Session,
        user: User,
        resource_type: str,
        resource_id: str,
        action: str,
        parent_chain: Optional[list[tuple[str, str]]] = None,
    ) -> PermissionDecision:
        return self.resolve(db, user, resource_type, resource_id, action, parent_chain)

    def get_effective_permissions_summary(self, db: Session, user: User) -> dict:
        ctx = self._get_user_context(db, user)
        return {
            "user_id": user.id,
            "username": user.username,
            "is_super_admin": ctx["is_super_admin"],
            "roles": ctx["roles"],
            "group_ids": ctx["group_ids"],
            "role_permissions": [
                {"resource_type": rt, "action": act, "effect": eff}
                for rt, act, eff in ctx["role_perms"]
            ],
            "resource_permissions": [
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
                for rp in ctx["resource_perms"]
            ],
        }


permission_resolver = PermissionResolver()
