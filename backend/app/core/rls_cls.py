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


def _compile_rls_rule(rule: dict[str, Any], resolved_val: Any) -> Optional[tuple[str, list[Any]]]:
    col = str(rule.get("column", ""))
    op = str(rule.get("operator", "eq")).lower()
    if not SAFE_IDENT_RE.match(col):
        return None
    if op == "eq":
        return f'"{col}" = ?', [resolved_val]
    if op == "neq":
        return f'"{col}" != ?', [resolved_val]
    if op == "in":
        vals = resolved_val if isinstance(resolved_val, list) else [resolved_val]
        if not vals:
            return "1 = 0", []
        placeholders = ", ".join(["?"] * len(vals))
        return f'"{col}" IN ({placeholders})', list(vals)
    return None


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
            resolved_val = _resolve_user_attr(user, group_names, rule.get("value"))
            compiled = _compile_rls_rule(rule, resolved_val)
            if compiled:
                rls_clauses.append(compiled[0])
                rls_params.extend(compiled[1])
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


def build_public_data_security_context(
    db: Session, organization_id: str, dataset_id: Optional[str] = None
) -> DataSecurityContext:
    """
    Fail-safe security context for unauthenticated public dashboard views:
    - Enforces ALL active CLS (hidden columns) and masking policies for the dataset.
    - Enforces ALL 'all'-targeted RLS rules; user-interpolated '${user.*}' RLS fails closed (1 = 0).
    """
    policies_q = db.query(AccessPolicy).filter(
        AccessPolicy.organization_id == organization_id,
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
        rule = pol.rule_json or {}
        if pol.policy_type == "cls":
            for c in rule.get("hidden_columns", []):
                if SAFE_IDENT_RE.match(str(c)):
                    hidden_cols.add(str(c))
        elif pol.policy_type == "masking":
            col = str(rule.get("column", ""))
            strategy = str(rule.get("strategy", "redact"))
            if SAFE_IDENT_RE.match(col):
                masked_cols[col] = strategy
        elif pol.policy_type == "rls" and pol.target_type == "all":
            val_expr = rule.get("value")
            if isinstance(val_expr, str) and "${user." in val_expr:
                rls_clauses.append("1 = 0")
            else:
                compiled = _compile_rls_rule(rule, val_expr)
                if compiled:
                    rls_clauses.append(compiled[0])
                    rls_params.extend(compiled[1])

    ctx_payload = {
        "public": True,
        "rls": rls_clauses,
        "params": rls_params,
        "hidden": sorted(hidden_cols),
        "masked": masked_cols,
    }
    ctx_hash = hashlib.sha256(json.dumps(ctx_payload, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return DataSecurityContext(
        user_id="public",
        rls_where_clauses=rls_clauses,
        rls_params=rls_params,
        hidden_columns=hidden_cols,
        masked_columns=masked_cols,
        context_hash=ctx_hash,
    )


def inspect_dataset_public_safety(db: Session, organization_id: str, dataset) -> tuple[bool, str]:
    """
    Determines whether a dataset is eligible for unauthenticated public dashboard publication.
    Fails closed on cross-organization datasets, restricted/PII flags, sensitive HR/PII prefixes,
    ANY active AccessPolicy (RLS, CLS, or masking), or policy-evaluation errors.
    """
    try:
        if dataset is None:
            return False, "Dataset not found"
        ds_org = getattr(dataset, "organization_id", None)
        if not ds_org or ds_org != organization_id:
            return False, "Cross-organization dataset cannot be exposed publicly"

        profile = getattr(dataset, "profile_json", None) or {}
        if profile.get("is_restricted") or profile.get("contains_pii"):
            return False, f"Dataset '{dataset.name}' is marked restricted/PII-sensitive"

        tlow = (getattr(dataset, "warehouse_table", "") or "").lower()
        if any(k in tlow for k in ("hr_", "payroll", "pii", "secret", "confidential")):
            return False, f"Dataset '{dataset.name}' contains sensitive HR/PII data"

        policies = (
            db.query(AccessPolicy)
            .filter(
                AccessPolicy.organization_id == organization_id,
                AccessPolicy.is_active == True,
                (AccessPolicy.dataset_id == dataset.id) | (AccessPolicy.dataset_id == None),
            )
            .all()
        )
        for pol in policies:
            if pol.policy_type in ("cls", "masking", "rls"):
                return (
                    False,
                    f"Dataset '{dataset.name}' has active {pol.policy_type.upper()} security policy '{pol.name}'",
                )

        return True, "public_safe"
    except Exception as exc:
        return False, f"Public safety policy evaluation failed ({type(exc).__name__})"


def build_masked_column_sql(col_name: str, strategy: str) -> str:
    if not SAFE_IDENT_RE.match(col_name):
        raise ValueError(f"Unsafe column identifier: {col_name}")
    qcol = f'"{col_name}"'
    st = (strategy or "redact").lower()
    if st == "partial":
        return (
            f"CASE WHEN {qcol} IS NULL THEN NULL "
            f"WHEN CONTAINS(CAST({qcol} AS VARCHAR), '@') THEN "
            f"CONCAT(LEFT(SPLIT_PART(CAST({qcol} AS VARCHAR), '@', 1), 2), '***@', SPLIT_PART(CAST({qcol} AS VARCHAR), '@', 2)) "
            f"WHEN LENGTH(CAST({qcol} AS VARCHAR)) > 4 THEN "
            f"CONCAT(REPEAT('*', LENGTH(CAST({qcol} AS VARCHAR)) - 4), RIGHT(CAST({qcol} AS VARCHAR), 4)) "
            f"ELSE '****' END AS {qcol}"
        )
    if st == "hash":
        return f"CASE WHEN {qcol} IS NULL THEN NULL ELSE LEFT(SHA256(CAST({qcol} AS VARCHAR)), 12) END AS {qcol}"
    return f"CASE WHEN {qcol} IS NULL THEN NULL ELSE '***REDACTED***' END AS {qcol}"


def build_security_projection_and_where(
    table_name: str,
    columns_meta: list[dict[str, str]],
    sec_ctx: DataSecurityContext,
    source_schema: Optional[str] = "main",
) -> tuple[str, list[Any], list[str]]:
    """
    Builds a security-enforced SELECT subquery over physical `table_name` that:
    1. Excludes all `hidden_columns` (CLS) BEFORE user SQL / WHERE / GROUP BY / aggregates run.
    2. Masks all `masked_columns` inside SQL projection BEFORE user SQL / WHERE / SUBSTR / MIN / MAX run.
    3. Applies parameterized `rls_where_clauses`.
    """
    if not SAFE_IDENT_RE.match(table_name):
        raise ValueError(f"Unsafe warehouse table identifier: {table_name}")

    proj_exprs: list[str] = []
    visible_cols: list[str] = []
    for c in columns_meta:
        cname = c["column_name"]
        if not SAFE_IDENT_RE.match(cname):
            continue
        if cname in sec_ctx.hidden_columns:
            continue
        visible_cols.append(cname)
        if cname in sec_ctx.masked_columns:
            proj_exprs.append(build_masked_column_sql(cname, sec_ctx.masked_columns[cname]))
        else:
            proj_exprs.append(f'"{cname}"')

    table_ref = f'{source_schema}."{table_name}"' if source_schema else f'"{table_name}"'
    if not proj_exprs:
        return f'SELECT 1 AS "_cls_restricted" FROM {table_ref} WHERE 1 = 0', [], []

    where_sql = f" WHERE {' AND '.join(sec_ctx.rls_where_clauses)}" if sec_ctx.rls_where_clauses else ""
    sql = f"SELECT {', '.join(proj_exprs)} FROM {table_ref}{where_sql}"
    return sql, list(sec_ctx.rls_params), visible_cols


def build_security_cte_sql(
    table_name: str,
    columns_meta: list[dict[str, str]],
    sec_ctx: DataSecurityContext,
    source_schema: Optional[str] = "main",
) -> tuple[str, list[Any], list[str]]:
    sub_sql, params, visible_cols = build_security_projection_and_where(
        table_name, columns_meta, sec_ctx, source_schema=source_schema
    )
    return f'"{table_name}" AS ({sub_sql})', params, visible_cols


def apply_cls_and_masking(
    columns: list[str], rows: list[dict[str, Any]], sec_ctx: DataSecurityContext
) -> tuple[list[str], list[dict[str, Any]]]:
    filtered_cols = [c for c in columns if c != "_cls_restricted" and c not in sec_ctx.hidden_columns]
    if not sec_ctx.hidden_columns and not sec_ctx.masked_columns and len(filtered_cols) == len(columns):
        return columns, rows

    masked_rows: list[dict[str, Any]] = []
    for row in rows:
        new_row = {}
        for c in filtered_cols:
            val = row.get(c)
            strategy = sec_ctx.masked_columns.get(c)
            if strategy and val is not None:
                s = str(val)
                if strategy == "redact":
                    val = "***REDACTED***"
                elif strategy == "partial":
                    # Avoid double-masking values already masked in SQL CTE
                    if s == "****" or "***@" in s or (len(s) > 4 and s.startswith("*" * (len(s) - 4))):
                        pass
                    elif "@" in s:
                        name, dom = s.split("@", 1)
                        val = f"{name[:2]}***@{dom}"
                    elif len(s) > 4:
                        val = f"{'*' * (len(s) - 4)}{s[-4:]}"
                    else:
                        val = "****"
                elif strategy == "hash":
                    if not (len(s) == 12 and all(ch in "0123456789abcdef" for ch in s)):
                        val = hashlib.sha256(s.encode()).hexdigest()[:12]
            new_row[c] = val
        masked_rows.append(new_row)
    return filtered_cols, masked_rows
