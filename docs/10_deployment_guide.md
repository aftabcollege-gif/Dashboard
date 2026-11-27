# Deliverable 10 — Deployment Guide (Stage 4 Production Hardened)

The platform supports **Zero-Config Local Development** (`docker-compose.dev.yml` or local Python/Node), **Production Container Deployment** (PostgreSQL 16 + authenticated Redis 7 + Alembic Migration Job + DuckDB + FastAPI + Next.js 16), and **Standalone Offline Windows x64 Desktop Installation** (`winsetup/`).

---

## Option A — Production Deployment via Docker Compose

### 1. Configure Environment
```bash
cp .env.example .env
# Generate unique secrets (NEVER leave placeholder strings in .env):
#   openssl rand -hex 32                     -> BI_JWT_SECRET, BI_INITIAL_SETUP_TOKEN, REDIS_PASSWORD, POSTGRES_PASSWORD
#   python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" -> BI_CRED_KEY
```

### 2. Launch Full Stack
```bash
docker compose up --build -d
```

Services started by `docker-compose.yml`:
1. `bi_app_db` (`postgres:16-alpine` on port `5432`) — durable primary metadata database.
2. `bi_cache_redis` (`redis:7-alpine` with `--requirepass ${REDIS_PASSWORD}`) — permission-keyed result cache, atomic Lua rate limiting, and scheduler leader election.
3. `bi_db_migrate` (runs `python -m app.db_migrate`) — explicitly upgrades PostgreSQL to Alembic `head` (`0003_stage4_security_reliability`) before the API starts; production startup refuses `BI_AUTO_CREATE_SCHEMA=true` or behind-head revisions.
4. `bi_api_server` (FastAPI + Uvicorn `--workers 1` + locked DuckDB Warehouse + APScheduler on `127.0.0.1:8000`) — mounts persistent volumes `bi_warehouse`, `bi_uploads`, `bi_exports`.
5. `bi_web_ui` (Next.js 16 production server on `127.0.0.1:3000`) — proxies `/api/*` directly to `http://backend:8000/api/*` via `BACKEND_INTERNAL_URL`.

### 3. Verify Health & Complete First-Run Setup
```bash
curl http://127.0.0.1:8000/api/v1/health
curl http://127.0.0.1:3000/api/v1/health
```
Open `http://127.0.0.1:3000` (behind your TLS reverse proxy configured in `BI_CORS_ORIGINS`), enter your `BI_INITIAL_SETUP_TOKEN`, and create the initial Super Administrator account. Demo users are strictly disabled (`BI_SEED_DEMO_USERS=false`).

---

## Option B — Standalone Offline Windows x64 Desktop Installer (`v1.1.0`)

Build on Windows (`winsetup/build.ps1`) or download the CI release artifact `NexusBI-Enterprise-v1.1.0-x64-Setup.exe`:
- Bundles embedded Python + FastAPI + locked DuckDB + Next.js 16 standalone + Node.js `web_gateway.mjs` + `.NET` `NexusBI.Launcher.exe` (zero external Python, Node.js, or Docker required on the target machine).
- Runs in strict production security mode (`ENVIRONMENT=production`, `BI_SEED_DEMO_USERS=0`, DPAPI-encrypted `BI_JWT_SECRET` & `BI_CRED_KEY`, one-time `BI_INITIAL_SETUP_TOKEN`, `HttpOnly` loopback cookies + `X-CSRF-Token`).
- Verified end-to-end by `winsetup/tests/verify_windows_bundle.ps1` and `winsetup/tests/test_desktop_security_e2e.py`.

---

## Option C — Native / Bare-Metal Linux Deployment

```bash
cd /home/user/Dashboard/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export ENVIRONMENT="production"
export BI_SEED_DEMO_USERS="false"
export BI_REQUIRE_GENERATED_SECRETS="true"
export BI_AUTO_CREATE_SCHEMA="false"
export DATABASE_URL="postgresql+psycopg2://bi_admin:STRONG_PASS@127.0.0.1:5432/bi_platform"
export REDIS_URL="redis://:STRONG_REDIS_PASS@127.0.0.1:6379/0"
export BI_JWT_SECRET="..."
export BI_CRED_KEY="..."
export BI_INITIAL_SETUP_TOKEN="..."
python -m app.db_migrate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```
*Note: Single worker prevents DuckDB exclusive write-lock contention; APScheduler leader election + DB lease fencing protect background schedules.*

---

## Option D — Deploying the Frontend via GitHub → Vercel (`frontend/`)

1. Push to GitHub (`aftabcollege-gif/Dashboard`).
2. In Vercel (`https://vercel.com/new`), import `aftabcollege-gif/Dashboard`:
   - **Root Directory**: `frontend` (`frontend/vercel.json` is pre-configured for Next.js 16)
   - **Build Command**: `npm run build`
   - **Environment Variable**: Set `BACKEND_INTERNAL_URL` to the public HTTPS URL of your hosted FastAPI + DuckDB backend service (`backend/Dockerfile` on port `8000`). If `BACKEND_INTERNAL_URL` is unset on a Vercel preview, `frontend` cleanly activates interactive Demo Mode (`vercelDemoApi`).
