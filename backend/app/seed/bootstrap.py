import json
import os
import random
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any
import pandas as pd
from sqlalchemy.orm import Session
from app.config import settings
from app.core.permissions import PERMISSION_CATALOG
from app.core.security import encrypt_secret, hash_password
from app.engines.etl_engine import run_etl_pipeline
from app.engines.warehouse import warehouse
from app.models.dashboard import (
    Dashboard,
    DashboardFilter,
    DashboardLayout,
    DashboardPage,
    DashboardTheme,
    Widget,
    WidgetConfig,
    WidgetDrillRule,
    WidgetQuery,
    Workspace,
)
from app.models.data import (
    DataLineage,
    DataSource,
    DataSourceCredential,
    Dataset,
    DatasetField,
    DatasetRelationship,
    SavedQuery,
    SemanticMetric,
)
from app.models.etl import EtlPipeline, EtlSchedule
from app.models.identity import (
    AccessPolicy,
    DirectoryConnection,
    Group,
    GroupMember,
    GroupRole,
    Organization,
    Permission,
    ResourcePermission,
    Role,
    RolePermission,
    User,
    UserProfile,
    UserRole,
)
from app.models.performance import MaterializedViewMetadata

BUILTIN_THEMES = [
    {
        "name": "Light Enterprise",
        "slug": "light-enterprise",
        "mode": "light",
        "tokens": {
            "primary": "#2563eb",
            "secondary": "#0ea5e9",
            "background": "#f1f5f9",
            "surface": "#ffffff",
            "card_bg": "#ffffff",
            "text": "#0f172a",
            "muted_text": "#64748b",
            "border": "#e2e8f0",
            "radius": 12,
            "shadow": "0 4px 20px rgba(15, 23, 42, 0.06)",
            "blur": 0,
            "font_family": "Inter, Vazirmatn, sans-serif",
            "chart_palette": ["#2563eb", "#0ea5e9", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#ec4899"],
        },
    },
    {
        "name": "Dark Enterprise",
        "slug": "dark-enterprise",
        "mode": "dark",
        "tokens": {
            "primary": "#3b82f6",
            "secondary": "#06b6d4",
            "background": "#090d16",
            "surface": "#111827",
            "card_bg": "#151e2e",
            "text": "#f8fafc",
            "muted_text": "#94a3b8",
            "border": "#1e293b",
            "radius": 12,
            "shadow": "0 8px 28px rgba(0, 0, 0, 0.45)",
            "blur": 0,
            "font_family": "Inter, Vazirmatn, sans-serif",
            "chart_palette": ["#60a5fa", "#22d3ee", "#34d399", "#fbbf24", "#f87171", "#a78bfa", "#f472b6"],
        },
    },
    {
        "name": "Minimal",
        "slug": "minimal",
        "mode": "light",
        "tokens": {
            "primary": "#18181b",
            "secondary": "#52525b",
            "background": "#ffffff",
            "surface": "#fafafa",
            "card_bg": "#ffffff",
            "text": "#09090b",
            "muted_text": "#71717a",
            "border": "#e4e4e7",
            "radius": 6,
            "shadow": "none",
            "blur": 0,
            "font_family": "Inter, Vazirmatn, sans-serif",
            "chart_palette": ["#18181b", "#52525b", "#a1a1aa", "#2563eb", "#16a34a", "#dc2626"],
        },
    },
    {
        "name": "Liquid Glass",
        "slug": "liquid",
        "mode": "light",
        "tokens": {
            "primary": "#0284c7",
            "secondary": "#7c3aed",
            "background": "linear-gradient(135deg, #e0f2fe 0%, #ede9fe 50%, #fce7f3 100%)",
            "surface": "rgba(255, 255, 255, 0.58)",
            "card_bg": "rgba(255, 255, 255, 0.65)",
            "text": "#0f172a",
            "muted_text": "#475569",
            "border": "rgba(255, 255, 255, 0.72)",
            "radius": 20,
            "shadow": "0 12px 36px rgba(31, 38, 135, 0.14)",
            "blur": 18,
            "font_family": "Inter, Vazirmatn, sans-serif",
            "chart_palette": ["#0284c7", "#7c3aed", "#ec4899", "#14b8a6", "#f59e0b", "#6366f1"],
        },
    },
    {
        "name": "Glassmorphism",
        "slug": "glassmorphism",
        "mode": "dark",
        "tokens": {
            "primary": "#38bdf8",
            "secondary": "#c084fc",
            "background": "linear-gradient(135deg, #0f172a 0%, #1e1b4b 55%, #0f172a 100%)",
            "surface": "rgba(30, 41, 59, 0.48)",
            "card_bg": "rgba(30, 41, 59, 0.55)",
            "text": "#f8fafc",
            "muted_text": "#cbd5e1",
            "border": "rgba(255, 255, 255, 0.16)",
            "radius": 18,
            "shadow": "0 12px 40px rgba(0, 0, 0, 0.45)",
            "blur": 16,
            "font_family": "Inter, Vazirmatn, sans-serif",
            "chart_palette": ["#38bdf8", "#c084fc", "#34d399", "#fb7185", "#fbbf24", "#818cf8"],
        },
    },
    {
        "name": "Neon",
        "slug": "neon",
        "mode": "dark",
        "tokens": {
            "primary": "#00f5d4",
            "secondary": "#f72585",
            "background": "#050510",
            "surface": "#0b0b1d",
            "card_bg": "#0f0f29",
            "text": "#e2e8f0",
            "muted_text": "#94a3b8",
            "border": "rgba(0, 245, 212, 0.35)",
            "radius": 12,
            "shadow": "0 0 22px rgba(0, 245, 212, 0.16)",
            "blur": 4,
            "font_family": "Inter, Vazirmatn, sans-serif",
            "chart_palette": ["#00f5d4", "#f72585", "#7209b7", "#4cc9f0", "#fee440", "#00bbf9"],
        },
    },
    {
        "name": "Corporate",
        "slug": "corporate",
        "mode": "light",
        "tokens": {
            "primary": "#1e3a8a",
            "secondary": "#b45309",
            "background": "#f3f4f6",
            "surface": "#ffffff",
            "card_bg": "#ffffff",
            "text": "#111827",
            "muted_text": "#4b5563",
            "border": "#d1d5db",
            "radius": 8,
            "shadow": "0 2px 10px rgba(17, 24, 39, 0.08)",
            "blur": 0,
            "font_family": "Inter, Vazirmatn, sans-serif",
            "chart_palette": ["#1e3a8a", "#b45309", "#047857", "#374151", "#7c2d12", "#4338ca"],
        },
    },
    {
        "name": "Material Design",
        "slug": "material",
        "mode": "light",
        "tokens": {
            "primary": "#6750a4",
            "secondary": "#625b71",
            "background": "#fef7ff",
            "surface": "#f3edf7",
            "card_bg": "#ffffff",
            "text": "#1d1b20",
            "muted_text": "#49454f",
            "border": "#cac4d0",
            "radius": 16,
            "shadow": "0 3px 12px rgba(29, 27, 32, 0.12)",
            "blur": 0,
            "font_family": "Inter, Vazirmatn, sans-serif",
            "chart_palette": ["#6750a4", "#006a6a", "#b3261e", "#7d5260", "#386a20", "#0061a4"],
        },
    },
]


def create_sample_erp_database(db_path: str) -> None:
    """Creates a realistic Operational ERP SQLite database for live DB queries & CDC/Incremental ETL."""
    if os.path.exists(db_path):
        return
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    rng = random.Random(42)

    regions = {
        "North": [("Iran", "Tehran", "Tehran"), ("Iran", "Alborz", "Karaj"), ("Iran", "Mazandaran", "Sari")],
        "South": [("Iran", "Fars", "Shiraz"), ("Iran", "Khuzestan", "Ahvaz"), ("Iran", "Hormozgan", "Bandar Abbas")],
        "East": [("Iran", "Khorasan", "Mashhad"), ("Iran", "Kerman", "Kerman")],
        "West": [("Iran", "Isfahan", "Isfahan"), ("Iran", "East Azerbaijan", "Tabriz")],
    }
    products = [
        ("Cloud Analytics Suite", "Software", 12500, 3200),
        ("Enterprise Data Hub", "Software", 22000, 6100),
        ("AI Forecasting Engine", "AI Modules", 18000, 4800),
        ("Security Governance Pack", "Security", 9500, 2100),
        ("Managed ETL Gateway", "Infrastructure", 14200, 5200),
        ("BI Mobile License", "Licensing", 4800, 900),
    ]
    channels = ["Direct Enterprise", "Partner Channel", "Cloud Marketplace", "Inside Sales"]
    departments = ["Engineering", "Sales", "Finance", "Operations", "Marketing", "Security"]

    orders = []
    start_d = date(2024, 1, 1)
    for i in range(1, 1201):
        d = start_d + timedelta(days=rng.randint(0, 1020))
        reg = rng.choice(list(regions.keys()))
        country, province, city = rng.choice(regions[reg])
        prod, cat, base_rev, base_cost = rng.choice(products)
        qty = rng.randint(1, 12)
        factor = rng.uniform(0.85, 1.28)
        revenue = round(base_rev * qty * factor, 2)
        cost = round(base_cost * qty * rng.uniform(0.9, 1.15), 2)
        profit = round(revenue - cost, 2)
        orders.append(
            (
                i,
                f"ORD-{10000 + i}",
                d.isoformat(),
                d.year,
                f"Q{(d.month - 1) // 3 + 1}",
                d.strftime("%Y-%m"),
                d.strftime("%Y-%m-%d"),
                country,
                reg,
                province,
                city,
                prod,
                cat,
                rng.choice(channels),
                rng.choice(departments),
                qty,
                revenue,
                cost,
                profit,
                round((profit / revenue) * 100, 2) if revenue else 0.0,
                f"2026-10-{(i % 28) + 1:02d}T10:00:00Z",
            )
        )

    expenses = []
    expense_cats = ["Cloud Infrastructure", "Payroll & Benefits", "Software Licenses", "Marketing Campaigns", "Security Audit", "Travel & Events"]
    for i in range(1, 451):
        d = start_d + timedelta(days=rng.randint(0, 1020))
        dept = rng.choice(departments)
        reg = rng.choice(list(regions.keys()))
        ecat = rng.choice(expense_cats)
        budget = round(rng.uniform(15000, 85000), 2)
        actual = round(budget * rng.uniform(0.72, 1.18), 2)
        expenses.append(
            (
                i,
                f"EXP-{5000 + i}",
                d.isoformat(),
                d.year,
                f"Q{(d.month - 1) // 3 + 1}",
                d.strftime("%Y-%m"),
                dept,
                reg,
                ecat,
                budget,
                actual,
                round(budget - actual, 2),
                f"2026-10-{(i % 28) + 1:02d}T12:00:00Z",
            )
        )

    employees = [
        (1, "EMP-001", "Darius Farhadi", "darius@enterprise.local", "Engineering", "North", "Principal Architect", 145000, 94.5),
        (2, "EMP-002", "Neda Moradi", "neda@enterprise.local", "Sales", "North", "VP Sales North", 132000, 96.0),
        (3, "EMP-003", "Reza Tehrani", "reza@enterprise.local", "Sales", "South", "VP Sales South", 128000, 91.2),
        (4, "EMP-004", "Mina Hosseini", "mina@enterprise.local", "Finance", "Central", "Financial Controller", 118000, 95.8),
        (5, "EMP-005", "Kaveh Bahrami", "kaveh@enterprise.local", "Security", "North", "Enterprise Security Lead", 139000, 97.1),
        (6, "EMP-006", "Parisa Niknam", "parisa@enterprise.local", "Operations", "East", "Operations Manager", 109000, 89.4),
        (7, "EMP-007", "Omid Shirazi", "omid@enterprise.local", "Marketing", "West", "Growth Director", 115000, 92.0),
        (8, "EMP-008", "Taraneh Azimi", "taraneh@enterprise.local", "Engineering", "South", "Data Engineer", 124000, 93.7),
    ]

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE erp_orders (
                order_id INTEGER PRIMARY KEY,
                order_code TEXT,
                order_date TEXT,
                year INTEGER,
                quarter TEXT,
                month TEXT,
                day TEXT,
                country TEXT,
                region TEXT,
                province TEXT,
                city TEXT,
                product TEXT,
                category TEXT,
                channel TEXT,
                department TEXT,
                quantity INTEGER,
                revenue REAL,
                cost REAL,
                profit REAL,
                margin_pct REAL,
                updated_at TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO erp_orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            orders,
        )

        conn.execute(
            """
            CREATE TABLE erp_expenses (
                expense_id INTEGER PRIMARY KEY,
                voucher_code TEXT,
                transaction_date TEXT,
                year INTEGER,
                quarter TEXT,
                month TEXT,
                department TEXT,
                region TEXT,
                expense_category TEXT,
                budget_amount REAL,
                actual_amount REAL,
                variance_amount REAL,
                updated_at TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO erp_expenses VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            expenses,
        )

        conn.execute(
            """
            CREATE TABLE erp_employees (
                employee_id INTEGER PRIMARY KEY,
                emp_code TEXT,
                full_name TEXT,
                email TEXT,
                department TEXT,
                region TEXT,
                job_title TEXT,
                salary REAL,
                performance_score REAL
            )
            """
        )
        conn.executemany("INSERT INTO erp_employees VALUES (?,?,?,?,?,?,?,?,?)", employees)
        conn.execute(
            "CREATE VIEW vw_regional_summary AS SELECT region, COUNT(*) AS order_count, SUM(revenue) AS total_revenue, SUM(profit) AS total_profit FROM erp_orders GROUP BY region"
        )
        conn.commit()
    finally:
        conn.close()


def _create_role_with_perms(
    db: Session,
    org_id: str,
    name: str,
    slug: str,
    desc: str,
    perm_map: dict[tuple[str, str], Permission],
    allowed_pairs: list[tuple[str, str]],
    is_super: bool = False,
) -> Role:
    role = db.query(Role).filter(Role.organization_id == org_id, Role.slug == slug).first()
    if not role:
        role = Role(
            organization_id=org_id,
            name=name,
            slug=slug,
            description=desc,
            is_system=True,
            is_super_admin=is_super,
        )
        db.add(role)
        db.flush()
    for rt, act in allowed_pairs:
        p = perm_map.get((rt, act))
        if p:
            exists = (
                db.query(RolePermission)
                .filter(RolePermission.role_id == role.id, RolePermission.permission_id == p.id)
                .first()
            )
            if not exists:
                db.add(RolePermission(role_id=role.id, permission_id=p.id, effect="allow"))
    return role


def seed_core_catalog(db: Session) -> tuple[Organization, dict[str, Role], dict[str, DashboardTheme]]:
    """Idempotently seeds Organization, Permissions, Roles & Themes (zero user accounts/passwords)."""
    org = db.query(Organization).filter(Organization.slug == "enterprise-corp").first()
    if not org:
        org = Organization(name="Enterprise Analytics Corp", slug="enterprise-corp")
        db.add(org)
        db.flush()

    # 1. Permission Catalog
    perm_map: dict[tuple[str, str], Permission] = {}
    for p in db.query(Permission).all():
        perm_map[(p.resource_type, p.action)] = p
    for rt, act, desc in PERMISSION_CATALOG:
        if (rt, act) not in perm_map:
            p = Permission(resource_type=rt, action=act, description=desc)
            db.add(p)
            db.flush()
            perm_map[(rt, act)] = p

    # 2. Roles (10 Enterprise Roles + Scoped Viewer)
    all_pairs = list(perm_map.keys())
    roles: dict[str, Role] = {}
    roles["super_admin"] = _create_role_with_perms(
        db, org.id, "Super Administrator", "super_admin", "Full unrestricted platform control", perm_map, all_pairs, is_super=True
    )
    roles["org_admin"] = _create_role_with_perms(
        db, org.id, "Organization Administrator", "org_admin", "Manage organization users, data & dashboards",
        perm_map, [k for k in all_pairs if k[0] != "organization" or k[1] == "view"]
    )
    roles["security_admin"] = _create_role_with_perms(
        db, org.id, "Security Administrator", "security_admin", "Manage RBAC, AD, RLS/CLS & Audit logs",
        perm_map, [k for k in all_pairs if k[0] in ("admin", "organization", "dashboard", "widget")]
    )
    roles["dashboard_admin"] = _create_role_with_perms(
        db, org.id, "Dashboard Administrator", "dashboard_admin", "Manage dashboards, layouts, themes & permissions",
        perm_map, [k for k in all_pairs if k[0] in ("workspace", "dashboard", "dashboard_page", "widget", "drill_down", "filter", "export", "dataset", "query")]
    )
    roles["dashboard_designer"] = _create_role_with_perms(
        db, org.id, "Dashboard Designer", "dashboard_designer", "Design dashboards, widgets, themes & queries",
        perm_map,
        [
            ("workspace", "view"), ("dashboard", "view"), ("dashboard", "edit"), ("dashboard", "add"),
            ("dashboard", "add_widget"), ("dashboard", "delete_widget"), ("dashboard", "add_page"),
            ("dashboard", "change_layout"), ("dashboard", "change_theme"), ("dashboard", "manage_filters"),
            ("dashboard", "refresh"), ("dashboard", "export"), ("dashboard_page", "view"),
            ("dashboard_page", "edit"), ("dashboard_page", "add"),
            ("widget", "view"), ("widget", "edit"), ("widget", "add"), ("widget", "delete"),
            ("widget", "change_chart_type"), ("widget", "change_style"), ("widget", "change_query"),
            ("widget", "change_dataset"), ("widget", "change_filter"), ("widget", "refresh"),
            ("widget", "view_details"), ("widget", "export"),
            ("drill_down", "drill_down"), ("drill_down", "drill_up"), ("drill_down", "drill_through"),
            ("drill_down", "drill_level_1"), ("drill_down", "drill_level_2"), ("drill_down", "drill_level_3"),
            ("drill_down", "view_raw_records"), ("drill_down", "drill_through_target"),
            ("dataset", "view"), ("data_source", "view"), ("query", "view"), ("query", "add"),
            ("query", "edit"), ("query", "execute"), ("filter", "view"), ("filter", "edit"),
            ("filter", "add"), ("export", "view"), ("export", "export_pdf"), ("export", "export_excel"), ("export", "export_csv"),
        ],
    )
    roles["data_analyst"] = _create_role_with_perms(
        db, org.id, "Data Analyst", "data_analyst", "Explore datasets, execute queries & analyze dashboards",
        perm_map,
        [
            ("workspace", "view"), ("dashboard", "view"), ("dashboard", "refresh"), ("dashboard", "export"),
            ("dashboard_page", "view"), ("widget", "view"), ("widget", "change_chart_type"),
            ("widget", "refresh"), ("widget", "view_details"), ("widget", "export"),
            ("drill_down", "drill_down"), ("drill_down", "drill_up"), ("drill_down", "drill_through"),
            ("drill_down", "drill_level_1"), ("drill_down", "drill_level_2"), ("drill_down", "drill_level_3"),
            ("dataset", "view"), ("dataset", "add"), ("dataset", "edit"),
            ("data_source", "view"), ("query", "view"), ("query", "add"), ("query", "execute"),
            ("filter", "view"), ("filter", "edit"), ("export", "view"), ("export", "export_pdf"),
            ("export", "export_excel"), ("export", "export_csv"),
        ],
    )
    roles["data_engineer"] = _create_role_with_perms(
        db, org.id, "Data Engineer", "data_engineer", "Manage Data Sources, Datasets & ETL Pipelines",
        perm_map,
        [k for k in all_pairs if k[0] in ("data_source", "dataset", "query", "workspace", "dashboard")]
    )
    roles["report_viewer"] = _create_role_with_perms(
        db, org.id, "Report Viewer", "report_viewer", "Read-only viewer with interactive filtering",
        perm_map,
        [
            ("workspace", "view"), ("dashboard", "view"), ("dashboard_page", "view"),
            ("widget", "view"), ("filter", "view"), ("filter", "edit"),
            ("drill_down", "drill_down"), ("drill_down", "drill_up"), ("drill_down", "drill_level_1"),
            ("export", "view"), ("export", "export_pdf"),
        ],
    )
    roles["executive_viewer"] = _create_role_with_perms(
        db, org.id, "Executive Viewer", "executive_viewer", "Executive view with drill down and exports",
        perm_map,
        [
            ("workspace", "view"), ("dashboard", "view"), ("dashboard", "export"),
            ("dashboard_page", "view"), ("widget", "view"), ("widget", "view_details"),
            ("drill_down", "drill_down"), ("drill_down", "drill_up"), ("drill_down", "drill_through"),
            ("drill_down", "drill_level_1"), ("drill_down", "drill_level_2"), ("drill_down", "drill_level_3"),
            ("filter", "view"), ("filter", "edit"), ("export", "view"), ("export", "export_pdf"), ("export", "export_excel"),
        ],
    )
    roles["custom_scoped"] = _create_role_with_perms(
        db, org.id, "Scoped Custom Role", "custom_scoped", "Zero blanket allows; requires explicit resource grants",
        perm_map, [("workspace", "view")]
    )

    # Built-in 8 Themes
    theme_objs: dict[str, DashboardTheme] = {}
    for dt in db.query(DashboardTheme).filter(DashboardTheme.organization_id == org.id).all():
        theme_objs[dt.slug] = dt
    for t in BUILTIN_THEMES:
        if t["slug"] not in theme_objs:
            dt = DashboardTheme(
                organization_id=org.id,
                name=t["name"],
                slug=t["slug"],
                is_builtin=True,
                mode=t["mode"],
                tokens_json=t["tokens"],
                created_by=None,
            )
            db.add(dt)
            db.flush()
            theme_objs[t["slug"]] = dt

    return org, roles, theme_objs


def ensure_contractor_sample_dataset(db: Session, org: Organization) -> None:
    """Idempotently seeds the Persian sample dataset 'تعداد' (Contractor Counts & Performance) into DuckDB & metadata."""
    existing = db.query(Dataset).filter(
        Dataset.organization_id == org.id, Dataset.warehouse_table == "wh_contractor_counts"
    ).first()
    if existing and warehouse.table_exists("wh_contractor_counts"):
        return

    rows = [
        {"contractor_name": "پیمانکاری آفتاب", "region": "North", "project_type": "فناوری اطلاعات", "project_count": 14, "completed_tasks": 128, "performance_score": 94.5, "contract_value": 4200},
        {"contractor_name": "پیمانکاری آفتاب", "region": "South", "project_type": "عمرانی", "project_count": 9, "completed_tasks": 76, "performance_score": 89.0, "contract_value": 3100},
        {"contractor_name": "سازه گستر پارس", "region": "South", "project_type": "عمرانی", "project_count": 18, "completed_tasks": 152, "performance_score": 91.2, "contract_value": 5800},
        {"contractor_name": "سازه گستر پارس", "region": "West", "project_type": "صنعتی", "project_count": 11, "completed_tasks": 90, "performance_score": 87.5, "contract_value": 3950},
        {"contractor_name": "فناوران البرز", "region": "North", "project_type": "فناوری اطلاعات", "project_count": 21, "completed_tasks": 195, "performance_score": 96.4, "contract_value": 4900},
        {"contractor_name": "فناوران البرز", "region": "East", "project_type": "فناوری اطلاعات", "project_count": 8, "completed_tasks": 64, "performance_score": 92.0, "contract_value": 2400},
        {"contractor_name": "توسعه انرژی شرق", "region": "East", "project_type": "برق و تاسیسات", "project_count": 16, "completed_tasks": 134, "performance_score": 88.8, "contract_value": 5100},
        {"contractor_name": "توسعه انرژی شرق", "region": "North", "project_type": "برق و تاسیسات", "project_count": 7, "completed_tasks": 58, "performance_score": 86.0, "contract_value": 2150},
        {"contractor_name": "مهندسی کارا", "region": "West", "project_type": "صنعتی", "project_count": 13, "completed_tasks": 110, "performance_score": 90.5, "contract_value": 4450},
        {"contractor_name": "مهندسی کارا", "region": "North", "project_type": "عمرانی", "project_count": 10, "completed_tasks": 82, "performance_score": 88.2, "contract_value": 3300},
        {"contractor_name": "پارس نیرو", "region": "South", "project_type": "برق و تاسیسات", "project_count": 15, "completed_tasks": 124, "performance_score": 93.1, "contract_value": 4700},
        {"contractor_name": "پارس نیرو", "region": "East", "project_type": "صنعتی", "project_count": 9, "completed_tasks": 71, "performance_score": 89.7, "contract_value": 2950},
    ]
    df = pd.DataFrame(rows)
    warehouse.load_dataframe("wh_contractor_counts", df, mode="replace")

    if existing:
        existing.row_count = len(rows)
        existing.freshness_status = "fresh"
        existing.last_refreshed_at = datetime.now(timezone.utc)
        db.commit()
        return

    src = db.query(DataSource).filter(DataSource.organization_id == org.id).first()
    if not src:
        src = DataSource(
            organization_id=org.id,
            name="Enterprise ERP Operational DB",
            source_type="sqlite",
            read_only=True,
            config_json={"file_path": settings.SAMPLE_ERP_DB_PATH, "default_table": "erp_orders"},
            refresh_policy="manual",
            health_status="healthy",
            last_tested_at=datetime.now(timezone.utc),
        )
        db.add(src)
        db.flush()

    ds_contractors = Dataset(
        organization_id=org.id,
        data_source_id=src.id,
        name="تعداد",
        description="آمار تعداد پروژه‌ها و عملکرد پیمانکاران (نمونه تستی فارسی)",
        warehouse_table="wh_contractor_counts",
        schema_type="fact",
        refresh_policy="cached",
        row_count=len(rows),
        last_refreshed_at=datetime.now(timezone.utc),
        freshness_status="fresh",
    )
    db.add(ds_contractors)
    db.flush()

    fields_spec = [
        ("contractor_name", "نام پیمانکار", "string", "dimension"),
        ("region", "منطقه", "string", "geo"),
        ("project_type", "حوزه پروژه", "string", "dimension"),
        ("project_count", "تعداد پروژه", "integer", "measure"),
        ("completed_tasks", "تعداد کار انجام‌شده", "integer", "measure"),
        ("performance_score", "امتیاز عملکرد", "float", "measure"),
        ("contract_value", "مبلغ قرارداد", "float", "measure"),
    ]
    for fname, disp, dtype, srole in fields_spec:
        db.add(
            DatasetField(
                dataset_id=ds_contractors.id,
                field_name=fname,
                display_name=disp,
                data_type=dtype,
                semantic_role=srole,
            )
        )
    db.commit()


def _build_bpmn_sample_rows() -> list[dict[str, Any]]:
    """Generates realistic Persian procurement BPMN workflow events with clear bottlenecks & cartable assignees."""
    templates = [
        # (step_id, step_name, sla_hours, pending_cases[(assignee, role, dept, wait, status, priority)], completed_cases)
        (
            "step_submit",
            "ثبت درخواست خرید",
            8.0,
            [
                ("ندا مرادی", "کارشناس درخواست", "فنی و مهندسی", 5.5, "در انتظار اقدام", "متوسط"),
                ("ندا مرادی", "کارشناس درخواست", "عملیات و لجستیک", 9.2, "دارای تاخیر", "بالا"),
                ("پویا کیانی", "کارشناس تدارکات", "فناوری اطلاعات", 4.0, "در انتظار اقدام", "عادی"),
                ("سارا احمدی", "کارشناس بازرگانی", "بازرگانی", 6.5, "در انتظار اقدام", "متوسط"),
                ("پویا کیانی", "کارشناس تدارکات", "مالی و اداری", 3.5, "در انتظار اقدام", "عادی"),
            ],
            [
                ("ندا مرادی", "کارشناس درخواست", "فنی و مهندسی", 3.0, "تکمیل‌شده", "متوسط"),
                ("پویا کیانی", "کارشناس تدارکات", "فناوری اطلاعات", 4.2, "تکمیل‌شده", "عادی"),
                ("سارا احمدی", "کارشناس بازرگانی", "بازرگانی", 5.0, "تکمیل‌شده", "متوسط"),
                ("ندا مرادی", "کارشناس درخواست", "عملیات و لجستیک", 6.1, "تکمیل‌شده", "بالا"),
            ],
        ),
        (
            "step_tech",
            "بررسی فنی و نیازسنجی",
            12.0,
            [
                ("آرش شریفی", "مهندس بررسی فنی", "فنی و مهندسی", 14.5, "دارای تاخیر", "بالا"),
                ("آرش شریفی", "مهندس بررسی فنی", "فناوری اطلاعات", 11.0, "در انتظار اقدام", "متوسط"),
                ("سپیده نادری", "کارشناس ارشد فنی", "عملیات و لجستیک", 16.2, "دارای تاخیر", "بالا"),
                ("سپیده نادری", "کارشناس ارشد فنی", "فنی و مهندسی", 9.5, "در انتظار اقدام", "متوسط"),
                ("کیانوش پرتوی", "تحلیلگر فنی", "فناوری اطلاعات", 8.0, "در انتظار اقدام", "عادی"),
                ("کیانوش پرتوی", "تحلیلگر فنی", "بازرگانی", 13.0, "دارای تاخیر", "متوسط"),
                ("آرش شریفی", "مهندس بررسی فنی", "عملیات و لجستیک", 10.5, "در انتظار اقدام", "عادی"),
            ],
            [
                ("آرش شریفی", "مهندس بررسی فنی", "فنی و مهندسی", 7.5, "تکمیل‌شده", "متوسط"),
                ("سپیده نادری", "کارشناس ارشد فنی", "فناوری اطلاعات", 9.0, "تکمیل‌شده", "بالا"),
                ("کیانوش پرتوی", "تحلیلگر فنی", "بازرگانی", 8.4, "تکمیل‌شده", "عادی"),
                ("آرش شریفی", "مهندس بررسی فنی", "عملیات و لجستیک", 10.2, "تکمیل‌شده", "متوسط"),
            ],
        ),
        (
            "step_fix",
            "اصلاح مستندات و پیوست",
            16.0,
            [
                ("ندا مرادی", "کارشناس درخواست", "عملیات و لجستیک", 21.0, "دارای تاخیر", "بالا"),
                ("پویا کیانی", "کارشناس تدارکات", "بازرگانی", 15.5, "در انتظار اقدام", "متوسط"),
                ("سارا احمدی", "کارشناس بازرگانی", "فنی و مهندسی", 18.0, "دارای تاخیر", "متوسط"),
                ("ندا مرادی", "کارشناس درخواست", "مالی و اداری", 11.5, "در انتظار اقدام", "عادی"),
            ],
            [
                ("پویا کیانی", "کارشناس تدارکات", "فناوری اطلاعات", 10.0, "تکمیل‌شده", "عادی"),
                ("سارا احمدی", "کارشناس بازرگانی", "بازرگانی", 12.5, "تکمیل‌شده", "متوسط"),
            ],
        ),
        (
            "step_budget",
            "تأیید بودجه و مالی",
            24.0,
            [
                ("مهسا کریمی", "رئیس اداره بودجه", "فنی و مهندسی", 64.5, "دارای تاخیر", "بحرانی"),
                ("مهسا کریمی", "رئیس اداره بودجه", "عملیات و لجستیک", 58.0, "دارای تاخیر", "بالا"),
                ("مهسا کریمی", "رئیس اداره بودجه", "فناوری اطلاعات", 49.0, "دارای تاخیر", "بالا"),
                ("مهسا کریمی", "رئیس اداره بودجه", "بازرگانی", 38.5, "در انتظار اقدام", "متوسط"),
                ("دکتر کاوه نوری", "معاون مالی و اقتصادی", "فنی و مهندسی", 72.0, "دارای تاخیر", "بحرانی"),
                ("دکتر کاوه نوری", "معاون مالی و اقتصادی", "عملیات و لجستیک", 61.5, "دارای تاخیر", "بالا"),
                ("دکتر کاوه نوری", "معاون مالی و اقتصادی", "مالی و اداری", 44.0, "دارای تاخیر", "متوسط"),
                ("امیرحسین رضایی", "کارشناس کنترل بودجه", "فناوری اطلاعات", 52.0, "دارای تاخیر", "بالا"),
                ("امیرحسین رضایی", "کارشناس کنترل بودجه", "بازرگانی", 41.0, "دارای تاخیر", "متوسط"),
                ("مهدی تهرانی", "حسابرس داخلی", "فنی و مهندسی", 56.0, "دارای تاخیر", "بالا"),
                ("مهدی تهرانی", "حسابرس داخلی", "مالی و اداری", 39.0, "در انتظار اقدام", "عادی"),
            ],
            [
                ("مهسا کریمی", "رئیس اداره بودجه", "فنی و مهندسی", 28.0, "تکمیل‌شده", "بالا"),
                ("دکتر کاوه نوری", "معاون مالی و اقتصادی", "فناوری اطلاعات", 34.0, "تکمیل‌شده", "متوسط"),
                ("امیرحسین رضایی", "کارشناس کنترل بودجه", "بازرگانی", 22.0, "تکمیل‌شده", "عادی"),
            ],
        ),
        (
            "step_committee",
            "کمیسیون معاملات و مناقصه",
            36.0,
            [
                ("بهرام شریفی", "دبیر کمیسیون معاملات", "فنی و مهندسی", 118.0, "دارای تاخیر", "بحرانی"),
                ("بهرام شریفی", "دبیر کمیسیون معاملات", "عملیات و لجستیک", 96.5, "دارای تاخیر", "بحرانی"),
                ("بهرام شریفی", "دبیر کمیسیون معاملات", "فناوری اطلاعات", 84.0, "دارای تاخیر", "بالا"),
                ("بهرام شریفی", "دبیر کمیسیون معاملات", "بازرگانی", 71.0, "دارای تاخیر", "بالا"),
                ("دکتر کاوه نوری", "رئیس کمیسیون معاملات", "فنی و مهندسی", 104.0, "دارای تاخیر", "بحرانی"),
                ("دکتر کاوه نوری", "رئیس کمیسیون معاملات", "عملیات و لجستیک", 88.0, "دارای تاخیر", "بالا"),
                ("دکتر کاوه نوری", "رئیس کمیسیون معاملات", "مالی و اداری", 69.5, "دارای تاخیر", "بالا"),
                ("حسین شفیعی", "عضو حقوقی کمیسیون", "فنی و مهندسی", 92.0, "دارای تاخیر", "بحرانی"),
                ("حسین شفیعی", "عضو حقوقی کمیسیون", "بازرگانی", 78.5, "دارای تاخیر", "بالا"),
                ("حسین شفیعی", "عضو حقوقی کمیسیون", "فناوری اطلاعات", 63.0, "دارای تاخیر", "متوسط"),
                ("سارا احمدی", "کارشناس مناقصات", "عملیات و لجستیک", 76.0, "دارای تاخیر", "بالا"),
                ("سارا احمدی", "کارشناس مناقصات", "بازرگانی", 54.5, "دارای تاخیر", "متوسط"),
                ("مهدی تهرانی", "ناظر کمیسیون", "فنی و مهندسی", 82.0, "دارای تاخیر", "بالا"),
                ("مهدی تهرانی", "ناظر کمیسیون", "مالی و اداری", 61.0, "دارای تاخیر", "متوسط"),
            ],
            [
                ("بهرام شریفی", "دبیر کمیسیون معاملات", "فنی و مهندسی", 48.0, "تکمیل‌شده", "بالا"),
                ("دکتر کاوه نوری", "رئیس کمیسیون معاملات", "عملیات و لجستیک", 42.0, "تکمیل‌شده", "متوسط"),
                ("حسین شفیعی", "عضو حقوقی کمیسیون", "فناوری اطلاعات", 39.0, "تکمیل‌شده", "عادی"),
            ],
        ),
        (
            "step_po",
            "صدور سفارش و پرداخت",
            16.0,
            [
                ("مریم موسوی", "مسئول صدور حواله", "فناوری اطلاعات", 11.5, "در انتظار اقدام", "متوسط"),
                ("مریم موسوی", "مسئول صدور حواله", "بازرگانی", 18.0, "دارای تاخیر", "بالا"),
                ("فرهاد جلالی", "کارشناس پرداخت", "عملیات و لجستیک", 9.0, "در انتظار اقدام", "عادی"),
                ("فرهاد جلالی", "کارشناس پرداخت", "فنی و مهندسی", 14.5, "در انتظار اقدام", "متوسط"),
                ("امیرحسین رضایی", "کارشناس کنترل بودجه", "مالی و اداری", 12.0, "در انتظار اقدام", "عادی"),
                ("مریم موسوی", "مسئول صدور حواله", "فنی و مهندسی", 15.0, "در انتظار اقدام", "متوسط"),
            ],
            [
                ("مریم موسوی", "مسئول صدور حواله", "فناوری اطلاعات", 8.5, "تکمیل‌شده", "عادی"),
                ("فرهاد جلالی", "کارشناس پرداخت", "بازرگانی", 7.0, "تکمیل‌شده", "متوسط"),
                ("فرهاد جلالی", "کارشناس پرداخت", "عملیات و لجستیک", 10.0, "تکمیل‌شده", "عادی"),
                ("مریم موسوی", "مسئول صدور حواله", "مالی و اداری", 9.2, "تکمیل‌شده", "عادی"),
            ],
        ),
    ]

    rows: list[dict[str, Any]] = []
    idx = 1
    for step_id, step_name, sla_hours, pending_list, done_list in templates:
        for assignee, role, dept, wait_h, status, prio in pending_list + done_list:
            day_num = 1 + ((idx * 3) % 15)
            hour_num = 8 + (idx % 9)
            rows.append(
                {
                    "event_id": idx,
                    "request_code": f"PRQ-{1000 + idx}",
                    "process_name": "فرآیند تأمین و خرید سازمانی",
                    "step_id": step_id,
                    "step_name": step_name,
                    "assignee_name": assignee,
                    "assignee_role": role,
                    "department": dept,
                    "wait_hours": round(float(wait_h), 1),
                    "sla_hours": round(float(sla_hours), 1),
                    "status": status,
                    "priority": prio,
                    "entered_at": f"2026-11-{day_num:02d}T{hour_num:02d}:30:00Z",
                }
            )
            idx += 1
    return rows


DEFAULT_BPMN_GRAPH_PAYLOAD: dict[str, Any] = {
    "process_name": "فرآیند تأمین و خرید سازمانی",
    "step_field": "step_name",
    "step_id_field": "step_id",
    "wait_field": "wait_hours",
    "sla_field": "sla_hours",
    "assignee_field": "assignee_name",
    "assignee_role_field": "assignee_role",
    "request_id_field": "request_code",
    "status_field": "status",
    "department_field": "department",
    "priority_field": "priority",
    "heatmap_enabled": True,
    "heatmap_metric": "avg_wait",
    "nodes": [
        {"id": "evt_start", "label": "شروع", "type": "startEvent", "x": 32, "y": 128, "width": 44, "height": 44, "stepKey": "__start__"},
        {"id": "step_submit", "label": "ثبت درخواست خرید", "type": "userTask", "x": 112, "y": 102, "width": 178, "height": 96, "stepKey": "ثبت درخواست خرید"},
        {"id": "step_tech", "label": "بررسی فنی و نیازسنجی", "type": "userTask", "x": 326, "y": 102, "width": 178, "height": 96, "stepKey": "بررسی فنی و نیازسنجی"},
        {"id": "gw_valid", "label": "تأیید مدارک؟", "type": "exclusiveGateway", "x": 540, "y": 124, "width": 52, "height": 52, "stepKey": "__gw_valid__"},
        {"id": "step_fix", "label": "اصلاح مستندات و پیوست", "type": "userTask", "x": 480, "y": 248, "width": 178, "height": 96, "stepKey": "اصلاح مستندات و پیوست"},
        {"id": "step_budget", "label": "تأیید بودجه و مالی", "type": "userTask", "x": 634, "y": 102, "width": 178, "height": 96, "stepKey": "تأیید بودجه و مالی"},
        {"id": "step_committee", "label": "کمیسیون معاملات و مناقصه", "type": "userTask", "x": 850, "y": 102, "width": 184, "height": 96, "stepKey": "کمیسیون معاملات و مناقصه"},
        {"id": "step_po", "label": "صدور سفارش و پرداخت", "type": "serviceTask", "x": 1070, "y": 102, "width": 182, "height": 96, "stepKey": "صدور سفارش و پرداخت"},
        {"id": "evt_end", "label": "پایان", "type": "endEvent", "x": 1288, "y": 128, "width": 44, "height": 44, "stepKey": "__end__"},
    ],
    "edges": [
        {"id": "f1", "source": "evt_start", "target": "step_submit"},
        {"id": "f2", "source": "step_submit", "target": "step_tech"},
        {"id": "f3", "source": "step_tech", "target": "gw_valid"},
        {"id": "f4", "source": "gw_valid", "target": "step_budget", "label": "تأیید"},
        {"id": "f5", "source": "gw_valid", "target": "step_fix", "label": "نقص مدرک"},
        {"id": "f6", "source": "step_fix", "target": "step_tech", "label": "ارسال مجدد"},
        {"id": "f7", "source": "step_budget", "target": "step_committee"},
        {"id": "f8", "source": "step_committee", "target": "step_po"},
        {"id": "f9", "source": "step_po", "target": "evt_end"},
    ],
}


def ensure_bpmn_process_sample_dataset(db: Session, org: Organization) -> None:
    """Idempotently seeds BPMN process mining dataset ('wh_process_bpmn_events') & a full BPMN bottleneck dashboard."""
    existing_ds = db.query(Dataset).filter(
        Dataset.organization_id == org.id, Dataset.warehouse_table == "wh_process_bpmn_events"
    ).first()
    rows = _build_bpmn_sample_rows()
    if not existing_ds or not warehouse.table_exists("wh_process_bpmn_events"):
        df = pd.DataFrame(rows)
        warehouse.load_dataframe("wh_process_bpmn_events", df, mode="replace")

    if existing_ds:
        existing_ds.row_count = len(rows)
        existing_ds.freshness_status = "fresh"
        existing_ds.last_refreshed_at = datetime.now(timezone.utc)
        ds_bpmn = existing_ds
    else:
        src = db.query(DataSource).filter(DataSource.organization_id == org.id).first()
        if not src:
            src = DataSource(
                organization_id=org.id,
                name="Enterprise ERP Operational DB",
                source_type="sqlite",
                read_only=True,
                config_json={"file_path": settings.SAMPLE_ERP_DB_PATH, "default_table": "erp_orders"},
                refresh_policy="manual",
                health_status="healthy",
                last_tested_at=datetime.now(timezone.utc),
            )
            db.add(src)
            db.flush()

        ds_bpmn = Dataset(
            organization_id=org.id,
            data_source_id=src.id,
            name="رویدادهای فرآیند BPMN و کارتابل",
            description="سوابق جریان درخواست‌ها در مراحل BPMN، زمان انتظار در کارتابل افراد و کشف گلوگاه‌ها",
            warehouse_table="wh_process_bpmn_events",
            schema_type="fact",
            refresh_policy="cached",
            row_count=len(rows),
            last_refreshed_at=datetime.now(timezone.utc),
            freshness_status="fresh",
        )
        db.add(ds_bpmn)
        db.flush()

        fields_spec = [
            ("request_code", "کد درخواست", "string", "dimension"),
            ("process_name", "نام فرآیند", "string", "dimension"),
            ("step_id", "شناسه گام BPMN", "string", "dimension"),
            ("step_name", "نام گام / مرحله فرآیند", "string", "dimension"),
            ("assignee_name", "مسئول کارتابل", "string", "dimension"),
            ("assignee_role", "سمت مسئول", "string", "dimension"),
            ("department", "واحد متقاضی", "string", "dimension"),
            ("wait_hours", "زمان انتظار (ساعت)", "float", "measure"),
            ("sla_hours", "سقف مجاز SLA (ساعت)", "float", "measure"),
            ("status", "وضعیت درخواست", "string", "dimension"),
            ("priority", "اولویت", "string", "dimension"),
            ("entered_at", "تاریخ ورود به گام", "date", "time"),
        ]
        for fname, disp, dtype, srole in fields_spec:
            db.add(
                DatasetField(
                    dataset_id=ds_bpmn.id,
                    field_name=fname,
                    display_name=disp,
                    data_type=dtype,
                    semantic_role=srole,
                )
            )
        db.commit()

    # Seed the BPMN Process Bottleneck Discovery dashboard if missing
    ws = db.query(Workspace).filter(Workspace.organization_id == org.id).first()
    if not ws:
        return
    dash_title = "تحلیل گلوگاه فرآیندهای BPMN (کارتابل و نقشه حرارتی)"
    existing_dash = db.query(Dashboard).filter(
        Dashboard.workspace_id == ws.id, Dashboard.title == dash_title
    ).first()
    if existing_dash:
        return

    owner = db.query(User).filter(User.organization_id == org.id).first()
    theme = (
        db.query(DashboardTheme)
        .filter(DashboardTheme.organization_id == org.id, DashboardTheme.slug == "light-enterprise")
        .first()
        or db.query(DashboardTheme).filter(DashboardTheme.organization_id == org.id).first()
    )

    dash_bpmn = Dashboard(
        workspace_id=ws.id,
        title=dash_title,
        description="پایش گلوگاه‌های فرآیند بر اساس BPMN، نقشه حرارتی زمان انتظار و دریل‌داون کارتابل افراد",
        theme_id=theme.id if theme else None,
        created_by=owner.id if owner else None,
        is_public=False,
        is_template=False,
        embed_token=f"pub-bpmn-{org.id[:8]}",
        refresh_interval_sec=0,
    )
    db.add(dash_bpmn)
    db.flush()

    page = DashboardPage(dashboard_id=dash_bpmn.id, title="نمای فرآیند و گلوگاه‌ها", page_order=0)
    db.add(page)
    db.flush()

    created_bpmn_widgets = []

    def _add_bpmn_widget(title: str, chart_type: str, x: int, y: int, w: int, h: int, vspec: dict[str, Any]):
        wi = Widget(
            dashboard_id=dash_bpmn.id,
            page_id=page.id,
            title=title,
            description="",
            chart_type=chart_type,
            dataset_id=ds_bpmn.id,
            layout_x=x,
            layout_y=y,
            layout_w=w,
            layout_h=h,
        )
        db.add(wi)
        db.flush()
        db.add(
            WidgetConfig(
                widget_id=wi.id,
                style_json={},
                visualization_json={"show_legend": True, "show_tooltip": True, "data_labels": False},
                interactions_json={"cross_filter": True},
                advanced_json={},
            )
        )
        db.add(
            WidgetQuery(
                widget_id=wi.id,
                dataset_id=ds_bpmn.id,
                query_mode="visual",
                visual_spec_json=vspec,
            )
        )
        db.add(WidgetDrillRule(widget_id=wi.id, hierarchy_json=[], level_rules_json=[]))
        created_bpmn_widgets.append(wi)
        return wi

    _add_bpmn_widget("کل رویدادها و درخواست‌های فرآیند", "kpi", 0, 0, 3, 3, {"y_field": "*", "aggregation": "COUNT", "prefix": "", "suffix": ""})
    _add_bpmn_widget("میانگین زمان انتظار در مراحل", "kpi", 3, 0, 3, 3, {"y_field": "wait_hours", "aggregation": "AVG", "prefix": "", "suffix": " ساعت"})
    _add_bpmn_widget("بیشترین زمان توقف در کارتابل", "kpi", 6, 0, 3, 3, {"y_field": "wait_hours", "aggregation": "MAX", "prefix": "", "suffix": " ساعت"})
    _add_bpmn_widget("میانگین سقف مجاز SLA", "kpi", 9, 0, 3, 3, {"y_field": "sla_hours", "aggregation": "AVG", "prefix": "", "suffix": " ساعت"})

    _add_bpmn_widget(
        "نقشه فرآیند BPMN، گلوگاه‌ها و نقشه حرارتی زمان انتظار",
        "bpmn",
        0,
        3,
        12,
        10,
        {
            "x_field": "step_name",
            "y_field": "wait_hours",
            "aggregation": "AVG",
            "bpmn_config": DEFAULT_BPMN_GRAPH_PAYLOAD,
        },
    )
    _add_bpmn_widget(
        "میانگین زمان انتظار به تفکیک گام (کشف گلوگاه)",
        "horizontal_bar",
        0,
        13,
        6,
        7,
        {"x_field": "step_name", "y_field": "wait_hours", "aggregation": "AVG", "sort_by": "value_desc", "limit": 20},
    )
    _add_bpmn_widget(
        "میانگین زمان انتظار در کارتابل افراد",
        "bar",
        6,
        13,
        6,
        7,
        {"x_field": "assignee_name", "y_field": "wait_hours", "aggregation": "AVG", "sort_by": "value_desc", "limit": 20},
    )
    _add_bpmn_widget(
        "فهرست درخواست‌ها و وضعیت کارتابل‌ها",
        "table",
        0,
        20,
        12,
        7,
        {"aggregation": "RAW", "limit": 50},
    )

    db.add(
        DashboardLayout(
            page_id=page.id,
            columns_count=12,
            layout_json=[
                {"i": wi.id, "x": wi.layout_x, "y": wi.layout_y, "w": wi.layout_w, "h": wi.layout_h}
                for wi in created_bpmn_widgets
            ],
        )
    )

    for fname, label in [
        ("step_name", "گام فرآیند"),
        ("status", "وضعیت درخواست"),
        ("department", "واحد متقاضی"),
        ("assignee_name", "مسئول کارتابل"),
    ]:
        db.add(
            DashboardFilter(
                dashboard_id=dash_bpmn.id,
                page_id=page.id,
                scope="global",
                name=label,
                field_name=fname,
                dataset_id=ds_bpmn.id,
                filter_type="multi_select",
                operator="in",
            )
        )
    db.commit()


def seed_sample_bi_content(
    db: Session,
    org: Organization,
    owner_user: User | None,
    theme_objs: dict[str, DashboardTheme],
    scoped_role: Role | None = None,
    rls_group: Group | None = None,
) -> None:
    """Seeds sample ERP data source, DuckDB analytical datasets, ETL pipelines & 3 dashboards."""
    create_sample_erp_database(settings.SAMPLE_ERP_DB_PATH)
    if db.query(Dataset).filter(Dataset.organization_id == org.id, Dataset.warehouse_table == "wh_enterprise_sales").first():
        return
    owner_id = owner_user.id if owner_user else None

    # Operational ERP Data Source & Datasets & ETL Pipelines
    erp_source = DataSource(
        organization_id=org.id,
        name="Enterprise ERP Operational DB",
        source_type="sqlite",
        read_only=True,
        config_json={"file_path": settings.SAMPLE_ERP_DB_PATH, "default_table": "erp_orders"},
        refresh_policy="scheduled",
        health_status="healthy",
        last_tested_at=datetime.now(timezone.utc),
        created_by=owner_id,
    )
    db.add(erp_source)
    db.flush()
    db.add(DataSourceCredential(data_source_id=erp_source.id, encrypted_payload=encrypt_secret(json.dumps({}))))

    ds_sales = Dataset(
        organization_id=org.id,
        data_source_id=erp_source.id,
        name="Enterprise Sales Fact",
        description="Star-schema sales transactions extracted via incremental ETL from ERP",
        warehouse_table="wh_enterprise_sales",
        schema_type="fact",
        refresh_policy="incremental",
    )
    ds_expenses = Dataset(
        organization_id=org.id,
        data_source_id=erp_source.id,
        name="Departmental Expenses Fact",
        description="Departmental budget vs actual expenditures synced via CDC Hash",
        warehouse_table="wh_enterprise_expenses",
        schema_type="fact",
        refresh_policy="scheduled",
    )
    ds_hr = Dataset(
        organization_id=org.id,
        data_source_id=erp_source.id,
        name="Workforce & Compensation",
        description="Protected HR workforce directory with RLS & Column Masking examples",
        warehouse_table="wh_hr_workforce",
        schema_type="dimension",
        refresh_policy="cached",
    )
    db.add_all([ds_sales, ds_expenses, ds_hr])
    db.flush()

    # Dataset Relationship (Star Join)
    db.add(
        DatasetRelationship(
            left_dataset_id=ds_sales.id,
            left_field="department",
            right_dataset_id=ds_expenses.id,
            right_field="department",
            join_type="left",
            cardinality="many_to_one",
        )
    )

    # ETL Pipelines + Schedules
    pl_sales = EtlPipeline(
        organization_id=org.id,
        name="ERP Orders Incremental Sync",
        description="Extracts new/updated orders incrementally using updated_at watermark",
        data_source_id=erp_source.id,
        target_dataset_id=ds_sales.id,
        extract_mode="incremental",
        incremental_column="updated_at",
        primary_key_column="order_id",
        source_query="SELECT * FROM erp_orders",
        priority=1,
    )
    pl_expenses = EtlPipeline(
        organization_id=org.id,
        name="ERP Expenses CDC Hash Sync",
        description="Detects changed voucher records using SHA-256 content hashing",
        data_source_id=erp_source.id,
        target_dataset_id=ds_expenses.id,
        extract_mode="cdc_hash",
        primary_key_column="expense_id",
        source_query="SELECT * FROM erp_expenses",
        priority=2,
    )
    pl_hr = EtlPipeline(
        organization_id=org.id,
        name="ERP Workforce Full Sync",
        description="Synchronizes workforce records into analytical dimension table",
        data_source_id=erp_source.id,
        target_dataset_id=ds_hr.id,
        extract_mode="full",
        primary_key_column="employee_id",
        source_query="SELECT * FROM erp_employees",
        priority=3,
    )
    db.add_all([pl_sales, pl_expenses, pl_hr])
    db.flush()

    db.add_all([
        EtlSchedule(pipeline_id=pl_sales.id, schedule_type="interval", interval_minutes=15, is_enabled=True),
        EtlSchedule(pipeline_id=pl_expenses.id, schedule_type="interval", interval_minutes=30, is_enabled=True),
        EtlSchedule(pipeline_id=pl_hr.id, schedule_type="daily", interval_minutes=1440, is_enabled=True),
    ])
    db.commit()

    # Run Initial ETL Extraction into DuckDB Warehouse!
    run_etl_pipeline(db, pl_sales.id, trigger_type="initial_seed")
    run_etl_pipeline(db, pl_expenses.id, trigger_type="initial_seed")
    run_etl_pipeline(db, pl_hr.id, trigger_type="initial_seed")

    # Pre-Aggregated Materialized Summary Views (Section 27E)
    mv_sales_sql = 'SELECT year, quarter, month, region, category, SUM(revenue) AS total_revenue, SUM(cost) AS total_cost, SUM(profit) AS total_profit, COUNT(*) AS order_count FROM wh_enterprise_sales GROUP BY 1,2,3,4,5'
    cnt_mv = warehouse.create_materialized_summary("mv_sales_monthly", mv_sales_sql)
    db.add(
        MaterializedViewMetadata(
            dataset_id=ds_sales.id,
            view_name="mv_sales_monthly",
            granularity="monthly",
            definition_sql=mv_sales_sql,
            row_count=cnt_mv,
            last_refreshed_at=datetime.now(timezone.utc),
        )
    )

    if rls_group is not None:
        db.add(
            AccessPolicy(
                organization_id=org.id,
                name="Regional Manager Row-Level Security",
                policy_type="rls",
                dataset_id=ds_sales.id,
                target_type="group",
                target_id=rls_group.id,
                rule_json={"column": "region", "operator": "eq", "value": "${user.region}"},
                priority=10,
                is_active=True,
            )
        )
    db.add(
        AccessPolicy(
            organization_id=org.id,
            name="HR Email Partial Masking",
            policy_type="masking",
            dataset_id=ds_hr.id,
            target_type="all",
            rule_json={"column": "email", "strategy": "partial"},
            priority=20,
            is_active=True,
        )
    )

    # Semantic Layer Metrics (Section 3)
    db.add_all([
        SemanticMetric(organization_id=org.id, dataset_id=ds_sales.id, name="Total Revenue", slug="total_revenue", description="Gross enterprise sales revenue", formula="SUM(revenue)", format_type="currency", unit="$"),
        SemanticMetric(organization_id=org.id, dataset_id=ds_sales.id, name="Total Cost", slug="total_cost", description="Total COGS and delivery cost", formula="SUM(cost)", format_type="currency", unit="$"),
        SemanticMetric(organization_id=org.id, dataset_id=ds_sales.id, name="Net Profit", slug="net_profit", description="Net gross profit", formula="SUM(profit)", format_type="currency", unit="$"),
        SemanticMetric(organization_id=org.id, dataset_id=ds_sales.id, name="Profit Margin %", slug="profit_margin", description="Net profit margin percentage", formula="(SUM(profit) * 100.0) / NULLIF(SUM(revenue), 0)", format_type="percentage", unit="%"),
        SemanticMetric(organization_id=org.id, dataset_id=ds_sales.id, name="Order Volume", slug="order_count", description="Total distinct orders", formula="COUNT_DISTINCT(order_id)", format_type="number", unit=""),
    ])

    # Saved Queries
    db.add(
        SavedQuery(
            organization_id=org.id,
            name="Regional Revenue & Profit Summary",
            description="Aggregated revenue, cost, and profit margin across regions",
            dataset_id=ds_sales.id,
            query_mode="sql",
            sql_text=(
                "SELECT region, COUNT(*) AS orders, ROUND(SUM(revenue), 2) AS total_revenue, "
                "ROUND(SUM(profit), 2) AS total_profit "
                "FROM wh_enterprise_sales GROUP BY region ORDER BY total_revenue DESC"
            ),
            created_by=owner_id,
        )
    )

    # 6. Workspaces & 3 Enterprise Dashboards
    ws = db.query(Workspace).filter(Workspace.organization_id == org.id).first()
    if not ws:
        ws = Workspace(organization_id=org.id, name="Executive Intelligence Workspace", description="Core enterprise BI dashboards", created_by=owner_id)
        db.add(ws)
        db.flush()

    # Helper to create widgets cleanly
    def _add_widget(
        dash: Dashboard, page: DashboardPage, title: str, chart_type: str, dataset: Dataset,
        x: int, y: int, w: int, h: int, visual_spec: dict, style: dict = None,
        viz: dict = None, drill_hierarchy: list = None, drill_rules: list = None,
        drill_through_page_id: str = None, interactions: dict = None,
    ) -> Widget:
        wid = Widget(
            dashboard_id=dash.id, page_id=page.id, title=title, chart_type=chart_type,
            dataset_id=dataset.id, layout_x=x, layout_y=y, layout_w=w, layout_h=h,
        )
        db.add(wid)
        db.flush()
        db.add(WidgetConfig(
            widget_id=wid.id,
            style_json=style or {"number_format": "currency", "decimals": 0, "border_radius": 12},
            visualization_json=viz or {"show_legend": True, "show_tooltip": True, "data_labels": False},
            interactions_json=interactions or {"cross_filter": True},
            advanced_json={},
        ))
        db.add(WidgetQuery(widget_id=wid.id, dataset_id=dataset.id, query_mode="visual", visual_spec_json=visual_spec))
        if drill_hierarchy:
            db.add(WidgetDrillRule(
                widget_id=wid.id,
                hierarchy_json=[{"field": f} for f in drill_hierarchy],
                level_rules_json=drill_rules or [],
                drill_through_page_id=drill_through_page_id,
            ))
        if not db.query(DataLineage).filter(
            DataLineage.source_type == "dataset",
            DataLineage.source_id == dataset.id,
            DataLineage.target_type == "dashboard",
            DataLineage.target_id == dash.id,
        ).first():
            db.add(DataLineage(source_type="dataset", source_id=dataset.id, target_type="dashboard", target_id=dash.id))
        return wid

    # DASHBOARD 1: Enterprise Revenue & Executive BI
    d1 = Dashboard(
        workspace_id=ws.id,
        title="Enterprise Revenue & Executive Intelligence",
        description="Executive revenue, profitability, regional performance, and hierarchical time drill-down",
        theme_id=theme_objs["light-enterprise"].id,
        created_by=owner_id,
    )
    db.add(d1)
    db.flush()
    d1_p1 = DashboardPage(dashboard_id=d1.id, title="Executive Overview", page_order=0)
    d1_p2 = DashboardPage(dashboard_id=d1.id, title="Transaction Drill-Through", page_order=1, is_drill_through=True)
    db.add_all([d1_p1, d1_p2])
    db.flush()

    w1_kpi1 = _add_widget(d1, d1_p1, "Total Revenue", "kpi", ds_sales, 0, 0, 3, 3, {"y_field": "revenue", "aggregation": "SUM"}, {"number_format": "currency", "target_value": 150000000, "previous_value": 132000000, "show_sparkline": True})
    w1_kpi2 = _add_widget(d1, d1_p1, "Net Profit", "kpi", ds_sales, 3, 0, 3, 3, {"y_field": "profit", "aggregation": "SUM"}, {"number_format": "currency", "target_value": 110000000, "previous_value": 95000000, "show_sparkline": True})
    w1_kpi3 = _add_widget(d1, d1_p1, "Total Orders", "kpi", ds_sales, 6, 0, 3, 3, {"y_field": "order_id", "aggregation": "COUNT"}, {"number_format": "number", "target_value": 1500, "previous_value": 1050, "show_sparkline": True})
    w1_kpi4 = _add_widget(d1, d1_p1, "Target Achievement", "gauge", ds_sales, 9, 0, 3, 3, {"y_field": "margin_pct", "aggregation": "AVG"}, {"number_format": "percentage", "max_value": 100})

    w1_trend = _add_widget(
        d1, d1_p1, "Revenue Performance (Click Bar to Drill Year → Quarter → Month → Day)", "bar", ds_sales,
        0, 3, 8, 7, {"x_field": "year", "y_field": "revenue", "aggregation": "SUM"},
        drill_hierarchy=["year", "quarter", "month", "day"],
        drill_rules=[
            {"level": 0, "chart_type": "bar"},
            {"level": 1, "chart_type": "line"},
            {"level": 2, "chart_type": "area"},
            {"level": 3, "chart_type": "table"},
        ],
        drill_through_page_id=d1_p2.id,
    )
    w1_reg = _add_widget(
        d1, d1_p1, "Revenue by Region", "donut", ds_sales,
        8, 3, 4, 7, {"x_field": "region", "y_field": "revenue", "aggregation": "SUM"},
        drill_hierarchy=["region", "province", "city"],
        drill_rules=[{"level": 0, "chart_type": "donut"}, {"level": 1, "chart_type": "bar"}, {"level": 2, "chart_type": "horizontal_bar"}],
    )
    w1_prod = _add_widget(
        d1, d1_p1, "Product Revenue & Profit Comparison", "combo", ds_sales,
        0, 10, 6, 7, {"x_field": "product", "y_field": "revenue", "secondary_y_field": "profit", "aggregation": "SUM", "secondary_aggregation": "SUM"}
    )
    w1_chan = _add_widget(
        d1, d1_p1, "Sales by Channel & Category", "stacked_bar", ds_sales,
        6, 10, 6, 7, {"x_field": "channel", "series_field": "category", "y_field": "revenue", "aggregation": "SUM"}
    )
    db.add(DashboardLayout(
        page_id=d1_p1.id, columns_count=12,
        layout_json=[
            {"i": w.id, "x": w.layout_x, "y": w.layout_y, "w": w.layout_w, "h": w.layout_h}
            for w in [w1_kpi1, w1_kpi2, w1_kpi3, w1_kpi4, w1_trend, w1_reg, w1_prod, w1_chan]
        ],
    ))

    # Page 2: Drill-Through Detail Table
    w1_p2_tbl = _add_widget(
        d1, d1_p2, "Detailed Sales Transactions", "table", ds_sales,
        0, 0, 12, 10,
        {"columns": ["order_code", "order_date", "region", "city", "product", "channel", "quantity", "revenue", "profit"], "aggregation": "RAW", "limit": 200},
        style={"number_format": "currency"},
    )
    db.add(DashboardLayout(page_id=d1_p2.id, columns_count=12, layout_json=[{"i": w1_p2_tbl.id, "x": 0, "y": 0, "w": 12, "h": 10}]))

    # Global & Cascading Filters on Dashboard 1
    f_year = DashboardFilter(dashboard_id=d1.id, scope="global", name="Year", field_name="year", dataset_id=ds_sales.id, filter_type="multi_select", operator="in")
    f_region = DashboardFilter(dashboard_id=d1.id, scope="global", name="Region", field_name="region", dataset_id=ds_sales.id, filter_type="multi_select", operator="in")
    db.add_all([f_year, f_region])
    db.flush()
    db.add(DashboardFilter(dashboard_id=d1.id, scope="global", name="City", field_name="city", dataset_id=ds_sales.id, filter_type="searchable_dropdown", operator="in", parent_filter_id=f_region.id))
    db.add(DashboardFilter(dashboard_id=d1.id, scope="dashboard", page_id=d1_p1.id, name="Product Category", field_name="category", dataset_id=ds_sales.id, filter_type="dropdown", operator="eq"))

    # DASHBOARD 2: Departmental Cost & Budget Control
    d2 = Dashboard(
        workspace_id=ws.id,
        title="Departmental Cost & Budget Control",
        description="Operational expenditure tracking, budget variance, and financial efficiency",
        theme_id=theme_objs["dark-enterprise"].id,
        created_by=owner_id,
    )
    db.add(d2)
    db.flush()
    d2_p1 = DashboardPage(dashboard_id=d2.id, title="Budget Overview", page_order=0)
    db.add(d2_p1)
    db.flush()
    d2_w1 = _add_widget(d2, d2_p1, "Total Actual Spend", "kpi", ds_expenses, 0, 0, 4, 3, {"y_field": "actual_amount", "aggregation": "SUM"}, {"number_format": "currency", "show_sparkline": True})
    d2_w2 = _add_widget(d2, d2_p1, "Allocated Budget", "kpi", ds_expenses, 4, 0, 4, 3, {"y_field": "budget_amount", "aggregation": "SUM"}, {"number_format": "currency"})
    d2_w3 = _add_widget(d2, d2_p1, "Budget Variance", "kpi", ds_expenses, 8, 0, 4, 3, {"y_field": "variance_amount", "aggregation": "SUM"}, {"number_format": "currency"})
    d2_w4 = _add_widget(d2, d2_p1, "Spend by Department", "horizontal_bar", ds_expenses, 0, 3, 6, 7, {"x_field": "department", "y_field": "actual_amount", "aggregation": "SUM"})
    d2_w5 = _add_widget(d2, d2_p1, "Expense Category Breakdown (Treemap)", "treemap", ds_expenses, 6, 3, 6, 7, {"x_field": "expense_category", "y_field": "actual_amount", "aggregation": "SUM"})
    d2_w6 = _add_widget(d2, d2_p1, "Department vs Category Heatmap", "heatmap", ds_expenses, 0, 10, 7, 7, {"x_field": "department", "series_field": "expense_category", "y_field": "actual_amount", "aggregation": "SUM"})
    d2_w7 = _add_widget(d2, d2_p1, "Budget Burn Progress", "progress", ds_expenses, 7, 10, 5, 7, {"x_field": "department", "y_field": "actual_amount", "secondary_y_field": "budget_amount", "aggregation": "SUM", "secondary_aggregation": "SUM"})
    db.add(DashboardLayout(
        page_id=d2_p1.id, columns_count=12,
        layout_json=[{"i": w.id, "x": w.layout_x, "y": w.layout_y, "w": w.layout_w, "h": w.layout_h} for w in [d2_w1, d2_w2, d2_w3, d2_w4, d2_w5, d2_w6, d2_w7]],
    ))
    db.add(DashboardFilter(dashboard_id=d2.id, scope="global", name="Department", field_name="department", dataset_id=ds_expenses.id, filter_type="multi_select", operator="in"))

    # DASHBOARD 3: Advanced Visualization Showcase Template
    d3 = Dashboard(
        workspace_id=ws.id,
        title="Advanced BI Visualizations Showcase",
        description="Template featuring Funnel, Radar, Scatter, Waterfall, Sunburst, Geographic Map, Pivot & Statistical Charts",
        theme_id=theme_objs["glassmorphism"].id,
        is_template=True,
        is_public=True,
        created_by=owner_id,
    )
    db.add(d3)
    db.flush()
    d3_p1 = DashboardPage(dashboard_id=d3.id, title="Analytical Gallery", page_order=0)
    db.add(d3_p1)
    db.flush()
    d3_widgets = [
        _add_widget(d3, d3_p1, "Conversion Funnel by Stage", "funnel", ds_sales, 0, 0, 4, 6, {"x_field": "channel", "y_field": "revenue", "aggregation": "SUM"}),
        _add_widget(d3, d3_p1, "Department Radar Profile", "radar", ds_sales, 4, 0, 4, 6, {"x_field": "department", "y_field": "revenue", "secondary_y_field": "profit", "aggregation": "SUM", "secondary_aggregation": "SUM"}),
        _add_widget(d3, d3_p1, "Regional Revenue Waterfall", "waterfall", ds_sales, 8, 0, 4, 6, {"x_field": "region", "y_field": "profit", "aggregation": "SUM"}),
        _add_widget(d3, d3_p1, "Regional Geo Distribution", "geo_map", ds_sales, 0, 6, 6, 7, {"x_field": "city", "y_field": "revenue", "aggregation": "SUM"}),
        _add_widget(d3, d3_p1, "Revenue vs Profit Bubble Analysis", "bubble", ds_sales, 6, 6, 6, 7, {"x_field": "product", "y_field": "revenue", "secondary_y_field": "profit", "size_field": "quantity", "aggregation": "SUM", "secondary_aggregation": "SUM"}),
        _add_widget(d3, d3_p1, "Channel → Category Sankey Flow", "sankey", ds_sales, 0, 13, 6, 7, {"x_field": "channel", "series_field": "category", "y_field": "revenue", "aggregation": "SUM"}),
        _add_widget(d3, d3_p1, "Pivot Matrix: Region × Category", "pivot_table", ds_sales, 6, 13, 6, 7, {"x_field": "region", "series_field": "category", "y_field": "revenue", "aggregation": "SUM"}),
    ]
    db.add(DashboardLayout(
        page_id=d3_p1.id, columns_count=12,
        layout_json=[{"i": w.id, "x": w.layout_x, "y": w.layout_y, "w": w.layout_w, "h": w.layout_h} for w in d3_widgets],
    ))

    if scoped_role is not None:
        db.add(
            ResourcePermission(
                organization_id=org.id,
                subject_type="role",
                subject_id=scoped_role.id,
                resource_type="dashboard",
                resource_id=d1.id,
                action="view",
                effect="allow",
                inherit_to_children=False,
            )
        )
    db.commit()
    ensure_contractor_sample_dataset(db, org)
    ensure_bpmn_process_sample_dataset(db, org)


def seed_database(db: Session) -> None:
    """Idempotent Enterprise Bootstrapper.
    In development/demo mode (BI_SEED_DEMO_USERS=True), creates demo users + mock AD + dashboards.
    In production/Windows standalone mode (BI_SEED_DEMO_USERS=False), seeds ONLY roles & themes
    with zero default passwords or demo user accounts.
    """
    existing_org = db.query(Organization).filter(Organization.slug == "enterprise-corp").first()
    if existing_org:
        # Ensure warehouse tables exist if already seeded earlier and warehouse file was cleaned
        if db.query(EtlPipeline).count() > 0 and not warehouse.table_exists("wh_enterprise_sales"):
            for pl in db.query(EtlPipeline).all():
                try:
                    run_etl_pipeline(db, pl.id, trigger_type="seed")
                except Exception:
                    pass
        if db.query(Dataset).filter(Dataset.organization_id == existing_org.id, Dataset.warehouse_table == "wh_enterprise_sales").first():
            ensure_contractor_sample_dataset(db, existing_org)
            ensure_bpmn_process_sample_dataset(db, existing_org)
        return

    org, roles, theme_objs = seed_core_catalog(db)

    if not settings.BI_SEED_DEMO_USERS:
        # Production / Windows Secure Mode: zero default passwords, zero demo accounts, zero mock AD!
        db.commit()
        return

    # Development / Acceptance Demo Mode
    def _mk_user(username: str, email: str, pw: str, full_name: str, dept: str, region: str, role: Role):
        u = User(
            organization_id=org.id,
            username=username,
            email=email,
            password_hash=hash_password(pw),
            auth_source="local",
            is_active=True,
        )
        db.add(u)
        db.flush()
        db.add(UserProfile(user_id=u.id, full_name=full_name, department=dept, org_unit=dept, region=region))
        db.add(UserRole(user_id=u.id, role_id=role.id))
        return u

    admin_user = _mk_user("admin", "admin@enterprise.local", "Admin!2026", "System Administrator", "IT & Security", "All", roles["super_admin"])
    _mk_user("designer", "designer@enterprise.local", "Designer!2026", "Darius BI Designer", "Analytics", "All", roles["dashboard_designer"])
    _mk_user("analyst", "analyst@enterprise.local", "Analyst!2026", "Laleh Data Analyst", "Finance", "All", roles["data_analyst"])
    _mk_user("viewer", "viewer@enterprise.local", "Viewer!2026", "Kian Report Viewer", "Operations", "All", roles["report_viewer"])
    north_mgr = _mk_user("north_mgr", "north.mgr@enterprise.local", "Manager!2026", "Neda North Manager", "Sales", "North", roles["report_viewer"])
    south_mgr = _mk_user("south_mgr", "south.mgr@enterprise.local", "Manager!2026", "Reza South Manager", "Sales", "South", roles["report_viewer"])

    # Groups
    grp_exec = Group(organization_id=org.id, name="Executive Leadership", description="Executive Dashboard Access", source="local")
    grp_north = Group(organization_id=org.id, name="Regional Sales Managers", description="RLS Regional Managers", source="local")
    db.add_all([grp_exec, grp_north])
    db.flush()
    db.add_all([
        GroupMember(group_id=grp_exec.id, user_id=admin_user.id),
        GroupMember(group_id=grp_north.id, user_id=north_mgr.id),
        GroupMember(group_id=grp_north.id, user_id=south_mgr.id),
    ])

    # Default Active Directory Connection (Demo Mode Only)
    db.add(
        DirectoryConnection(
            organization_id=org.id,
            name="Corporate Active Directory",
            domain_name="corp.enterprise.local",
            server_url="ldap://dc01.corp.enterprise.local:389",
            use_ldaps=False,
            base_dn="DC=corp,DC=enterprise,DC=local",
            bind_dn="CN=bi-svc,OU=ServiceAccounts,DC=corp,DC=enterprise,DC=local",
            bind_password_encrypted=encrypt_secret("AdServiceBind!2026"),
            directory_mode="mock_ad",
            sync_policy="manual",
            selected_groups_json=[],
        )
    )

    seed_sample_bi_content(
        db,
        org=org,
        owner_user=admin_user,
        theme_objs=theme_objs,
        scoped_role=roles["custom_scoped"],
        rls_group=grp_north,
    )
