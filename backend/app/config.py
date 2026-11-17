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
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Fernet key for encrypting DB source credentials & AD bind accounts (base64 32-byte)
    CREDENTIAL_ENCRYPTION_KEY: str = (
        os.environ.get("BI_CRED_KEY")
        or os.environ.get("CREDENTIAL_ENCRYPTION_KEY")
        or DEV_CRED_FALLBACK
    )

    # Secure Initial Bootstrapping Controls
    # In production/Windows standalone: no default demo accounts or dev secrets are active.
    BI_SEED_DEMO_USERS: bool = _env_bool("BI_SEED_DEMO_USERS", _ENV_NAME != "production")
    BI_REQUIRE_GENERATED_SECRETS: bool = _env_bool(
        "BI_REQUIRE_GENERATED_SECRETS", _ENV_NAME == "production"
    )
    BI_INITIAL_SETUP_TOKEN: str | None = os.environ.get("BI_INITIAL_SETUP_TOKEN") or None

    # Security Lockout & Query Guardrails
    MAX_FAILED_LOGINS: int = 5
    LOCKOUT_MINUTES: int = 15
    QUERY_TIMEOUT_SECONDS: float = 15.0
    QUERY_MAX_CONCURRENCY: int = 8
    QUERY_DEFAULT_LIMIT: int = 5000

    # Uploads & Exports
    UPLOADS_DIR: str = os.environ.get("UPLOADS_DIR") or os.environ.get("UPLOAD_DIR") or str(DATA_DIR / "uploads")
    EXPORTS_DIR: str = os.environ.get("EXPORTS_DIR") or os.environ.get("EXPORT_DIR") or str(DATA_DIR / "exports")

    # Optional LLM Key for AI enhancements (deterministic BI AI works out of box without it)
    OPENAI_API_KEY: str | None = os.environ.get("OPENAI_API_KEY") or None

    def validate_runtime_security(self) -> None:
        Path(self.UPLOADS_DIR).mkdir(parents=True, exist_ok=True)
        Path(self.EXPORTS_DIR).mkdir(parents=True, exist_ok=True)
        Path(self.WAREHOUSE_PATH).parent.mkdir(parents=True, exist_ok=True)
        if self.ENVIRONMENT == "production" or self.BI_REQUIRE_GENERATED_SECRETS:
            if not self.JWT_SECRET_KEY or self.JWT_SECRET_KEY == DEV_JWT_FALLBACK or len(self.JWT_SECRET_KEY) < 32:
                raise RuntimeError(
                    "Security policy violation: refusing to start with default/weak BI_JWT_SECRET in production."
                )
            if (
                not self.CREDENTIAL_ENCRYPTION_KEY
                or self.CREDENTIAL_ENCRYPTION_KEY == DEV_CRED_FALLBACK
                or len(self.CREDENTIAL_ENCRYPTION_KEY) < 32
            ):
                raise RuntimeError(
                    "Security policy violation: refusing to start with default/weak BI_CRED_KEY in production."
                )


settings = Settings()
