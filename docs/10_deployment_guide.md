# Deliverable 10 — Deployment Guide

The platform supports **Zero-Config Local Development** (SQLite WAL + DuckDB + In-Memory/Disk Cache) and **Production Container Deployment** (PostgreSQL 16 + Redis 7 + DuckDB + FastAPI + Next.js).

---

## Option A — Production Deployment via Docker Compose

### 1. Configure Environment
```bash
cp .env.example .env
# Edit .env to set strong SECRET_KEY, CREDENTIAL_ENCRYPTION_KEY (Fernet base64), POSTGRES password, and AD LDAPS parameters.
```

### 2. Launch Full Stack
```bash
docker compose up --build -d
```

Services started by `docker-compose.yml`:
1. `bi_app_db` (`postgres:16-alpine` on port `5432`) — automatically executes `backend/migrations/postgres_schema.sql`, creating all tables, composite indexes, and range-partitioned log tables (`audit_logs`, `widget_load_logs`, `query_execution_logs`).
2. `bi_cache_redis` (`redis:7-alpine` on port `6379`) — Layer 3 LRU-evicting result cache (`512MB`).
3. `bi_api_server` (FastAPI + Uvicorn + DuckDB Warehouse + APScheduler on port `8000`) — mounts persistent volumes `bi_warehouse`, `bi_uploads`, `bi_exports`.
4. `bi_web_ui` (Next.js 14 production server on port `3000`) — proxies `/api/*` directly to `http://backend:8000/api/*` via `BACKEND_INTERNAL_URL`.

### 3. Verify Health
```bash
curl http://localhost:8000/health
curl http://localhost:3000/api/v1/health
```

---

## Option B — Native / Bare-Metal Linux Deployment

### Backend Service (Systemd / Uvicorn)
```bash
cd /home/user/Dashboard/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL="postgresql+psycopg2://bi_admin:Pass@127.0.0.1:5432/bi_platform"
export REDIS_URL="redis://127.0.0.1:6379/0"
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
*Note: Because DuckDB uses an exclusive file writer lock per database file, run DuckDB inside a single dedicated FastAPI worker process or point multiple read replicas at materialized Parquet/PostgreSQL exports if scaling horizontally beyond 1 API node.*

### Frontend Service
```bash
cd /home/user/Dashboard/frontend
npm ci
npm run build
BACKEND_INTERNAL_URL=http://127.0.0.1:8000 npm run start
```

---

## Option C — Deploying the Frontend via GitHub → Vercel (`frontend/`)

1. Push branch `arena/6af8ec18-dashboard` (or merge to `main`) to `github.com/aftabcollege-gif/Dashboard`.
2. In Vercel (`https://vercel.com/new`), import `aftabcollege-gif/Dashboard`:
   - **Root Directory**: `frontend` (`frontend/vercel.json` is pre-configured for Next.js 14)
   - **Build Command**: `npm run build`
   - **Environment Variable**: Set `BACKEND_INTERNAL_URL` to the public HTTPS URL of your hosted FastAPI + DuckDB backend service (for example, Docker Compose / Fly.io / Railway / Render / Cloud VM running `backend/Dockerfile` on port `8000`).
3. Deploy — Next.js `rewrites` automatically proxy `/api/*` requests from Vercel edge/server to `BACKEND_INTERNAL_URL/api/*` with zero client-side CORS or `localhost` issues.

