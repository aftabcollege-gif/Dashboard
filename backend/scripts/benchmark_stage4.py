"""
Stage 4 reproducible benchmark suite (isolated temporary workspace; zero mutation to repo data).
Run from repo root: .venv/bin/python backend/scripts/benchmark_stage4.py
"""
import concurrent.futures
import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import pandas as pd

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="nexusbi_bench_") as tmp:
        tmp_path = Path(tmp)
        os.environ["ENVIRONMENT"] = "development"
        os.environ["BI_DATA_DIR"] = str(tmp_path)
        os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'bench_app.db'}"
        os.environ["WAREHOUSE_PATH"] = str(tmp_path / "bench_warehouse.duckdb")
        os.environ["SAMPLE_ERP_DB_PATH"] = str(tmp_path / "bench_erp.sqlite")
        os.environ["BI_SEED_DEMO_USERS"] = "1"
        os.environ["RATE_LIMIT_ENABLED"] = "0"

        from fastapi.testclient import TestClient
        from app.engines.warehouse import warehouse
        from app.main import app

        # 1. Analytical Warehouse Scale Benchmarks (100,000 rows with DuckDB lock active)
        rng = np.random.default_rng(42)
        n = 100_000
        regions = np.array(["North", "South", "East", "West"])
        cats = np.array(["Software", "AI Modules", "Security", "Infrastructure", "Licensing"])
        df = pd.DataFrame(
            {
                "order_id": np.arange(1, n + 1),
                "region": regions[rng.integers(0, len(regions), size=n)],
                "category": cats[rng.integers(0, len(cats), size=n)],
                "month": [f"2025-{(m % 12) + 1:02d}" for m in range(n)],
                "revenue": np.round(rng.uniform(500.0, 25000.0, size=n), 2),
                "cost": np.round(rng.uniform(200.0, 12000.0, size=n), 2),
            }
        )

        t0 = time.perf_counter()
        warehouse.load_dataframe("bench_fact_100k", df, mode="replace")
        load_ms = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        agg_res = warehouse.execute_query(
            "SELECT region, category, SUM(revenue) AS rev, SUM(revenue - cost) AS profit, COUNT(*) AS cnt "
            "FROM bench_fact_100k GROUP BY region, category ORDER BY rev DESC"
        )
        agg_ms = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        win_res = warehouse.execute_query(
            "SELECT month, SUM(revenue) AS m_rev, SUM(SUM(revenue)) OVER (ORDER BY month) AS running_rev "
            "FROM bench_fact_100k GROUP BY month ORDER BY month"
        )
        win_ms = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        rls_res = warehouse.execute_query(
            "SELECT category, SUM(revenue) AS rev FROM bench_fact_100k WHERE region = 'North' GROUP BY category"
        )
        rls_ms = (time.perf_counter() - t0) * 1000

        # 2. Live API, Dashboard Batch, RBAC & Export Benchmarks
        with TestClient(app) as client:
            tok = client.post(
                "/api/v1/auth/login",
                headers={"X-Auth-Mode": "bearer"},
                json={"username": "admin", "password": "Admin!2026"},
            ).json()["access_token"]
            headers = {"Authorization": f"Bearer {tok}"}

            dashes = client.get("/api/v1/dashboards", headers=headers).json()
            exec_dash = next((d for d in dashes if "Executive" in d["title"]), dashes[0])

            client.post("/api/v1/monitoring/cache/invalidate", headers=headers)
            t0 = time.perf_counter()
            cold = client.post(f"/api/v1/dashboards/{exec_dash['id']}/execute-batch", headers=headers, json={})
            cold_ms = (time.perf_counter() - t0) * 1000
            assert cold.status_code == 200

            t0 = time.perf_counter()
            warm = client.post(f"/api/v1/dashboards/{exec_dash['id']}/execute-batch", headers=headers, json={})
            warm_ms = (time.perf_counter() - t0) * 1000
            assert warm.status_code == 200

            def _hit(_idx: int) -> float:
                st = time.perf_counter()
                r = client.post(f"/api/v1/dashboards/{exec_dash['id']}/execute-batch", headers=headers, json={})
                assert r.status_code == 200
                return (time.perf_counter() - st) * 1000

            t0 = time.perf_counter()
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
                latencies = list(ex.map(_hit, range(10)))
            conc_wall_ms = (time.perf_counter() - t0) * 1000
            conc_avg_ms = sum(latencies) / len(latencies)

            # RBAC Matrix endpoint latency
            t0 = time.perf_counter()
            assert client.get("/api/v1/rbac/users", headers=headers).status_code == 200
            assert client.get("/api/v1/rbac/roles", headers=headers).status_code == 200
            assert client.get("/api/v1/rbac/permissions/matrix", headers=headers).status_code == 200
            rbac_ms = (time.perf_counter() - t0) * 1000

            # PDF & Excel export latency
            t0 = time.perf_counter()
            pdf_r = client.get(f"/api/v1/exports/dashboards/{exec_dash['id']}/pdf", headers=headers)
            pdf_ms = (time.perf_counter() - t0) * 1000
            assert pdf_r.status_code == 200 and pdf_r.content.startswith(b"%PDF")

            t0 = time.perf_counter()
            xlsx_r = client.get(f"/api/v1/exports/dashboards/{exec_dash['id']}/xlsx", headers=headers)
            xlsx_ms = (time.perf_counter() - t0) * 1000
            assert xlsx_r.status_code == 200

            perf = client.get("/api/v1/monitoring/performance", headers=headers).json()

        print("=== NexusBI Stage 4 Benchmark Results ===")
        print(f"duckdb_load_100k_ms: {load_ms:.2f}")
        print(f"duckdb_agg_100k_ms: {agg_ms:.2f} (groups={agg_res['row_count']})")
        print(f"duckdb_window_100k_ms: {win_ms:.2f} (buckets={win_res['row_count']})")
        print(f"duckdb_rls_100k_ms: {rls_ms:.2f} (rows={rls_res['row_count']})")
        print(f"batch_cold_ms: {cold_ms:.2f} (widgets={len(cold.json()['widgets'])})")
        print(f"batch_warm_ms: {warm_ms:.2f} (speedup={cold_ms / max(warm_ms, 0.01):.2f}x)")
        print(f"concurrent_10_wall_ms: {conc_wall_ms:.2f} (avg={conc_avg_ms:.2f})")
        print(f"rbac_users_roles_matrix_bundle_ms: {rbac_ms:.2f}")
        print(f"export_pdf_ms: {pdf_ms:.2f} (bytes={len(pdf_r.content)})")
        print(f"export_xlsx_ms: {xlsx_ms:.2f} (bytes={len(xlsx_r.content)})")
        print(f"cache_hit_ratio_pct: {perf['cache']['hit_ratio_percent']}")
        print(f"memory_mb: {perf['system']['memory_mb']}")


if __name__ == "__main__":
    main()
