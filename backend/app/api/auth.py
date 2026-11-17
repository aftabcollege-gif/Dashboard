import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.api.deps import get_client_ip, get_current_user
from app.config import settings
from app.core.audit import record_audit
from app.core.permissions import permission_resolver
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    decrypt_secret,
    encrypt_secret,
    generate_mfa_secret,
    get_mfa_provisioning_uri,
    hash_password,
    verify_mfa_code,
    verify_password,
)
from app.database import get_db
from app.engines.ad_ldap import ActiveDirectoryEngine
from app.engines.scheduler import sync_scheduler_jobs
from app.models.dashboard import Workspace
from app.models.identity import (
    DirectoryConnection,
    ExternalIdentity,
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
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class MfaConfirmRequest(BaseModel):
    code: str


def _validate_strong_password(password: str) -> None:
    if len(password) < 10:
        raise HTTPException(status_code=400, detail="Password must be at least 10 characters long")
    if not re.search(r"[A-Z]", password) or not re.search(r"[a-z]", password):
        raise HTTPException(status_code=400, detail="Password must include both uppercase and lowercase letters")
    if not re.search(r"[0-9\W_]", password):
        raise HTTPException(status_code=400, detail="Password must include at least one number or symbol")
    if password in ("Admin!2026", "Designer!2026", "Analyst!2026", "Viewer!2026", "Manager!2026"):
        raise HTTPException(status_code=400, detail="Default demo passwords cannot be used for initial setup")


@router.get("/status")
def auth_status(db: Session = Depends(get_db)):
    user_count = db.query(User).count()
    return {
        "needs_initial_setup": user_count == 0,
        "demo_mode": bool(settings.BI_SEED_DEMO_USERS and settings.ENVIRONMENT != "production"),
        "setup_token_required": bool(user_count == 0 and settings.BI_INITIAL_SETUP_TOKEN),
        "environment": settings.ENVIRONMENT,
    }


@router.post("/bootstrap-admin")
def bootstrap_admin(body: BootstrapAdminRequest, request: Request, db: Session = Depends(get_db)):
    ip = get_client_ip(request)
    now = datetime.now(timezone.utc)

    with _BOOTSTRAP_LOCK:
        if db.query(User).count() > 0:
            raise HTTPException(status_code=403, detail="Initial setup already completed")

        if settings.BI_INITIAL_SETUP_TOKEN:
            provided = (body.setup_token or request.headers.get("x-setup-token") or "").strip()
            if not provided or not secrets.compare_digest(provided, settings.BI_INITIAL_SETUP_TOKEN):
                raise HTTPException(status_code=403, detail="Invalid or missing initial setup token")

        uname = body.username.strip()
        if not re.match(r"^[A-Za-z0-9._-]{3,64}$", uname):
            raise HTTPException(status_code=400, detail="Username must be 3-64 alphanumeric, dot, underscore, or dash characters")
        if "@" not in body.email:
            raise HTTPException(status_code=400, detail="Valid email address is required")
        _validate_strong_password(body.password)

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

        access_token, jti, exp = create_access_token(
            admin_user.id, admin_user.username, admin_user.organization_id, admin_user.permission_version
        )
        refresh_tok = create_refresh_token(admin_user.id, admin_user.organization_id, jti)
        db.add(
            UserSession(
                user_id=admin_user.id,
                jti=jti,
                ip_address=ip,
                user_agent=request.headers.get("user-agent", "")[:280],
                expires_at=exp,
            )
        )
        record_audit(
            db, org.id, "initial_admin_bootstrapped", "user", status="success",
            user_id=admin_user.id, username=admin_user.username, resource_id=admin_user.id, ip_address=ip,
            details={"include_sample_data": body.include_sample_data}
        )
        db.commit()

        eff = permission_resolver.get_effective_permissions_summary(db, admin_user)
        return {
            "mfa_required": False,
            "access_token": access_token,
            "refresh_token": refresh_tok,
            "token_type": "bearer",
            "user": {
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
            },
            "effective_permissions": eff,
        }


@router.post("/login")
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    ip = get_client_ip(request)
    now = datetime.now(timezone.utc)

    if body.auth_mode in ("active_directory", "ad"):
        conn_q = db.query(DirectoryConnection).filter(DirectoryConnection.is_active == True)
        if body.domain:
            conn_q = conn_q.filter(DirectoryConnection.domain_name == body.domain)
        ad_conn = conn_q.first()
        if not ad_conn:
            raise HTTPException(status_code=400, detail="No active Active Directory connection configured")

        ad_profile = ActiveDirectoryEngine.authenticate_domain_user(ad_conn, body.username, body.password)
        if not ad_profile:
            record_audit(
                db, ad_conn.organization_id, "ad_login_failed", "user", status="denied",
                username=body.username, ip_address=ip, details={"domain": ad_conn.domain_name}
            )
            raise HTTPException(status_code=401, detail="Invalid domain credentials or disabled AD account")

        # Sync/on-login upsert without ever storing AD password
        if ad_conn.sync_policy == "on_login" or not db.query(User).filter(User.username == ad_profile["sAMAccountName"]).first():
            ActiveDirectoryEngine.sync_directory(db, ad_conn, sync_type="on_login")

        user = db.query(User).filter(User.username == ad_profile["sAMAccountName"]).first()
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

    # Check MFA if enabled
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

    access_token, jti, exp = create_access_token(
        user.id, user.username, user.organization_id, user.permission_version
    )
    refresh_token = create_refresh_token(user.id, user.organization_id, jti)
    user.last_login_at = now
    db.add(
        UserSession(
            user_id=user.id,
            jti=jti,
            ip_address=ip,
            user_agent=request.headers.get("user-agent", "")[:280],
            expires_at=exp,
        )
    )
    db.commit()

    record_audit(
        db, user.organization_id, "login", "user", status="success",
        user_id=user.id, username=user.username, resource_id=user.id, ip_address=ip,
        details={"auth_mode": body.auth_mode}
    )

    eff = permission_resolver.get_effective_permissions_summary(db, user)
    return {
        "mfa_required": False,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": {
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
        },
        "effective_permissions": eff,
    }


@router.post("/refresh")
def refresh_token(body: RefreshRequest, db: Session = Depends(get_db)):
    try:
        payload = decode_token(body.refresh_token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid token type")
    user = db.query(User).filter(User.id == payload.get("sub")).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User inactive")
    access_token, jti, exp = create_access_token(
        user.id, user.username, user.organization_id, user.permission_version
    )
    db.add(UserSession(user_id=user.id, jti=jti, expires_at=exp))
    db.commit()
    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/logout")
def logout(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    auth_hdr = request.headers.get("authorization", "")
    if auth_hdr.lower().startswith("bearer "):
        try:
            payload = decode_token(auth_hdr[7:])
            jti = payload.get("jti")
            if jti:
                sess = db.query(UserSession).filter(UserSession.jti == jti).first()
                if sess:
                    sess.is_revoked = True
        except Exception:
            pass
    record_audit(
        db, user.organization_id, "logout", "user", status="success",
        user_id=user.id, username=user.username, resource_id=user.id, ip_address=get_client_ip(request)
    )
    db.commit()
    return {"status": "logged_out"}


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
    body: ChangePasswordRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    if user.auth_source != "local":
        raise HTTPException(status_code=400, detail="External directory passwords cannot be modified here")
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password incorrect")
    user.password_hash = hash_password(body.new_password)
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
