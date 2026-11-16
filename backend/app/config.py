import os
from pathlib import Path
from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
(DATA_DIR / "uploads").mkdir(parents=True, exist_ok=True)
(DATA_DIR / "exports").mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    APP_NAME: str = "Enterprise Intelligence BI Platform"
    API_PREFIX: str = "/api/v1"
    ENVIRONMENT: str = "development"

    # Layer 1: Application Database (PostgreSQL/Neon in prod, SQLite WAL locally)
    DATABASE_URL: str = (
        os.environ.get("DATABASE_URL")
        or os.environ.get("POSTGRES_URL")
        or f"sqlite:///{DATA_DIR / 'app_metadata.db'}"
    )

    # Layer 2: Analytical Data Warehouse (DuckDB)
    WAREHOUSE_PATH: str = str(DATA_DIR / "warehouse.duckdb")

    # Sample Operational ERP SQLite source for live connector + CDC/Incremental ETL demos
    SAMPLE_ERP_DB_PATH: str = str(DATA_DIR / "sample_erp_source.sqlite")

    # Layer 3: Cache (Optional Redis URL; falls back cleanly to in-process LRU)
    REDIS_URL: str | None = None
    CACHE_TTL_SECONDS: int = 300

    # Security Keys (override via env in production)
    JWT_SECRET_KEY: str = os.environ.get(
        "BI_JWT_SECRET", "dev-only-change-in-prod-enterprise-bi-jwt-secret-key-987654321"
    )
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Fernet key for encrypting DB source credentials & AD bind accounts (base64 32-byte)
    CREDENTIAL_ENCRYPTION_KEY: str = os.environ.get(
        "BI_CRED_KEY", "Q2hhbmdlVGhpc0tleUluUHJvZEVudmlyb25tZW50MzI="
    )

    # Security Lockout & Query Guardrails
    MAX_FAILED_LOGINS: int = 5
    LOCKOUT_MINUTES: int = 15
    QUERY_TIMEOUT_SECONDS: float = 15.0
    QUERY_MAX_CONCURRENCY: int = 8
    QUERY_DEFAULT_LIMIT: int = 5000

    # Uploads & Exports
    UPLOADS_DIR: str = str(DATA_DIR / "uploads")
    EXPORTS_DIR: str = str(DATA_DIR / "exports")

    # Optional LLM Key for AI enhancements (deterministic BI AI works out of box without it)
    OPENAI_API_KEY: str | None = None


settings = Settings()
