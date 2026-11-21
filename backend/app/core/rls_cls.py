import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Optional
from sqlalchemy.orm import Session
from app.models.identity import AccessPolicy, User, GroupMember, UserRole, GroupRole

SAFE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class DataSecurityContext:
    user_id: str
    rls_where_clauses: list[str]
    rls_params: list[Any]
    hidden_columns: set[str]
    masked_columns: dict[str, str]  # column_name -> strategy (redact|partial|hash)
    context_hash: str


def _resolve_user_attr(user: User, group_names: list[str], expr: str) -> Any:
    profile = user.profile
    mapping = {
        "${user.id}": user.id,
        "${user.username}": user.username,
        "${user.email}": user.email,
        "${user.department}": profile.department if profile else "",
        "${user.org_unit}": profile.org_unit if profile else "",
        "${user.region}": profile.region if profile else "",
        "${user.ad_groups}": group_names,
    }
    if expr in mapping:
        return mapping[expr]
    if isinstance(expr, str) and expr.startswith("${user.custom.") and expr.endswith("}"):
        key = expr[len("${user.custom."):-1]
        if profile and isinstance(profile.custom_attributes, dict):
            return profile.custom_attributes.get(key, "")
        return ""
    return expr


def build_data_security_context(
    db: Session, user: User, dataset_id: Optional[str] = None
) -> DataSecurityContext:
    from app.models.identity import Group, Role

    group_members = db.query(GroupMember).filter(GroupMember.user_id == user.id).all()
    group_ids = [gm.group_id for gm in group_members]
    groups = db.query(Group).filter(Group.id.in_(group_ids)).all() if group_ids else []
    group_names = [g.name for g in groups]

    direct_role_ids = [ur.role_id for ur in db.query(UserRole).filter(UserRole.user_id == user.id).all()]
    group_role_ids = (
        [gr.role_id for gr in db.query(GroupRole).filter(GroupRole.group_id.in_(group_ids)).all()]
        if group_ids
        else []
    )
    role_ids = list(set(direct_role_ids + group_role_ids))
    roles = db.query(Role).filter(Role.id.in_(role_ids)).all() if role_ids else []
    is_super_admin = any(r.is_super_admin for r in roles)

    # Super Admin bypasses RLS/CLS only if not explicitly targeted as user
    policies_q = db.query(AccessPolicy).filter(
        AccessPolicy.organization_id == user.organization_id,
        AccessPolicy.is_active == True,
    )
    if dataset_id:
        policies_q = policies_q.filter(
            (AccessPolicy.dataset_id == dataset_id) | (AccessPolicy.dataset_id == None)
        )
    policies = policies_q.order_by(AccessPolicy.priority.asc()).all()

    rls_clauses: list[str] = []
    rls_params: list[Any] = []
    hidden_cols: set[str] = set()
    masked_cols: dict[str, str] = {}

    for pol in policies:
        applies = False
        if pol.target_type == "all" and not is_super_admin:
            applies = True
        elif pol.target_type == "user" and pol.target_id == user.id:
            applies = True
        elif pol.target_type == "group" and pol.target_id in group_ids:
            applies = True
        elif pol.target_type == "role" and pol.target_id in role_ids:
            applies = True

        if not applies:
            continue

        rule = pol.rule_json or {}
        if pol.policy_type == "rls":
            col = str(rule.get("column", ""))
            op = str(rule.get("operator", "eq")).lower()
            val_expr = rule.get("value")
            if not SAFE_IDENT_RE.match(col):
                continue
            resolved_val = _resolve_user_attr(user, group_names, val_expr)
            if op == "eq":
                rls_clauses.append(f'"{col}" = ?')
                rls_params.append(resolved_val)
            elif op == "neq":
                rls_clauses.append(f'"{col}" != ?')
                rls_params.append(resolved_val)
            elif op == "in":
                vals = resolved_val if isinstance(resolved_val, list) else [resolved_val]
                if not vals:
                    rls_clauses.append("1 = 0")
                else:
                    placeholders = ", ".join(["?"] * len(vals))
                    rls_clauses.append(f'"{col}" IN ({placeholders})')
                    rls_params.extend(vals)
        elif pol.policy_type == "cls":
            for c in rule.get("hidden_columns", []):
                if SAFE_IDENT_RE.match(str(c)):
                    hidden_cols.add(str(c))
        elif pol.policy_type == "masking":
            col = str(rule.get("column", ""))
            strategy = str(rule.get("strategy", "redact"))
            if SAFE_IDENT_RE.match(col):
                masked_cols[col] = strategy

    ctx_payload = {
        "rls": rls_clauses,
        "params": rls_params,
        "hidden": sorted(hidden_cols),
        "masked": masked_cols,
    }
    ctx_hash = hashlib.sha256(json.dumps(ctx_payload, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return DataSecurityContext(
        user_id=user.id,
        rls_where_clauses=rls_clauses,
        rls_params=rls_params,
        hidden_columns=hidden_cols,
        masked_columns=masked_cols,
        context_hash=ctx_hash,
    )


def apply_cls_and_masking(
    columns: list[str], rows: list[dict[str, Any]], sec_ctx: DataSecurityContext
) -> tuple[list[str], list[dict[str, Any]]]:
    if not sec_ctx.hidden_columns and not sec_ctx.masked_columns:
        return columns, rows

    visible_cols = [c for c in columns if c not in sec_ctx.hidden_columns]
    masked_rows: list[dict[str, Any]] = []
    for row in rows:
        new_row = {}
        for c in visible_cols:
            val = row.get(c)
            strategy = sec_ctx.masked_columns.get(c)
            if strategy and val is not None:
                s = str(val)
                if strategy == "redact":
                    val = "***REDACTED***"
                elif strategy == "partial":
                    if "@" in s:
                        name, dom = s.split("@", 1)
                        val = f"{name[:2]}***@{dom}"
                    elif len(s) > 4:
                        val = f"{'*' * (len(s) - 4)}{s[-4:]}"
                    else:
                        val = "****"
                elif strategy == "hash":
                    val = hashlib.sha256(s.encode()).hexdigest()[:12]
            new_row[c] = val
        masked_rows.append(new_row)
    return visible_cols, masked_rows
