import secrets
from datetime import datetime, timezone
from typing import Optional
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session
from app.config import settings
from app.core.audit import record_audit
from app.core.permissions import permission_resolver
from app.core.security import decode_token
from app.database import get_db
from app.models.identity import User, UserSession

bearer_scheme = HTTPBearer(auto_error=False)


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _extract_token_and_mode(
    request: Request,
    creds: Optional[HTTPAuthorizationCredentials],
) -> tuple[Optional[str], str]:
    # Prefer explicit Bearer header so API/test clients override ambient cookies cleanly
    if creds and creds.credentials:
        return creds.credentials, "bearer"
    cookie_tok = request.cookies.get(settings.ACCESS_COOKIE_NAME)
    if cookie_tok:
        return cookie_tok, "cookie"
    return None, "none"


def _verify_csrf_for_cookie_request(request: Request) -> None:
    if request.method.upper() in ("GET", "HEAD", "OPTIONS"):
        return
    csrf_cookie = request.cookies.get(settings.CSRF_COOKIE_NAME) or ""
    csrf_header = request.headers.get("x-csrf-token") or ""
    if not csrf_cookie or not csrf_header or not secrets.compare_digest(csrf_cookie, csrf_header):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF validation failed",
        )


def get_current_user(
    request: Request,
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    token, auth_mode = _extract_token_and_mode(request, creds)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token required",
        )

    if auth_mode == "cookie":
        _verify_csrf_for_cookie_request(request)

    try:
        payload = decode_token(token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    user_id = payload.get("sub")
    jti = payload.get("jti")
    org_id = payload.get("org_id")
    if not user_id or not jti:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed authentication token")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if org_id and user.organization_id != org_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Organization context mismatch")

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is deactivated")

    now = datetime.now(timezone.utc)
    if user.expires_at:
        exp = user.expires_at if user.expires_at.tzinfo else user.expires_at.replace(tzinfo=timezone.utc)
        if exp < now:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account has expired")

    if user.is_locked:
        if user.locked_until:
            lu = user.locked_until if user.locked_until.tzinfo else user.locked_until.replace(tzinfo=timezone.utc)
            if lu > now:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is locked")
            user.is_locked = False
            user.failed_login_attempts = 0
            db.commit()
        else:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is locked")

    # Fail-closed session validation
    sess = (
        db.query(UserSession)
        .filter(UserSession.jti == jti, UserSession.user_id == user.id)
        .first()
    )
    if not sess or sess.is_revoked:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired or revoked")
    if sess.expires_at:
        sexp = sess.expires_at if sess.expires_at.tzinfo else sess.expires_at.replace(tzinfo=timezone.utc)
        if sexp < now:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session has expired")

    request.state.current_jti = jti
    request.state.auth_mode = auth_mode
    return user


def enforce_permission(
    db: Session,
    user: User,
    resource_type: str,
    resource_id: str,
    action: str,
    parent_chain: Optional[list[tuple[str, str]]] = None,
    ip_address: Optional[str] = None,
) -> None:
    decision = permission_resolver.resolve(db, user, resource_type, resource_id, action, parent_chain)
    if not decision.allowed:
        record_audit(
            db,
            organization_id=user.organization_id,
            action=f"permission_denied:{resource_type}.{action}",
            resource_type=resource_type,
            resource_id=resource_id,
            status="denied",
            user_id=user.id,
            username=user.username,
            ip_address=ip_address,
            details={"reason": decision.reason, "source": decision.source},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permission denied: {resource_type}:{action} ({decision.reason})",
        )
