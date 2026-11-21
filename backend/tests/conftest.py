import os
import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

# Use isolated test databases inside backend/data/test_run/ so dev data stays clean
TEST_DATA_DIR = BACKEND_DIR / "data" / "test_run"
TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)
for fname in ("test_app.db", "test_warehouse.duckdb", "test_erp.sqlite"):
    p = TEST_DATA_DIR / fname
    if p.exists():
        p.unlink()

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DATA_DIR / 'test_app.db'}"
os.environ["WAREHOUSE_PATH"] = str(TEST_DATA_DIR / "test_warehouse.duckdb")
os.environ["SAMPLE_ERP_DB_PATH"] = str(TEST_DATA_DIR / "test_erp.sqlite")

from app.config import settings
settings.DATABASE_URL = os.environ["DATABASE_URL"]
settings.WAREHOUSE_PATH = os.environ["WAREHOUSE_PATH"]
settings.SAMPLE_ERP_DB_PATH = os.environ["SAMPLE_ERP_DB_PATH"]

from app.database import engine, SessionLocal, Base
from app.engines.warehouse import warehouse
warehouse.reopen(settings.WAREHOUSE_PATH)

from app.main import app


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def admin_headers(client):
    res = client.post("/api/v1/auth/login", json={"username": "admin", "password": "Admin!2026"})
    assert res.status_code == 200
    return {"Authorization": f"Bearer {res.json()['access_token']}"}
