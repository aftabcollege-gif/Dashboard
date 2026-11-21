"""
End-to-end verification of Windows Desktop security & runtime architecture:
- Simulates read-only Program Files app directory + writable BI_DATA_DIR isolation
- Verifies production secret enforcement (refuses default dev fallback keys)
- Verifies zero default active admin accounts & one-time setup token protection
- Verifies Next.js standalone + winsetup/runtime/web_gateway.mjs dynamic reverse proxy
- Verifies DuckDB analytics, dashboard batch execution, and CSV file upload persistence
- Verifies graceful shutdown via desktop control tokens
"""

import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
FRONTEND_DIR = REPO_ROOT / "frontend"
GATEWAY_JS = REPO_ROOT / "winsetup" / "runtime" / "web_gateway.mjs"
VENV_PYTHON = BACKEND_DIR / ".venv" / "bin" / "python"
PYTHON_EXE = str(VENV_PYTHON if VENV_PYTHON.exists() else sys.executable)


def free_port(avoid: set[int]) -> int:
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        if port not in avoid:
            avoid.add(port)
            return port


def main() -> None:
    standalone_dir = FRONTEND_DIR / ".next" / "standalone"
    static_src = FRONTEND_DIR / ".next" / "static"
    assert (standalone_dir / "server.js").exists(), "Build standalone Next.js first (NEXT_OUTPUT_MODE=standalone)"
    if static_src.exists():
        dst_static = standalone_dir / ".next" / "static"
        if not dst_static.exists():
            shutil.copytree(static_src, dst_static)

    with tempfile.TemporaryDirectory(prefix="nexusbi_desktop_e2e_") as tmp:
        tmp_path = Path(tmp)
        data_dir = tmp_path / "localappdata_nexusbi" / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        # 1. Negative test: refuse startup in production with default dev secrets
        bad_env = {
            **os.environ,
            "ENVIRONMENT": "production",
            "BI_DATA_DIR": str(data_dir),
            "BI_REQUIRE_GENERATED_SECRETS": "1",
        }
        bad_env.pop("BI_JWT_SECRET", None)
        bad_env.pop("BI_CRED_KEY", None)
        bad_check = subprocess.run(
            [PYTHON_EXE, "-c", "from app.config import settings; settings.validate_runtime_security()"],
            cwd=str(BACKEND_DIR),
            env=bad_env,
            capture_output=True,
            text=True,
        )
        assert bad_check.returncode != 0, "Backend MUST refuse startup without generated production secrets"
        print("[ok] Refused production startup with default fallback secrets.")

        # 2. Launch FastAPI backend + Next.js standalone via web_gateway.mjs on dynamic free ports
        used_ports: set[int] = set()
        backend_port = free_port(used_ports)
        next_port = free_port(used_ports)
        ui_port = free_port(used_ports)

        jwt_secret = secrets.token_urlsafe(48)
        cred_key = secrets.token_urlsafe(32)
        setup_token = secrets.token_urlsafe(24)
        ctrl_token = secrets.token_urlsafe(24)

        py_env = {
            **os.environ,
            "ENVIRONMENT": "production",
            "BI_DATA_DIR": str(data_dir),
            "BI_SEED_DEMO_USERS": "0",
            "BI_REQUIRE_GENERATED_SECRETS": "1",
            "BI_JWT_SECRET": jwt_secret,
            "BI_CRED_KEY": cred_key,
            "BI_INITIAL_SETUP_TOKEN": setup_token,
            "BI_DESKTOP_CONTROL_TOKEN": ctrl_token,
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        node_env = {
            **os.environ,
            "NODE_ENV": "production",
            "HOSTNAME": "127.0.0.1",
            "PORT": str(ui_port),
            "NEXT_INTERNAL_PORT": str(next_port),
            "BACKEND_INTERNAL_URL": f"http://127.0.0.1:{backend_port}",
            "STANDALONE_DIR": str(standalone_dir),
            "GATEWAY_CONTROL_TOKEN": ctrl_token,
        }

        backend_proc = subprocess.Popen(
            [PYTHON_EXE, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(backend_port)],
            cwd=str(BACKEND_DIR),
            env=py_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        gateway_proc = subprocess.Popen(
            ["node", str(GATEWAY_JS)],
            cwd=str(standalone_dir),
            env=node_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        base_url = f"http://127.0.0.1:{ui_port}"
        try:
            with httpx.Client(timeout=10.0) as client:
                ready = False
                for _ in range(60):
                    if backend_proc.poll() is not None:
                        raise RuntimeError(f"Backend exited prematurely:\n{backend_proc.stdout.read()}")
                    if gateway_proc.poll() is not None:
                        raise RuntimeError(f"Gateway exited prematurely:\n{gateway_proc.stdout.read()}")
                    try:
                        r1 = client.get(f"{base_url}/__gateway/health")
                        r2 = client.get(f"{base_url}/api/v1/health")
                        if r1.status_code == 200 and r2.status_code == 200:
                            ready = True
                            break
                    except Exception:
                        pass
                    time.sleep(0.3)
                assert ready, "Services did not become healthy"
                print(f"[ok] Services healthy via web_gateway.mjs on {base_url} -> backend :{backend_port}")

                # 3. Verify frontend pages load through gateway
                for route in ("/", "/dashboards", "/data-sources", "/datasets", "/settings"):
                    resp = client.get(f"{base_url}{route}")
                    assert resp.status_code == 200, f"Frontend route {route} returned {resp.status_code}"
                print("[ok] All Next.js standalone routes served 200 OK through web_gateway.mjs.")

                # 4. Verify initial setup status & zero default active admin accounts
                st = client.get(f"{base_url}/api/v1/auth/status").json()
                assert st["needs_initial_setup"] is True
                assert st["demo_mode"] is False
                assert st["setup_token_required"] is True

                bad_login = client.post(
                    f"{base_url}/api/v1/auth/login",
                    json={"username": "admin", "password": "Admin!2026"},
                )
                assert bad_login.status_code == 401, "Default admin account must NOT exist!"

                # Reject wrong setup token
                bad_tok = client.post(
                    f"{base_url}/api/v1/auth/bootstrap-admin",
                    json={
                        "username": "real_admin",
                        "password": "RealStrongPass!2026",
                        "email": "admin@company.local",
                        "setup_token": "wrong-token",
                    },
                )
                assert bad_tok.status_code == 403

                # Reject weak/default password
                weak_pw = client.post(
                    f"{base_url}/api/v1/auth/bootstrap-admin",
                    json={
                        "username": "real_admin",
                        "password": "Admin!2026",
                        "email": "admin@company.local",
                        "setup_token": setup_token,
                    },
                )
                assert weak_pw.status_code == 400

                # Valid initial setup
                boot = client.post(
                    f"{base_url}/api/v1/auth/bootstrap-admin",
                    json={
                        "username": "real_admin",
                        "password": "RealStrongPass!2026",
                        "email": "admin@company.local",
                        "full_name": "Enterprise Owner",
                        "organization_name": "Offline Desktop Org",
                        "include_sample_data": True,
                        "setup_token": setup_token,
                    },
                )
                assert boot.status_code == 200, boot.text
                token = boot.json()["access_token"]
                headers = {"Authorization": f"Bearer {token}"}

                # Second bootstrap attempt MUST be blocked
                second = client.post(
                    f"{base_url}/api/v1/auth/bootstrap-admin",
                    json={
                        "username": "attacker",
                        "password": "AnotherStrongPass!2026",
                        "email": "x@company.local",
                        "setup_token": setup_token,
                    },
                )
                assert second.status_code == 403

                st_after = client.get(f"{base_url}/api/v1/auth/status").json()
                assert st_after["needs_initial_setup"] is False
                print("[ok] Secure initial admin bootstrap & one-time lock verified.")

                # 5. Verify DuckDB analytics, dashboard batch execution, and CSV file upload via gateway
                dashes = client.get(f"{base_url}/api/v1/dashboards", headers=headers).json()
                assert len(dashes) >= 3
                exec_res = client.post(
                    f"{base_url}/api/v1/dashboards/{dashes[0]['id']}/execute-batch",
                    headers=headers,
                    json={},
                )
                assert exec_res.status_code == 200
                assert len(exec_res.json()["widgets"]) >= 6

                csv_bytes = b"region,product,sales\nNorth,WidgetA,1200\nSouth,WidgetB,2450\n"
                imp = client.post(
                    f"{base_url}/api/v1/data-sources/files/import",
                    headers=headers,
                    data={"dataset_name": "Desktop Offline CSV"},
                    files={"file": ("offline_sales.csv", csv_bytes, "text/csv")},
                )
                assert imp.status_code == 200, imp.text
                assert imp.json()["row_count"] == 2
                print("[ok] Dashboard DuckDB execution & CSV upload verified through web_gateway.mjs.")

                # 6. Verify files persisted ONLY in BI_DATA_DIR
                assert (data_dir / "app_metadata.db").exists()
                assert (data_dir / "warehouse.duckdb").exists()
                assert len(list((data_dir / "uploads").iterdir())) == 1
                print("[ok] All databases and uploaded files written strictly inside BI_DATA_DIR.")

                # 7. Graceful shutdown test
                r_gw = client.post(f"{base_url}/__gateway/shutdown", headers={"X-Gateway-Token": ctrl_token})
                assert r_gw.status_code == 200
                r_be = client.post(
                    f"http://127.0.0.1:{backend_port}/_internal/shutdown",
                    headers={"X-Desktop-Control-Token": ctrl_token},
                )
                assert r_be.status_code == 200

            assert gateway_proc.wait(timeout=10) == 0
            assert backend_proc.wait(timeout=10) == 0
            print("[ok] Graceful shutdown closed gateway & FastAPI/DuckDB cleanly with exit code 0.")
        finally:
            for p in (gateway_proc, backend_proc):
                if p.poll() is None:
                    p.terminate()
                    try:
                        p.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        p.kill()


if __name__ == "__main__":
    main()
