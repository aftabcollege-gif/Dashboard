import base64
import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
import bcrypt
import jwt
import pyotp
from cryptography.fernet import Fernet
from fastapi import HTTPException
from app.config import settings


COMMON_WEAK_PASSWORDS = {
    "Admin!2026",
    "Designer!2026",
    "Analyst!2026",
    "Viewer!2026",
    "Manager!2026",
    "Password123!",
    "ChangeMe!123",
    "1234567890Aa!",
}


def validate_password_strength(password: str, *, allow_demo_passwords: bool = False) -> None:
    if not password or len(password) < 10:
        raise HTTPException(status_code=400, detail="Password must be at least 10 characters long")
    if len(password) > 128:
        raise HTTPException(status_code=400, detail="Password length must not exceed 128 characters")
    if not re.search(r"[A-Z]", password) or not re.search(r"[a-z]", password):
        raise HTTPException(status_code=400, detail="Password must include both uppercase and lowercase letters")
    if not re.search(r"[0-9\W_]", password):
        raise HTTPException(status_code=400, detail="Password must include at least one number or symbol")
    if not allow_demo_passwords and password in COMMON_WEAK_PASSWORDS:
        raise HTTPException(status_code=400, detail="Common or default demo passwords cannot be used")


def _get_fernet() -> Fernet:
    raw_key = hashlib.sha256(settings.CREDENTIAL_ENCRYPTION_KEY.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(raw_key))


def encrypt_secret(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    return _get_fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt(rounds=10)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, password_hash: Optional[str]) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def create_access_token(
    user_id: str,
    username: str,
    organization_id: str,
    permission_version: int,
    jti: Optional[str] = None,
    expires_minutes: Optional[int] = None,
) -> tuple[str, str, datetime]:
    token_jti = jti or secrets.token_hex(16)
    exp = datetime.now(timezone.utc) + timedelta(
        minutes=expires_minutes or settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {
        "sub": user_id,
        "username": username,
        "org_id": organization_id,
        "perm_ver": permission_version,
        "jti": token_jti,
        "type": "access",
        "exp": exp,
        "iat": datetime.now(timezone.utc),
    }
    encoded = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return encoded, token_jti, exp


def create_refresh_token(user_id: str, organization_id: str, jti: str) -> tuple[str, datetime]:
    exp = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": user_id,
        "org_id": organization_id,
        "jti": jti,
        "type": "refresh",
        "exp": exp,
        "iat": datetime.now(timezone.utc),
    }
    encoded = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return encoded, exp


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


def generate_mfa_secret() -> str:
    return pyotp.random_base32()


def get_mfa_provisioning_uri(username: str, secret: str, issuer: str = "EnterpriseBI") -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=issuer)


def verify_mfa_code(secret: str, code: str) -> bool:
    if not secret or not code:
        return False
    totp = pyotp.TOTP(secret)
    return bool(totp.verify(code.strip(), valid_window=1))
