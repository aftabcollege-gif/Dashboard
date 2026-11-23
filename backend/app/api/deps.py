import ipaddress
import secrets
from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session
from app.config import settings
from app.core.audit import record_audit
from app.core.permissions import PermissionDecision, permission_resolver
from app.core.rate_limit import rate_limiter
from app.core.security import decode_token
from app.database import get_db
from app.models.identity import User, UserSession

bearer_scheme = HTTPBearer(auto_error=False)


@lru_cache(maxsize=16)
def _parse_trusted_proxy_nets(raw: str) -> tuple[ipaddress._BaseNetwork, ...]:
    nets: list[ipaddress._BaseNetwork] = []
    for item in (raw or "").split(","):
        token = item.strip()
        if not token:
            continue
        try:
            nets.append(ipaddress.ip_network(token, strict=False))
        except ValueError:
            continue
    return tuple(nets)


def _normalize_ip_str(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    try:
        ip_obj = ipaddress.ip_address(raw.strip())
        if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped is not None:
            ip_obj = ip_obj.ipv4_mapped
        return str(ip_obj)
    except ValueError:
        return None


def is_trusted_proxy_ip(host: Optional[str]) -> bool:
    if not settings.TRUST_PROXY_HEADERS or not host:
        return False
    norm = _normalize_ip_str(host)
    if not norm:
        return False
    ip_obj = ipaddress.ip_address(norm)
    return any(ip_obj in net for net in _parse_trusted_proxy_nets(settings.TRUSTED_PROXY_IPS))


def is_https_request(request: Request) -> bool:
    if settings.COOKIE_SECURE == "true":
        return True
    if settings.COOKIE_SECURE == "false":
        return False
    peer_host = request.client.host if request.client else None
    if is_trusted_proxy_ip(peer_host):
        proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
        if proto:
            return proto == "https"
    return request.url.scheme == "https"


def get_client_ip(request: Request) -> str:
    raw_peer = request.client.host if request.client else "unknown"
    peer_host = _normalize_ip_str(raw_peer) or raw_peer
    if not is_trusted_proxy_ip(peer_host):
        return peer_host
    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        real_ip = _normalize_ip_str(request.headers.get("x-real-ip"))
        return real_ip or peer_host
    hops = [h.strip() for h in forwarded.split(",") if h.strip()]
    trusted_nets = _parse_trusted_proxy_nets(settings.TRUSTED_PROXY_IPS)
    # Walk right-to-left stripping trusted proxy hops so spoofed leftmost headers cannot bypass rate limits
    for hop in reversed(hops):
        norm_hop = _normalize_ip_str(hop)
        if not norm_hop:
            # Malformed/spoofed non-IP hop cannot be trusted as client IP
            return peer_host
        ip_obj = ipaddress.ip_address(norm_hop)
        if any(ip_obj in net for net in trusted_nets):
            continue
        return norm_hop
    first_norm = _normalize_ip_str(hops[0]) if hops else None
    return first_norm or peer_host


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


def enforce_scoped_rate_limit(
    endpoint: str,
    *,
    ip: Optional[str] = None,
    user_id: Optional[str] = None,
    org_id: Optional[str] = None,
    ip_limit: int = 0,
    user_limit: int = 0,
    org_limit: int = 0,
    window_seconds: int = 60,
) -> None:
    dims: list[tuple[str, int, int]] = []
    ep = (endpoint or "default").strip().lower()
    if ip and ip_limit > 0:
        dims.append((f"ep:{ep}:ip:{ip}", ip_limit, window_seconds))
    if user_id and user_limit > 0:
        dims.append((f"ep:{ep}:user:{user_id}", user_limit, window_seconds))
    if org_id and org_limit > 0:
        dims.append((f"ep:{ep}:org:{org_id}", org_limit, window_seconds))
    if dims:
        rate_limiter.check_dimensions(dims)


def enforce_permission(
    db: Session,
    user: User,
    resource_type: str,
    resource_id: str,
    action: str,
    parent_chain: Optional[list[tuple[str, str]]] = None,
    ip_address: Optional[str] = None,
) -> None:
    try:
        decision = permission_resolver.resolve(db, user, resource_type, resource_id, action, parent_chain)
    except Exception as exc:
        record_audit(
            db,
            organization_id=user.organization_id,
            action=f"permission_eval_error:{resource_type}.{action}",
            resource_type=resource_type,
            resource_id=resource_id,
            status="denied",
            user_id=user.id,
            username=user.username,
            ip_address=ip_address,
            details={"reason": f"resolver_error:{type(exc).__name__}"},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permission denied: {resource_type}:{action} (policy evaluation failed)",
        ) from exc
    if not isinstance(decision, PermissionDecision) or not decision.allowed:
        reason = decision.reason if isinstance(decision, PermissionDecision) else "invalid_decision"
        source = decision.source if isinstance(decision, PermissionDecision) else "resolver"
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
            details={"reason": reason, "source": source},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permission denied: {resource_type}:{action} ({reason})",
        )
