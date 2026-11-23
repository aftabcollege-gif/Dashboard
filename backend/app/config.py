import os
from pathlib import Path
from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parent.parent


def _resolve_data_dir() -> Path:
    raw = os.environ.get("BI_DATA_DIR")
    if raw:
        return Path(raw).expanduser().resolve()
    return BASE_DIR / "data"


DATA_DIR = _resolve_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)
(DATA_DIR / "uploads").mkdir(parents=True, exist_ok=True)
(DATA_DIR / "exports").mkdir(parents=True, exist_ok=True)

DEV_JWT_FALLBACK = "dev-only-change-in-prod-enterprise-bi-jwt-secret-key-987654321"
DEV_CRED_FALLBACK = "Q2hhbmdlVGhpc0tleUluUHJvZEVudmlyb25tZW50MzI="


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


_ENV_NAME = os.environ.get("ENVIRONMENT", "development").strip().lower()


class Settings(BaseSettings):
    APP_NAME: str = "Enterprise Intelligence BI Platform"
    API_PREFIX: str = "/api/v1"
    ENVIRONMENT: str = _ENV_NAME

    # Layer 1: Application Database (PostgreSQL/Neon in prod, SQLite WAL locally)
    DATABASE_URL: str = (
        os.environ.get("DATABASE_URL")
        or os.environ.get("POSTGRES_URL")
        or f"sqlite:///{DATA_DIR / 'app_metadata.db'}"
    )

    # Layer 2: Analytical Data Warehouse (DuckDB)
    WAREHOUSE_PATH: str = (
        os.environ.get("WAREHOUSE_PATH")
        or os.environ.get("WAREHOUSE_DUCKDB_PATH")
        or str(DATA_DIR / "warehouse.duckdb")
    )

    # Sample Operational ERP SQLite source for live connector + CDC/Incremental ETL demos
    SAMPLE_ERP_DB_PATH: str = (
        os.environ.get("SAMPLE_ERP_DB_PATH")
        or str(DATA_DIR / "sample_erp_source.sqlite")
    )

    # Layer 3: Cache (Optional Redis URL; falls back cleanly to in-process LRU)
    REDIS_URL: str | None = os.environ.get("REDIS_URL") or None
    CACHE_TTL_SECONDS: int = int(os.environ.get("CACHE_TTL_SEC", "300"))

    # Security Keys (must be overridden in production / Windows standalone)
    JWT_SECRET_KEY: str = (
        os.environ.get("BI_JWT_SECRET")
        or os.environ.get("SECRET_KEY")
        or DEV_JWT_FALLBACK
    )
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "480"))
    REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.environ.get("REFRESH_TOKEN_EXPIRE_DAYS", "7"))

    # Fernet key for encrypting DB source credentials & AD bind accounts (base64 32-byte)
    CREDENTIAL_ENCRYPTION_KEY: str = (
        os.environ.get("BI_CRED_KEY")
        or os.environ.get("CREDENTIAL_ENCRYPTION_KEY")
        or DEV_CRED_FALLBACK
    )

    # Secure Cookie & CORS Policy
    CORS_ORIGINS: str = os.environ.get(
        "BI_CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000,http://localhost:3005,http://127.0.0.1:3005",
    )
    COOKIE_SECURE: str = os.environ.get("BI_COOKIE_SECURE", "auto").strip().lower()
    COOKIE_SAMESITE: str = os.environ.get("BI_COOKIE_SAMESITE", "lax").strip().lower()
    ACCESS_COOKIE_NAME: str = "bi_access_token"
    REFRESH_COOKIE_NAME: str = "bi_refresh_token"
    CSRF_COOKIE_NAME: str = "bi_csrf_token"

    # Secure Initial Bootstrapping Controls
    # In production/Windows standalone: no default demo accounts or dev secrets are active.
    BI_SEED_DEMO_USERS: bool = _env_bool("BI_SEED_DEMO_USERS", _ENV_NAME != "production")
    BI_REQUIRE_GENERATED_SECRETS: bool = _env_bool(
        "BI_REQUIRE_GENERATED_SECRETS", _ENV_NAME == "production"
    )
    AUTO_CREATE_SCHEMA: bool = _env_bool("BI_AUTO_CREATE_SCHEMA", _ENV_NAME != "production")
    BI_INITIAL_SETUP_TOKEN: str | None = os.environ.get("BI_INITIAL_SETUP_TOKEN") or None
    BI_REQUIRE_ADMIN_MFA_IN_PROD: bool = _env_bool("BI_REQUIRE_ADMIN_MFA_IN_PROD", False)

    # Security Lockout, Rate Limits & Query Guardrails
    MAX_FAILED_LOGINS: int = int(os.environ.get("MAX_FAILED_LOGINS", "5"))
    LOCKOUT_MINUTES: int = int(os.environ.get("LOCKOUT_MINUTES", "15"))
    RATE_LIMIT_ENABLED: bool = _env_bool("RATE_LIMIT_ENABLED", True)
    RATE_LIMIT_FAIL_CLOSED_PROD: bool = _env_bool("RATE_LIMIT_FAIL_CLOSED_PROD", True)
    RATE_LIMIT_LOGIN_PER_MIN: int = int(os.environ.get("RATE_LIMIT_LOGIN_PER_MIN", "30"))
    RATE_LIMIT_SQL_PER_MIN: int = int(os.environ.get("RATE_LIMIT_SQL_PER_MIN", "120"))
    RATE_LIMIT_WIDGET_PER_MIN: int = int(os.environ.get("RATE_LIMIT_WIDGET_PER_MIN", "180"))
    RATE_LIMIT_PUBLIC_PER_MIN: int = int(os.environ.get("RATE_LIMIT_PUBLIC_PER_MIN", "240"))

    TRUST_PROXY_HEADERS: bool = _env_bool("TRUST_PROXY_HEADERS", False)
    TRUSTED_PROXY_IPS: str = os.environ.get("TRUSTED_PROXY_IPS", "127.0.0.1,::1")

    QUERY_TIMEOUT_SECONDS: float = float(os.environ.get("QUERY_TIMEOUT_SECONDS", "15.0"))
    QUERY_MAX_CONCURRENCY: int = int(os.environ.get("QUERY_MAX_CONCURRENCY", "12"))
    QUERY_DEFAULT_LIMIT: int = int(os.environ.get("QUERY_DEFAULT_LIMIT", "5000"))
    MAX_QUERY_ROWS: int = int(os.environ.get("MAX_QUERY_ROWS", "10000"))
    MAX_EXTRACT_ROWS: int = int(os.environ.get("MAX_EXTRACT_ROWS", "100000"))
    EXTERNAL_QUERY_TIMEOUT_SECONDS: int = int(os.environ.get("EXTERNAL_QUERY_TIMEOUT_SECONDS", "15"))
    DUCKDB_MEMORY_LIMIT: str = os.environ.get("DUCKDB_MEMORY_LIMIT", "1GB")
    DUCKDB_THREADS: int = int(os.environ.get("DUCKDB_THREADS", "4"))

    # Background Scheduler & ETL Reliability
    BI_ENABLE_SCHEDULER: bool = _env_bool("BI_ENABLE_SCHEDULER", True)
    ETL_STALE_RUN_TIMEOUT_MINUTES: int = int(os.environ.get("ETL_STALE_RUN_TIMEOUT_MINUTES", "30"))
    ETL_RUN_TIMEOUT_SECONDS: int = int(os.environ.get("ETL_RUN_TIMEOUT_SECONDS", "120"))

    # LDAP TLS CA Certificate Path (optional custom CA bundle)
    LDAP_TLS_CA_CERT_FILE: str | None = os.environ.get("LDAP_TLS_CA_CERT_FILE") or None

    # Uploads, Exports & Multi-Tenant Quotas
    UPLOADS_DIR: str = os.environ.get("UPLOADS_DIR") or os.environ.get("UPLOAD_DIR") or str(DATA_DIR / "uploads")
    EXPORTS_DIR: str = os.environ.get("EXPORTS_DIR") or os.environ.get("EXPORT_DIR") or str(DATA_DIR / "exports")
    MAX_UPLOAD_BYTES: int = int(os.environ.get("MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
    MAX_UPLOAD_ROWS: int = int(os.environ.get("MAX_UPLOAD_ROWS", "250000"))
    MAX_UPLOAD_COLS: int = int(os.environ.get("MAX_UPLOAD_COLS", "200"))
    MAX_ZIP_UNCOMPRESSED_BYTES: int = int(os.environ.get("MAX_ZIP_UNCOMPRESSED_BYTES", str(120 * 1024 * 1024)))
    ORG_STORAGE_QUOTA_BYTES: int = int(os.environ.get("ORG_STORAGE_QUOTA_BYTES", str(512 * 1024 * 1024)))

    # Optional LLM Key for AI enhancements (deterministic BI AI works out of box without it)
    OPENAI_API_KEY: str | None = os.environ.get("OPENAI_API_KEY") or None

    def is_desktop_mode(self) -> bool:
        return bool(os.environ.get("BI_DESKTOP_CONTROL_TOKEN"))

    def get_cors_origins(self) -> list[str]:
        origins = [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip() and o.strip() != "*"]
        if not origins:
            return ["http://localhost:3000", "http://127.0.0.1:3000"]
        return origins

    def validate_runtime_security(self) -> None:
        Path(self.UPLOADS_DIR).mkdir(parents=True, exist_ok=True)
        Path(self.EXPORTS_DIR).mkdir(parents=True, exist_ok=True)
        Path(self.WAREHOUSE_PATH).parent.mkdir(parents=True, exist_ok=True)

        def _looks_like_placeholder(val: str) -> bool:
            low = (val or "").strip().lower()
            return any(p in low for p in ("replace_with", "changeme", "change-in-prod", "dev-only", "example"))

        if self.ENVIRONMENT == "production" or self.BI_REQUIRE_GENERATED_SECRETS:
            if (
                not self.JWT_SECRET_KEY
                or self.JWT_SECRET_KEY == DEV_JWT_FALLBACK
                or len(self.JWT_SECRET_KEY) < 32
                or _looks_like_placeholder(self.JWT_SECRET_KEY)
            ):
                raise RuntimeError(
                    "Security policy violation: refusing to start with default/weak BI_JWT_SECRET in production."
                )
            if (
                not self.CREDENTIAL_ENCRYPTION_KEY
                or self.CREDENTIAL_ENCRYPTION_KEY == DEV_CRED_FALLBACK
                or len(self.CREDENTIAL_ENCRYPTION_KEY) < 32
                or _looks_like_placeholder(self.CREDENTIAL_ENCRYPTION_KEY)
            ):
                raise RuntimeError(
                    "Security policy violation: refusing to start with default/weak BI_CRED_KEY in production."
                )

        if self.ENVIRONMENT == "production":
            if self.BI_SEED_DEMO_USERS:
                raise RuntimeError(
                    "Security policy violation: BI_SEED_DEMO_USERS must be disabled in production."
                )
            if not self.RATE_LIMIT_ENABLED:
                raise RuntimeError(
                    "Security policy violation: RATE_LIMIT_ENABLED must remain true in production."
                )
            if self.AUTO_CREATE_SCHEMA:
                raise RuntimeError(
                    "Security policy violation: BI_AUTO_CREATE_SCHEMA must be disabled in production; run Alembic migrations explicitly."
                )
            if self.BI_INITIAL_SETUP_TOKEN and (
                len(self.BI_INITIAL_SETUP_TOKEN.strip()) < 20
                or _looks_like_placeholder(self.BI_INITIAL_SETUP_TOKEN)
            ):
                raise RuntimeError(
                    "Security policy violation: BI_INITIAL_SETUP_TOKEN is too weak or contains placeholder text."
                )
            if self.REDIS_URL and _looks_like_placeholder(self.REDIS_URL):
                raise RuntimeError(
                    "Security policy violation: REDIS_URL contains placeholder credentials."
                )
            if self.TRUST_PROXY_HEADERS and any(
                tok.strip() in ("*", "0.0.0.0/0", "::/0") for tok in (self.TRUSTED_PROXY_IPS or "").split(",")
            ):
                raise RuntimeError(
                    "Security policy violation: TRUSTED_PROXY_IPS cannot contain wildcard/all-internet CIDR in production."
                )


settings = Settings()
