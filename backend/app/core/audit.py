from typing import Any, Optional
from sqlalchemy.orm import Session
from app.models.identity import AuditLog


def record_audit(
    db: Session,
    organization_id: str,
    action: str,
    resource_type: str,
    status: str = "success",
    user_id: Optional[str] = None,
    username: Optional[str] = None,
    resource_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    before_state: Optional[dict[str, Any]] = None,
    after_state: Optional[dict[str, Any]] = None,
    details: Optional[dict[str, Any]] = None,
    commit: bool = True,
) -> AuditLog:
    entry = AuditLog(
        organization_id=organization_id,
        user_id=user_id,
        username=username,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        status=status,
        ip_address=ip_address,
        before_json=before_state,
        after_json=after_state,
        details_json=details or {},
    )
    db.add(entry)
    if commit:
        try:
            db.commit()
        except Exception:
            db.rollback()
    return entry
