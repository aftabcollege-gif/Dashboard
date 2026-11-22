import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.api.deps import get_client_ip, get_current_user, is_https_request
from app.config import settings
from app.core.audit import record_audit
from app.core.permissions import permission_resolver
from app.core.rate_limit import rate_limiter
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    decrypt_secret,
    encrypt_secret,
    generate_mfa_secret,
    get_mfa_provisioning_uri,
    hash_password,
    validate_password_strength,
    verify_mfa_code,
    verify_password,
)
from app.database import get_db
from app.engines.ad_ldap import ActiveDirectoryEngine
from app.engines.scheduler import sync_scheduler_jobs
from app.models.dashboard import Workspace
from app.models.identity import (
    DirectoryConnection,
    User,
    UserProfile,
    UserRole,
    UserSession,
)
from app.seed.bootstrap import seed_core_catalog, seed_sample_bi_content

router = APIRouter(prefix="/auth", tags=["Authentication"])
_BOOTSTRAP_LOCK = threading.Lock()


class LoginRequest(BaseModel):
    username: str
    password: str
    auth_mode: str = "local"  # local | active_directory
    domain: Optional[str] = None
    mfa_code: Optional[str] = None


class BootstrapAdminRequest(BaseModel):
    username: str
    password: str
    email: str
    full_name: str = "Administrator"
    organization_name: str = "Enterprise Analytics"
    include_sample_data: bool = True
    setup_token: Optional[str] = None


class RefreshRequest(BaseModel):
    refresh_token: Optional[str] = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class MfaConfirmRequest(BaseModel):
    code: str


def _wants_bearer_tokens(request: Request) -> bool:
    return (request.headers.get("x-auth-mode") or "").strip().lower() == "bearer"


def _set_auth_cookies(
    response: Response,
    request: Request,
    access_token: str,
    refresh_token: str,
) -> str:
    secure = is_https_request(request)
    samesite = settings.COOKIE_SAMESITE if settings.COOKIE_SAMESITE in ("lax", "strict", "none") else "lax"
    csrf_token = secrets.token_urlsafe(24)

    response.set_cookie(
        key=settings.ACCESS_COOKIE_NAME,
        value=access_token,
        httponly=True,
        secure=secure,
        samesite=samesite,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )
    response.set_cookie(
        key=settings.REFRESH_COOKIE_NAME,
        value=refresh_token,
        httponly=True,
        secure=secure,
        samesite=samesite,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path=f"{settings.API_PREFIX}/auth",
    )
    response.set_cookie(
        key=settings.CSRF_COOKIE_NAME,
        value=csrf_token,
        httponly=False,
        secure=secure,
        samesite=samesite,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/",
    )
    return csrf_token


def _clear_auth_cookies(response: Response, request: Request) -> None:
    secure = is_https_request(request)
    samesite = settings.COOKIE_SAMESITE if settings.COOKIE_SAMESITE in ("lax", "strict", "none") else "lax"
    response.delete_cookie(settings.ACCESS_COOKIE_NAME, path="/", samesite=samesite, secure=secure)
    response.delete_cookie(settings.REFRESH_COOKIE_NAME, path=f"{settings.API_PREFIX}/auth", samesite=samesite, secure=secure)
    response.delete_cookie(settings.CSRF_COOKIE_NAME, path="/", samesite=samesite, secure=secure)


@router.get("/status")
def auth_status(db: Session = Depends(get_db)):
    user_count = db.query(User).count()
    return {
        "needs_initial_setup": user_count == 0,
        "demo_mode": bool(settings.BI_SEED_DEMO_USERS and settings.ENVIRONMENT != "production"),
        "setup_token_required": bool(
            user_count == 0 and (settings.BI_INITIAL_SETUP_TOKEN or settings.ENVIRONMENT == "production")
        ),
        "environment": settings.ENVIRONMENT,
    }


@router.post("/bootstrap-admin")
def bootstrap_admin(
    body: BootstrapAdminRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    ip = get_client_ip(request)
    rate_limiter.check(f"bootstrap:{ip}", limit=10, window_seconds=60)
    now = datetime.now(timezone.utc)

    with _BOOTSTRAP_LOCK:
        # Acquire DB-level advisory lock on PostgreSQL so multi-worker/replica bootstraps cannot race
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(72839104)"))

        if db.query(User).count() > 0:
            raise HTTPException(status_code=403, detail="Initial setup already completed")

        if settings.ENVIRONMENT == "production" and not settings.BI_INITIAL_SETUP_TOKEN:
            raise HTTPException(
                status_code=403,
                detail="Initial admin bootstrap requires BI_INITIAL_SETUP_TOKEN in production",
            )

        if settings.BI_INITIAL_SETUP_TOKEN:
            provided = (body.setup_token or request.headers.get("x-setup-token") or "").strip()
            if not provided or not secrets.compare_digest(provided, settings.BI_INITIAL_SETUP_TOKEN):
                raise HTTPException(status_code=403, detail="Invalid or missing initial setup token")

        uname = body.username.strip()
        if not re.match(r"^[A-Za-z0-9._-]{3,64}$", uname):
            raise HTTPException(status_code=400, detail="Username must be 3-64 alphanumeric, dot, underscore, or dash characters")
        if "@" not in body.email:
            raise HTTPException(status_code=400, detail="Valid email address is required")
        validate_password_strength(body.password, allow_demo_passwords=False)

        try:
            org, roles, theme_objs = seed_core_catalog(db)
            if body.organization_name.strip():
                org.name = body.organization_name.strip()[:200]

            admin_user = User(
                organization_id=org.id,
                username=uname,
                email=body.email.strip(),
                password_hash=hash_password(body.password),
                auth_source="local",
                is_active=True,
                last_login_at=now,
            )
            db.add(admin_user)
            db.flush()
            db.add(
                UserProfile(
                    user_id=admin_user.id,
                    full_name=body.full_name.strip() or uname,
                    department="Administration",
                    org_unit="Administration",
                    region="All",
                )
            )
            db.add(UserRole(user_id=admin_user.id, role_id=roles["super_admin"].id))

            if not db.query(Workspace).filter(Workspace.organization_id == org.id).first():
                db.add(
                    Workspace(
                        organization_id=org.id,
                        name="Executive Intelligence Workspace",
                        description="Primary BI workspace",
                        created_by=admin_user.id,
                    )
                )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(status_code=403, detail="Initial setup already completed") from exc

        if body.include_sample_data:
            seed_sample_bi_content(
                db,
                org=org,
                owner_user=admin_user,
                theme_objs=theme_objs,
                scoped_role=None,
                rls_group=None,
            )
            try:
                sync_scheduler_jobs()
            except Exception:
                pass

        access_token, jti, _ = create_access_token(
            admin_user.id, admin_user.username, admin_user.organization_id, admin_user.permission_version
        )
        refresh_tok, refresh_exp = create_refresh_token(admin_user.id, admin_user.organization_id, jti)
        db.add(
            UserSession(
                user_id=admin_user.id,
                jti=jti,
                ip_address=ip,
                user_agent=request.headers.get("user-agent", "")[:280],
                expires_at=refresh_exp,
            )
        )
        record_audit(
            db, org.id, "initial_admin_bootstrapped", "user", status="success",
            user_id=admin_user.id, username=admin_user.username, resource_id=admin_user.id, ip_address=ip,
            details={"include_sample_data": body.include_sample_data}
        )
        db.commit()

        eff = permission_resolver.get_effective_permissions_summary(db, admin_user)
        user_payload = {
            "id": admin_user.id,
            "username": admin_user.username,
            "email": admin_user.email,
            "organization_id": admin_user.organization_id,
            "auth_source": admin_user.auth_source,
            "mfa_enabled": admin_user.mfa_enabled,
            "full_name": admin_user.profile.full_name if admin_user.profile else admin_user.username,
            "department": admin_user.profile.department if admin_user.profile else None,
            "region": admin_user.profile.region if admin_user.profile else None,
            "locale": admin_user.profile.locale if admin_user.profile else "en",
            "roles": eff["roles"],
            "is_super_admin": eff["is_super_admin"],
        }

        if _wants_bearer_tokens(request):
            return {
                "mfa_required": False,
                "auth_mode": "bearer",
                "access_token": access_token,
                "refresh_token": refresh_tok,
                "token_type": "bearer",
                "user": user_payload,
                "effective_permissions": eff,
            }

        csrf_token = _set_auth_cookies(response, request, access_token, refresh_tok)
        return {
            "mfa_required": False,
            "auth_mode": "cookie",
            "csrf_token": csrf_token,
            "token_type": "cookie",
            "user": user_payload,
            "effective_permissions": eff,
        }


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = get_client_ip(request)
    rate_limiter.check(f"login_ip:{ip}", limit=settings.RATE_LIMIT_LOGIN_PER_MIN, window_seconds=60)
    rate_limiter.check(f"login_user:{ip}:{(body.username or '').lower()}", limit=15, window_seconds=60)
    now = datetime.now(timezone.utc)

    if body.auth_mode in ("active_directory", "ad"):
        conn_q = db.query(DirectoryConnection).filter(DirectoryConnection.is_active == True)
        if body.domain:
            conn_q = conn_q.filter(DirectoryConnection.domain_name == body.domain)
        matching_conns = conn_q.all()
        if not matching_conns:
            raise HTTPException(status_code=400, detail="No active Active Directory connection configured")
        if len(matching_conns) > 1 and not body.domain:
            raise HTTPException(status_code=400, detail="Multiple directory domains active; specify domain")
        ad_conn = matching_conns[0]

        ad_profile = ActiveDirectoryEngine.authenticate_domain_user(ad_conn, body.username, body.password)
        if not ad_profile:
            record_audit(
                db, ad_conn.organization_id, "ad_login_failed", "user", status="denied",
                username=body.username, ip_address=ip, details={"domain": ad_conn.domain_name}
            )
            raise HTTPException(status_code=401, detail="Invalid domain credentials or disabled AD account")

        if ad_conn.sync_policy == "on_login" or not db.query(User).filter(
            User.organization_id == ad_conn.organization_id,
            User.username == ad_profile["sAMAccountName"],
        ).first():
            ActiveDirectoryEngine.sync_directory(db, ad_conn, sync_type="on_login")

        user = db.query(User).filter(
            User.organization_id == ad_conn.organization_id,
            User.username == ad_profile["sAMAccountName"],
        ).first()
        if not user or not user.is_active:
            raise HTTPException(status_code=403, detail="Directory user is disabled")
    else:
        user = db.query(User).filter(User.username == body.username).first()
        if not user:
            raise HTTPException(status_code=401, detail="Invalid username or password")

        if user.auth_source == "active_directory":
            raise HTTPException(status_code=400, detail="Use Active Directory login mode for domain users")

        if not user.is_active:
            raise HTTPException(status_code=403, detail="Account is deactivated")

        if user.expires_at:
            exp = user.expires_at if user.expires_at.tzinfo else user.expires_at.replace(tzinfo=timezone.utc)
            if exp < now:
                raise HTTPException(status_code=403, detail="Account has expired")

        if user.is_locked:
            lu = user.locked_until.replace(tzinfo=timezone.utc) if user.locked_until and not user.locked_until.tzinfo else user.locked_until
            if lu and lu > now:
                raise HTTPException(status_code=403, detail="Account is locked due to repeated failed attempts")
            user.is_locked = False
            user.failed_login_attempts = 0

        if not verify_password(body.password, user.password_hash):
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= settings.MAX_FAILED_LOGINS:
                user.is_locked = True
                user.locked_until = now + timedelta(minutes=settings.LOCKOUT_MINUTES)
            db.commit()
            record_audit(
                db, user.organization_id, "login_failed", "user", status="denied",
                user_id=user.id, username=user.username, ip_address=ip,
                details={"failed_attempts": user.failed_login_attempts}
            )
            raise HTTPException(status_code=401, detail="Invalid username or password")

        user.failed_login_attempts = 0

    eff = permission_resolver.get_effective_permissions_summary(db, user)
    if (
        settings.ENVIRONMENT == "production"
        and settings.BI_REQUIRE_ADMIN_MFA_IN_PROD
        and eff["is_super_admin"]
        and not user.mfa_enabled
    ):
        raise HTTPException(status_code=403, detail="Administrator accounts require MFA to be enabled in production")

    if user.mfa_enabled:
        if not body.mfa_code:
            return {"mfa_required": True, "user_id": user.id}
        secret = decrypt_secret(user.mfa_secret_encrypted or "")
        if not verify_mfa_code(secret, body.mfa_code):
            record_audit(
                db, user.organization_id, "mfa_failed", "user", status="denied",
                user_id=user.id, username=user.username, ip_address=ip
            )
            raise HTTPException(status_code=401, detail="Invalid MFA code")

    access_token, jti, _ = create_access_token(
        user.id, user.username, user.organization_id, user.permission_version
    )
    refresh_token, refresh_exp = create_refresh_token(user.id, user.organization_id, jti)
    user.last_login_at = now
    db.add(
        UserSession(
            user_id=user.id,
            jti=jti,
            ip_address=ip,
            user_agent=request.headers.get("user-agent", "")[:280],
            expires_at=refresh_exp,
        )
    )
    db.commit()

    record_audit(
        db, user.organization_id, "login", "user", status="success",
        user_id=user.id, username=user.username, resource_id=user.id, ip_address=ip,
        details={"auth_mode": body.auth_mode}
    )

    user_payload = {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "organization_id": user.organization_id,
        "auth_source": user.auth_source,
        "mfa_enabled": user.mfa_enabled,
        "full_name": user.profile.full_name if user.profile else user.username,
        "department": user.profile.department if user.profile else None,
        "region": user.profile.region if user.profile else None,
        "locale": user.profile.locale if user.profile else "en",
        "roles": eff["roles"],
        "is_super_admin": eff["is_super_admin"],
    }

    if _wants_bearer_tokens(request):
        return {
            "mfa_required": False,
            "auth_mode": "bearer",
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "user": user_payload,
            "effective_permissions": eff,
        }

    csrf_token = _set_auth_cookies(response, request, access_token, refresh_token)
    return {
        "mfa_required": False,
        "auth_mode": "cookie",
        "csrf_token": csrf_token,
        "token_type": "cookie",
        "user": user_payload,
        "effective_permissions": eff,
    }


@router.post("/refresh")
def refresh_token(
    request: Request,
    response: Response,
    body: Optional[RefreshRequest] = None,
    db: Session = Depends(get_db),
):
    ip = get_client_ip(request)
    rate_limiter.check(f"refresh:{ip}", limit=60, window_seconds=60)

    has_body_refresh = bool(body and body.refresh_token)
    raw_refresh = (body.refresh_token if has_body_refresh else None) or request.cookies.get(
        settings.REFRESH_COOKIE_NAME
    )
    if not raw_refresh:
        raise HTTPException(status_code=401, detail="Refresh token required")

    # Enforce CSRF when relying on refresh cookie without explicit body token
    if not has_body_refresh and request.cookies.get(settings.REFRESH_COOKIE_NAME):
        csrf_cookie = request.cookies.get(settings.CSRF_COOKIE_NAME) or ""
        csrf_header = request.headers.get("x-csrf-token") or ""
        if not csrf_cookie or not csrf_header or not secrets.compare_digest(csrf_cookie, csrf_header):
            raise HTTPException(status_code=403, detail="CSRF validation failed")

    try:
        payload = decode_token(raw_refresh)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid token type")

    user_id = payload.get("sub")
    old_jti = payload.get("jti")
    if not user_id or not old_jti:
        raise HTTPException(status_code=401, detail="Malformed refresh token")

    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active or user.is_locked:
        raise HTTPException(status_code=401, detail="User inactive or locked")

    sess = (
        db.query(UserSession)
        .filter(UserSession.jti == old_jti, UserSession.user_id == user.id)
        .first()
    )
    if not sess:
        raise HTTPException(status_code=401, detail="Session not found")

    now = datetime.now(timezone.utc)
    if sess.expires_at:
        sexp = sess.expires_at if sess.expires_at.tzinfo else sess.expires_at.replace(tzinfo=timezone.utc)
        if sexp < now:
            sess.is_revoked = True
            db.commit()
            raise HTTPException(status_code=401, detail="Refresh session expired")

    # Atomic compare-and-swap rotation prevents concurrent refresh token replay races
    updated = (
        db.query(UserSession)
        .filter(
            UserSession.id == sess.id,
            UserSession.user_id == user.id,
            UserSession.is_revoked == False,
        )
        .update({"is_revoked": True}, synchronize_session=False)
    )
    if updated == 0:
        # Refresh token reuse / concurrent replay detected -> revoke all active sessions for safety!
        db.query(UserSession).filter(UserSession.user_id == user.id, UserSession.is_revoked == False).update(
            {"is_revoked": True}, synchronize_session=False
        )
        db.commit()
        record_audit(
            db, user.organization_id, "refresh_token_reuse_detected", "user", status="denied",
            user_id=user.id, username=user.username, ip_address=ip
        )
        raise HTTPException(status_code=401, detail="Refresh token has already been revoked")

    access_token, new_jti, _ = create_access_token(
        user.id, user.username, user.organization_id, user.permission_version
    )
    new_refresh, new_refresh_exp = create_refresh_token(user.id, user.organization_id, new_jti)
    db.add(
        UserSession(
            user_id=user.id,
            jti=new_jti,
            ip_address=ip,
            user_agent=request.headers.get("user-agent", "")[:280],
            expires_at=new_refresh_exp,
        )
    )
    db.commit()

    if has_body_refresh or _wants_bearer_tokens(request):
        return {
            "auth_mode": "bearer",
            "access_token": access_token,
            "refresh_token": new_refresh,
            "token_type": "bearer",
        }

    csrf_token = _set_auth_cookies(response, request, access_token, new_refresh)
    return {
        "auth_mode": "cookie",
        "csrf_token": csrf_token,
        "token_type": "cookie",
    }


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    jti = getattr(request.state, "current_jti", None)
    if jti:
        sess = db.query(UserSession).filter(UserSession.jti == jti, UserSession.user_id == user.id).first()
        if sess:
            sess.is_revoked = True
    record_audit(
        db, user.organization_id, "logout", "user", status="success",
        user_id=user.id, username=user.username, resource_id=user.id, ip_address=get_client_ip(request)
    )
    db.commit()
    _clear_auth_cookies(response, request)
    return {"status": "logged_out"}


@router.post("/logout-all")
def logout_all_sessions(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    revoked_count = (
        db.query(UserSession)
        .filter(UserSession.user_id == user.id, UserSession.is_revoked == False)
        .update({"is_revoked": True})
    )
    record_audit(
        db, user.organization_id, "logout_all_sessions", "user", status="success",
        user_id=user.id, username=user.username, resource_id=user.id,
        ip_address=get_client_ip(request), details={"revoked": revoked_count}
    )
    db.commit()
    _clear_auth_cookies(response, request)
    return {"status": "logged_out_all", "revoked_sessions": revoked_count}


@router.get("/sessions")
def list_active_sessions(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    current_jti = getattr(request.state, "current_jti", None)
    now = datetime.now(timezone.utc)
    rows = (
        db.query(UserSession)
        .filter(UserSession.user_id == user.id, UserSession.is_revoked == False)
        .order_by(UserSession.created_at.desc())
        .all()
    )
    out = []
    for s in rows:
        exp = s.expires_at if s.expires_at.tzinfo else s.expires_at.replace(tzinfo=timezone.utc)
        if exp < now:
            continue
        out.append({
            "id": s.id,
            "ip_address": s.ip_address,
            "user_agent": s.user_agent,
            "created_at": s.created_at,
            "expires_at": s.expires_at,
            "is_current": s.jti == current_jti,
        })
    return out


@router.delete("/sessions/{session_id}")
def revoke_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sess = (
        db.query(UserSession)
        .filter(UserSession.id == session_id, UserSession.user_id == user.id)
        .first()
    )
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    sess.is_revoked = True
    db.commit()
    return {"status": "revoked", "id": session_id}


@router.get("/me")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    eff = permission_resolver.get_effective_permissions_summary(db, user)
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "organization_id": user.organization_id,
        "auth_source": user.auth_source,
        "mfa_enabled": user.mfa_enabled,
        "last_login_at": user.last_login_at,
        "profile": {
            "full_name": user.profile.full_name if user.profile else user.username,
            "department": user.profile.department if user.profile else None,
            "org_unit": user.profile.org_unit if user.profile else None,
            "region": user.profile.region if user.profile else None,
            "locale": user.profile.locale if user.profile else "en",
            "timezone": user.profile.timezone if user.profile else "UTC",
        },
        "effective_permissions": eff,
    }


@router.post("/change-password")
def change_password(
    body: ChangePasswordRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.auth_source != "local":
        raise HTTPException(status_code=400, detail="External directory passwords cannot be modified here")
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password incorrect")
    validate_password_strength(body.new_password, allow_demo_passwords=False)
    user.password_hash = hash_password(body.new_password)
    current_jti = getattr(request.state, "current_jti", None)
    # Revoke all other active sessions on password change
    q = db.query(UserSession).filter(UserSession.user_id == user.id, UserSession.is_revoked == False)
    if current_jti:
        q = q.filter(UserSession.jti != current_jti)
    q.update({"is_revoked": True})
    record_audit(
        db, user.organization_id, "password_changed", "user", status="success",
        user_id=user.id, username=user.username, resource_id=user.id, ip_address=get_client_ip(request)
    )
    db.commit()
    return {"status": "password_updated"}


@router.post("/mfa/setup")
def mfa_setup(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    secret = generate_mfa_secret()
    user.mfa_secret_encrypted = encrypt_secret(secret)
    db.commit()
    uri = get_mfa_provisioning_uri(user.username, secret)
    return {"secret": secret, "provisioning_uri": uri}


@router.post("/mfa/enable")
def mfa_enable(
    body: MfaConfirmRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    secret = decrypt_secret(user.mfa_secret_encrypted or "")
    if not verify_mfa_code(secret, body.code):
        raise HTTPException(status_code=400, detail="Invalid verification code")
    user.mfa_enabled = True
    db.commit()
    return {"mfa_enabled": True}


@router.post("/mfa/disable")
def mfa_disable(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    user.mfa_enabled = False
    user.mfa_secret_encrypted = None
    db.commit()
    return {"mfa_enabled": False}
