"""
5-Stage Intelligent KPI & Multi-Page Dashboard Engine.

Integrates tightly with:
- RLS / CLS / Masking (`app.core.rls_cls`) & RBAC (`app.core.permissions`)
- SQL AST Guard (`app.core.sql_guard`) & Formula Engine (`app.engines.formula_engine`)
- Query Execution Engine (`app.engines.query_engine`)
- Descriptive Dashboard Builder & Relationship Graph (`app.engines.descriptive_builder`)
- TTL / Versioned Cache (`app.core.cache`)
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import re
import time
from typing import Any, Optional
import sqlglot
from sqlalchemy.orm import Session

from app.core.cache import cache_manager
from app.core.permissions import permission_resolver
from app.core.rls_cls import (
    DataSecurityContext,
    apply_cls_and_masking,
    build_data_security_context,
    build_security_projection_and_where,
)
from app.core.sql_guard import validate_formula_ast
from app.engines.descriptive_builder import (
    _build_factual_narrative_fa,
    _fmt_num,
    analyze_dataset_relationships,
)
from app.engines.formula_engine import expand_jalali_sql_calls, validate_and_compile_formula
from app.engines.query_engine import build_visual_query_sql
from app.engines.warehouse import sanitize_identifier, warehouse
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
)
from app.models.data import DataLineage, Dataset, DatasetField, DatasetRelationship, SemanticMetric
from app.models.identity import User

IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
NUMERIC_SQL_HINTS = ("int", "double", "float", "decimal", "numeric", "real", "hugeint", "bigint", "smallint")
DATE_SQL_HINTS = ("date", "timestamp", "time", "datetime")
BOOL_SQL_HINTS = ("bool",)

LARGE_TABLE_THRESHOLD = 100_000
SAMPLE_TARGET_ROWS = 40_000
MAX_PROFILE_BATCH = 10

# 22 Semantic Classes + Aggregation Policies (Section 3 & Section 5)
SEMANTIC_CLASS_META: dict[str, dict[str, Any]] = {
    "Additive Measure": {"fa": "سنجه جمع‌پذیر", "policy": "additive", "default_agg": "SUM", "allowed": ["SUM", "AVG", "MEDIAN", "MIN", "MAX", "STDDEV"], "sum_allowed": True},
    "Semi-additive Measure": {"fa": "سنجه نیمه‌جمع‌پذیر", "policy": "semi_additive", "default_agg": "LAST_VALUE", "allowed": ["LAST_VALUE", "AVG", "MIN", "MAX", "MEDIAN"], "sum_allowed": False},
    "Non-additive Measure": {"fa": "سنجه غیرجمع‌پذیر", "policy": "non_additive", "default_agg": "AVG", "allowed": ["AVG", "MEDIAN", "MIN", "MAX"], "sum_allowed": False},
    "Count Measure": {"fa": "سنجه شمارشی", "policy": "count", "default_agg": "SUM", "allowed": ["SUM", "AVG", "MAX", "MIN", "MEDIAN"], "sum_allowed": True},
    "Ratio": {"fa": "نسبت", "policy": "non_additive", "default_agg": "AVG", "allowed": ["AVG", "MEDIAN", "MIN", "MAX"], "sum_allowed": False},
    "Percentage": {"fa": "درصد / نرخ", "policy": "non_additive", "default_agg": "AVG", "allowed": ["AVG", "MEDIAN", "MIN", "MAX"], "sum_allowed": False},
    "Currency": {"fa": "مبلغ مالی", "policy": "additive", "default_agg": "SUM", "allowed": ["SUM", "AVG", "MEDIAN", "MIN", "MAX", "STDDEV"], "sum_allowed": True},
    "Duration": {"fa": "مدت زمان / تاخیر", "policy": "duration", "default_agg": "AVG", "allowed": ["AVG", "MEDIAN", "MAX", "MIN", "P90", "SUM"], "sum_allowed": True},
    "Date/Time": {"fa": "تاریخ و زمان", "policy": "temporal", "default_agg": "NONE", "allowed": ["COUNT_DISTINCT", "MIN", "MAX", "NONE"], "sum_allowed": False},
    "Identifier": {"fa": "شناسه / کد یکتا", "policy": "identifier", "default_agg": "COUNT_DISTINCT", "allowed": ["COUNT_DISTINCT", "COUNT", "NONE"], "sum_allowed": False},
    "Category": {"fa": "دسته‌بندی", "policy": "dimension", "default_agg": "COUNT_DISTINCT", "allowed": ["COUNT_DISTINCT", "COUNT", "NONE"], "sum_allowed": False},
    "Status": {"fa": "وضعیت", "policy": "dimension", "default_agg": "COUNT_DISTINCT", "allowed": ["COUNT_DISTINCT", "COUNT", "NONE"], "sum_allowed": False},
    "Geographic": {"fa": "موقعیت جغرافیایی", "policy": "dimension", "default_agg": "COUNT_DISTINCT", "allowed": ["COUNT_DISTINCT", "COUNT", "NONE"], "sum_allowed": False},
    "Boolean": {"fa": "بولی / دودویی", "policy": "boolean", "default_agg": "COUNT", "allowed": ["COUNT", "AVG", "NONE"], "sum_allowed": False},
    "Target": {"fa": "هدف / بودجه", "policy": "additive", "default_agg": "SUM", "allowed": ["SUM", "AVG", "MIN", "MAX"], "sum_allowed": True},
    "Actual": {"fa": "عملکرد واقعی", "policy": "additive", "default_agg": "SUM", "allowed": ["SUM", "AVG", "MEDIAN", "MIN", "MAX"], "sum_allowed": True},
    "Quantity": {"fa": "تعداد / مقدار", "policy": "additive", "default_agg": "SUM", "allowed": ["SUM", "AVG", "MEDIAN", "MIN", "MAX"], "sum_allowed": True},
    "Unit Price": {"fa": "قیمت واحد / فی", "policy": "non_additive", "default_agg": "AVG", "allowed": ["AVG", "MEDIAN", "MIN", "MAX"], "sum_allowed": False},
    "Cost": {"fa": "هزینه", "policy": "additive", "default_agg": "SUM", "allowed": ["SUM", "AVG", "MEDIAN", "MIN", "MAX", "STDDEV"], "sum_allowed": True},
    "Revenue": {"fa": "فروش / درآمد", "policy": "additive", "default_agg": "SUM", "allowed": ["SUM", "AVG", "MEDIAN", "MIN", "MAX", "STDDEV"], "sum_allowed": True},
    "Profit": {"fa": "سود", "policy": "additive", "default_agg": "SUM", "allowed": ["SUM", "AVG", "MEDIAN", "MIN", "MAX", "STDDEV"], "sum_allowed": True},
    "Balance": {"fa": "مانده / موجودی لحظه‌ای", "policy": "semi_additive", "default_agg": "LAST_VALUE", "allowed": ["LAST_VALUE", "AVG", "MIN", "MAX", "MEDIAN"], "sum_allowed": False},
}

# Canonical Business, Process, IT Operations, Security, Network & Mixed Domain Definitions
DOMAIN_FAMILY_MAP: dict[str, str] = {
    "sales_marketing": "Business",
    "finance_accounting": "Business",
    "hr": "Business",
    "project_contracting": "Business",
    "contract_management": "Business",
    "inventory_procurement": "Business",
    "manufacturing_operations": "Business",
    "energy_power": "Business",
    "logistics_transport": "Business",
    "customer_crm": "Business",
    "org_management": "Business",
    "general": "Business",
    "bpmn_process": "Process/Workflow",
    "service_support": "Process/Workflow",
    "it_observability": "IT Operations",
    "security": "Security",
    "network": "Network",
    "mixed": "Mixed",
}

DOMAIN_FAMILY_FA: dict[str, str] = {
    "Business": "کسب‌وکار و مالی",
    "Process/Workflow": "فرآیند و گردش کار",
    "IT Operations": "عملیات فناوری اطلاعات",
    "Security": "امنیت و لاگ‌های امنیتی",
    "Network": "شبکه و ترافیک",
    "Mixed": "ترکیبی (چنددامنه)",
}

DOMAIN_DEFINITIONS: dict[str, dict[str, Any]] = {
    "sales_marketing": {
        "fa": "فروش و بازاریابی",
        "family": "Business",
        "field_keywords": ["revenue", "sales", "order", "discount", "campaign", "lead", "channel", "conversion", "aov", "فروش", "سفارش", "تخفیف", "کمپین", "درآمد"],
        "dataset_keywords": ["sales", "order", "marketing", "retail", "commerce", "فروش", "سفارشات", "بازاریابی"],
    },
    "finance_accounting": {
        "fa": "مالی و حسابداری",
        "family": "Business",
        "field_keywords": ["budget", "actual", "expense", "cost", "profit", "balance", "cash", "invoice", "ledger", "receivable", "payable", "tax", "هزینه", "بودجه", "سود", "مانده", "تراز", "مالی", "حساب", "فاکتور"],
        "dataset_keywords": ["finance", "accounting", "budget", "ledger", "invoice", "مالی", "حسابداری", "بودجه"],
    },
    "hr": {
        "fa": "منابع انسانی",
        "family": "Business",
        "field_keywords": ["employee", "staff", "salary", "payroll", "wage", "hire", "attrition", "turnover", "tenure", "absence", "overtime", "department", "headcount", "کارمند", "حقوق", "پرسنل", "استخدام", "ترک کار", "اضافه‌کار"],
        "dataset_keywords": ["hr", "employee", "payroll", "staff", "workforce", "منابع انسانی", "پرسنل", "حقوق"],
    },
    "project_contracting": {
        "fa": "پروژه و پیمانکاری",
        "family": "Business",
        "field_keywords": ["project", "milestone", "ev", "pv", "ac", "cpi", "spi", "progress_pct", "planned_cost", "actual_cost", "wbs", "contractor", "پروژه", "پیمانکار", "پیشرفت", "زمان‌بندی"],
        "dataset_keywords": ["project", "pmo", "contracting", "construction", "پروژه", "پیمانکاری"],
    },
    "contract_management": {
        "fa": "مدیریت قراردادها",
        "family": "Business",
        "field_keywords": ["contract", "contract_value", "renewal", "expiry", "clause", "vendor", "amendment", "penalty", "قرارداد", "مبلغ قرارداد", "تمدید", "جریمه"],
        "dataset_keywords": ["contract", "agreement", "procurement", "قرارداد"],
    },
    "bpmn_process": {
        "fa": "مدیریت فرآیند (BPMN)",
        "family": "Process/Workflow",
        "field_keywords": ["step_name", "step_id", "wait_hours", "sla_hours", "assignee", "request_code", "case_id", "activity", "process", "bottleneck", "rework", "گام", "مرحله", "کارتابل", "فرآیند", "فرایند", "انتظار"],
        "dataset_keywords": ["bpmn", "workflow", "process", "approval", "event_log", "فرآیند", "فرایند", "گردش کار"],
    },
    "service_support": {
        "fa": "خدمات و پشتیبانی",
        "family": "Process/Workflow",
        "field_keywords": ["ticket", "incident", "sla", "response_time", "resolution_time", "mttr", "csat", "agent", "priority", "تیکت", "پشتیبانی", "پاسخگویی", "رضایت", "حادثه"],
        "dataset_keywords": ["ticket", "support", "helpdesk", "service", "پشتیبانی", "تیکت"],
    },
    "inventory_procurement": {
        "fa": "انبار و تدارکات",
        "family": "Business",
        "field_keywords": ["stock", "inventory", "warehouse", "sku", "reorder", "supplier", "po_", "purchase", "lead_time", "receipt", "موجودی", "انبار", "کالا", "تامین‌کننده", "سفارش خرید"],
        "dataset_keywords": ["inventory", "warehouse", "stock", "supply", "procurement", "انبار", "تدارکات"],
    },
    "manufacturing_operations": {
        "fa": "تولید و عملیات",
        "family": "Business",
        "field_keywords": ["production", "output", "defect", "scrap", "oee", "downtime", "machine", "shift", "line", "yield", "batch", "تولید", "ضایعات", "خرابی", "توقف", "خط تولید", "شیفت"],
        "dataset_keywords": ["manufacturing", "production", "factory", "plant", "operations", "تولید", "کارخانه"],
    },
    "it_observability": {
        "fa": "فناوری اطلاعات و مانیتورینگ",
        "family": "IT Operations",
        "field_keywords": ["latency", "status_code", "endpoint", "method", "response_bytes", "error_code", "level", "service", "cpu", "memory", "uptime", "http", "ip_address", "لاگ", "خطا"],
        "dataset_keywords": ["log", "nginx", "apache", "access", "service", "telemetry", "apm", "observability", "لاگ", "سرور"],
    },
    "security": {
        "fa": "امنیت و لاگ‌های امنیتی",
        "family": "Security",
        "field_keywords": [
            "threat", "malware", "quarantine", "action", "severity", "rule_name", "policy_name",
            "file_hash", "sha256", "cve", "auth", "failed_login", "src_ip", "dst_ip", "hostname",
            "username", "blocked", "detect", "incident", "event_type", "تهدید", "امنیت", "قرنطینه", "مسدود",
        ],
        "dataset_keywords": ["security", "siem", "edr", "av", "antivirus", "firewall", "syslog", "auth", "امنیت", "امنیتی"],
    },
    "network": {
        "fa": "شبکه و ترافیک",
        "family": "Network",
        "field_keywords": [
            "src_ip", "dst_ip", "src_port", "dst_port", "protocol", "bytes_in", "bytes_out",
            "packet", "vlan", "jitter", "latency_ms", "dns", "bandwidth", "ttl", "شبکه", "پهنای باند",
        ],
        "dataset_keywords": ["network", "netflow", "firewall", "router", "switch", "packet", "dns", "ترافیک", "شبکه"],
    },
    "energy_power": {
        "fa": "انرژی و برق",
        "family": "Business",
        "field_keywords": ["kwh", "mwh", "kw", "mw", "feeder", "substation", "outage", "interruption", "saidi", "saifi", "caidi", "generation", "consumption", "load", "peak_load", "transformer", "meter", "برق", "انرژی", "خاموشی", "فیدر", "پست", "مصرف", "تولید برق", "تلفات"],
        "dataset_keywords": ["energy", "power", "grid", "electricity", "utility", "substation", "برق", "انرژی", "توزیع"],
    },
    "logistics_transport": {
        "fa": "حمل‌ونقل و لجستیک",
        "family": "Business",
        "field_keywords": ["shipment", "delivery", "freight", "vehicle", "fleet", "route", "origin", "destination", "transit", "carrier", "distance", "حمل", "ارسال", "مرسوله", "ناوگان", "مسیر"],
        "dataset_keywords": ["logistics", "transport", "shipping", "fleet", "delivery", "لجستیک", "حمل"],
    },
    "customer_crm": {
        "fa": "مدیریت مشتریان (CRM)",
        "family": "Business",
        "field_keywords": ["customer", "client", "churn", "retention", "ltv", "segment", "nps", "cohort", "signup", "مشتری", "وفاداری", "ریزش"],
        "dataset_keywords": ["crm", "customer", "client", "مشتریان"],
    },
    "org_management": {
        "fa": "مدیریت سازمانی",
        "family": "Business",
        "field_keywords": ["department", "division", "branch", "unit", "kpi_score", "performance_score", "directorate", "واحد", "شعبه", "معاونت", "امتیاز عملکرد"],
        "dataset_keywords": ["organization", "branch", "unit", "management", "سازمان", "واحدها"],
    },
    "general": {
        "fa": "عمومی / تحلیلی",
        "family": "Business",
        "field_keywords": [],
        "dataset_keywords": [],
    },
}


def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except Exception:
        return None


def _format_kpi_value(val: Optional[float], format_type: str = "number", unit: str = "") -> str:
    if val is None:
        return "—"
    if format_type == "percentage":
        return f"{val:,.1f}%"
    if format_type == "currency":
        u = unit or "ریال"
        if abs(val) >= 1_000_000_000:
            return f"{val / 1_000_000_000:,.2f}B {u}".strip()
        if abs(val) >= 1_000_000:
            return f"{val / 1_000_000:,.2f}M {u}".strip()
        return f"{val:,.0f} {u}".strip()
    if format_type == "duration":
        u = unit or "ساعت"
        return f"{val:,.2f} {u}".strip()
    if format_type == "count":
        return f"{int(round(val)):,} {unit}".strip()
    return f"{_fmt_num(val)} {unit}".strip()


COMPUTABILITY_STATUS_FA: dict[str, str] = {
    "computable": "قابل محاسبه",
    "computable_with_warning": "قابل محاسبه با هشدار",
    "needs_data": "نیازمند تکمیل داده",
    "uncomputable": "غیرقابل محاسبه",
}


def _default_display_format(format_type: str, unit: str = "") -> str:
    ft = (format_type or "number").lower()
    if ft in ("percentage", "currency", "count", "number"):
        return ft
    if ft in ("integer",):
        return "count"
    return "number"


def _default_comparison_mode(k: dict[str, Any]) -> str:
    if k.get("target_value") is not None or k.get("id") == "std_target_achievement" or k.get("category") == "comparison":
        return "target"
    if k.get("previous_value") is not None or k.get("category") == "growth" or k.get("id") == "std_mom_growth":
        return "previous_period"
    return "none"


def _enrich_kpi_metadata(k: dict[str, Any], idx_priority: int = 10) -> dict[str, Any]:
    """Ensure every KPI carries standardized diagnostic, display, comparison, and confirmation metadata."""
    c_status = k.get("computability_status")
    if not c_status:
        if k.get("status") == "uncomputable" or k.get("rejection_reason_fa"):
            c_status = "uncomputable"
        elif k.get("limitations"):
            c_status = "computable_with_warning"
        else:
            c_status = "computable"
    k["computability_status"] = c_status
    k["computability_status_fa"] = COMPUTABILITY_STATUS_FA.get(c_status, c_status)
    k.setdefault("is_standard", False)
    k.setdefault("base_fields", list(k.get("required_fields") or ([] if not k.get("field") or k.get("field") == "*" else [k["field"]])))
    k.setdefault("missing_fields", [])
    k.setdefault("missing_fields_fa", [])
    k.setdefault("description", k.get("comparison_basis") or f"محاسبه شاخص «{k.get('name', '')}» بر اساس فرمول {k.get('formula', '')}")
    fmt = k.get("display_format") or _default_display_format(k.get("format_type", "number"), k.get("unit", ""))
    k["display_format"] = fmt
    k.setdefault("comparison_mode", _default_comparison_mode(k))
    k.setdefault("priority", idx_priority)
    k.setdefault("suggested_selected", c_status in ("computable", "computable_with_warning") and k.get("status") == "selected")
    k.setdefault("domain_scope", "general")
    k.setdefault("dataset_names", [])
    k.setdefault("drill_through_method", "widget_drill_details")
    if "evidence" not in k:
        k["evidence"] = {
            "sample_count": k.get("sample_count"),
            "period": k.get("period"),
            "matched_fields": list(k.get("base_fields") or []),
            "dataset_names": list(k.get("dataset_names") or []),
        }
    if "diagnostics" not in k:
        warns = list(k.get("limitations") or [])
        errs = [k["rejection_reason_fa"]] if k.get("rejection_reason_fa") else []
        k["diagnostics"] = {
            "fields_exist": c_status != "needs_data",
            "types_compatible": c_status != "uncomputable" or "نوع" not in " ".join(errs),
            "join_valid": True,
            "nulls_and_zeros_handled": True,
            "time_basis_clear": bool(k.get("period")),
            "warnings_fa": warns,
            "errors_fa": errs,
            "data_quality_summary_fa": (
                errs[0] if errs else (warns[0] if warns else "تمامی فیلدها، نوع داده و شروط محاسبه بدون خطا تأیید شدند.")
            ),
        }
    return k


def _match_field_tokens(c: dict[str, Any], tokens: tuple[str, ...]) -> int:
    fn = str(c.get("field_name") or "").lower()
    dn = str(c.get("display_name") or "").lower()
    desc = str(c.get("description") or "").lower()
    score = 0
    for t in tokens:
        tl = t.lower()
        if fn == tl:
            score += 6
        elif tl in fn:
            score += 4
        if tl in dn:
            score += 3
        elif tl in desc:
            score += 1
    return score


def _evaluate_standard_kpis(
    db: Session,
    ds: Dataset,
    user: Optional[User],
    deep_profile: dict[str, Any],
    classifications: list[dict[str, Any]],
    sub_sql: str,
    sub_params: list[Any],
    partner_ids: list[str],
    eff_targets: dict[str, float],
    calendar_mode: str = "gregorian",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Dynamically discovers & computes the 7 Core Standard Business KPIs with full diagnostics:
      1. std_total_sales        (مجموع فروش)
      2. std_profit_margin      (حاشیه سود)
      3. std_mom_growth         (رشد ماهانه)
      4. std_aov                (میانگین مبلغ سفارش - Order-level two-stage pre-aggregation)
      5. std_unique_customers   (تعداد مشتریان یکتا)
      6. std_target_achievement (درصد تحقق هدف - multi-source fan-out safe + manual target support)
      7. std_delay_rate         (نرخ تأخیر - explicit eligible & delayed definitions)
    """
    row_count = int(deep_profile.get("row_count") or 0)
    dup_count = int(deep_profile.get("duplicate_row_count") or 0)

    # Build unified visible field pool across primary dataset + accessible related datasets
    pool: list[dict[str, Any]] = []
    for c in classifications:
        pool.append({
            **c,
            "_dataset_id": ds.id,
            "_dataset_name": ds.name,
            "_is_primary": True,
            "_join": None,
        })

    rel_plan: dict[str, Any] = {"ready_for_join": False, "resolved_joins": [], "blocked_relationships": []}
    join_by_target_ds: dict[str, dict[str, Any]] = {}
    if partner_ids and user is not None:
        try:
            rel_plan = analyze_dataset_relationships(db, user, [ds.id, *partner_ids], primary_dataset_id=ds.id)
            for j in rel_plan.get("resolved_joins") or []:
                join_by_target_ds[j["target_dataset_id"]] = j
        except Exception:
            pass

    for pid in partner_ids:
        if pid == ds.id:
            continue
        pds = db.query(Dataset).filter(Dataset.id == pid, Dataset.organization_id == ds.organization_id).first()
        if not pds:
            continue
        if user is not None and not permission_resolver.resolve(db, user, "dataset", pds.id, "view").allowed:
            continue
        psc = build_data_security_context(db, user, pds.id) if user is not None else DataSecurityContext("sys", [], [], set(), {}, "sys")
        p_fields = db.query(DatasetField).filter(DatasetField.dataset_id == pds.id).all()
        p_join = join_by_target_ds.get(pds.id)
        for pf in p_fields:
            if pf.field_name in psc.hidden_columns:
                continue
            dt = (pf.data_type or "string").lower()
            is_num = dt in ("integer", "double", "float", "decimal", "numeric")
            is_dt = dt in ("date", "datetime", "timestamp")
            sem_cls = pf.semantic_class or (
                "Date/Time" if is_dt else ("Identifier" if pf.semantic_role == "id" else ("Revenue" if is_num else "Category"))
            )
            pool.append({
                "field_name": pf.field_name,
                "display_name": pf.display_name or pf.field_name,
                "description": pf.description or "",
                "data_type": "numeric" if is_num else ("date" if is_dt else "categorical"),
                "raw_type": pf.data_type or "string",
                "semantic_class": sem_cls,
                "semantic_role": pf.semantic_role or "dimension",
                "sum_allowed": bool(SEMANTIC_CLASS_META.get(sem_cls, {}).get("sum_allowed", is_num)),
                "stats": {},
                "_dataset_id": pds.id,
                "_dataset_name": pds.name,
                "_is_primary": False,
                "_join": p_join,
            })

    def _pick_best(candidates_list: list[tuple[int, dict[str, Any]]]) -> Optional[dict[str, Any]]:
        if not candidates_list:
            return None
        candidates_list.sort(key=lambda item: (1 if item[1]["_is_primary"] else 0, item[0]), reverse=True)
        return candidates_list[0][1]

    def _pick_numeric(
        include_classes: tuple[str, ...],
        pos_tokens: tuple[str, ...],
        neg_tokens: tuple[str, ...] = (),
        *,
        require_token: bool = False,
    ) -> Optional[dict[str, Any]]:
        scored: list[tuple[int, dict[str, Any]]] = []
        for c in pool:
            if c.get("data_type") != "numeric" or c.get("semantic_class") == "Identifier":
                continue
            if c.get("semantic_class") in ("Percentage", "Ratio", "Unit Price") and "unit_price" not in pos_tokens:
                continue
            fn_low = c["field_name"].lower()
            if any(nt in fn_low for nt in neg_tokens):
                continue
            tok_sc = _match_field_tokens(c, pos_tokens)
            if require_token and tok_sc <= 0:
                continue
            sc = tok_sc
            if c.get("semantic_class") in include_classes:
                sc += 5
            if sc > 0:
                scored.append((sc, c))
        return _pick_best(scored)

    sales_f = _pick_numeric(
        ("Revenue", "Actual", "Currency"),
        ("total_sales", "sales_amount", "sale_amount", "revenue", "sales", "turnover", "gmv", "income", "net_sales", "amount", "فروش", "درآمد", "مبلغ"),
        neg_tokens=("cost", "expense", "cogs", "target", "budget", "quota", "discount", "unit_price", "profit", "margin", "tax", "هزینه", "بها", "هدف", "بودجه", "تخفیف", "سود"),
    )
    cost_f = _pick_numeric(
        ("Cost",),
        ("total_cost", "cost_amount", "cogs", "cost", "expense", "spend", "بهای تمام", "بها", "هزینه"),
        neg_tokens=("target", "budget", "unit_cost_pct", "ratio"),
    )
    profit_f = _pick_numeric(
        ("Profit",),
        ("net_profit", "gross_profit", "profit", "margin_amount", "سود"),
        neg_tokens=("margin_pct", "profit_pct", "rate", "ratio", "درصد"),
    )
    target_f = _pick_numeric(
        ("Target",),
        ("target_revenue", "target_sales", "sales_target", "target_amount", "target", "budget", "quota", "goal", "plan_amount", "هدف", "بودجه"),
        neg_tokens=("target_date", "due_date", "rate", "pct"),
    )

    # Order ID (primary preferred, avoid row_id / customer_id)
    order_scored: list[tuple[int, dict[str, Any]]] = []
    for c in pool:
        if not c["_is_primary"]:
            continue
        fn = c["field_name"].lower()
        if any(bt in fn for bt in ("customer", "client", "user", "product", "item_id", "مشتری", "کالا")):
            continue
        sc = _match_field_tokens(c, ("order_id", "invoice_id", "bill_id", "cart_id", "transaction_id", "tx_id", "receipt_id", "ticket_id", "request_id", "order_no", "شماره سفارش", "شناسه سفارش", "کد سفارش", "فاکتور"))
        if sc > 0:
            order_scored.append((sc, c))
    order_f = _pick_best(order_scored)

    # Customer ID
    cust_scored: list[tuple[int, dict[str, Any]]] = []
    for c in pool:
        if not c["_is_primary"]:
            continue
        sc = _match_field_tokens(c, ("customer_id", "client_id", "buyer_id", "account_id", "subscriber_id", "member_id", "customer_code", "customer_name", "customer", "client", "شناسه مشتری", "کد مشتری", "نام مشتری", "مشتری"))
        if sc > 0 and c.get("data_type") != "date":
            cust_scored.append((sc, c))
    cust_f = _pick_best(cust_scored)

    # Time field (prefer order/transaction date over delivery/due date for MoM)
    date_candidates = [c for c in pool if c["_is_primary"] and c.get("semantic_class") == "Date/Time"]
    date_candidates.sort(
        key=lambda c: (
            1 if any(k in c["field_name"].lower() for k in ("due", "delivery", "promised", "deadline", "سررسید", "تحویل")) else 0,
            -_match_field_tokens(c, ("order_date", "tx_date", "sale_date", "invoice_date", "created_at", "date", "تاریخ")),
        )
    )
    time_f = date_candidates[0] if date_candidates else None

    # Delay Rate fields (delivery vs due, duration vs SLA, delay_days, or delay flag/status)
    delivery_f = _pick_best([
        (_match_field_tokens(c, ("actual_delivery", "delivery_date", "delivered_at", "actual_date", "shipped_date", "completed_at", "finish_date", "end_date", "تاریخ تحویل", "تحویل واقعی", "تحویل")), c)
        for c in pool if c.get("semantic_class") == "Date/Time" and _match_field_tokens(c, ("actual_delivery", "delivery_date", "delivered_at", "actual_date", "shipped_date", "completed_at", "finish_date", "end_date", "تاریخ تحویل", "تحویل واقعی", "تحویل")) > 0
    ])
    due_f = _pick_best([
        (_match_field_tokens(c, ("due_date", "promised_date", "expected_date", "planned_date", "target_date", "deadline", "sla_due", "تاریخ سررسید", "موعد تحویل", "سررسید", "موعد")), c)
        for c in pool if c.get("semantic_class") == "Date/Time" and _match_field_tokens(c, ("due_date", "promised_date", "expected_date", "planned_date", "target_date", "deadline", "sla_due", "تاریخ سررسید", "موعد تحویل", "سررسید", "موعد")) > 0
    ])
    dur_f = _pick_numeric(
        ("Duration",),
        ("wait_minutes", "wait_hours", "duration", "lead_time", "processing_time", "cycle_time", "مدت انتظار", "زمان انتظار", "مدت"),
        neg_tokens=("delay_days", "late_days", "overdue_days"),
        require_token=True,
    )
    sla_f = _pick_numeric(
        ("Target", "Duration", "Quantity"),
        ("sla_limit", "sla_minutes", "sla_hours", "sla_days", "sla", "max_wait", "deadline_days", "سقف مجاز", "حد مجاز"),
        neg_tokens=("target_revenue", "target_sales", "target_amount", "sales_target", "budget", "quota"),
        require_token=True,
    )
    delay_days_f = _pick_numeric(
        ("Duration", "Quantity", "Count Measure"),
        ("delay_days", "late_days", "overdue_days", "delay_hours", "delay_minutes", "روز تأخیر", "میزان تأخیر", "مدت تأخیر"),
        require_token=True,
    )
    delay_flag_f = _pick_best([
        (_match_field_tokens(c, ("is_delayed", "delayed", "is_late", "overdue", "sla_breach", "delay_status", "delivery_status", "وضعیت تأخیر", "تأخیر", "تاخیر")), c)
        for c in pool if c["_is_primary"] and c.get("semantic_class") in ("Boolean", "Status", "Category")
        and _match_field_tokens(c, ("is_delayed", "delayed", "is_late", "overdue", "sla_breach", "delay_status", "delivery_status", "وضعیت تأخیر", "تأخیر", "تاخیر")) > 0
    ])

    # Detect cancellation status column (to exclude cancelled orders from Delay Rate denominator)
    cancel_tokens = {"cancelled", "canceled", "void", "aborted", "rejected", "لغو", "لغوشده", "کنسل"}
    cancel_status_col: Optional[str] = None
    cancel_vals_sql: list[str] = []
    for c in classifications:
        if c.get("semantic_class") in ("Status", "Category"):
            obs = [str(tv.get("value", "")) for tv in (c.get("stats", {}).get("top_values") or [])]
            matched_c = [v for v in obs if v.strip().lower() in cancel_tokens]
            if matched_c:
                cancel_status_col = c["field_name"]
                cancel_vals_sql = ["'" + v.replace("'", "''") + "'" for v in matched_c[:5]]
                break

    def _exec_scalar(sql_expr: str) -> Optional[float]:
        if row_count == 0:
            return None
        r = warehouse.execute_query(f"SELECT ({sql_expr}) AS v FROM ({sub_sql}) AS _std_probe", sub_params, limit=1)["rows"]
        return _safe_float(r[0]["v"]) if r else None

    def _exec_multi(vspec: dict[str, Any]) -> Optional[float]:
        if row_count == 0:
            return None
        jsql, jparams, _ = build_visual_query_sql(db, ds, vspec, [], [], [], user=user, enforce_join_rbac=True)
        jr = warehouse.execute_query(jsql, jparams, limit=1)["rows"]
        return _safe_float(jr[0].get("value")) if jr else None

    def _build_msa_if_cross(left_f: dict[str, Any], right_f: dict[str, Any], final_expr: str, left_agg: str = "SUM", right_agg: str = "SUM") -> tuple[Optional[dict[str, Any]], bool, str]:
        if left_f["_is_primary"] and right_f["_is_primary"]:
            return None, True, ""
        # Resolve join between primary dataset ds and the non-primary dataset
        other_f = right_f if left_f["_is_primary"] else left_f
        j = other_f.get("_join")
        if not j:
            return None, False, f"فیلد «{other_f['display_name']}» در منبع «{other_f['_dataset_name']}» است اما رابطه اتصال (Join Key) معتبری بین دو منبع وجود ندارد."
        if left_f["_is_primary"]:
            msa = {
                "right_dataset_id": other_f["_dataset_id"],
                "left_key": j["left_field"],
                "right_key": j["right_field"],
                "join_type": "INNER",
                "left_agg": left_agg,
                "left_field": left_f["field_name"],
                "right_agg": right_agg,
                "right_field": right_f["field_name"],
                "final_expr": final_expr,
            }
        else:
            # left_f is on partner dataset, right_f is on primary dataset -> swap aliases carefully
            msa = {
                "right_dataset_id": other_f["_dataset_id"],
                "left_key": j["left_field"],
                "right_key": j["right_field"],
                "join_type": "INNER",
                "left_agg": right_agg,
                "left_field": right_f["field_name"],
                "right_agg": left_agg,
                "right_field": left_f["field_name"],
                "final_expr": final_expr.replace("_l_val", "__TMP__").replace("_r_val", "_l_val").replace("__TMP__", "_r_val"),
            }
        note = f"تجمیع کلید-محور چندمنبعی روی کلید «{j['left_field']} ↔ {j['right_field']}» برای جلوگیری از تکرار و Fan-out اعمال شد."
        return msa, True, note

    std_kpis: list[dict[str, Any]] = []

    def _make_std(
        *,
        kpi_id: str,
        name: str,
        description: str,
        formula: str,
        display_format: str,
        unit: str = "",
        direction: str = "higher_is_better",
        base_fields: Optional[list[str]] = None,
        missing_fields: Optional[list[str]] = None,
        missing_fields_fa: Optional[list[str]] = None,
        source_datasets: Optional[list[str]] = None,
        value: Optional[float] = None,
        previous_value: Optional[float] = None,
        target_value: Optional[float] = None,
        computability_status: str = "computable",
        warnings_fa: Optional[list[str]] = None,
        errors_fa: Optional[list[str]] = None,
        fields_exist: bool = True,
        types_compatible: bool = True,
        join_valid: bool = True,
        nulls_and_zeros_handled: bool = True,
        time_basis_clear: bool = True,
        comparison_basis: str = "کل بازه داده‌ها",
        comparison_mode: str = "none",
        priority: int = 1,
        field_primary: Optional[str] = None,
        time_field: Optional[str] = None,
        eligible_definition_fa: Optional[str] = None,
        delayed_definition_fa: Optional[str] = None,
        multi_source_agg: Optional[dict[str, Any]] = None,
        pre_agg_grain: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        warns = list(warnings_fa or [])
        errs = list(errors_fa or [])
        summary_parts = errs if errs else (warns if warns else ["تمام بررسی‌های وجود فیلد، سازگاری نوع، مدیریت NULL/صفر و اعتبار محاسبه با موفقیت تأیید شد."])
        formatted_val = _format_kpi_value(value, display_format, unit)
        if errs and computability_status not in ("needs_data", "uncomputable"):
            computability_status = "uncomputable"
        elif warns and computability_status == "computable":
            computability_status = "computable_with_warning"

        item: dict[str, Any] = {
            "id": kpi_id,
            "slug": kpi_id,
            "name": name,
            "description": description,
            "is_standard": True,
            "category": "standard",
            "formula": formula,
            "compiled_sql": formula,
            "aggregation": "FORMULA",
            "field": field_primary,
            "time_field": time_field,
            "format_type": display_format,
            "display_format": display_format,
            "unit": unit,
            "direction": direction,
            "base_fields": base_fields or [],
            "required_fields": base_fields or [],
            "missing_fields": missing_fields or [],
            "missing_fields_fa": missing_fields_fa or [],
            "source_datasets": source_datasets or [ds.name],
            "source": " + ".join(source_datasets or [ds.name]),
            "joins": [],
            "multi_source_agg": multi_source_agg,
            "pre_agg_grain": pre_agg_grain,
            "value": round(value, 4) if value is not None else None,
            "formatted_value": formatted_val,
            "previous_value": round(previous_value, 4) if previous_value is not None else None,
            "target_value": round(target_value, 4) if target_value is not None else None,
            "period": comparison_basis,
            "comparison_basis": comparison_basis,
            "comparison_mode": comparison_mode,
            "priority": priority,
            "importance": 0.98 - (priority - 1) * 0.02,
            "domain_fit": 0.96,
            "confidence": 0.96 if computability_status == "computable" else (0.86 if computability_status == "computable_with_warning" else 0.0),
            "score": round(98.0 - (priority - 1) * 1.5, 1) if computability_status in ("computable", "computable_with_warning") else 0.0,
            "limitations": warns,
            "rejection_reason_fa": errs[0] if errs else (f"فیلد(های) موردنیاز یافت نشد: {'، '.join(missing_fields_fa or [])}" if computability_status == "needs_data" else None),
            "computability_status": computability_status,
            "computability_status_fa": COMPUTABILITY_STATUS_FA[computability_status],
            "eligible_definition_fa": eligible_definition_fa,
            "delayed_definition_fa": delayed_definition_fa,
            "diagnostics": {
                "fields_exist": fields_exist,
                "types_compatible": types_compatible,
                "join_valid": join_valid,
                "nulls_and_zeros_handled": nulls_and_zeros_handled,
                "time_basis_clear": time_basis_clear,
                "warnings_fa": warns,
                "errors_fa": errs,
                "data_quality_summary_fa": " | ".join(summary_parts),
            },
            "status": "selected" if computability_status in ("computable", "computable_with_warning") else computability_status,
            "suggested_selected": computability_status in ("computable", "computable_with_warning"),
            "needs_user_confirmation": False,
            "is_data_quality": False,
        }
        return item

    # ------------------------------------------------------------------
    # 1. مجموع فروش (Total Sales)
    # ------------------------------------------------------------------
    if not sales_f:
        std_kpis.append(_make_std(
            kpi_id="std_total_sales",
            name="مجموع فروش",
            description="مجموع مبلغ فروش کل در دوره انتخاب‌شده با مدیریت مقادیر NULL.",
            formula="COALESCE(SUM(<sales_amount>), 0)",
            display_format="currency",
            unit="ریال",
            missing_fields=["sales_amount"],
            missing_fields_fa=["فیلد مبلغ فروش / درآمد (Sales Amount)"],
            computability_status="needs_data",
            fields_exist=False,
            priority=1,
        ))
    else:
        s_fn = sales_f["field_name"]
        warns: list[str] = []
        errs: list[str] = []
        val = None
        if not sales_f["_is_primary"]:
            errs.append("مجموع فروش نیازمند وجود ستون مبلغ فروش در منبع اصلی داده است.")
        else:
            null_pct = float(sales_f.get("stats", {}).get("null_pct") or 0)
            if null_pct > 0:
                warns.append(f"{null_pct}% از رکوردهای «{sales_f['display_name']}» خالی (NULL) است و با COALESCE در مجموع لحاظ نمی‌شوند.")
            if dup_count > 0:
                warns.append(f"{dup_count:,} ردیف تکراری در دیتاست شناسایی شد؛ در صورت تکراری بودن ردیف‌های فاکتور، جمع فروش می‌تواند بیش‌برآورد شود.")
            if row_count == 0:
                warns.append("جدول فاقد ردیف است؛ مقدار پیش‌فرض صفر بازگردانده شد.")
                val = 0.0
            else:
                r_s = warehouse.execute_query(
                    f'SELECT COALESCE(SUM("{s_fn}"), 0) AS s, COUNT("{s_fn}") AS nn FROM ({sub_sql}) AS _s',
                    sub_params, limit=1,
                )["rows"]
                nn = int(r_s[0]["nn"] or 0) if r_s else 0
                val = _safe_float(r_s[0]["s"]) if r_s else 0.0
                if nn == 0:
                    warns.append("تمامی مقادیر ستون فروش خالی (NULL) هستند.")
        t_override = eff_targets.get("std_total_sales") or eff_targets.get(s_fn)
        std_kpis.append(_make_std(
            kpi_id="std_total_sales",
            name="مجموع فروش",
            description=f"جمع کل ستون «{sales_f['display_name']}» با حذف ایمن مقادیر NULL.",
            formula=f'COALESCE(SUM("{s_fn}"), 0)',
            display_format="currency",
            unit="ریال",
            base_fields=[s_fn],
            source_datasets=[sales_f["_dataset_name"]],
            value=val,
            target_value=t_override,
            computability_status="uncomputable" if errs else ("computable_with_warning" if warns else "computable"),
            warnings_fa=warns,
            errors_fa=errs,
            comparison_mode="target" if t_override is not None else "none",
            priority=1,
            field_primary=s_fn,
            time_field=time_f["field_name"] if time_f else None,
        ))

    # ------------------------------------------------------------------
    # 2. حاشیه سود (Profit Margin %)
    # ------------------------------------------------------------------
    if not sales_f or (not cost_f and not profit_f):
        miss_fa = []
        miss_en = []
        if not sales_f:
            miss_fa.append("فیلد مبلغ فروش / درآمد (Sales Amount)")
            miss_en.append("sales_amount")
        if not cost_f and not profit_f:
            miss_fa.append("فیلد هزینه / بهای تمام‌شده (Cost) یا سود (Profit)")
            miss_en.append("cost_or_profit")
        std_kpis.append(_make_std(
            kpi_id="std_profit_margin",
            name="حاشیه سود",
            description="نسبت سود خالص به مجموع فروش بر حسب درصد: (فروش − هزینه) ÷ فروش × ۱۰۰",
            formula="(COALESCE(SUM(<sales>), 0) - COALESCE(SUM(<cost>), 0)) * 100.0 / NULLIF(SUM(<sales>), 0)",
            display_format="percentage",
            unit="%",
            base_fields=[f["field_name"] for f in (sales_f, cost_f, profit_f) if f],
            missing_fields=miss_en,
            missing_fields_fa=miss_fa,
            computability_status="needs_data",
            fields_exist=False,
            priority=2,
        ))
    else:
        s_fn = sales_f["field_name"]
        warns = []
        errs = []
        val = None
        msa_pm = None
        j_valid = True
        if cost_f and cost_f["field_name"] != s_fn:
            c_fn = cost_f["field_name"]
            pm_formula = f'(COALESCE(SUM("{s_fn}"), 0) - COALESCE(SUM("{c_fn}"), 0)) * 100.0 / NULLIF(SUM("{s_fn}"), 0)'
            used_ds = list(dict.fromkeys([sales_f["_dataset_name"], cost_f["_dataset_name"]]))
            b_fields = [s_fn, c_fn]
            if not (sales_f["_is_primary"] and cost_f["_is_primary"]):
                msa_pm, j_valid, j_note = _build_msa_if_cross(
                    sales_f, cost_f,
                    "((COALESCE(SUM(_l_val), 0) - COALESCE(SUM(_r_val), 0)) * 100.0) / NULLIF(SUM(_l_val), 0)",
                )
                if not j_valid:
                    errs.append(j_note)
                else:
                    warns.append(j_note)
        else:
            p_fn = profit_f["field_name"]  # type: ignore[index]
            pm_formula = f'COALESCE(SUM("{p_fn}"), 0) * 100.0 / NULLIF(SUM("{s_fn}"), 0)'
            used_ds = list(dict.fromkeys([sales_f["_dataset_name"], profit_f["_dataset_name"]]))  # type: ignore[index]
            b_fields = [s_fn, p_fn]
            warns.append(f"بهای تمام‌شده جداگانه یافت نشد؛ حاشیه سود مستقیماً از ستون «{profit_f['display_name']}» محاسبه شد.")  # type: ignore[index]
            if not (sales_f["_is_primary"] and profit_f["_is_primary"]):  # type: ignore[index]
                msa_pm, j_valid, j_note = _build_msa_if_cross(
                    sales_f, profit_f,  # type: ignore[arg-type]
                    "(COALESCE(SUM(_r_val), 0) * 100.0) / NULLIF(SUM(_l_val), 0)",
                )
                if not j_valid:
                    errs.append(j_note)
                else:
                    warns.append(j_note)

        if dup_count > 0:
            warns.append(f"در دیتاست {dup_count:,} ردیف تکراری وجود دارد.")

        if not errs:
            try:
                if msa_pm:
                    val = _exec_multi({"multi_source_agg": msa_pm, "limit": 1})
                else:
                    r_pm = warehouse.execute_query(
                        f'SELECT COALESCE(SUM("{s_fn}"), 0) AS denom, ({pm_formula}) AS pm FROM ({sub_sql}) AS _pm',
                        sub_params, limit=1,
                    )["rows"]
                    denom = float(r_pm[0]["denom"] or 0) if r_pm else 0.0
                    if denom == 0.0:
                        errs.append("مجموع فروش برابر صفر است؛ تقسیم بر صفر رخ می‌دهد و حاشیه سود قابل محاسبه نیست.")
                    else:
                        val = _safe_float(r_pm[0]["pm"])
                if val is None and not errs:
                    errs.append("مجموع فروش صفر یا نامعتبر است؛ حاشیه سود محاسبه نشد.")
            except Exception as exc:
                errs.append(f"خطا در محاسبه حاشیه سود: {exc}")

        std_kpis.append(_make_std(
            kpi_id="std_profit_margin",
            name="حاشیه سود",
            description="نسبت سود خالص به مجموع فروش بر حسب درصد با محافظت NULLIF در برابر مخرج صفر.",
            formula=pm_formula,
            display_format="percentage",
            unit="%",
            base_fields=b_fields,
            source_datasets=used_ds,
            value=val,
            computability_status="uncomputable" if errs else ("computable_with_warning" if warns else "computable"),
            warnings_fa=warns,
            errors_fa=errs,
            join_valid=j_valid,
            priority=2,
            field_primary=s_fn,
            multi_source_agg=msa_pm,
        ))

    # ------------------------------------------------------------------
    # 3. رشد ماهانه هم‌تراز با تقویم (MoM Growth %)
    # ------------------------------------------------------------------
    if not sales_f or not time_f:
        miss_fa = []
        miss_en = []
        if not sales_f:
            miss_fa.append("فیلد مبلغ فروش (Sales Amount)")
            miss_en.append("sales_amount")
        if not time_f:
            miss_fa.append("ستون تاریخ / زمان (Date/Time)")
            miss_en.append("date_field")
        std_kpis.append(_make_std(
            kpi_id="std_mom_growth",
            name="رشد ماهانه",
            description="درصد رشد فروش ماه جاری تقویمی نسبت به ماه تقویمی بلافاصله قبل.",
            formula="MOM_CALENDAR(<sales_amount>, <date_field>)",
            display_format="percentage",
            unit="%",
            base_fields=[f["field_name"] for f in (sales_f, time_f) if f],
            missing_fields=miss_en,
            missing_fields_fa=miss_fa,
            computability_status="needs_data",
            fields_exist=False,
            time_basis_clear=False,
            priority=3,
        ))
    else:
        s_fn = sales_f["field_name"]
        t_fn = time_f["field_name"]
        mom_formula = f'MOM_CALENDAR("{s_fn}", "{t_fn}")'
        warns = []
        errs = []
        miss_fa = []
        miss_en = []
        c_status = "computable"
        val = None
        prev_v = None
        curr_v = None
        comp_basis = "نیازمند حداقل ۲ ماه تقویمی متوالی"

        if not sales_f["_is_primary"]:
            errs.append("رشد ماهانه نیازمند قرار گرفتن فروش و تاریخ در منبع اصلی است.")
            c_status = "uncomputable"
        else:
            if calendar_mode == "jalali":
                m_idx_expr = expand_jalali_sql_calls(f'(JALALI_YEAR("{t_fn}") * 12 + JALALI_MONTH("{t_fn}"))')
                m_lbl_expr = expand_jalali_sql_calls(f'SUBSTRING(JALALI_DATE("{t_fn}"), 1, 7)')
            else:
                m_idx_expr = f'(EXTRACT(YEAR FROM TRY_CAST("{t_fn}" AS DATE)) * 12 + EXTRACT(MONTH FROM TRY_CAST("{t_fn}" AS DATE)))'
                m_lbl_expr = f'STRFTIME(TRY_CAST("{t_fn}" AS DATE), \'%Y-%m\')'
            try:
                m_rows = warehouse.execute_query(
                    f'SELECT ({m_idx_expr}) AS idx, MIN({m_lbl_expr}) AS lbl, COALESCE(SUM("{s_fn}"), 0) AS val '
                    f'FROM ({sub_sql}) AS _m WHERE TRY_CAST("{t_fn}" AS DATE) IS NOT NULL GROUP BY 1 ORDER BY 1 ASC',
                    sub_params, limit=240,
                )["rows"]
                null_date_r = warehouse.execute_query(
                    f'SELECT SUM(CASE WHEN TRY_CAST("{t_fn}" AS DATE) IS NULL THEN 1 ELSE 0 END) AS bad_d '
                    f'FROM ({sub_sql}) AS _md',
                    sub_params, limit=1,
                )["rows"]
                bad_dates = int((null_date_r[0]["bad_d"] or 0) if null_date_r else 0)
                if bad_dates > 0:
                    warns.append(f"{bad_dates:,} رکورد فاقد تاریخ معتبر بوده و در تحلیل ماهانه لحاظ نشدند.")

                m_map = {int(r["idx"]): (str(r["lbl"]), float(r["val"] or 0.0)) for r in m_rows if r.get("idx") is not None}
                if len(m_map) < 2:
                    c_status = "needs_data"
                    miss_en = ["consecutive_calendar_months"]
                    miss_fa = ["حداقل ۲ ماه تقویمی متوالی در داده‌ها"]
                    warns.append(f"فقط {len(m_map)} ماه تقویمی در داده‌ها وجود دارد؛ برای رشد ماهانه حداقل ۲ ماه متوالی لازم است.")
                else:
                    curr_idx = max(m_map.keys())
                    prev_idx = curr_idx - 1
                    curr_lbl, curr_v = m_map[curr_idx]
                    if prev_idx not in m_map:
                        c_status = "needs_data"
                        miss_en = ["adjacent_previous_month"]
                        miss_fa = [f"داده‌های ماه تقویمی بلافاصله قبل از «{curr_lbl}»"]
                        warns.append(f"بین آخرین ماه ({curr_lbl}) و داده‌های قبلی فاصله تقویمی وجود دارد؛ ماه قبل مستقیماً در داده ثبت نشده است.")
                    else:
                        prev_lbl, prev_v = m_map[prev_idx]
                        comp_basis = f"مقایسه ماه {curr_lbl} ({_fmt_num(curr_v)}) با ماه قبل {prev_lbl} ({_fmt_num(prev_v)})"
                        if prev_v == 0:
                            c_status = "uncomputable"
                            errs.append(f"مقدار فروش ماه قبل ({prev_lbl}) صفر است؛ درصد رشد ماهانه به دلیل تقسیم بر صفر قابل محاسبه نیست.")
                        else:
                            if prev_v < 0:
                                warns.append(f"مقدار ماه قبل ({prev_lbl}) منفی است؛ رشد نسبت به قدر مطلق دوره قبل محاسبه شد.")
                            val = ((curr_v - prev_v) * 100.0) / abs(prev_v)
            except Exception as exc:
                c_status = "uncomputable"
                errs.append(f"خطا در محاسبه رشد ماهانه هم‌تراز با تقویم: {exc}")

        std_kpis.append(_make_std(
            kpi_id="std_mom_growth",
            name="رشد ماهانه",
            description="درصد رشد فروش آخرین ماه تقویمی نسبت به ماه تقویمی بلافاصله قبل (با رعایت شکاف ماه‌ها و تقسیم بر صفر).",
            formula=mom_formula,
            display_format="percentage",
            unit="%",
            base_fields=[s_fn, t_fn],
            missing_fields=miss_en,
            missing_fields_fa=miss_fa,
            source_datasets=[ds.name],
            value=val,
            previous_value=prev_v,
            computability_status=c_status,
            warnings_fa=warns,
            errors_fa=errs,
            fields_exist=(c_status != "needs_data"),
            time_basis_clear=(c_status != "needs_data"),
            comparison_basis=comp_basis,
            comparison_mode="previous_period" if c_status in ("computable", "computable_with_warning") else "none",
            priority=3,
            field_primary=s_fn,
            time_field=t_fn,
        ))

    # ------------------------------------------------------------------
    # 4. میانگین مبلغ سفارش (AOV - Two-Stage Order-Level Pre-Aggregation)
    # ------------------------------------------------------------------
    if not sales_f or not order_f:
        miss_fa = []
        miss_en = []
        if not sales_f:
            miss_fa.append("فیلد مبلغ فروش (Sales Amount)")
            miss_en.append("sales_amount")
        if not order_f:
            miss_fa.append("فیلد شناسه سفارش (Order / Invoice ID)")
            miss_en.append("order_id")
        std_kpis.append(_make_std(
            kpi_id="std_aov",
            name="میانگین مبلغ سفارش",
            description="ابتدا جمع مبلغ هر سفارش در سطح شناسه سفارش محاسبه شده و سپس میانگین سفارش‌ها گرفته می‌شود (تجمیع دومرحله‌ای).",
            formula="AOV(<sales_amount>, <order_id>)",
            display_format="currency",
            unit="ریال",
            base_fields=[f["field_name"] for f in (sales_f, order_f) if f],
            missing_fields=miss_en,
            missing_fields_fa=miss_fa,
            computability_status="needs_data",
            fields_exist=False,
            priority=4,
        ))
    else:
        s_fn = sales_f["field_name"]
        o_fn = order_f["field_name"]
        aov_formula = f'AOV("{s_fn}", "{o_fn}")'
        warns = []
        errs = []
        val = None
        if not sales_f["_is_primary"]:
            errs.append("میانگین مبلغ سفارش نیازمند حضور فیلد فروش در منبع اصلی است.")
        else:
            try:
                aov_r = warehouse.execute_query(
                    f'WITH _g AS ('
                    f'SELECT "{o_fn}" AS _ok, COALESCE(SUM("{s_fn}"), 0) AS _ov, COUNT(*) AS _rc '
                    f'FROM ({sub_sql}) AS _a WHERE "{o_fn}" IS NOT NULL GROUP BY 1'
                    f') SELECT AVG(_ov) AS aov, COUNT(*) AS orders_cnt, COALESCE(SUM(_rc), 0) AS rows_cnt FROM _g',
                    sub_params, limit=1,
                )["rows"]
                null_o_r = warehouse.execute_query(
                    f'SELECT SUM(CASE WHEN "{o_fn}" IS NULL THEN 1 ELSE 0 END) AS null_o FROM ({sub_sql}) AS _no',
                    sub_params, limit=1,
                )["rows"]
                null_orders = int((null_o_r[0]["null_o"] or 0) if null_o_r else 0)
                orders_cnt = int((aov_r[0]["orders_cnt"] or 0) if aov_r else 0)
                rows_cnt = int((aov_r[0]["rows_cnt"] or 0) if aov_r else 0)
                val = _safe_float(aov_r[0]["aov"]) if aov_r else None
                if orders_cnt == 0 or val is None:
                    errs.append("هیچ شناسه سفارش معتبر (غیرخالی) برای محاسبه میانگین مبلغ سفارش وجود ندارد.")
                else:
                    if null_orders > 0:
                        warns.append(f"{null_orders:,} ردیف فاقد شناسه سفارش (NULL) از مخرج و صورت محاسبه AOV کنار گذاشته شد.")
                    if rows_cnt > orders_cnt:
                        warns.append(f"تجمیع دومرحله‌ای روی {orders_cnt:,} سفارش یکتا (از {rows_cnt:,} ردیف/آیتم) انجام شد تا سفارش‌های چندردیفه دوبار شمرده نشوند.")
            except Exception as exc:
                errs.append(f"خطا در محاسبه میانگین مبلغ سفارش: {exc}")

        std_kpis.append(_make_std(
            kpi_id="std_aov",
            name="میانگین مبلغ سفارش",
            description=f"تجمیع دومرحله‌ای مبلغ «{sales_f['display_name']}» در سطح «{order_f['display_name']}» و سپس میانگین‌گیری از سفارش‌های معتبر.",
            formula=aov_formula,
            display_format="currency",
            unit="ریال",
            base_fields=[s_fn, o_fn],
            source_datasets=[ds.name],
            value=val,
            computability_status="uncomputable" if errs else ("computable_with_warning" if warns else "computable"),
            warnings_fa=warns,
            errors_fa=errs,
            priority=4,
            field_primary=s_fn,
            pre_agg_grain={"grain_field": o_fn, "measure_field": s_fn, "grain_agg": "SUM"},
        ))

    # ------------------------------------------------------------------
    # 5. تعداد مشتریان یکتا (Unique Customers)
    # ------------------------------------------------------------------
    if not cust_f:
        std_kpis.append(_make_std(
            kpi_id="std_unique_customers",
            name="تعداد مشتریان یکتا",
            description="شمارش تعداد مشتریان متمایز بدون تکرار.",
            formula="COUNT(DISTINCT <customer_id>)",
            display_format="count",
            unit="مشتری",
            missing_fields=["customer_id"],
            missing_fields_fa=["فیلد شناسه یا کد مشتری (Customer ID)"],
            computability_status="needs_data",
            fields_exist=False,
            priority=5,
        ))
    else:
        cu_fn = cust_f["field_name"]
        cu_formula = f'COUNT(DISTINCT "{cu_fn}")'
        warns = []
        errs = []
        val = None
        try:
            cu_r = warehouse.execute_query(
                f'SELECT COUNT(DISTINCT "{cu_fn}") AS ucnt, '
                f'SUM(CASE WHEN "{cu_fn}" IS NULL THEN 1 ELSE 0 END) AS ncnt, '
                f'COUNT(*) AS tcnt FROM ({sub_sql}) AS _cu',
                sub_params, limit=1,
            )["rows"]
            ucnt = int((cu_r[0]["ucnt"] or 0) if cu_r else 0)
            ncnt = int((cu_r[0]["ncnt"] or 0) if cu_r else 0)
            tcnt = int((cu_r[0]["tcnt"] or 0) if cu_r else 0)
            val = float(ucnt)
            if tcnt == 0 or ucnt == 0:
                errs.append("هیچ مقدار معتبر غیرخالی برای شناسه مشتری یافت نشد.")
            else:
                if ncnt > 0:
                    warns.append(f"{ncnt:,} رکورد فاقد شناسه مشتری (NULL) است و در شمارش مشتریان یکتا لحاظ نشد.")
                if tcnt > ucnt:
                    warns.append(f"{tcnt:,} رکورد به {ucnt:,} مشتری یکتا خلاصه شد (حذف تکرار مشتری).")
        except Exception as exc:
            errs.append(f"خطا در شمارش مشتریان یکتا: {exc}")

        std_kpis.append(_make_std(
            kpi_id="std_unique_customers",
            name="تعداد مشتریان یکتا",
            description=f"شمارش دقیق مقادیر متمایز در ستون «{cust_f['display_name']}».",
            formula=cu_formula,
            display_format="count",
            unit="مشتری",
            base_fields=[cu_fn],
            source_datasets=[ds.name],
            value=val,
            computability_status="uncomputable" if errs else ("computable_with_warning" if warns else "computable"),
            warnings_fa=warns,
            errors_fa=errs,
            priority=5,
            field_primary=cu_fn,
        ))

    # ------------------------------------------------------------------
    # 6. درصد تحقق هدف (Target Achievement %)
    # ------------------------------------------------------------------
    manual_target = _safe_float(
        eff_targets.get("std_target_achievement")
        or eff_targets.get("target")
        or (eff_targets.get(sales_f["field_name"]) if sales_f else None)
    )
    if not sales_f or (not target_f and not (manual_target and manual_target > 0)):
        miss_fa = []
        miss_en = []
        if not sales_f:
            miss_fa.append("فیلد مبلغ فروش واقعی (Sales / Actual)")
            miss_en.append("sales_amount")
        if not target_f:
            miss_fa.append("فیلد مقدار هدف (Target / Budget) یا ثبت هدف دستی")
            miss_en.append("target_amount")
        std_kpis.append(_make_std(
            kpi_id="std_target_achievement",
            name="درصد تحقق هدف",
            description="نسبت عملکرد واقعی فروش به مقدار هدف برنامه‌ریزی‌شده (درصد).",
            formula="COALESCE(SUM(<sales>), 0) * 100.0 / NULLIF(SUM(<target>), 0)",
            display_format="percentage",
            unit="%",
            base_fields=[sales_f["field_name"]] if sales_f else [],
            missing_fields=miss_en,
            missing_fields_fa=miss_fa,
            computability_status="needs_data",
            fields_exist=False,
            priority=6,
        ))
    else:
        s_fn = sales_f["field_name"]
        warns = []
        errs = []
        val = None
        msa_ta = None
        j_valid = True
        t_val_num = None
        if target_f and target_f["field_name"] != s_fn:
            tfn = target_f["field_name"]
            ta_formula = f'COALESCE(SUM("{s_fn}"), 0) * 100.0 / NULLIF(SUM("{tfn}"), 0)'
            b_fields = [s_fn, tfn]
            used_ds = list(dict.fromkeys([sales_f["_dataset_name"], target_f["_dataset_name"]]))
            if not (sales_f["_is_primary"] and target_f["_is_primary"]):
                msa_ta, j_valid, j_note = _build_msa_if_cross(
                    sales_f, target_f,
                    "(COALESCE(SUM(_l_val), 0) * 100.0) / NULLIF(SUM(_r_val), 0)",
                )
                if not j_valid:
                    errs.append(j_note)
                else:
                    warns.append(j_note)
            if not errs:
                try:
                    if msa_ta:
                        val = _exec_multi({"multi_source_agg": msa_ta, "limit": 1})
                        # Also probe target sum
                        t_val_num = _exec_multi({
                            "multi_source_agg": {**msa_ta, "final_expr": "COALESCE(SUM(_r_val), 0)"},
                            "limit": 1,
                        })
                    else:
                        r_ta = warehouse.execute_query(
                            f'SELECT COALESCE(SUM("{s_fn}"), 0) AS a_sum, COALESCE(SUM("{tfn}"), 0) AS t_sum, ({ta_formula}) AS pct '
                            f'FROM ({sub_sql}) AS _ta',
                            sub_params, limit=1,
                        )["rows"]
                        t_val_num = _safe_float(r_ta[0]["t_sum"]) if r_ta else 0.0
                        if not t_val_num:
                            errs.append("مجموع مقدار هدف برابر صفر است؛ درصد تحقق هدف قابل محاسبه نیست.")
                        else:
                            val = _safe_float(r_ta[0]["pct"])
                except Exception as exc:
                    errs.append(f"خطا در محاسبه درصد تحقق هدف: {exc}")
        else:
            # Manual target entered by user
            ta_formula = f'COALESCE(SUM("{s_fn}"), 0) * 100.0 / NULLIF({manual_target}, 0)'
            b_fields = [s_fn]
            used_ds = [sales_f["_dataset_name"]]
            t_val_num = manual_target
            warns.append(f"هدف دستی ({_fmt_num(manual_target)}) به عنوان مبنای محاسبه اعمال شده است.")
            try:
                val = _exec_scalar(ta_formula)
            except Exception as exc:
                errs.append(f"خطا در محاسبه درصد تحقق هدف: {exc}")

        if val is None and not errs:
            errs.append("مقدار هدف صفر یا نامعتبر است؛ درصد تحقق قابل محاسبه نیست.")

        std_kpis.append(_make_std(
            kpi_id="std_target_achievement",
            name="درصد تحقق هدف",
            description="درصد تحقق فروش واقعی نسبت به هدف (با پشتیبانی از هدف ستونی، منبع مرتبط بدون Fan-out یا هدف دستی).",
            formula=ta_formula,
            display_format="percentage",
            unit="%",
            base_fields=b_fields,
            source_datasets=used_ds,
            value=val,
            target_value=t_val_num,
            computability_status="uncomputable" if errs else ("computable_with_warning" if warns else "computable"),
            warnings_fa=warns,
            errors_fa=errs,
            join_valid=j_valid,
            comparison_basis="مقایسه فروش واقعی با مقدار هدف",
            comparison_mode="target",
            priority=6,
            field_primary=s_fn,
            multi_source_agg=msa_ta,
        ))

    # ------------------------------------------------------------------
    # 7. نرخ تأخیر (Delay Rate % - with explicit eligible & delayed rules)
    # ------------------------------------------------------------------
    elig_cond = None
    del_cond = None
    elig_fa = None
    del_fa = None
    dr_fields: list[str] = []
    dr_ds_list = [ds.name]
    dr_formula = "SUM(CASE WHEN <eligible> AND <delayed> THEN 1.0 ELSE 0.0 END) * 100.0 / NULLIF(SUM(CASE WHEN <eligible> THEN 1.0 ELSE 0.0 END), 0)"

    cancel_excl_sql = f' AND COALESCE(CAST("{cancel_status_col}" AS VARCHAR), \'\') NOT IN ({", ".join(cancel_vals_sql)})' if cancel_status_col and cancel_vals_sql else ""
    cancel_note_fa = f" و وضعیت «{cancel_status_col}» لغو نباشد" if cancel_status_col else ""

    if delivery_f and due_f and delivery_f["_is_primary"] and due_f["_is_primary"] and delivery_f["field_name"] != due_f["field_name"]:
        afn, dfn = delivery_f["field_name"], due_f["field_name"]
        elig_cond = f'TRY_CAST("{afn}" AS TIMESTAMP) IS NOT NULL AND TRY_CAST("{dfn}" AS TIMESTAMP) IS NOT NULL{cancel_excl_sql}'
        del_cond = f'TRY_CAST("{afn}" AS TIMESTAMP) > TRY_CAST("{dfn}" AS TIMESTAMP)'
        elig_fa = f"کلیه رکوردهای دارای تاریخ «{delivery_f['display_name']}» و تاریخ سررسید «{due_f['display_name']}» معتبر{cancel_note_fa}"
        del_fa = f"مواردی که تاریخ «{delivery_f['display_name']}» از تاریخ «{due_f['display_name']}» دیرتر بوده است"
        dr_fields = [afn, dfn] + ([cancel_status_col] if cancel_status_col else [])
    elif dur_f and sla_f and dur_f["_is_primary"] and sla_f["_is_primary"] and dur_f["field_name"] != sla_f["field_name"]:
        wfn, sfn_sla = dur_f["field_name"], sla_f["field_name"]
        elig_cond = f'"{wfn}" IS NOT NULL AND "{sfn_sla}" IS NOT NULL AND "{sfn_sla}" > 0{cancel_excl_sql}'
        del_cond = f'"{wfn}" > "{sfn_sla}"'
        elig_fa = f"کلیه رکوردهای دارای مدت زمان «{dur_f['display_name']}» و سقف مجاز مثبت «{sla_f['display_name']}»{cancel_note_fa}"
        del_fa = f"مواردی که «{dur_f['display_name']}» از سقف مجاز «{sla_f['display_name']}» بیشتر باشد"
        dr_fields = [wfn, sfn_sla] + ([cancel_status_col] if cancel_status_col else [])
    elif delay_days_f and delay_days_f["_is_primary"]:
        ddfn = delay_days_f["field_name"]
        elig_cond = f'"{ddfn}" IS NOT NULL{cancel_excl_sql}'
        del_cond = f'"{ddfn}" > 0'
        elig_fa = f"کلیه رکوردهای دارای مقدار غیرخالی در «{delay_days_f['display_name']}»{cancel_note_fa}"
        del_fa = f"مواردی که «{delay_days_f['display_name']}» بزرگ‌تر از صفر باشد"
        dr_fields = [ddfn] + ([cancel_status_col] if cancel_status_col else [])
    elif delay_flag_f and delay_flag_f["_is_primary"]:
        dffn = delay_flag_f["field_name"]
        if delay_flag_f.get("data_type") == "boolean" or delay_flag_f.get("raw_type", "").lower() in ("bool", "boolean"):
            elig_cond = f'"{dffn}" IS NOT NULL{cancel_excl_sql}'
            del_cond = f'CAST("{dffn}" AS BOOLEAN) = TRUE'
            elig_fa = f"کلیه رکوردهای دارای وضعیت مشخص در «{delay_flag_f['display_name']}»{cancel_note_fa}"
            del_fa = f"مواردی که پرچم «{delay_flag_f['display_name']}» برابر True باشد"
            dr_fields = [dffn]
        else:
            obs_df = [str(tv.get("value", "")) for tv in (delay_flag_f.get("stats", {}).get("top_values") or [])]
            del_tokens = {"delayed", "late", "overdue", "breached", "1", "true", "yes", "تأخیر", "تاخیر", "تاخیردار", "تأخیردار", "بله"}
            matched_del = [v for v in obs_df if v.strip().lower() in del_tokens]
            if matched_del:
                q_del = ", ".join("'" + v.replace("'", "''") + "'" for v in matched_del[:5])
                elig_cond = f'"{dffn}" IS NOT NULL{cancel_excl_sql}'
                del_cond = f'CAST("{dffn}" AS VARCHAR) IN ({q_del})'
                elig_fa = f"کلیه رکوردهای دارای وضعیت مشخص در «{delay_flag_f['display_name']}»{cancel_note_fa}"
                del_fa = f"مواردی که مقدار «{delay_flag_f['display_name']}» جزو ({'، '.join(matched_del[:3])}) باشد"
                dr_fields = [dffn]

    if not elig_cond or not del_cond:
        std_kpis.append(_make_std(
            kpi_id="std_delay_rate",
            name="نرخ تأخیر",
            description="نسبت موارد تأخیردار به کل موارد واجد شرایط (درصد).",
            formula=dr_formula,
            display_format="percentage",
            unit="%",
            direction="lower_is_better",
            missing_fields=["delivery_and_due_date_or_sla"],
            missing_fields_fa=["فیلدهای تاریخ تحویل واقعی و تاریخ سررسید، یا مدت انتظار و SLA، یا ستون روز/وضعیت تأخیر"],
            computability_status="needs_data",
            fields_exist=False,
            eligible_definition_fa="نیازمند ستون‌های تاریخ تحویل/سررسید یا SLA برای تعریف موارد واجد شرایط",
            delayed_definition_fa="نیازمند مبنای مقایسه جهت تشخیص تأخیر",
            priority=7,
        ))
    else:
        dr_formula = (
            f'SUM(CASE WHEN ({elig_cond}) AND ({del_cond}) THEN 1.0 ELSE 0.0 END) * 100.0 '
            f'/ NULLIF(SUM(CASE WHEN ({elig_cond}) THEN 1.0 ELSE 0.0 END), 0)'
        )
        warns = []
        errs = []
        val = None
        try:
            dr_r = warehouse.execute_query(
                f'SELECT '
                f'SUM(CASE WHEN ({elig_cond}) THEN 1.0 ELSE 0.0 END) AS elig_cnt, '
                f'SUM(CASE WHEN ({elig_cond}) AND ({del_cond}) THEN 1.0 ELSE 0.0 END) AS del_cnt, '
                f'COUNT(*) AS tot_cnt '
                f'FROM ({sub_sql}) AS _dr',
                sub_params, limit=1,
            )["rows"]
            elig_cnt = int(round(float(dr_r[0]["elig_cnt"] or 0))) if dr_r else 0
            del_cnt = int(round(float(dr_r[0]["del_cnt"] or 0))) if dr_r else 0
            tot_cnt = int((dr_r[0]["tot_cnt"] or 0)) if dr_r else 0
            excl_cnt = max(0, tot_cnt - elig_cnt)
            if elig_cnt == 0:
                errs.append("هیچ رکورد واجد شرایطی برای محاسبه نرخ تأخیر وجود ندارد (مخرج صفر).")
            else:
                val = (del_cnt * 100.0) / elig_cnt
                if excl_cnt > 0:
                    warns.append(f"{excl_cnt:,} رکورد فاقد تاریخ معتبر یا لغوشده از مخرج مستثنی شد (واجد شرایط: {elig_cnt:,} | تأخیردار: {del_cnt:,}).")
        except Exception as exc:
            errs.append(f"خطا در محاسبه نرخ تأخیر: {exc}")

        std_kpis.append(_make_std(
            kpi_id="std_delay_rate",
            name="نرخ تأخیر",
            description="نسبت دقیق موارد تأخیردار به کل موارد واجد شرایط بر حسب درصد (مقدار کمتر بهتر است).",
            formula=dr_formula,
            display_format="percentage",
            unit="%",
            direction="lower_is_better",
            base_fields=dr_fields,
            source_datasets=dr_ds_list,
            value=val,
            computability_status="uncomputable" if errs else ("computable_with_warning" if warns else "computable"),
            warnings_fa=warns,
            errors_fa=errs,
            comparison_basis="بر اساس رکوردهای واجد شرایط",
            eligible_definition_fa=elig_fa,
            delayed_definition_fa=del_fa,
            priority=7,
            field_primary=dr_fields[0] if dr_fields else None,
        ))

    return std_kpis, rel_plan


def _normalize_formula_signature(expr: str) -> str:
    s = re.sub(r"\s+", "", (expr or "").lower()).replace('"', "")
    return s


def _build_cache_key(
    ds: Dataset,
    sec_ctx: DataSecurityContext,
    fields: list[DatasetField],
    rels: list[DatasetRelationship],
    prefix: str = "kpi_analyze",
) -> str:
    field_sig = "|".join(
        f"{f.field_name}:{f.semantic_role}:{f.semantic_class}:{f.aggregation_policy}:{f.version}:{json.dumps(f.user_override_json or {}, sort_keys=True)}"
        for f in sorted(fields, key=lambda x: x.field_name)
    )
    rel_sig = "|".join(
        f"{r.id}:{r.left_field}:{r.right_field}:{r.cardinality}:{r.is_active}"
        for r in sorted(rels, key=lambda x: x.id)
    )
    raw = f"{ds.organization_id}:{ds.id}:{ds.data_version}:{sec_ctx.context_hash}:{field_sig}:{rel_sig}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}:{ds.organization_id}:{ds.id}:{digest}"


class IntelligentKpiEngine:
    """5-Stage Intelligent KPI & Multi-Page Dashboard Engine."""

    # ------------------------------------------------------------------
    # STAGE 1: DEEP DATASET PROFILING (Batched, RLS/CLS Safe, Sampled >100k)
    # ------------------------------------------------------------------
    @staticmethod
    def deep_profile_dataset(
        db: Session,
        ds: Dataset,
        user: Optional[User] = None,
        sec_ctx: Optional[DataSecurityContext] = None,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        eff_ctx = sec_ctx or (
            build_data_security_context(db, user, ds.id)
            if user is not None
            else DataSecurityContext("system", [], [], set(), {}, "system")
        )
        db_fields = db.query(DatasetField).filter(DatasetField.dataset_id == ds.id).all()
        db_rels = (
            db.query(DatasetRelationship)
            .filter(
                (DatasetRelationship.left_dataset_id == ds.id) | (DatasetRelationship.right_dataset_id == ds.id),
                DatasetRelationship.is_active == True,
            )
            .all()
        )
        cache_key = _build_cache_key(ds, eff_ctx, db_fields, db_rels, prefix="kpi_deep_prof")
        if not force_refresh:
            cached = cache_manager.get(cache_key)
            if isinstance(cached, dict):
                return cached

        cols_meta = warehouse.describe_table(ds.warehouse_table)
        sub_sql, params, visible_cols = build_security_projection_and_where(
            ds.warehouse_table, cols_meta, eff_ctx, source_schema="main"
        )
        visible_set = set(visible_cols)
        visible_meta = [c for c in cols_meta if c["column_name"] in visible_set]

        cnt_res = warehouse.execute_query(f"SELECT COUNT(*) AS cnt FROM ({sub_sql}) AS _dp_cnt", params, limit=1)
        row_count = int(cnt_res["rows"][0]["cnt"]) if cnt_res["rows"] else 0

        is_approximate = row_count > LARGE_TABLE_THRESHOLD
        sample_size = min(row_count, SAMPLE_TARGET_ROWS) if is_approximate else row_count
        if is_approximate:
            scan_sql = f"SELECT * FROM ({sub_sql}) AS _dp_src USING SAMPLE {SAMPLE_TARGET_ROWS} ROWS"
            approx_note = f"با توجه به حجم بالای داده ({row_count:,} ردیف)، پروفایل آماری بر اساس نمونه کنترل‌شده {sample_size:,} ردیفی و توابع تقریبی محاسبه شده است."
        else:
            scan_sql = f"SELECT * FROM ({sub_sql}) AS _dp_src"
            approx_note = None

        field_by_name = {f.field_name: f for f in db_fields if f.field_name not in eff_ctx.hidden_columns}
        fk_fields: set[str] = set()
        for r in db_rels:
            if r.left_dataset_id == ds.id:
                fk_fields.add(r.left_field)
            if r.right_dataset_id == ds.id:
                fk_fields.add(r.right_field)

        # Duplicate row estimation across up to 6 visible physical columns
        duplicate_row_count = 0
        if row_count > 1 and visible_cols:
            key_cols = [f'COALESCE(CAST("{c}" AS VARCHAR), \'__NULL__\')' for c in visible_cols[:6]]
            dup_sql = (
                f"SELECT COUNT(*) AS total_n, COUNT(DISTINCT CONCAT_WS('|', {', '.join(key_cols)})) AS uniq_n "
                f"FROM ({scan_sql}) AS _dup_chk"
            )
            try:
                dup_r = warehouse.execute_query(dup_sql, params, limit=1)["rows"]
                if dup_r:
                    t_n = int(dup_r[0].get("total_n") or 0)
                    u_n = int(dup_r[0].get("uniq_n") or 0)
                    duplicate_row_count = max(0, t_n - u_n)
                    if is_approximate and t_n > 0:
                        duplicate_row_count = int(round(duplicate_row_count * (row_count / t_n)))
            except Exception:
                duplicate_row_count = 0

        fields_profile: list[dict[str, Any]] = []
        numeric_cols_for_outliers: list[tuple[str, float, float]] = []
        categorical_cols_for_topk: list[str] = []
        date_cols_for_temporal: list[str] = []

        # Batch 1: Core descriptive & quantile metrics (max 10 columns per SQL batch)
        for start in range(0, len(visible_meta), MAX_PROFILE_BATCH):
            batch = visible_meta[start : start + MAX_PROFILE_BATCH]
            sel_parts: list[str] = ["COUNT(*) AS _n_sample"]
            flags: list[tuple[dict[str, str], bool, bool, bool, bool]] = []
            for idx, c in enumerate(batch):
                cname = c["column_name"]
                ctype = (c.get("column_type") or "").lower()
                is_masked = cname in eff_ctx.masked_columns
                is_bool = (not is_masked) and any(k in ctype for k in BOOL_SQL_HINTS)
                is_date = (not is_masked) and (
                    any(k in ctype for k in DATE_SQL_HINTS)
                    or (cname in field_by_name and (field_by_name[cname].data_type or "").lower() in ("date", "datetime"))
                )
                is_num = (not is_masked) and (not is_bool) and (not is_date) and any(k in ctype for k in NUMERIC_SQL_HINTS)
                flags.append((c, is_masked, is_num, is_date, is_bool))

                dist_expr = f'APPROX_COUNT_DISTINCT("{cname}")' if is_approximate else f'COUNT(DISTINCT "{cname}")'
                sel_parts.append(f'COUNT("{cname}") AS "nn_{idx}"')
                sel_parts.append(f'(COUNT(*) - COUNT("{cname}")) AS "nl_{idx}"')
                sel_parts.append(f'{dist_expr} AS "ds_{idx}"')

                if is_num:
                    sel_parts.extend([
                        f'MIN("{cname}") AS "mn_{idx}"',
                        f'MAX("{cname}") AS "mx_{idx}"',
                        f'AVG("{cname}") AS "av_{idx}"',
                        f'MEDIAN("{cname}") AS "md_{idx}"',
                        f'STDDEV_SAMP("{cname}") AS "sd_{idx}"',
                        f'QUANTILE_CONT("{cname}", 0.25) AS "p25_{idx}"',
                        f'QUANTILE_CONT("{cname}", 0.75) AS "p75_{idx}"',
                        f'QUANTILE_CONT("{cname}", 0.90) AS "p90_{idx}"',
                        f'QUANTILE_CONT("{cname}", 0.95) AS "p95_{idx}"',
                        f'SUM(CASE WHEN "{cname}" = 0 THEN 1 ELSE 0 END) AS "zc_{idx}"',
                        f'SUM(CASE WHEN "{cname}" < 0 THEN 1 ELSE 0 END) AS "nc_{idx}"',
                    ])
                elif is_date:
                    sel_parts.extend([
                        f'CAST(MIN(TRY_CAST("{cname}" AS DATE)) AS VARCHAR) AS "mn_{idx}"',
                        f'CAST(MAX(TRY_CAST("{cname}" AS DATE)) AS VARCHAR) AS "mx_{idx}"',
                    ])
                else:
                    sel_parts.extend([
                        f'CAST(MIN("{cname}") AS VARCHAR) AS "mn_{idx}"',
                        f'CAST(MAX("{cname}") AS VARCHAR) AS "mx_{idx}"',
                    ])

            batch_sql = f'SELECT {", ".join(sel_parts)} FROM ({scan_sql}) AS _dp_b'
            b_rows = warehouse.execute_query(batch_sql, params, limit=1)["rows"]
            row0 = b_rows[0] if b_rows else {}
            n_sample = max(int(row0.get("_n_sample") or 0), 0)

            for idx, (c, is_masked, is_num, is_date, is_bool) in enumerate(flags):
                cname = c["column_name"]
                f_orm = field_by_name.get(cname)
                non_null = int(row0.get(f"nn_{idx}") or 0)
                null_cnt = int(row0.get(f"nl_{idx}") or 0)
                distinct_cnt = int(row0.get(f"ds_{idx}") or 0)
                if is_approximate and n_sample > 0:
                    scale = row_count / n_sample
                    non_null_scaled = int(round(non_null * scale))
                    null_cnt_scaled = max(0, row_count - non_null_scaled)
                else:
                    non_null_scaled = non_null
                    null_cnt_scaled = null_cnt

                null_pct = round((null_cnt / n_sample) * 100.0, 2) if n_sample > 0 else 0.0
                dup_val_cnt = max(0, non_null_scaled - distinct_cnt)
                cardinality_ratio = round(distinct_cnt / max(non_null, 1), 4)

                stat_entry: dict[str, Any] = {
                    "field_name": cname,
                    "display_name": f_orm.display_name if f_orm else cname.replace("_", " "),
                    "description": f_orm.description if f_orm else "",
                    "raw_type": "VARCHAR" if is_masked else c["column_type"],
                    "normalized_type": (
                        "string"
                        if is_masked
                        else ("boolean" if is_bool else ("date" if is_date else ("numeric" if is_num else "string")))
                    ),
                    "is_masked": is_masked,
                    "is_primary_key": bool(f_orm.is_primary_key) if f_orm else (cname == "id"),
                    "is_foreign_key": cname in fk_fields,
                    "is_calculated": bool(f_orm.is_calculated) if f_orm else False,
                    "existing_semantic_role": f_orm.semantic_role if f_orm else None,
                    "non_null_count": non_null_scaled,
                    "null_count": null_cnt_scaled,
                    "null_pct": null_pct,
                    "distinct_count": distinct_cnt,
                    "duplicate_value_count": dup_val_cnt,
                    "cardinality_ratio": cardinality_ratio,
                    "min": row0.get(f"mn_{idx}"),
                    "max": row0.get(f"mx_{idx}"),
                    "avg": _safe_float(row0.get(f"av_{idx}")),
                    "median": _safe_float(row0.get(f"md_{idx}")),
                    "stddev": _safe_float(row0.get(f"sd_{idx}")),
                    "p25": _safe_float(row0.get(f"p25_{idx}")),
                    "p75": _safe_float(row0.get(f"p75_{idx}")),
                    "p90": _safe_float(row0.get(f"p90_{idx}")),
                    "p95": _safe_float(row0.get(f"p95_{idx}")),
                    "zero_count": int(row0.get(f"zc_{idx}") or 0) if is_num else 0,
                    "zero_pct": round((int(row0.get(f"zc_{idx}") or 0) / max(non_null, 1)) * 100.0, 2) if is_num else 0.0,
                    "negative_count": int(row0.get(f"nc_{idx}") or 0) if is_num else 0,
                    "negative_pct": round((int(row0.get(f"nc_{idx}") or 0) / max(non_null, 1)) * 100.0, 2) if is_num else 0.0,
                    "iqr_outlier_count": 0,
                    "top_values": [],
                }
                if stat_entry["avg"] is not None:
                    stat_entry["avg"] = round(stat_entry["avg"], 4)
                if stat_entry["median"] is not None:
                    stat_entry["median"] = round(stat_entry["median"], 4)
                if stat_entry["stddev"] is not None:
                    stat_entry["stddev"] = round(stat_entry["stddev"], 4)
                for pk in ("p25", "p75", "p90", "p95"):
                    if stat_entry[pk] is not None:
                        stat_entry[pk] = round(stat_entry[pk], 4)

                fields_profile.append(stat_entry)

                if is_num and stat_entry["p25"] is not None and stat_entry["p75"] is not None:
                    iqr = stat_entry["p75"] - stat_entry["p25"]
                    if iqr > 0:
                        numeric_cols_for_outliers.append((cname, stat_entry["p25"] - 1.5 * iqr, stat_entry["p75"] + 1.5 * iqr))
                if is_date or ("date" in cname.lower() and not is_num):
                    date_cols_for_temporal.append(cname)
                if (not is_num) and (not is_date) and (not is_masked) and 1 <= distinct_cnt <= 500:
                    categorical_cols_for_topk.append(cname)

        # Batch 2: Exact IQR outlier counts across all numeric columns in ONE query
        if numeric_cols_for_outliers and row_count >= 4:
            out_exprs = []
            for idx, (cname, lo_b, hi_b) in enumerate(numeric_cols_for_outliers[:20]):
                out_exprs.append(
                    f'SUM(CASE WHEN "{cname}" < {float(lo_b)} OR "{cname}" > {float(hi_b)} THEN 1 ELSE 0 END) AS "out_{idx}"'
                )
            try:
                out_res = warehouse.execute_query(
                    f'SELECT {", ".join(out_exprs)} FROM ({scan_sql}) AS _dp_out', params, limit=1
                )["rows"]
                if out_res:
                    fp_map = {f["field_name"]: f for f in fields_profile}
                    for idx, (cname, _lo, _hi) in enumerate(numeric_cols_for_outliers[:20]):
                        fp_map[cname]["iqr_outlier_count"] = int(out_res[0].get(f"out_{idx}") or 0)
            except Exception:
                pass

        # Batch 3: Top-5 values across categorical columns in ONE UNION ALL query
        if categorical_cols_for_topk and row_count > 0:
            topk_parts = []
            for cname in categorical_cols_for_topk[:10]:
                safe_c = cname.replace("'", "''")
                topk_parts.append(
                    f"(SELECT '{safe_c}' AS col_name, CAST(\"{cname}\" AS VARCHAR) AS val_str, COUNT(*) AS cnt "
                    f"FROM ({scan_sql}) AS _dp_tk WHERE \"{cname}\" IS NOT NULL "
                    f"GROUP BY 2 ORDER BY 3 DESC LIMIT 5)"
                )
            try:
                tk_rows = warehouse.execute_query(
                    " UNION ALL ".join(topk_parts),
                    params * len(topk_parts),
                    limit=100,
                )["rows"]
                fp_map = {f["field_name"]: f for f in fields_profile}
                grouped_tk: dict[str, list[dict[str, Any]]] = {}
                for tr in tk_rows:
                    cn = tr["col_name"]
                    grouped_tk.setdefault(cn, []).append({
                        "value": tr["val_str"],
                        "count": int(tr["cnt"] or 0),
                        "pct": round(int(tr["cnt"] or 0) / max(sample_size or row_count, 1) * 100.0, 2),
                    })
                for cn, items in grouped_tk.items():
                    if cn in fp_map:
                        fp_map[cn]["top_values"] = items
                        fp_map[cn]["group_count"] = fp_map[cn]["distinct_count"]
                        if items:
                            fp_map[cn]["dominant_category"] = items[0]["value"]
                            fp_map[cn]["dominant_pct"] = items[0]["pct"]
            except Exception:
                pass

        # Batch 4: Temporal profiling (granularity, span, expected periods, gaps)
        temporal_summary: dict[str, Any] = {}
        fp_map = {f["field_name"]: f for f in fields_profile}
        for dcol in date_cols_for_temporal[:3]:
            try:
                t_sql = (
                    f'SELECT '
                    f'MIN(TRY_CAST("{dcol}" AS DATE)) AS dmin, '
                    f'MAX(TRY_CAST("{dcol}" AS DATE)) AS dmax, '
                    f'COUNT(DISTINCT TRY_CAST("{dcol}" AS DATE)) AS n_days, '
                    f'COUNT(DISTINCT DATE_TRUNC(\'week\', TRY_CAST("{dcol}" AS DATE))) AS n_weeks, '
                    f'COUNT(DISTINCT DATE_TRUNC(\'month\', TRY_CAST("{dcol}" AS DATE))) AS n_months, '
                    f'COUNT(DISTINCT DATE_TRUNC(\'year\', TRY_CAST("{dcol}" AS DATE))) AS n_years '
                    f'FROM ({scan_sql}) AS _dp_t WHERE TRY_CAST("{dcol}" AS DATE) IS NOT NULL'
                )
                tr = warehouse.execute_query(t_sql, params, limit=1)["rows"]
                if tr and tr[0].get("dmin") and tr[0].get("dmax"):
                    dmin_str = str(tr[0]["dmin"])[:10]
                    dmax_str = str(tr[0]["dmax"])[:10]
                    dmin_dt = datetime.fromisoformat(dmin_str).date()
                    dmax_dt = datetime.fromisoformat(dmax_str).date()
                    span_days = max(1, (dmax_dt - dmin_dt).days + 1)
                    n_days = int(tr[0].get("n_days") or 0)
                    n_weeks = int(tr[0].get("n_weeks") or 0)
                    n_months = int(tr[0].get("n_months") or 0)
                    n_years = int(tr[0].get("n_years") or 0)

                    if n_days >= 14 and (n_days / max(span_days, 1)) >= 0.45:
                        gran = "daily"
                        expected = span_days
                        actual_p = n_days
                    elif n_weeks >= 6 and (n_months < 4 or n_weeks > n_months * 2.2):
                        gran = "weekly"
                        expected = max(1, (span_days // 7) + 1)
                        actual_p = n_weeks
                    elif n_months >= 2:
                        gran = "monthly"
                        expected = max(1, (dmax_dt.year - dmin_dt.year) * 12 + (dmax_dt.month - dmin_dt.month) + 1)
                        actual_p = n_months
                    elif n_years >= 2:
                        gran = "yearly"
                        expected = max(1, (dmax_dt.year - dmin_dt.year) + 1)
                        actual_p = n_years
                    else:
                        gran = "daily" if n_days >= 2 else "single_period"
                        expected = max(n_days, 1)
                        actual_p = n_days

                    gap_count = max(0, expected - actual_p) if not is_approximate else 0
                    completeness = round(min(100.0, (actual_p / max(expected, 1)) * 100.0), 1)
                    t_info = {
                        "field_name": dcol,
                        "min_date": dmin_str,
                        "max_date": dmax_str,
                        "span_days": span_days,
                        "distinct_days": n_days,
                        "distinct_months": n_months,
                        "distinct_years": n_years,
                        "detected_granularity": gran,
                        "expected_periods": expected,
                        "actual_periods": actual_p,
                        "gap_count": gap_count,
                        "has_time_gaps": gap_count > 0,
                        "sequence_completeness_pct": completeness,
                    }
                    temporal_summary[dcol] = t_info
                    if dcol in fp_map:
                        fp_map[dcol].update({
                            "min": dmin_str,
                            "max": dmax_str,
                            "temporal": t_info,
                        })
            except Exception:
                continue

        # Overall dataset quality metrics
        total_cells = max(1, row_count * max(1, len(fields_profile)))
        total_nulls = sum(int(f["null_count"] or 0) for f in fields_profile)
        completeness_pct = round(max(0.0, 100.0 - (total_nulls / total_cells) * 100.0), 2) if row_count > 0 else 100.0
        heavy_null_fields = [f["field_name"] for f in fields_profile if f["null_pct"] >= 10.0]
        constant_fields = [f["field_name"] for f in fields_profile if row_count > 1 and f["distinct_count"] <= 1]
        outlier_fields = [f["field_name"] for f in fields_profile if f["iqr_outlier_count"] > 0]
        total_outliers = sum(int(f["iqr_outlier_count"] or 0) for f in fields_profile)

        result = {
            "dataset_id": ds.id,
            "dataset_name": ds.name,
            "table_name": ds.warehouse_table,
            "row_count": row_count,
            "field_count": len(fields_profile),
            "sample_size": sample_size,
            "is_approximate": is_approximate,
            "approximation_note_fa": approx_note,
            "duplicate_row_count": duplicate_row_count,
            "completeness_pct": completeness_pct,
            "total_null_cells": total_nulls,
            "total_outliers": total_outliers,
            "quality_summary": {
                "completeness_pct": completeness_pct,
                "duplicate_row_count": duplicate_row_count,
                "heavy_null_fields": heavy_null_fields,
                "constant_fields": constant_fields,
                "outlier_fields": outlier_fields,
                "time_gap_fields": [k for k, v in temporal_summary.items() if v.get("has_time_gaps")],
            },
            "temporal_summary": temporal_summary,
            "fields": fields_profile,
            "profiling_ms": round((time.perf_counter() - t0) * 1000, 2),
        }
        cache_manager.set(cache_key, result, ttl_seconds=180)
        return result

    # ------------------------------------------------------------------
    # STAGE 2: SEMANTIC FIELD CLASSIFICATION (22 Classes + Aggregation Policy)
    # ------------------------------------------------------------------
    @staticmethod
    def classify_fields(
        db: Session,
        ds: Dataset,
        deep_profile: dict[str, Any],
    ) -> list[dict[str, Any]]:
        db_fields = {f.field_name: f for f in db.query(DatasetField).filter(DatasetField.dataset_id == ds.id).all()}
        visible_names = {f["field_name"] for f in deep_profile.get("fields", [])}
        row_count = int(deep_profile.get("row_count") or 0)
        ds_hay = f"{ds.name} {ds.description or ''}".lower()
        has_target_sibling = any(
            any(k in fn.lower() for k in ("target", "budget", "goal", "plan", "quota", "هدف", "بودجه"))
            for fn in visible_names
        )

        classified: list[dict[str, Any]] = []
        for fp in deep_profile.get("fields", []):
            fname = fp["field_name"]
            f_orm = db_fields.get(fname)
            override = (f_orm.user_override_json if f_orm and isinstance(f_orm.user_override_json, dict) else {}) or {}
            disp = (override.get("display_name") or fp.get("display_name") or fname).strip()
            desc = (f_orm.description if f_orm else "") or fp.get("description") or ""
            ntype = fp["normalized_type"]
            raw_low = f"{fname} {disp} {desc}".lower()
            tokens = set(re.split(r"[\s_\-./]+", raw_low))
            distinct_cnt = int(fp.get("distinct_count") or 0)
            card = float(fp.get("cardinality_ratio") or 0.0)
            mn = _safe_float(fp.get("min"))
            mx = _safe_float(fp.get("max"))
            neg_pct = float(fp.get("negative_pct") or 0.0)
            top_vals = [str(x.get("value", "")).lower() for x in (fp.get("top_values") or [])]

            evidences: list[str] = []
            sem_class = "Category"
            conf = 0.72
            override_applied = False

            # Signal 5: Explicit user override always takes highest priority
            if override.get("semantic_class") in SEMANTIC_CLASS_META:
                sem_class = override["semantic_class"]
                conf = 0.99
                override_applied = True
                evidences.append("تنظیم‌شده توسط کاربر (Override)")
            else:
                # Signal 3 & 4 & 1 & 2: Type + Name + Distribution + Sample Values + Sibling context
                if ntype == "boolean" or (
                    ntype in ("string", "numeric")
                    and distinct_cnt <= 2
                    and top_vals
                    and all(v in ("0", "1", "true", "false", "yes", "no", "بله", "خیر", "y", "n") for v in top_vals)
                ):
                    sem_class = "Boolean"
                    conf = 0.94
                    evidences.append("مقادیر دودویی (بله/خیر یا True/False)")
                elif fp.get("temporal") or ntype == "date" or (
                    fp.get("existing_semantic_role") == "time" and ntype != "numeric"
                ) or any(k in tokens for k in ("date", "timestamp", "created_at", "updated_at", "تاریخ", "زمان")):
                    sem_class = "Date/Time"
                    conf = 0.95
                    evidences.append("نوع یا الگوی تاریخ و زمان")
                elif fp.get("is_primary_key") or fp.get("is_foreign_key") or (
                    any(k in raw_low for k in ("_id", "code", "national_id", "sku", "uuid", "شناسه", "کد"))
                    and not any(k in raw_low for k in ("zip", "postal", "status_code"))
                ) or (ntype == "numeric" and distinct_cnt == row_count and row_count >= 10 and "id" in tokens):
                    sem_class = "Identifier"
                    conf = 0.93 if (fp.get("is_primary_key") or fp.get("is_foreign_key") or "_id" in raw_low or "id" in tokens) else 0.78
                    evidences.append("کلید/شناسه یا کد یکتا (جلوگیری از SUM)")
                elif ntype == "numeric":
                    if any(k in raw_low for k in ("pct", "percent", "percentage", "margin_pct", "rate_pct", "درصد", "نرخ")):
                        sem_class = "Percentage"
                        conf = 0.94
                        evidences.append("نام یا الگوی درصدی (غیرقابل جمع)")
                    elif mn is not None and mx is not None and 0 <= mn and mx <= 100 and (
                        "rate" in tokens or "share" in tokens or "utilization" in tokens or "efficiency" in tokens or "availability" in tokens or "بهره‌وری" in raw_low or "سهم" in raw_low
                    ):
                        sem_class = "Percentage"
                        conf = 0.87
                        evidences.append("بازه ۰ تا ۱۰۰ و نام نرخ/سهم")
                    elif any(k in raw_low for k in ("ratio", "index", "score", "cpi", "spi", "nps", "rating", "نسبت", "شاخص", "امتیاز")):
                        sem_class = "Ratio" if ("ratio" in raw_low or "نسبت" in raw_low or "cpi" in tokens or "spi" in tokens) else "Non-additive Measure"
                        conf = 0.89
                        evidences.append("نسبت/امتیاز غیرجمع‌پذیر")
                    elif any(k in raw_low for k in ("unit_price", "price_per", "unit_cost", "tariff", "فی", "قیمت واحد", "نرخ واحد")):
                        sem_class = "Unit Price"
                        conf = 0.92
                        evidences.append("قیمت واحد (میانگین‌پذیر، غیرقابل SUM)")
                    elif any(k in raw_low for k in ("balance", "stock", "inventory", "on_hand", "headcount", "مانده", "موجودی")):
                        sem_class = "Balance" if ("balance" in raw_low or "مانده" in raw_low or "stock" in raw_low or "inventory" in raw_low or "موجودی" in raw_low) else "Semi-additive Measure"
                        conf = 0.91
                        evidences.append("سنجه لحظه‌ای/نیمه‌جمع‌پذیر (اولویت با آخرین مقدار/میانگین)")
                    elif any(k in raw_low for k in ("duration", "wait", "latency", "delay", "cycle_time", "response_time", "resolution_time", "hours", "minutes", "seconds", "sla_hours", "outage_minutes", "مدت", "انتظار", "تاخیر", "زمان")):
                        sem_class = "Duration"
                        conf = 0.91
                        evidences.append("سنجه مدت زمان / تاخیر")
                    elif any(k in raw_low for k in ("target", "budget", "goal", "plan", "quota", "planned", "هدف", "بودجه", "برنامه")):
                        sem_class = "Target"
                        conf = 0.92
                        evidences.append("ستون هدف / بودجه")
                    elif any(k in raw_low for k in ("actual", "realized", "achieved", "عملکرد واقعی", "محقق")):
                        sem_class = "Actual"
                        conf = 0.90
                        evidences.append("عملکرد واقعی")
                    elif any(k in raw_low for k in ("revenue", "sales", "income", "turnover", "gmv", "فروش", "درآمد")):
                        sem_class = "Revenue"
                        conf = 0.94
                        evidences.append("درآمد / فروش")
                    elif any(k in raw_low for k in ("cost", "expense", "cogs", "spend", "payroll", "salary", "wage", "هزینه", "حقوق", "بهای تمام‌شده")):
                        sem_class = "Cost"
                        conf = 0.93
                        evidences.append("هزینه")
                    elif any(k in raw_low for k in ("profit", "ebitda", "net_income", "margin_amount", "سود")):
                        sem_class = "Profit"
                        conf = 0.93
                        evidences.append("سود")
                    elif any(k in raw_low for k in ("qty", "quantity", "units", "volume", "pieces", "items", "تعداد", "مقدار")):
                        sem_class = "Quantity"
                        conf = 0.91
                        evidences.append("مقدار / تعداد فیزیکی")
                    elif any(k in raw_low for k in ("count", "num_", "total_orders", "tickets", "outages", "customers", "شمار", "تعداد")):
                        sem_class = "Count Measure"
                        conf = 0.89
                        evidences.append("سنجه شمارشی")
                    elif any(k in raw_low for k in ("amount", "price", "value", "fee", "payment", "contract_value", "مبلغ", "بها", "پرداخت", "ریال", "تومان")):
                        if has_target_sibling and "amount" in raw_low:
                            sem_class = "Actual"
                            conf = 0.84
                        else:
                            sem_class = "Currency"
                            conf = 0.88
                        evidences.append("مبلغ مالی / ارزی")
                    elif any(k in tokens for k in ("year", "month", "quarter", "day", "week")):
                        sem_class = "Date/Time"
                        conf = 0.85
                        evidences.append("بعد زمانی عددی (سال/ماه/فصل)")
                    elif fp.get("existing_semantic_role") == "measure":
                        sem_class = "Additive Measure"
                        conf = 0.78
                        evidences.append("نقش معنایی Measure ثبت‌شده در دیتاست")
                    else:
                        # Ambiguous pure numeric column (e.g. "x1" or "col_a")
                        if mn is not None and mx is not None and 0 <= mn and mx <= 1.0 and neg_pct == 0 and distinct_cnt > 5:
                            sem_class = "Ratio"
                            conf = 0.62
                            evidences.append("مقدار پیوسته در بازه ۰ تا ۱ (احتمالاً نسبت)")
                        elif card > 0.9 and distinct_cnt > 20 and all(float(v or 0).is_integer() for v in (mn, mx) if v is not None) and "num" not in raw_low and "val" not in raw_low:
                            sem_class = "Additive Measure"
                            conf = 0.65
                            evidences.append("ستون عددی عمومی بدون الگوی نام مشخص (نیازمند بررسی کاربر)")
                        else:
                            sem_class = "Additive Measure"
                            conf = 0.75
                            evidences.append("ستون عددی پیوسته")
                else:
                    # String / categorical field
                    if any(k in raw_low for k in ("country", "province", "city", "region", "state", "area", "zone", "branch_city", "latitude", "longitude", "استان", "شهر", "منطقه", "کشور")):
                        sem_class = "Geographic"
                        conf = 0.92
                        evidences.append("بعد جغرافیایی")
                    elif any(k in raw_low for k in ("status", "state", "stage", "level", "priority", "severity", "وضعیت", "مرحله", "سطح", "اولویت")) or (
                        top_vals and any(v in ("completed", "done", "open", "closed", "pending", "delayed", "failed", "error", "ok", "active", "تکمیل", "تأخیر", "باز", "بسته", "موفق", "ناموفق") for v in top_vals)
                    ):
                        sem_class = "Status"
                        conf = 0.90
                        evidences.append("ستون وضعیت / مرحله")
                    else:
                        sem_class = "Category"
                        conf = 0.84 if distinct_cnt <= 200 else 0.70
                        evidences.append("دسته‌بندی ابعادی" if distinct_cnt <= 200 else "تنوع مقادیر بالا (نیازمند بررسی شناسه یا متن)")

            meta = SEMANTIC_CLASS_META[sem_class]
            eff_policy = override.get("aggregation_policy") or meta["policy"]
            eff_sum_allowed = meta["sum_allowed"] if override.get("aggregation_policy") is None else (eff_policy in ("additive", "count", "duration"))
            eff_default_agg = override.get("default_aggregation") or meta["default_agg"]
            eff_allowed_aggs = list(meta["allowed"])
            if eff_sum_allowed and "SUM" not in eff_allowed_aggs:
                eff_allowed_aggs.insert(0, "SUM")
            if not eff_sum_allowed and "SUM" in eff_allowed_aggs:
                eff_allowed_aggs.remove("SUM")

            # Map back to core ORM semantic_role cleanly
            if sem_class in ("Date/Time",):
                core_role = "time"
            elif sem_class == "Geographic":
                core_role = "geo"
            elif sem_class == "Identifier":
                core_role = "id"
            elif sem_class in ("Category", "Status", "Boolean"):
                core_role = "dimension"
            else:
                core_role = "measure"

            if "hr" in ds_hay and sem_class == "Currency" and "salary" in raw_low:
                sem_class = "Cost"
            target_val = _safe_float(override.get("target_value"))

            classified.append({
                "field_name": fname,
                "display_name": disp,
                "data_type": ntype,
                "raw_type": fp["raw_type"],
                "semantic_class": sem_class,
                "semantic_class_fa": SEMANTIC_CLASS_META[sem_class]["fa"],
                "semantic_role": core_role,
                "confidence": round(conf, 2),
                "needs_user_confirmation": (not override_applied) and conf < 0.72,
                "override_applied": override_applied,
                "aggregation_policy": eff_policy,
                "sum_allowed": eff_sum_allowed,
                "default_aggregation": eff_default_agg,
                "allowed_aggregations": eff_allowed_aggs,
                "evidence_fa": "؛ ".join(evidences),
                "target_value": target_val,
                "is_primary_key": fp["is_primary_key"],
                "is_foreign_key": fp["is_foreign_key"],
                "is_calculated": fp["is_calculated"],
                "stats": fp,
            })

        return classified

    # ------------------------------------------------------------------
    # STAGE 3: EVIDENCE-BASED BUSINESS DOMAIN DETECTION (15 Domains)
    # ------------------------------------------------------------------
    @staticmethod
    def detect_business_domain(
        ds: Dataset,
        classifications: list[dict[str, Any]],
        deep_profile: dict[str, Any],
        *,
        domain_override: Optional[str] = None,
        partner_datasets: Optional[list[Dataset]] = None,
    ) -> dict[str, Any]:
        ds_text = f"{ds.name} {ds.description or ''}".lower()
        scores: list[dict[str, Any]] = []

        for dom_id, dom_def in DOMAIN_DEFINITIONS.items():
            if dom_id == "general":
                continue
            matched_fields: list[str] = []
            matched_ds_kws: list[str] = []
            score = 0.0

            for f in classifications:
                hay = f"{f['field_name']} {f['display_name']}".lower()
                for kw in dom_def["field_keywords"]:
                    if kw in hay:
                        score += 2.5
                        if f["field_name"] not in matched_fields:
                            matched_fields.append(f["field_name"])
                        break
                # Check sample categorical values for domain clues (only non-masked visible values)
                for tv in (f.get("stats", {}).get("top_values") or []):
                    v_low = str(tv.get("value", "")).lower()
                    for kw in dom_def["field_keywords"]:
                        if len(kw) >= 4 and kw == v_low:
                            score += 1.0
                            if f["field_name"] not in matched_fields:
                                matched_fields.append(f["field_name"])
                            break

            for dkw in dom_def["dataset_keywords"]:
                if dkw in ds_text:
                    score += 3.0
                    matched_ds_kws.append(dkw)

            if score > 0:
                scores.append({
                    "domain": dom_id,
                    "domain_fa": dom_def["fa"],
                    "family": DOMAIN_FAMILY_MAP.get(dom_id, "Business"),
                    "family_fa": DOMAIN_FAMILY_FA.get(DOMAIN_FAMILY_MAP.get(dom_id, "Business"), "کسب‌وکار"),
                    "score": round(score, 2),
                    "matched_fields": matched_fields,
                    "matched_dataset_keywords": matched_ds_kws,
                })

        scores.sort(key=lambda x: -x["score"])

        # Honor explicit user domain clarification / override
        if domain_override and domain_override in DOMAIN_DEFINITIONS:
            ov_def = DOMAIN_DEFINITIONS[domain_override]
            ov_fam = DOMAIN_FAMILY_MAP.get(domain_override, "Business")
            matched_cand = next((s for s in scores if s["domain"] == domain_override), None)
            return {
                "primary_domain": domain_override,
                "primary_domain_fa": ov_def["fa"],
                "domain_family": ov_fam,
                "domain_family_fa": DOMAIN_FAMILY_FA.get(ov_fam, "کسب‌وکار"),
                "secondary_domains": [{"domain": s["domain"], "domain_fa": s["domain_fa"], "score": s["score"]} for s in scores[:3] if s["domain"] != domain_override],
                "confidence": 1.0,
                "is_ambiguous": False,
                "user_confirmed_domain": True,
                "clarification_question": None,
                "evidence_fa": f"دامنه توسط کاربر به «{ov_def['fa']}» تأیید شد.",
                "candidates_evaluated": scores[:5],
                "matched_fields": matched_cand["matched_fields"] if matched_cand else [],
            }

        if not scores or scores[0]["score"] < 2.5:
            return {
                "primary_domain": "general",
                "primary_domain_fa": DOMAIN_DEFINITIONS["general"]["fa"],
                "domain_family": "Business",
                "domain_family_fa": DOMAIN_FAMILY_FA["Business"],
                "secondary_domains": [],
                "confidence": 0.45,
                "is_ambiguous": True,
                "user_confirmed_domain": False,
                "clarification_question": {
                    "question_fa": "شواهد اسکیما برای تشخیص دقیق دامنه کافی نیست؛ لطفاً حوزه اصلی این مجموعه داده را مشخص کنید:",
                    "options": [
                        {"id": "sales_marketing", "label_fa": "کسب‌وکار / فروش و مالی", "family": "Business"},
                        {"id": "bpmn_process", "label_fa": "فرآیند و گردش کار", "family": "Process/Workflow"},
                        {"id": "it_observability", "label_fa": "عملیات فناوری اطلاعات", "family": "IT Operations"},
                        {"id": "security", "label_fa": "امنیت و لاگ‌های امنیتی", "family": "Security"},
                        {"id": "network", "label_fa": "شبکه و ترافیک", "family": "Network"},
                        {"id": "general", "label_fa": "عمومی / ترکیبی", "family": "Mixed"},
                    ],
                },
                "evidence_fa": "نشانگرهای کافی برای یک حوزه تخصصی خاص یافت نشد؛ تحلیل بر پایه الگوی عمومی انجام می‌شود.",
                "candidates_evaluated": scores[:5],
            }

        top = scores[0]
        conf = round(min(0.98, 0.45 + (top["score"] / (top["score"] + 4.0)) * 0.53), 2)
        secondaries = [s for s in scores[1:3] if s["score"] >= max(3.0, top["score"] * 0.5)]
        top_family = DOMAIN_FAMILY_MAP.get(top["domain"], "Business")

        close_rival_families = [
            s for s in scores[1:4]
            if s["score"] >= max(3.0, top["score"] * 0.72) and DOMAIN_FAMILY_MAP.get(s["domain"], "Business") != top_family
        ]
        is_mixed_signal = bool(close_rival_families)
        is_ambiguous = conf < 0.65 or is_mixed_signal
        eff_family = "Mixed" if is_mixed_signal else top_family

        # Check multi-dataset cross-domain mix
        if partner_datasets and len(partner_datasets) > 0:
            partner_text = " ".join(f"{p.name} {p.description or ''}".lower() for p in partner_datasets)
            for p_dom, p_def in DOMAIN_DEFINITIONS.items():
                if p_dom == "general":
                    continue
                if any(kw in partner_text for kw in p_def["dataset_keywords"]) and DOMAIN_FAMILY_MAP.get(p_dom) != top_family:
                    eff_family = "Mixed"
                    break

        clarification_q = None
        if is_ambiguous:
            opts = [
                {
                    "id": s["domain"],
                    "label_fa": f"{s['domain_fa']} ({s['family_fa']})",
                    "family": s["family"],
                    "score": s["score"],
                }
                for s in scores[:4]
            ]
            clarification_q = {
                "question_fa": (
                    "چند دامنه تحلیلی به‌طور هم‌زمان امتیاز بالایی دارند (یا اطمینان تشخیص زیر ۶۵٪ است)؛ کدام دامنه ملاک ساخت داشبورد باشد؟"
                ),
                "options": opts,
            }

        ev_parts = []
        if top["matched_fields"]:
            ev_parts.append(f"ستون‌های مرتبط: {', '.join(top['matched_fields'][:6])}")
        if top["matched_dataset_keywords"]:
            ev_parts.append(f"عنوان منبع داده: {', '.join(top['matched_dataset_keywords'][:3])}")
        return {
            "primary_domain": top["domain"],
            "primary_domain_fa": top["domain_fa"],
            "domain_family": eff_family,
            "domain_family_fa": DOMAIN_FAMILY_FA.get(eff_family, eff_family),
            "secondary_domains": [{"domain": s["domain"], "domain_fa": s["domain_fa"], "family": s.get("family"), "score": s["score"]} for s in secondaries],
            "confidence": conf,
            "is_ambiguous": is_ambiguous,
            "user_confirmed_domain": False,
            "clarification_question": clarification_q,
            "evidence_fa": " | ".join(ev_parts),
            "candidates_evaluated": scores[:5],
        }

    # ------------------------------------------------------------------
    # STAGE 4: DIVERSE KPI CANDIDATE GENERATION, VALIDATION & SCORING
    # ------------------------------------------------------------------
    @staticmethod
    def generate_and_score_kpis(
        db: Session,
        ds: Dataset,
        user: Optional[User],
        deep_profile: dict[str, Any],
        classifications: list[dict[str, Any]],
        domain_info: dict[str, Any],
        related_dataset_ids: Optional[list[str]] = None,
        target_values: Optional[dict[str, float]] = None,
        calendar_mode: str = "gregorian",
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        row_count = int(deep_profile.get("row_count") or 0)
        eff_targets = dict(target_values or {})
        for c in classifications:
            if c.get("target_value") is not None and c["field_name"] not in eff_targets:
                eff_targets[c["field_name"]] = float(c["target_value"])

        # Visible column classification buckets
        class_map = {c["field_name"]: c for c in classifications}
        time_fields = [c for c in classifications if c["semantic_class"] == "Date/Time" and c.get("stats", {}).get("temporal")]
        all_time_cols = [c for c in classifications if c["semantic_class"] == "Date/Time"]
        primary_time = time_fields[0] if time_fields else (all_time_cols[0] if all_time_cols else None)
        time_periods = int((primary_time.get("stats", {}).get("temporal") or {}).get("actual_periods") or 0) if primary_time else 0
        time_gran = ((primary_time.get("stats", {}).get("temporal") or {}).get("detected_granularity") or "monthly") if primary_time else "none"

        measures_additive = [c for c in classifications if c["sum_allowed"] and c["data_type"] == "numeric"]
        measures_semi = [c for c in classifications if c["semantic_class"] in ("Balance", "Semi-additive Measure")]
        measures_non_add = [c for c in classifications if c["semantic_class"] in ("Percentage", "Ratio", "Unit Price", "Non-additive Measure")]
        duration_fields = [c for c in classifications if c["semantic_class"] == "Duration"]
        id_fields = [c for c in classifications if c["semantic_class"] == "Identifier"]
        dim_fields = [c for c in classifications if c["semantic_class"] in ("Category", "Status", "Geographic")]
        status_fields = [c for c in classifications if c["semantic_class"] in ("Status", "Boolean")]

        # Helper to find first matching classified field by predicate
        def _find(pred) -> Optional[dict[str, Any]]:
            return next((c for c in classifications if pred(c)), None)

        rev_f = _find(lambda c: c["semantic_class"] == "Revenue")
        cost_f = _find(lambda c: c["semantic_class"] == "Cost")
        profit_f = _find(lambda c: c["semantic_class"] == "Profit")
        actual_f = _find(lambda c: c["semantic_class"] == "Actual") or rev_f
        target_f = _find(lambda c: c["semantic_class"] == "Target")
        qty_f = _find(lambda c: c["semantic_class"] == "Quantity")
        cust_f = _find(lambda c: any(k in c["field_name"].lower() for k in ("customer", "client", "subscriber", "user_id", "مشتری")))
        order_f = _find(lambda c: any(k in c["field_name"].lower() for k in ("order_id", "invoice_id", "request_id", "request_code", "ticket_id", "شماره سفارش", "کد درخواست")))
        disc_f = _find(lambda c: any(k in c["field_name"].lower() for k in ("discount", "refund", "return_amount", "تخفیف", "مرجوعی")))

        candidates: list[dict[str, Any]] = []
        primary_domain = domain_info.get("primary_domain", "general")

        def _add_cand(
            *,
            kpi_id: str,
            name: str,
            category: str,
            formula: str,
            agg: str = "FORMULA",
            field: Optional[str] = None,
            format_type: str = "number",
            unit: str = "",
            direction: str = "higher_is_better",
            required_fields: Optional[list[str]] = None,
            importance: float = 0.75,
            domain_fit: float = 0.65,
            confidence: float = 0.90,
            comparison_basis: Optional[str] = None,
            limitations: Optional[list[str]] = None,
            target_val: Optional[float] = None,
            source_datasets: Optional[list[str]] = None,
            joins: Optional[list[dict[str, Any]]] = None,
            visual_spec_extra: Optional[dict[str, Any]] = None,
            needs_confirm: bool = False,
            rejection_reason: Optional[str] = None,
            is_data_quality: bool = False,
        ) -> None:
            candidates.append({
                "id": kpi_id,
                "slug": re.sub(r"[^a-z0-9_]+", "_", kpi_id.lower()).strip("_")[:100],
                "name": name,
                "category": "data_quality" if is_data_quality else category,
                "formula": formula,
                "aggregation": agg,
                "field": field,
                "format_type": format_type,
                "unit": unit,
                "direction": direction,
                "required_fields": required_fields or ([field] if field else []),
                "importance": importance,
                "domain_fit": domain_fit,
                "confidence": round(confidence, 2),
                "comparison_basis": comparison_basis,
                "limitations": list(limitations or []),
                "target_value": target_val,
                "source_datasets": source_datasets or [ds.name],
                "joins": joins or [],
                "visual_spec_extra": visual_spec_extra or {},
                "needs_user_confirmation": needs_confirm,
                "rejection_reason_fa": rejection_reason,
                "is_data_quality": is_data_quality,
            })

        # --- 4.1 Record Count & Distinct Entity KPIs ---
        _add_cand(
            kpi_id="total_records",
            name="تعداد کل رکوردها",
            category="aggregate",
            formula="COUNT(*)",
            agg="COUNT",
            field="*",
            importance=0.68,
            domain_fit=0.60,
            confidence=0.99,
            required_fields=[],
        )
        for idx_f in id_fields[:3]:
            fn = idx_f["field_name"]
            _add_cand(
                kpi_id=f"distinct_{fn}",
                name=f"تعداد یکتای {idx_f['display_name']}",
                category="aggregate",
                formula=f'COUNT(DISTINCT "{fn}")',
                agg="COUNT_DISTINCT",
                field=fn,
                importance=0.82 if idx_f in (cust_f, order_f) else 0.70,
                domain_fit=0.80,
                confidence=idx_f["confidence"],
                required_fields=[fn],
            )
            # Explicitly prevent SUM on Identifier (logged only if numeric ID was tempting)
            if idx_f["data_type"] == "numeric":
                _add_cand(
                    kpi_id=f"blocked_sum_{fn}",
                    name=f"مجموع {idx_f['display_name']}",
                    category="aggregate",
                    formula=f'SUM("{fn}")',
                    agg="SUM",
                    field=fn,
                    importance=0.0,
                    domain_fit=0.0,
                    confidence=0.99,
                    required_fields=[fn],
                    rejection_reason=f"سیاست تجمیع: ستون «{idx_f['display_name']}» از نوع شناسه (Identifier) است و جمع عددی (SUM) روی آن نامعتبر است؛ از COUNT DISTINCT استفاده شد.",
                )

        # --- 4.2 Additive Measures (SUM + AVG + MIN/MAX where meaningful) ---
        for m in measures_additive:
            fn = m["field_name"]
            disp = m["display_name"]
            fmt = "currency" if m["semantic_class"] in ("Currency", "Revenue", "Cost", "Profit", "Target", "Actual") else ("duration" if m["semantic_class"] == "Duration" else "number")
            imp = 0.94 if m["semantic_class"] in ("Revenue", "Profit", "Cost", "Actual") else (0.85 if m["semantic_class"] in ("Quantity", "Currency", "Count Measure") else 0.74)
            dom_fit = 0.90 if m["semantic_class"] in ("Revenue", "Profit", "Cost", "Quantity", "Target", "Actual") else 0.72
            lims = []
            if m.get("needs_user_confirmation"):
                lims.append("اطمینان طبقه‌بندی معنایی این فیلد متوسط است؛ لطفاً نوع سنجه را بررسی کنید.")
            if m["stats"].get("null_pct", 0) >= 10:
                lims.append(f"{m['stats']['null_pct']}% مقادیر این فیلد خالی (NULL) است.")

            # Duration defaults to AVG/MEDIAN/MAX before SUM
            if m["semantic_class"] == "Duration":
                _add_cand(
                    kpi_id=f"avg_{fn}",
                    name=f"میانگین {disp}",
                    category="temporal",
                    formula=f'AVG("{fn}")',
                    agg="AVG",
                    field=fn,
                    format_type="duration",
                    direction="lower_is_better",
                    importance=0.88,
                    domain_fit=0.88,
                    confidence=m["confidence"],
                    limitations=lims,
                    target_val=eff_targets.get(fn),
                    needs_confirm=m["needs_user_confirmation"],
                )
                _add_cand(
                    kpi_id=f"p90_{fn}",
                    name=f"صدک ۹۰ {disp}",
                    category="temporal",
                    formula=f'QUANTILE_CONT("{fn}", 0.90)',
                    agg="P90",
                    field=fn,
                    format_type="duration",
                    direction="lower_is_better",
                    importance=0.78,
                    domain_fit=0.80,
                    confidence=m["confidence"],
                    limitations=lims,
                    required_fields=[fn],
                )
            else:
                _add_cand(
                    kpi_id=f"sum_{fn}",
                    name=f"مجموع {disp}",
                    category="aggregate",
                    formula=f'SUM("{fn}")',
                    agg="SUM",
                    field=fn,
                    format_type=fmt,
                    direction="lower_is_better" if m["semantic_class"] == "Cost" else "higher_is_better",
                    importance=imp,
                    domain_fit=dom_fit,
                    confidence=m["confidence"],
                    limitations=lims,
                    target_val=eff_targets.get(fn),
                    needs_confirm=m["needs_user_confirmation"],
                )
                if m["stats"].get("distinct_count", 0) > 1 and m["semantic_class"] != "Target":
                    _add_cand(
                        kpi_id=f"avg_{fn}",
                        name=f"میانگین {disp}",
                        category="aggregate",
                        formula=f'AVG("{fn}")',
                        agg="AVG",
                        field=fn,
                        format_type=fmt,
                        direction="lower_is_better" if m["semantic_class"] == "Cost" else "higher_is_better",
                        importance=round(imp - 0.14, 2),
                        domain_fit=dom_fit,
                        confidence=m["confidence"],
                        limitations=lims,
                    )

        # --- 4.3 Semi-Additive (Balance / Stock) -> LAST_VALUE / AVG (NO SUM) ---
        for m in measures_semi:
            fn = m["field_name"]
            disp = m["display_name"]
            t_ref = primary_time["field_name"] if primary_time else None
            formula_lv = f'ARG_MAX("{fn}", "{t_ref}")' if t_ref else f'AVG("{fn}")'
            _add_cand(
                kpi_id=f"latest_{fn}",
                name=f"آخرین {disp}" if t_ref else f"میانگین {disp}",
                category="aggregate",
                formula=formula_lv,
                agg="LAST_VALUE" if t_ref else "AVG",
                field=fn,
                format_type="currency" if "balance" in fn.lower() or "مانده" in disp else "number",
                importance=0.86,
                domain_fit=0.88,
                confidence=m["confidence"],
                required_fields=[fn] + ([t_ref] if t_ref else []),
                limitations=["سنجه نیمه‌جمع‌پذیر (مانده/موجودی لحظه‌ای) در طول زمان جمع نمی‌شود؛ آخرین مقدار یا میانگین محاسبه شد."],
                visual_spec_extra={"time_field": t_ref} if t_ref else {},
            )
            _add_cand(
                kpi_id=f"blocked_sum_{fn}",
                name=f"مجموع {disp}",
                category="aggregate",
                formula=f'SUM("{fn}")',
                agg="SUM",
                field=fn,
                importance=0.0,
                domain_fit=0.0,
                confidence=0.98,
                required_fields=[fn],
                rejection_reason=f"سیاست تجمیع: «{disp}» از نوع نیمه‌جمع‌پذیر ({m['semantic_class_fa']}) است و جمع زدن آن در طول زمان باعث دوباره‌شماری می‌شود.",
            )

        # --- 4.4 Non-Additive (Percentage, Ratio, Unit Price) -> AVG / MEDIAN (NO SUM) ---
        for m in measures_non_add:
            fn = m["field_name"]
            disp = m["display_name"]
            fmt = "percentage" if m["semantic_class"] == "Percentage" else ("currency" if m["semantic_class"] == "Unit Price" else "number")
            _add_cand(
                kpi_id=f"avg_{fn}",
                name=f"میانگین {disp}",
                category="aggregate",
                formula=f'AVG("{fn}")',
                agg="AVG",
                field=fn,
                format_type=fmt,
                importance=0.82,
                domain_fit=0.82,
                confidence=m["confidence"],
                limitations=["سنجه غیرجمع‌پذیر است؛ به جای SUM از میانگین استفاده شده است."],
            )
            _add_cand(
                kpi_id=f"blocked_sum_{fn}",
                name=f"مجموع {disp}",
                category="aggregate",
                formula=f'SUM("{fn}")',
                agg="SUM",
                field=fn,
                importance=0.0,
                domain_fit=0.0,
                confidence=0.99,
                required_fields=[fn],
                rejection_reason=f"سیاست تجمیع: جمع (SUM) روی «{disp}» ({m['semantic_class_fa']}) از نظر ریاضی نامعتبر است.",
            )

        # --- 4.5 Meaningful Multi-Field Ratios & Compound Business KPIs ---
        if rev_f and cost_f and rev_f["field_name"] != cost_f["field_name"]:
            r_fn, c_fn = rev_f["field_name"], cost_f["field_name"]
            if not profit_f:
                _add_cand(
                    kpi_id="computed_net_profit",
                    name=f"سود خالص ({rev_f['display_name']} - {cost_f['display_name']})",
                    category="compound",
                    formula=f'COALESCE(SUM("{r_fn}"), 0) - COALESCE(SUM("{c_fn}"), 0)',
                    format_type="currency",
                    importance=0.96,
                    domain_fit=0.95,
                    confidence=min(rev_f["confidence"], cost_f["confidence"]),
                    required_fields=[r_fn, c_fn],
                )
            _add_cand(
                kpi_id="profit_margin_pct",
                name="حاشیه سود (%)",
                category="ratio",
                formula=(
                    f'SUM("{profit_f["field_name"]}") * 100.0 / NULLIF(SUM("{r_fn}"), 0)'
                    if profit_f
                    else f'(SUM("{r_fn}") - SUM("{c_fn}")) * 100.0 / NULLIF(SUM("{r_fn}"), 0)'
                ),
                format_type="percentage",
                unit="%",
                importance=0.95,
                domain_fit=0.94,
                confidence=0.93,
                required_fields=[r_fn, profit_f["field_name"] if profit_f else c_fn],
                limitations=["در صورت صفر بودن فروش/درآمد، مخرج با NULLIF محافظت می‌شود."],
            )
            _add_cand(
                kpi_id="cost_to_revenue_pct",
                name="نسبت هزینه به درآمد (%)",
                category="ratio",
                formula=f'SUM("{c_fn}") * 100.0 / NULLIF(SUM("{r_fn}"), 0)',
                format_type="percentage",
                unit="%",
                direction="lower_is_better",
                importance=0.88,
                domain_fit=0.90,
                confidence=0.92,
                required_fields=[c_fn, r_fn],
            )
        elif rev_f and profit_f and rev_f["field_name"] != profit_f["field_name"]:
            r_fn, p_fn = rev_f["field_name"], profit_f["field_name"]
            _add_cand(
                kpi_id="profit_margin_pct",
                name="حاشیه سود (%)",
                category="ratio",
                formula=f'SUM("{p_fn}") * 100.0 / NULLIF(SUM("{r_fn}"), 0)',
                format_type="percentage",
                unit="%",
                importance=0.95,
                domain_fit=0.95,
                confidence=0.94,
                required_fields=[r_fn, p_fn],
            )

        if rev_f and disc_f:
            _add_cand(
                kpi_id="net_revenue_after_disc",
                name="فروش خالص (پس از تخفیف/مرجوعی)",
                category="compound",
                formula=f'COALESCE(SUM("{rev_f["field_name"]}"), 0) - COALESCE(SUM("{disc_f["field_name"]}"), 0)',
                format_type="currency",
                importance=0.90,
                domain_fit=0.92,
                confidence=0.91,
                required_fields=[rev_f["field_name"], disc_f["field_name"]],
            )

        if rev_f and order_f:
            _add_cand(
                kpi_id="avg_order_value",
                name="متوسط ارزش هر سفارش (AOV)",
                category="ratio",
                formula=f'SUM("{rev_f["field_name"]}") / NULLIF(COUNT(DISTINCT "{order_f["field_name"]}"), 0)',
                format_type="currency",
                importance=0.90,
                domain_fit=0.92,
                confidence=0.92,
                required_fields=[rev_f["field_name"], order_f["field_name"]],
            )
        elif rev_f and cust_f:
            _add_cand(
                kpi_id="revenue_per_customer",
                name="میانگین درآمد به ازای هر مشتری",
                category="ratio",
                formula=f'SUM("{rev_f["field_name"]}") / NULLIF(COUNT(DISTINCT "{cust_f["field_name"]}"), 0)',
                format_type="currency",
                importance=0.87,
                domain_fit=0.89,
                confidence=0.90,
                required_fields=[rev_f["field_name"], cust_f["field_name"]],
            )

        # Target vs Actual comparisons ONLY when real target column exists
        if actual_f and target_f and actual_f["field_name"] != target_f["field_name"]:
            a_fn, t_fn = actual_f["field_name"], target_f["field_name"]
            _add_cand(
                kpi_id="target_achievement_pct",
                name=f"درصد تحقق {target_f['display_name']}",
                category="comparison",
                formula=f'SUM("{a_fn}") * 100.0 / NULLIF(SUM("{t_fn}"), 0)',
                format_type="percentage",
                unit="%",
                importance=0.95,
                domain_fit=0.95,
                confidence=0.95,
                required_fields=[a_fn, t_fn],
                comparison_basis=f"مقایسه {actual_f['display_name']} با {target_f['display_name']}",
            )
            _add_cand(
                kpi_id="target_variance",
                name=f"انحراف از {target_f['display_name']}",
                category="comparison",
                formula=f'COALESCE(SUM("{a_fn}"), 0) - COALESCE(SUM("{t_fn}"), 0)',
                format_type="currency" if actual_f["semantic_class"] in ("Currency", "Revenue", "Actual") else "number",
                importance=0.89,
                domain_fit=0.92,
                confidence=0.95,
                required_fields=[a_fn, t_fn],
                comparison_basis=f"اختلاف {actual_f['display_name']} منهای {target_f['display_name']}",
            )

        # Observed Status / Boolean Rate KPIs (STRICTLY from observed top_values, never guessed labels)
        pos_status_tokens = {"completed", "done", "closed", "resolved", "success", "ok", "on_time", "approved", "active", "converted", "تکمیل", "موفق", "تایید", "انجام‌شده", "به‌موقع", "true", "1", "yes", "بله"}
        neg_status_tokens = {"failed", "fail", "error", "delayed", "overdue", "rejected", "cancelled", "canceled", "churned", "attrited", "تأخیر", "تاخیر", "ناموفق", "لغو", "خطا", "رد شده", "خراب"}
        for sf in status_fields[:2]:
            s_fn = sf["field_name"]
            obs_vals = [str(tv.get("value", "")) for tv in (sf.get("stats", {}).get("top_values") or [])]
            pos_matches = [v for v in obs_vals if v.strip().lower() in pos_status_tokens]
            neg_matches = [v for v in obs_vals if v.strip().lower() in neg_status_tokens]
            if pos_matches:
                quoted_vals = ", ".join("'" + v.replace("'", "''") + "'" for v in pos_matches[:4])
                _add_cand(
                    kpi_id=f"rate_pos_{s_fn}",
                    name=f"نرخ موفقیت / تکمیل ({sf['display_name']})",
                    category="ratio",
                    formula=f'SUM(CASE WHEN CAST("{s_fn}" AS VARCHAR) IN ({quoted_vals}) THEN 1.0 ELSE 0.0 END) * 100.0 / NULLIF(COUNT(*), 0)',
                    format_type="percentage",
                    unit="%",
                    importance=0.86,
                    domain_fit=0.88,
                    confidence=0.91,
                    required_fields=[s_fn],
                )
            if neg_matches:
                quoted_vals = ", ".join("'" + v.replace("'", "''") + "'" for v in neg_matches[:4])
                _add_cand(
                    kpi_id=f"rate_neg_{s_fn}",
                    name=f"نرخ خطا / تأخیر / لغو ({sf['display_name']})",
                    category="ratio",
                    formula=f'SUM(CASE WHEN CAST("{s_fn}" AS VARCHAR) IN ({quoted_vals}) THEN 1.0 ELSE 0.0 END) * 100.0 / NULLIF(COUNT(*), 0)',
                    format_type="percentage",
                    unit="%",
                    direction="lower_is_better",
                    importance=0.85,
                    domain_fit=0.88,
                    confidence=0.91,
                    required_fields=[s_fn],
                )

        # --- 4.6 Conditional Domain-Specific KPIs (Strict Field Evidence) ---
        if primary_domain == "energy_power":
            gen_f = _find(lambda c: any(k in c["field_name"].lower() for k in ("generated", "generation", "supply", "تولید")))
            cons_f = _find(lambda c: any(k in c["field_name"].lower() for k in ("consumed", "consumption", "delivered", "billed", "مصرف")))
            out_dur_f = _find(lambda c: any(k in c["field_name"].lower() for k in ("outage_minutes", "outage_hours", "interruption_duration", "outage_dur", "مدت خاموشی")))
            out_cnt_f = _find(lambda c: any(k in c["field_name"].lower() for k in ("outage_count", "interruptions", "fault_count", "تعداد خاموشی")))
            cust_srv_f = _find(lambda c: any(k in c["field_name"].lower() for k in ("customers_served", "total_customers", "subscribers", "تعداد مشترک")))
            cust_aff_f = _find(lambda c: any(k in c["field_name"].lower() for k in ("affected_customers", "customers_affected", "مشترکین متاثر")))

            if gen_f and cons_f and gen_f["field_name"] != cons_f["field_name"]:
                _add_cand(
                    kpi_id="energy_loss_pct",
                    name="نسبت تلفات انرژی (%)",
                    category="domain",
                    formula=f'(SUM("{gen_f["field_name"]}") - SUM("{cons_f["field_name"]}")) * 100.0 / NULLIF(SUM("{gen_f["field_name"]}"), 0)',
                    format_type="percentage",
                    unit="%",
                    direction="lower_is_better",
                    importance=0.93,
                    domain_fit=0.96,
                    confidence=0.93,
                    required_fields=[gen_f["field_name"], cons_f["field_name"]],
                )
            # SAIDI / SAIFI / CAIDI ONLY if real outage & customer columns exist
            if out_dur_f and cust_srv_f:
                _add_cand(
                    kpi_id="energy_saidi",
                    name="شاخص SAIDI (متوسط مدت خاموشی هر مشترک)",
                    category="domain",
                    formula=(
                        f'SUM("{out_dur_f["field_name"]}" * "{cust_aff_f["field_name"]}") / NULLIF(SUM("{cust_srv_f["field_name"]}"), 0)'
                        if cust_aff_f
                        else f'SUM("{out_dur_f["field_name"]}") / NULLIF(SUM("{cust_srv_f["field_name"]}"), 0)'
                    ),
                    format_type="duration",
                    unit="دقیقه/ساعت",
                    direction="lower_is_better",
                    importance=0.95,
                    domain_fit=0.98,
                    confidence=0.94,
                    required_fields=[out_dur_f["field_name"], cust_srv_f["field_name"]] + ([cust_aff_f["field_name"]] if cust_aff_f else []),
                )
            if (out_cnt_f or cust_aff_f) and cust_srv_f:
                num_col = cust_aff_f["field_name"] if cust_aff_f else out_cnt_f["field_name"]
                _add_cand(
                    kpi_id="energy_saifi",
                    name="شاخص SAIFI (متوسط دفعات خاموشی هر مشترک)",
                    category="domain",
                    formula=f'SUM("{num_col}") / NULLIF(SUM("{cust_srv_f["field_name"]}"), 0)',
                    format_type="number",
                    unit="بار",
                    direction="lower_is_better",
                    importance=0.94,
                    domain_fit=0.98,
                    confidence=0.94,
                    required_fields=[num_col, cust_srv_f["field_name"]],
                )
            if out_dur_f and (out_cnt_f or cust_aff_f):
                den_col = cust_aff_f["field_name"] if cust_aff_f else out_cnt_f["field_name"]
                _add_cand(
                    kpi_id="energy_caidi",
                    name="شاخص CAIDI (متوسط زمان بازیابی هر خاموشی)",
                    category="domain",
                    formula=(
                        f'SUM("{out_dur_f["field_name"]}" * "{cust_aff_f["field_name"]}") / NULLIF(SUM("{den_col}"), 0)'
                        if cust_aff_f
                        else f'SUM("{out_dur_f["field_name"]}") / NULLIF(SUM("{den_col}"), 0)'
                    ),
                    format_type="duration",
                    direction="lower_is_better",
                    importance=0.90,
                    domain_fit=0.96,
                    confidence=0.92,
                    required_fields=[out_dur_f["field_name"], den_col],
                )

        if primary_domain == "project_contracting":
            ev_f = _find(lambda c: c["field_name"].lower() in ("ev", "earned_value"))
            pv_f = _find(lambda c: c["field_name"].lower() in ("pv", "planned_value"))
            ac_f = _find(lambda c: c["field_name"].lower() in ("ac", "actual_cost"))
            if ev_f and ac_f:
                _add_cand(
                    kpi_id="project_cpi",
                    name="شاخص عملکرد هزینه پروژه (CPI)",
                    category="domain",
                    formula=f'SUM("{ev_f["field_name"]}") / NULLIF(SUM("{ac_f["field_name"]}"), 0)',
                    importance=0.94, domain_fit=0.97, confidence=0.95, required_fields=[ev_f["field_name"], ac_f["field_name"]],
                )
            if ev_f and pv_f:
                _add_cand(
                    kpi_id="project_spi",
                    name="شاخص عملکرد زمان‌بندی پروژه (SPI)",
                    category="domain",
                    formula=f'SUM("{ev_f["field_name"]}") / NULLIF(SUM("{pv_f["field_name"]}"), 0)',
                    importance=0.94, domain_fit=0.97, confidence=0.95, required_fields=[ev_f["field_name"], pv_f["field_name"]],
                )

        if primary_domain == "bpmn_process" and duration_fields:
            sla_f = _find(lambda c: "sla" in c["field_name"].lower())
            w_f = duration_fields[0]
            if sla_f:
                _add_cand(
                    kpi_id="bpmn_sla_breach_pct",
                    name="نرخ نقض سقف مجاز SLA (%)",
                    category="domain",
                    formula=f'SUM(CASE WHEN "{w_f["field_name"]}" > "{sla_f["field_name"]}" THEN 1.0 ELSE 0.0 END) * 100.0 / NULLIF(COUNT(*), 0)',
                    format_type="percentage",
                    unit="%",
                    direction="lower_is_better",
                    importance=0.94,
                    domain_fit=0.97,
                    confidence=0.94,
                    required_fields=[w_f["field_name"], sla_f["field_name"]],
                )

        if primary_domain == "security":
            act_f = _find(lambda c: any(k in c["field_name"].lower() for k in ("action", "result", "outcome", "اقدام")))
            sev_f = _find(lambda c: any(k in c["field_name"].lower() for k in ("severity", "risk_level", "level", "شدت")))
            src_f = _find(lambda c: any(k in c["field_name"].lower() for k in ("src_ip", "source_ip", "ip_address")))
            host_f = _find(lambda c: any(k in c["field_name"].lower() for k in ("hostname", "host", "device")))
            if act_f:
                afn = act_f["field_name"]
                _add_cand(
                    kpi_id="sec_blocked_quarantined_pct",
                    name="نرخ رویدادهای مسدود / قرنطینه‌شده (%)",
                    category="domain",
                    formula=f'SUM(CASE WHEN LOWER(CAST("{afn}" AS VARCHAR)) IN (\'block\', \'blocked\', \'quarantine\', \'quarantined\', \'deny\', \'drop\') THEN 1.0 ELSE 0.0 END) * 100.0 / NULLIF(COUNT("{afn}"), 0)',
                    format_type="percentage",
                    unit="%",
                    direction="higher_is_better",
                    importance=0.95,
                    domain_fit=0.98,
                    confidence=0.92,
                    required_fields=[afn],
                    limitations=["Detect، Block، Quarantine و Failed به‌صورت مجزا سنجیده می‌شوند؛ نبود رویداد دلیل سلامت نیست."],
                )
                _add_cand(
                    kpi_id="sec_failed_action_pct",
                    name="نرخ رویدادهای ناموفق / Failed (%)",
                    category="domain",
                    formula=f'SUM(CASE WHEN LOWER(CAST("{afn}" AS VARCHAR)) IN (\'failed\', \'fail\', \'error\') THEN 1.0 ELSE 0.0 END) * 100.0 / NULLIF(COUNT("{afn}"), 0)',
                    format_type="percentage",
                    unit="%",
                    direction="lower_is_better",
                    importance=0.91,
                    domain_fit=0.95,
                    confidence=0.90,
                    required_fields=[afn],
                )
            if sev_f:
                sfn_c = sev_f["field_name"]
                _add_cand(
                    kpi_id="sec_high_critical_pct",
                    name="سهم رویدادهای بحرانی و بالا (%)",
                    category="domain",
                    formula=f'SUM(CASE WHEN LOWER(CAST("{sfn_c}" AS VARCHAR)) IN (\'critical\', \'high\', \'alert\', \'emerg\') THEN 1.0 ELSE 0.0 END) * 100.0 / NULLIF(COUNT("{sfn_c}"), 0)',
                    format_type="percentage",
                    unit="%",
                    direction="lower_is_better",
                    importance=0.96,
                    domain_fit=0.98,
                    confidence=0.93,
                    required_fields=[sfn_c],
                )
            if src_f:
                _add_cand(
                    kpi_id="sec_unique_src_ip",
                    name="تعداد IP مبدأ یکتا",
                    category="domain",
                    formula=f'COUNT(DISTINCT "{src_f["field_name"]}")',
                    format_type="count",
                    unit="IP",
                    importance=0.88,
                    domain_fit=0.92,
                    confidence=0.95,
                    required_fields=[src_f["field_name"]],
                )
            if host_f:
                _add_cand(
                    kpi_id="sec_unique_hosts",
                    name="تعداد میزبان / دستگاه یکتا",
                    category="domain",
                    formula=f'COUNT(DISTINCT "{host_f["field_name"]}")',
                    format_type="count",
                    unit="میزبان",
                    importance=0.86,
                    domain_fit=0.92,
                    confidence=0.95,
                    required_fields=[host_f["field_name"]],
                )

        if primary_domain == "network":
            bytes_f = _find(lambda c: any(k in c["field_name"].lower() for k in ("bytes", "traffic", "response_bytes")))
            lat_f = _find(lambda c: any(k in c["field_name"].lower() for k in ("latency", "rtt", "jitter")))
            if bytes_f and bytes_f["data_type"] == "numeric":
                _add_cand(
                    kpi_id="net_total_bytes",
                    name="مجموع حجم ترافیک انتقالی",
                    category="domain",
                    formula=f'SUM("{bytes_f["field_name"]}")',
                    format_type="number",
                    unit="بایت",
                    importance=0.92,
                    domain_fit=0.95,
                    confidence=0.94,
                    required_fields=[bytes_f["field_name"]],
                )
            if lat_f and lat_f["data_type"] == "numeric":
                _add_cand(
                    kpi_id="net_avg_latency",
                    name="میانگین تأخیر شبکه",
                    category="domain",
                    formula=f'AVG("{lat_f["field_name"]}")',
                    format_type="duration",
                    unit="ms",
                    direction="lower_is_better",
                    importance=0.94,
                    domain_fit=0.96,
                    confidence=0.93,
                    required_fields=[lat_f["field_name"]],
                )

        if primary_domain == "manufacturing_operations":
            def_f = _find(lambda c: any(k in c["field_name"].lower() for k in ("defect", "scrap", "reject", "ضایعات", "خراب")))
            out_f = _find(lambda c: any(k in c["field_name"].lower() for k in ("output", "produced", "total_units", "تولید"))) or qty_f
            if def_f and out_f and def_f["field_name"] != out_f["field_name"]:
                _add_cand(
                    kpi_id="mfg_defect_rate_pct",
                    name="نرخ ضایعات / خرابی تولید (%)",
                    category="domain",
                    formula=f'SUM("{def_f["field_name"]}") * 100.0 / NULLIF(SUM("{out_f["field_name"]}"), 0)',
                    format_type="percentage", unit="%", direction="lower_is_better",
                    importance=0.93, domain_fit=0.96, confidence=0.93, required_fields=[def_f["field_name"], out_f["field_name"]],
                )

        # --- 4.7 Statistical KPIs (ONLY when row_count >= 10 and valid numeric variance) ---
        stat_target = actual_f or (measures_additive[0] if measures_additive else (duration_fields[0] if duration_fields else None))
        if stat_target and row_count >= 10:
            sfn = stat_target["field_name"]
            sdisp = stat_target["display_name"]
            sd = stat_target["stats"].get("stddev")
            if sd is not None and sd > 0:
                _add_cand(
                    kpi_id=f"median_{sfn}",
                    name=f"میانه {sdisp}",
                    category="statistical",
                    formula=f'MEDIAN("{sfn}")',
                    agg="MEDIAN",
                    field=sfn,
                    importance=0.72,
                    domain_fit=0.70,
                    confidence=0.95,
                    required_fields=[sfn],
                )
                _add_cand(
                    kpi_id=f"cv_pct_{sfn}",
                    name=f"ضریب پراکندگی {sdisp} (CV%)",
                    category="statistical",
                    formula=f'STDDEV_SAMP("{sfn}") * 100.0 / NULLIF(ABS(AVG("{sfn}")), 0)',
                    format_type="percentage",
                    unit="%",
                    direction="neutral",
                    importance=0.68,
                    domain_fit=0.65,
                    confidence=0.92,
                    required_fields=[sfn],
                )
                _add_cand(
                    kpi_id=f"iqr_{sfn}",
                    name=f"دامنه میان‌چارکی {sdisp} (IQR)",
                    category="statistical",
                    formula=f'QUANTILE_CONT("{sfn}", 0.75) - QUANTILE_CONT("{sfn}", 0.25)',
                    importance=0.62,
                    domain_fit=0.60,
                    confidence=0.94,
                    required_fields=[sfn],
                )
                if len(measures_additive) >= 2:
                    m2 = next((x for x in measures_additive if x["field_name"] != sfn), None)
                    if m2 and (m2["stats"].get("stddev") or 0) > 0:
                        _add_cand(
                            kpi_id=f"corr_{sfn}_{m2['field_name']}",
                            name=f"همبستگی {sdisp} و {m2['display_name']}",
                            category="statistical",
                            formula=f'CORR("{sfn}", "{m2["field_name"]}")',
                            direction="neutral",
                            importance=0.74,
                            domain_fit=0.72,
                            confidence=0.90,
                            required_fields=[sfn, m2["field_name"]],
                            limitations=["همبستگی آماری لزوماً به معنای رابطه علیت نیست."],
                        )

        # --- 4.8 Data Quality KPIs (STRICTLY separated category="data_quality") ---
        vis_col_list = [c["field_name"] for c in classifications if not c.get("is_calculated")][:20]
        if vis_col_list:
            cnt_sum_expr = " + ".join(f'COUNT("{cn}")' for cn in vis_col_list)
            comp_formula = f'(({cnt_sum_expr}) * 100.0) / NULLIF(COUNT(*) * {len(vis_col_list)}, 0)'
            null_cells_formula = f'(COUNT(*) * {len(vis_col_list)}) - ({cnt_sum_expr})'
        else:
            comp_formula = "100.0"
            null_cells_formula = "0"

        _add_cand(
            kpi_id="dq_completeness_pct",
            name="شاخص تکمیل بودن داده‌ها (%)",
            category="data_quality",
            formula=comp_formula,
            importance=0.75,
            domain_fit=0.70,
            confidence=0.99,
            format_type="percentage",
            unit="%",
            is_data_quality=True,
            required_fields=vis_col_list,
        )
        if deep_profile["total_null_cells"] > 0:
            _add_cand(
                kpi_id="dq_null_cells",
                name="تعداد سلول‌های خالی (NULL)",
                category="data_quality",
                formula=null_cells_formula,
                direction="lower_is_better",
                importance=0.70,
                domain_fit=0.65,
                confidence=0.98,
                is_data_quality=True,
                required_fields=vis_col_list,
            )
        if deep_profile["duplicate_row_count"] > 0 and vis_col_list:
            key_cols_dq = [f'COALESCE(CAST("{cn}" AS VARCHAR), \'__NULL__\')' for cn in vis_col_list[:6]]
            dup_formula = f"COUNT(*) - COUNT(DISTINCT CONCAT_WS('|', {', '.join(key_cols_dq)}))"
            _add_cand(
                kpi_id="dq_duplicate_rows",
                name="تعداد ردیف‌های تکراری",
                category="data_quality",
                formula=dup_formula,
                direction="lower_is_better",
                importance=0.72,
                domain_fit=0.65,
                confidence=0.95,
                is_data_quality=True,
                required_fields=vis_col_list[:6],
            )
        if deep_profile["total_outliers"] > 0:
            out_conds: list[str] = []
            for c in classifications:
                st = c.get("stats") or {}
                p25, p75 = _safe_float(st.get("p25")), _safe_float(st.get("p75"))
                if c["data_type"] == "numeric" and p25 is not None and p75 is not None and (p75 - p25) > 0:
                    iqr_d = p75 - p25
                    out_conds.append(f'("{c["field_name"]}" < {round(p25 - 1.5 * iqr_d, 4)} OR "{c["field_name"]}" > {round(p75 + 1.5 * iqr_d, 4)})')
            if out_conds:
                out_formula = f'SUM(CASE WHEN {" OR ".join(out_conds[:6])} THEN 1 ELSE 0 END)'
                _add_cand(
                    kpi_id="dq_outlier_count",
                    name="تعداد رکوردهای دارای مقدار پرت (Outliers)",
                    category="data_quality",
                    formula=out_formula,
                    direction="lower_is_better",
                    importance=0.71,
                    domain_fit=0.65,
                    confidence=0.92,
                    is_data_quality=True,
                    required_fields=[c["field_name"] for c in classifications if c["data_type"] == "numeric"][:6],
                )

        # --- 4.9 Multi-Source Cross-Dataset KPIs (if relationships exist & safe) ---
        multi_source_info: dict[str, Any] = {"available": False, "joined_datasets": [], "blocked_reasons": []}
        rel_objs = (
            db.query(DatasetRelationship)
            .filter(
                (DatasetRelationship.left_dataset_id == ds.id) | (DatasetRelationship.right_dataset_id == ds.id),
                DatasetRelationship.is_active == True,
            )
            .all()
        )
        partner_ids = list(related_dataset_ids or [])
        for r in rel_objs:
            pid = r.right_dataset_id if r.left_dataset_id == ds.id else r.left_dataset_id
            if pid not in partner_ids:
                partner_ids.append(pid)

        if partner_ids and user is not None:
            try:
                rel_plan = analyze_dataset_relationships(
                    db, user, [ds.id, *partner_ids], primary_dataset_id=ds.id
                )
                for br in rel_plan.get("blocked_relationships", []):
                    multi_source_info["blocked_reasons"].append(br.get("reason"))
                if rel_plan.get("ready_for_join") and rel_plan.get("resolved_joins"):
                    multi_source_info["available"] = True
                    multi_source_info["resolved_joins"] = rel_plan["resolved_joins"]
                    joined_names = [n["name"] for n in rel_plan.get("graph_nodes", [])]
                    multi_source_info["joined_datasets"] = joined_names
                    # Look for safe additive/target columns on joined tables without double counting
                    for j in rel_plan["resolved_joins"]:
                        jds = db.query(Dataset).filter(Dataset.id == j["target_dataset_id"]).first()
                        if not jds:
                            continue
                        jsc = build_data_security_context(db, user, jds.id)
                        j_fields = [
                            jf for jf in db.query(DatasetField).filter(DatasetField.dataset_id == jds.id).all()
                            if jf.field_name not in jsc.hidden_columns
                        ]
                        j_target = next(
                            (jf for jf in j_fields if any(k in jf.field_name.lower() for k in ("target", "budget", "quota", "هدف", "بودجه"))),
                            None,
                        )
                        if j_target and actual_f and j.get("cardinality") in ("many_to_one", "one_to_one"):
                            # Safe only via DISTINCT key or single parent target per group
                            a_fn = actual_f["field_name"]
                            t_fn = j_target.field_name
                            if t_fn not in class_map:
                                _add_cand(
                                    kpi_id=f"multi_target_gap_{t_fn}",
                                    name=f"مقایسه عملکرد با {j_target.display_name} ({jds.name})",
                                    category="comparison",
                                    formula=f'SUM("{a_fn}") * 100.0 / NULLIF(AVG("{t_fn}") * COUNT(DISTINCT "{j["left_field"]}"), 0)',
                                    format_type="percentage",
                                    unit="%",
                                    importance=0.88,
                                    domain_fit=0.90,
                                    confidence=0.85,
                                    required_fields=[a_fn, j["left_field"]],
                                    source_datasets=[ds.name, jds.name],
                                    joins=[j],
                                    limitations=["محاسبه چندمنبعی با محافظت از Fan-out/تکرار رکورد."],
                                )
                        # Cross-dataset group dimension count
                        j_dim = next(
                            (jf for jf in j_fields if jf.semantic_role == "dimension" and jf.field_name not in class_map),
                            None,
                        )
                        if j_dim and actual_f:
                            multi_source_info["recommended_join_dimension"] = {
                                "field_name": j_dim.field_name,
                                "display_name": j_dim.display_name,
                                "dataset_name": jds.name,
                                "joins": [j],
                            }
            except Exception as exc:
                multi_source_info["blocked_reasons"].append(str(exc))

        # --- 4.10 AST Validation, CLS Check & Live Batch Probe Execution ---
        visible_col_set = set(class_map.keys())
        sec_ctx = build_data_security_context(db, user, ds.id) if user is not None else DataSecurityContext("system", [], [], set(), {}, "sys")
        cols_meta = warehouse.describe_table(ds.warehouse_table)
        sub_sql, sub_params, _ = build_security_projection_and_where(ds.warehouse_table, cols_meta, sec_ctx)

        valid_to_eval: list[dict[str, Any]] = []
        rejected_kpis: list[dict[str, Any]] = []
        seen_signatures: set[str] = set()

        for cand in candidates:
            if cand.get("rejection_reason_fa") or cand.get("rejection_reason"):
                cand["rejection_reason_fa"] = cand.get("rejection_reason_fa") or cand.get("rejection_reason")
                cand["status"] = "uncomputable"
                rejected_kpis.append(cand)
                continue

            # Deduplicate normalized formulas
            sig = _normalize_formula_signature(cand["formula"])
            if sig in seen_signatures and not cand["is_data_quality"]:
                continue
            seen_signatures.add(sig)

            # Static data quality KPIs bypass SQL formula eval
            if "_static_value" in cand.get("visual_spec_extra", {}):
                sval = cand["visual_spec_extra"]["_static_value"]
                cand["value"] = sval
                cand["formatted_value"] = _format_kpi_value(sval, cand["format_type"], cand["unit"])
                cand["period"] = "کل بازه داده"
                cand["source"] = "پروفایل هوشمند کیفیت داده"
                cand["status"] = "suggested"
                cand["score"] = round(cand["importance"] * 60 + cand["confidence"] * 40, 1)
                valid_to_eval.append(cand)
                continue

            # Strict AST + CLS validation
            try:
                if cand.get("joins") and user is not None:
                    # Validate via build_visual_query_sql with join RBAC/CLS
                    build_visual_query_sql(
                        db, ds, {"joins": cand["joins"], "formula": cand["formula"], "limit": 1},
                        [], [], [], user=user, enforce_join_rbac=True
                    )
                    cand["compiled_sql"] = cand["formula"]
                elif user is not None:
                    res_val = validate_and_compile_formula(
                        db,
                        user,
                        dataset_ids=[ds.id],
                        formula=cand["formula"],
                        calculation_type="analytical_metric",
                        qualify_with_alias=False,
                        auto_safe_divide=False,
                    )
                    cand["compiled_sql"] = res_val["compiled_sql"]
                else:
                    validate_formula_ast(cand["formula"], allowed_columns=visible_col_set)
                    cand["compiled_sql"] = cand["formula"]
                valid_to_eval.append(cand)
            except Exception as exc:
                cand["status"] = "uncomputable"
                cand["rejection_reason_fa"] = str(exc)
                rejected_kpis.append(cand)

        # Execute batch DuckDB SQL probe for all non-joined scalar candidates in 1 query!
        single_ds_cands = [
            c for c in valid_to_eval
            if not c.get("joins") and "_static_value" not in c.get("visual_spec_extra", {})
        ]
        if single_ds_cands and row_count > 0:
            probe_exprs = [f'({c["compiled_sql"]}) AS "k_{idx}"' for idx, c in enumerate(single_ds_cands)]
            try:
                p_res = warehouse.execute_query(
                    f'SELECT {", ".join(probe_exprs)} FROM ({sub_sql}) AS _kpi_probe',
                    sub_params,
                    limit=1,
                )["rows"]
                p_row = p_res[0] if p_res else {}
                for idx, c in enumerate(single_ds_cands):
                    val = _safe_float(p_row.get(f"k_{idx}"))
                    c["value"] = round(val, 4) if val is not None else None
                    c["formatted_value"] = _format_kpi_value(c["value"], c["format_type"], c["unit"])
            except Exception:
                # Fallback individually if any expression hits a runtime cast edge case
                for c in single_ds_cands:
                    try:
                        r1 = warehouse.execute_query(
                            f'SELECT ({c["compiled_sql"]}) AS v FROM ({sub_sql}) AS _kpi_1',
                            sub_params,
                            limit=1,
                        )["rows"]
                        val = _safe_float(r1[0]["v"]) if r1 else None
                        c["value"] = round(val, 4) if val is not None else None
                        c["formatted_value"] = _format_kpi_value(c["value"], c["format_type"], c["unit"])
                    except Exception as err1:
                        c["status"] = "uncomputable"
                        c["rejection_reason_fa"] = f"خطای محاسبه در موتور تحلیل: {err1}"
                        rejected_kpis.append(c)

        # Evaluate joined candidates cleanly under full join security
        for c in [x for x in valid_to_eval if x.get("joins")]:
            try:
                j_sql, j_params, _ = build_visual_query_sql(
                    db, ds, {"joins": c["joins"], "formula": c["formula"], "limit": 1},
                    [], [], [], user=user, enforce_join_rbac=True
                )
                jr = warehouse.execute_query(j_sql, j_params, limit=1)["rows"]
                val = _safe_float(jr[0].get("value")) if jr else None
                c["value"] = round(val, 4) if val is not None else None
                c["formatted_value"] = _format_kpi_value(c["value"], c["format_type"], c["unit"])
            except Exception as err_j:
                c["status"] = "uncomputable"
                c["rejection_reason_fa"] = str(err_j)
                rejected_kpis.append(c)

        # --- 4.11 Growth & Period-over-Period KPIs (if time field has >= 2 periods) ---
        growth_kpis: list[dict[str, Any]] = []
        primary_grow_measure = actual_f or (measures_additive[0] if measures_additive else None)
        if primary_time and time_periods >= 2 and primary_grow_measure and row_count >= 2:
            tfn = primary_time["field_name"]
            mfn = primary_grow_measure["field_name"]
            trunc_unit = {"daily": "day", "weekly": "week", "monthly": "month", "yearly": "year"}.get(time_gran, "month")
            try:
                g_sql = (
                    f'SELECT CAST(DATE_TRUNC(\'{trunc_unit}\', TRY_CAST("{tfn}" AS DATE)) AS VARCHAR) AS p_key, '
                    f'COALESCE(SUM("{mfn}"), 0) AS p_val '
                    f'FROM ({sub_sql}) AS _g_src '
                    f'WHERE TRY_CAST("{tfn}" AS DATE) IS NOT NULL '
                    f'GROUP BY 1 ORDER BY 1 ASC LIMIT 120'
                )
                g_rows = warehouse.execute_query(g_sql, sub_params, limit=120)["rows"]
                if len(g_rows) >= 2:
                    last_p, last_v = str(g_rows[-1]["p_key"])[:10], float(g_rows[-1]["p_val"] or 0)
                    prev_p, prev_v = str(g_rows[-2]["p_key"])[:10], float(g_rows[-2]["p_val"] or 0)
                    delta = last_v - prev_v
                    lims_g: list[str] = []
                    if prev_v == 0:
                        pct_g = None
                        lims_g.append(f"مقدار دوره قبل ({prev_p}) صفر است؛ درصد رشد تعریف‌نشده است و صرفاً اختلاف مطلق محاسبه شد.")
                    elif (prev_v < 0 and last_v > 0) or (prev_v > 0 and last_v < 0):
                        pct_g = (delta / abs(prev_v)) * 100.0
                        lims_g.append(f"تغییر علامت بین دوره قبل ({_fmt_num(prev_v)}) و دوره جاری ({_fmt_num(last_v)}) رخ داده؛ نسبت به قدر مطلق دوره قبل محاسبه شد.")
                    elif prev_v < 0:
                        pct_g = (delta / abs(prev_v)) * 100.0
                        lims_g.append("مقدار پایه دوره قبل منفی است؛ درصد تغییر با مخرج قدر مطلق محاسبه شد.")
                    else:
                        pct_g = (delta / prev_v) * 100.0

                    # Attach comparison context to primary sum KPI too
                    for c in valid_to_eval:
                        if c.get("field") == mfn and c.get("aggregation") == "SUM":
                            c["previous_value"] = round(prev_v, 4)
                            c["latest_period_value"] = round(last_v, 4)
                            c["period"] = f"{str(g_rows[0]['p_key'])[:10]} تا {last_p}"
                            c["comparison_basis"] = f"آخرین دوره ({last_p}) در مقایسه با دوره قبل ({prev_p})"
                            c["sparkline_values"] = [round(float(r["p_val"] or 0), 2) for r in g_rows[-12:]]
                            if lims_g:
                                c["limitations"].extend(lims_g)

                    if pct_g is not None:
                        growth_kpis.append({
                            "id": f"growth_pct_{mfn}",
                            "slug": f"growth_pct_{mfn}",
                            "name": f"رشد دوره‌ای {primary_grow_measure['display_name']} (%)",
                            "category": "growth",
                            "formula": f'MOM("{mfn}")',
                            "compiled_sql": f'MOM("{mfn}")',
                            "aggregation": "MOM",
                            "field": mfn,
                            "time_field": tfn,
                            "granularity": time_gran,
                            "format_type": "percentage",
                            "unit": "%",
                            "direction": "higher_is_better",
                            "value": round(pct_g, 2),
                            "formatted_value": _format_kpi_value(round(pct_g, 2), "percentage", "%"),
                            "previous_value": round(prev_v, 2),
                            "latest_period_value": round(last_v, 2),
                            "period": f"{prev_p} → {last_p}",
                            "comparison_basis": f"تغییر از {prev_p} ({_fmt_num(prev_v)}) به {last_p} ({_fmt_num(last_v)})",
                            "source_datasets": [ds.name],
                            "required_fields": [mfn, tfn],
                            "importance": 0.90,
                            "domain_fit": 0.88,
                            "confidence": 0.93,
                            "limitations": lims_g,
                            "needs_user_confirmation": False,
                            "is_data_quality": False,
                            "sparkline_values": [round(float(r["p_val"] or 0), 2) for r in g_rows[-12:]],
                        })
                    else:
                        growth_kpis.append({
                            "id": f"growth_delta_{mfn}",
                            "slug": f"growth_delta_{mfn}",
                            "name": f"تغییر مطلق دوره‌ای {primary_grow_measure['display_name']}",
                            "category": "growth",
                            "formula": f'SUM("{mfn}")',
                            "compiled_sql": f'SUM("{mfn}")',
                            "aggregation": "SUM",
                            "field": mfn,
                            "time_field": tfn,
                            "granularity": time_gran,
                            "format_type": "number",
                            "unit": "",
                            "direction": "higher_is_better",
                            "value": round(delta, 2),
                            "formatted_value": _format_kpi_value(round(delta, 2), "number", ""),
                            "previous_value": round(prev_v, 2),
                            "latest_period_value": round(last_v, 2),
                            "period": f"{prev_p} → {last_p}",
                            "comparison_basis": f"اختلاف از {prev_p} به {last_p}",
                            "source_datasets": [ds.name],
                            "required_fields": [mfn, tfn],
                            "importance": 0.84,
                            "domain_fit": 0.84,
                            "confidence": 0.90,
                            "limitations": lims_g,
                            "needs_user_confirmation": False,
                            "is_data_quality": False,
                        })
            except Exception:
                pass

        # --- 4.12 Evaluate 7 Standard KPIs, Merge, Score, Deduplicate & Classify ---
        std_kpis, std_rel_plan = _evaluate_standard_kpis(
            db,
            ds,
            user,
            deep_profile,
            classifications,
            sub_sql,
            sub_params,
            partner_ids,
            eff_targets,
            calendar_mode=calendar_mode,
        )
        std_computable = [
            sk for sk in std_kpis
            if sk["computability_status"] in ("computable", "computable_with_warning") and sk.get("value") is not None
        ]
        std_needs_data = [sk for sk in std_kpis if sk["computability_status"] == "needs_data"]
        std_uncomputable = [sk for sk in std_kpis if sk["computability_status"] == "uncomputable"]

        # Deduplicate redundant auto-discovered KPIs against computable standard KPIs
        std_ids_computable = {sk["id"] for sk in std_computable}
        std_sales_field = next((sk.get("field") for sk in std_computable if sk["id"] == "std_total_sales"), None)
        std_cust_field = next((sk.get("field") for sk in std_computable if sk["id"] == "std_unique_customers"), None)
        superseded_ids: set[str] = set()
        if "std_total_sales" in std_ids_computable and std_sales_field:
            superseded_ids.add(f"sum_{std_sales_field}")
        if "std_profit_margin" in std_ids_computable:
            superseded_ids.add("profit_margin_pct")
        if "std_aov" in std_ids_computable:
            superseded_ids.add("avg_order_value")
        if "std_unique_customers" in std_ids_computable and std_cust_field:
            superseded_ids.add(f"distinct_{std_cust_field}")
        if "std_target_achievement" in std_ids_computable:
            superseded_ids.add("target_achievement_pct")

        std_sigs = {_normalize_formula_signature(sk["formula"]) for sk in std_computable}
        discovered_computable = [
            c for c in ([x for x in valid_to_eval if x.get("status") != "uncomputable" and x.get("value") is not None] + growth_kpis)
            if c.get("id") not in superseded_ids and (c.get("is_data_quality") or _normalize_formula_signature(c.get("formula", "")) not in std_sigs)
        ]

        # Transfer comparison sparkline/previous info onto std_total_sales when available
        if std_sales_field:
            for c in valid_to_eval:
                if c.get("id") == f"sum_{std_sales_field}" and c.get("sparkline_values"):
                    for sk in std_computable:
                        if sk["id"] == "std_total_sales":
                            sk.setdefault("sparkline_values", c.get("sparkline_values"))
                            if sk.get("previous_value") is None and c.get("previous_value") is not None:
                                sk["previous_value"] = c["previous_value"]
                                sk["comparison_basis"] = c.get("comparison_basis") or sk["comparison_basis"]

        all_computable = std_computable + discovered_computable
        for c in valid_to_eval:
            if c.get("status") != "uncomputable" and c.get("value") is None:
                c["status"] = "uncomputable"
                c["rejection_reason_fa"] = "مقدار خروجی به دلیل خالی بودن تمام مقادیر یا صفر بودن مخرج (NULLIF) قابل محاسبه نبود."
                rejected_kpis.append(c)

        rejected_kpis = std_uncomputable + rejected_kpis

        t_meta = list((deep_profile.get("temporal_summary") or {}).values())
        default_period = f"{t_meta[0]['min_date']} تا {t_meta[0]['max_date']}" if t_meta else f"کل {row_count:,} رکورد"
        computed_at = datetime.now(timezone.utc).isoformat()

        # Check SemanticMetric DB for favorite / registered flags
        existing_metrics = {
            sm.formula.strip().lower(): sm
            for sm in db.query(SemanticMetric).filter(
                SemanticMetric.organization_id == ds.organization_id,
                SemanticMetric.dataset_id == ds.id,
            ).all()
        }

        for idx_k, kpi in enumerate(all_computable):
            req_fields = kpi.get("required_fields") or []
            req_comp = (
                min(
                    ((100.0 - float(class_map[rf]["stats"].get("null_pct", 0.0))) / 100.0 for rf in req_fields if rf in class_map),
                    default=1.0,
                )
                if req_fields else 1.0
            )
            dq_score = deep_profile["completeness_pct"] / 100.0
            cat = kpi["category"]
            diversity_bonus = 0.08 if cat in ("ratio", "growth", "comparison", "domain", "compound", "standard") else (0.04 if cat == "temporal" else 0.0)
            if not kpi.get("is_standard"):
                raw_score = (
                    kpi.get("importance", 0.7) * 34.0
                    + kpi.get("domain_fit", 0.65) * 24.0
                    + dq_score * 12.0
                    + kpi.get("confidence", 0.85) * 14.0
                    + req_comp * 10.0
                    + diversity_bonus * 100.0
                )
                kpi["score"] = round(min(100.0, max(0.0, raw_score)), 1)
            kpi.setdefault("period", default_period)
            kpi.setdefault("source", " + ".join(kpi.get("source_datasets") or [ds.name]))
            kpi["domain_scope"] = f"{domain_info.get('domain_family', 'Business')} / {domain_info.get('primary_domain_fa', 'عمومی')}"
            kpi["dataset_names"] = list(kpi.get("source_datasets") or [ds.name])
            kpi["sample_count"] = row_count
            kpi["computed_at"] = computed_at
            if kpi.get("target_value") is None and kpi.get("field") in eff_targets:
                kpi["target_value"] = eff_targets[kpi["field"]]

            sm_match = existing_metrics.get((kpi.get("formula") or "").strip().lower())
            kpi["registered_metric_id"] = sm_match.id if sm_match else None
            kpi["is_favorite"] = bool((sm_match.kpi_metadata_json or {}).get("favorite")) if sm_match else False
            _enrich_kpi_metadata(kpi, idx_priority=int(kpi.get("priority") or (10 + idx_k)))

        for sk in std_needs_data + rejected_kpis:
            sk["computed_at"] = computed_at
            sk.setdefault("domain_scope", f"{domain_info.get('domain_family', 'Business')} / {domain_info.get('primary_domain_fa', 'عمومی')}")
            sk.setdefault("dataset_names", list(sk.get("source_datasets") or [ds.name]))
            sk.setdefault("sample_count", row_count)
            _enrich_kpi_metadata(sk, idx_priority=int(sk.get("priority") or 99))

        # Adaptive dynamic selection — NO arbitrary "first 3 fields" rule!
        business_kpis = [k for k in all_computable if not k.get("is_data_quality")]
        dq_kpis = [k for k in all_computable if k.get("is_data_quality")]
        business_kpis.sort(key=lambda k: (-int(k.get("is_standard", False)), -int(k.get("is_favorite", False)), int(k.get("priority", 50)), -k["score"]))

        # Select diverse top business KPIs scaled to dataset richness (min 2, up to 14 on rich multi-field tables)
        max_selected = min(max(4, len(measures_additive) * 2 + len(measures_semi) + 3), 14)
        selected_ids: set[str] = set()
        cat_counts: dict[str, int] = {}

        # Guarantee computable standard KPIs & diverse high-scoring categories first
        for k in business_kpis:
            if k.get("needs_user_confirmation"):
                k["status"] = "needs_confirmation"
                continue
            if len(selected_ids) >= max_selected:
                k["status"] = "suggested"
                continue
            cat = k["category"]
            cap_per_cat = 7 if cat in ("standard", "aggregate", "domain", "ratio") else 2
            if k.get("is_standard") or cat_counts.get(cat, 0) < cap_per_cat or k["score"] >= 86.0:
                k["status"] = "selected"
                selected_ids.add(k["id"])
                cat_counts[cat] = cat_counts.get(cat, 0) + 1
            else:
                k["status"] = "suggested"

        # Fallback if all were needs_confirmation
        if not selected_ids and business_kpis:
            business_kpis[0]["status"] = "selected"
            selected_ids.add(business_kpis[0]["id"])

        for k in business_kpis:
            k["suggested_selected"] = (k["status"] == "selected")

        for dq in dq_kpis:
            dq["status"] = "selected" if dq["id"] == "dq_completeness_pct" else "suggested"
            _enrich_kpi_metadata(dq, idx_priority=90)

        all_candidates = list(std_kpis) + [k for k in business_kpis if not k.get("is_standard")] + [
            r for r in rejected_kpis if not r.get("is_standard")
        ]

        return {
            "standard": std_kpis,
            "selected": [k for k in business_kpis if k["status"] == "selected"],
            "suggested": [k for k in business_kpis if k["status"] == "suggested"],
            "needs_confirmation": [k for k in business_kpis if k["status"] == "needs_confirmation"],
            "needs_data": std_needs_data,
            "data_quality": dq_kpis,
            "uncomputable": rejected_kpis,
            "all_business": business_kpis,
            "all_candidates": all_candidates,
            "multi_source_analysis": multi_source_info,
            "kpi_engine_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    # ------------------------------------------------------------------
    # STAGE 5: MULTI-PAGE DASHBOARD & SMART CHART RECOMMENDATION
    # ------------------------------------------------------------------
    @staticmethod
    def build_intelligent_dashboard_plan(
        db: Session,
        ds: Dataset,
        user: Optional[User] = None,
        *,
        template_mode: str = "executive",      # executive | analytical | operational
        detail_level: str = "standard",        # summary | standard | comprehensive
        calendar_mode: str = "jalali",         # jalali | gregorian
        date_range: Optional[dict[str, Any]] = None,
        max_pages: int = 7,
        selected_kpi_ids: Optional[list[str]] = None,
        confirmed_kpis: Optional[list[dict[str, Any]]] = None,
        auto_generate_kpis: bool = False,
        custom_kpis: Optional[list[dict[str, Any]]] = None,
        target_values: Optional[dict[str, float]] = None,
        related_dataset_ids: Optional[list[str]] = None,
        domain_override: Optional[str] = None,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        deep_profile = IntelligentKpiEngine.deep_profile_dataset(db, ds, user=user, force_refresh=force_refresh)
        classifications = IntelligentKpiEngine.classify_fields(db, ds, deep_profile)
        partner_objs = (
            db.query(Dataset)
            .filter(Dataset.organization_id == ds.organization_id, Dataset.id.in_(related_dataset_ids))
            .all()
            if related_dataset_ids
            else []
        )
        eff_override = domain_override or (ds.profile_json or {}).get("domain_override")
        domain_info = IntelligentKpiEngine.detect_business_domain(
            ds, classifications, deep_profile,
            domain_override=eff_override,
            partner_datasets=partner_objs,
        )
        kpi_bundle = IntelligentKpiEngine.generate_and_score_kpis(
            db, ds, user, deep_profile, classifications, domain_info,
            related_dataset_ids=related_dataset_ids,
            target_values=target_values,
            calendar_mode=calendar_mode,
        )

        # Build lookup across ALL discovered & standard KPIs
        cand_by_id: dict[str, dict[str, Any]] = {}
        for lst_key in ("standard", "all_business", "needs_data", "data_quality", "uncomputable"):
            for item in (kpi_bundle.get(lst_key) or []):
                cand_by_id.setdefault(item["id"], item)

        sec_ctx_val = (
            build_data_security_context(db, user, ds.id)
            if user is not None
            else DataSecurityContext("sys", [], [], set(), {}, "sys")
        )
        sub_sql_ck, sub_p_ck, _ = build_security_projection_and_where(
            ds.warehouse_table, warehouse.describe_table(ds.warehouse_table), sec_ctx_val
        )
        all_ds_ids = [ds.id, *(related_dataset_ids or [])]

        def _compile_and_eval_confirmed_kpi(ck: dict[str, Any], base: Optional[dict[str, Any]], idx: int) -> dict[str, Any]:
            merged = dict(base or {})
            k_id = str(ck.get("id") or merged.get("id") or f"custom_{idx + 1}")
            k_name = str(ck.get("name") or merged.get("name") or f"شاخص {idx + 1}").strip()
            k_formula = str(ck.get("formula") or merged.get("formula") or "").strip()
            k_fmt = str(ck.get("display_format") or ck.get("format_type") or merged.get("display_format") or merged.get("format_type") or "number")
            k_unit = str(ck.get("unit") if ck.get("unit") is not None else merged.get("unit") or "")
            if k_fmt == "percentage" and not k_unit:
                k_unit = "%"
            elif k_fmt == "currency" and not k_unit:
                k_unit = "ریال"
            k_prio = int(ck.get("priority") if ck.get("priority") is not None else (merged.get("priority") or (idx + 1)))
            k_comp_mode = str(ck.get("comparison_mode") or merged.get("comparison_mode") or "none")
            k_target = _safe_float(ck.get("target_value") if "target_value" in ck else merged.get("target_value"))
            k_base_fields = list(ck.get("base_fields") or merged.get("base_fields") or [])
            k_msa = ck.get("multi_source_agg") or merged.get("multi_source_agg")
            k_pre_grain = ck.get("pre_agg_grain") or merged.get("pre_agg_grain")

            # Special case: user resolved std_target_achievement via manual target entry
            if k_id == "std_target_achievement" and base and base.get("computability_status") == "needs_data":
                if k_target and k_target > 0 and base.get("base_fields"):
                    sf = base["base_fields"][0]
                    k_formula = f'COALESCE(SUM("{sf}"), 0) * 100.0 / NULLIF({k_target}, 0)'
                    k_comp_mode = "target"
                    merged["computability_status"] = "computable_with_warning"
                else:
                    raise ValueError(f"شاخص «{k_name}» نیازمند تکمیل فیلد هدف یا وارد کردن مقدار هدف دستی معتبر است.")

            formula_changed = bool(base and k_formula and _normalize_formula_signature(k_formula) != _normalize_formula_signature(base.get("formula", "")))
            if base and base.get("computability_status") in ("needs_data", "uncomputable") and not formula_changed and not (k_id == "std_target_achievement" and k_target and k_target > 0):
                raise ValueError(
                    base.get("rejection_reason_fa")
                    or f"شاخص «{k_name}» در وضعیت «{base.get('computability_status_fa', 'غیرقابل محاسبه')}» است و قابل تولید نیست."
                )

            if not k_formula:
                raise ValueError(f"فرمول شاخص «{k_name}» نمی‌تواند خالی باشد.")

            val = merged.get("value")
            comp_sql = merged.get("compiled_sql") or k_formula
            # Re-validate and execute whenever custom, formula modified, or manual target updated
            if (not base) or formula_changed or (k_id == "std_target_achievement" and k_target and base and base.get("computability_status") == "needs_data"):
                if k_msa and not formula_changed:
                    jsql, jparams, _ = build_visual_query_sql(
                        db, ds, {"multi_source_agg": k_msa, "calendar_mode": calendar_mode, "limit": 1},
                        [], [], [], user=user, enforce_join_rbac=True,
                    )
                    jr = warehouse.execute_query(jsql, jparams, limit=1)["rows"]
                    val = _safe_float(jr[0].get("value")) if jr else None
                    k_msa = k_msa
                else:
                    k_msa = None
                    if user is not None:
                        v = validate_and_compile_formula(
                            db,
                            user,
                            dataset_ids=all_ds_ids,
                            formula=k_formula,
                            calculation_type="analytical_metric",
                            qualify_with_alias=False,
                            auto_safe_divide=False,
                        )
                        comp_sql = v["compiled_sql"]
                        if v.get("is_aov_macro"):
                            k_pre_grain = {"grain_field": v["order_field"], "measure_field": v["amount_field"], "grain_agg": "SUM"}
                        else:
                            k_pre_grain = None
                    else:
                        validate_formula_ast(k_formula, allowed_columns={f["field_name"] for f in classifications})
                        comp_sql = k_formula

                    # Execute via build_visual_query_sql so AOV/MOM_CALENDAR macros compile cleanly
                    v_spec_p: dict[str, Any] = {"formula": k_formula, "calendar_mode": calendar_mode, "limit": 1}
                    if merged.get("time_field"):
                        v_spec_p["time_field"] = merged["time_field"]
                    if k_pre_grain:
                        v_spec_p["pre_agg_grain"] = k_pre_grain
                    psql, pparams, _ = build_visual_query_sql(db, ds, v_spec_p, [], [], [], user=user)
                    pr = warehouse.execute_query(psql, pparams, limit=1)["rows"]
                    val = _safe_float(pr[0].get("value")) if pr else None

            out = {
                **merged,
                "id": k_id,
                "slug": merged.get("slug") or re.sub(r"[^a-z0-9_]+", "_", k_id.lower()).strip("_") or f"kpi_{idx + 1}",
                "name": k_name,
                "category": ck.get("category") or merged.get("category") or "custom",
                "formula": k_formula,
                "compiled_sql": comp_sql,
                "base_fields": k_base_fields,
                "required_fields": k_base_fields or merged.get("required_fields") or [],
                "format_type": k_fmt,
                "display_format": k_fmt,
                "unit": k_unit,
                "priority": k_prio,
                "comparison_mode": k_comp_mode,
                "target_value": k_target,
                "multi_source_agg": k_msa,
                "pre_agg_grain": k_pre_grain,
                "value": round(val, 4) if val is not None else None,
                "formatted_value": _format_kpi_value(val, k_fmt, k_unit),
                "source_datasets": merged.get("source_datasets") or [ds.name],
                "source": merged.get("source") or ds.name,
                "period": merged.get("period") or "کل بازه داده‌ها",
                "confidence": float(merged.get("confidence") or 0.96),
                "score": float(merged.get("score") or 95.0),
                "status": "selected",
                "computability_status": merged.get("computability_status") if merged.get("computability_status") in ("computable", "computable_with_warning") else "computable",
                "limitations": list(merged.get("limitations") or []),
            }
            return _enrich_kpi_metadata(out, idx_priority=k_prio)

        # Merge user custom KPIs (strictly AST + CLS validated)
        custom_resolved: list[dict[str, Any]] = []
        if custom_kpis:
            for idx, ck in enumerate(custom_kpis):
                c_formula = str(ck.get("formula") or "").strip()
                if not c_formula:
                    continue
                try:
                    custom_resolved.append(_compile_and_eval_confirmed_kpi(ck, None, idx))
                except Exception:
                    continue

        # ------------------------------------------------------------------
        # STRICT KPI CONFIRMATION GUARD (Zero silent auto-generation!)
        # ------------------------------------------------------------------
        explicitly_confirmed_mode = False
        if confirmed_kpis is not None:
            if len(confirmed_kpis) == 0:
                if auto_generate_kpis:
                    active_kpis = list(kpi_bundle["selected"])
                else:
                    raise ValueError(
                        "هیچ KPI انتخاب و تأیید نشده است. لطفاً حداقل یک KPI معتبر را انتخاب کنید یا حالت تولید خودکار را صراحتاً فعال نمایید."
                    )
            else:
                active_kpis = []
                for idx, ck in enumerate(confirmed_kpis):
                    base_match = cand_by_id.get(str(ck.get("id") or ""))
                    active_kpis.append(_compile_and_eval_confirmed_kpi(ck, base_match, idx))
                explicitly_confirmed_mode = True
        elif selected_kpi_ids is not None:
            if len(selected_kpi_ids) == 0:
                if auto_generate_kpis:
                    active_kpis = list(kpi_bundle["selected"])
                else:
                    raise ValueError(
                        "هیچ KPI انتخاب نشده است. حداقل یک KPI را انتخاب کنید یا حالت تولید خودکار را صراحتاً فعال نمایید."
                    )
            else:
                by_id_all = {**cand_by_id, **{c["id"]: c for c in custom_resolved}}
                active_kpis = []
                for pos, kid in enumerate(selected_kpi_ids):
                    item = by_id_all.get(kid)
                    if not item:
                        continue
                    if item.get("computability_status") in ("needs_data", "uncomputable"):
                        if kid == "std_target_achievement" and target_values and (target_values.get(kid) or target_values.get("target")):
                            tv = float(target_values.get(kid) or target_values.get("target"))
                            active_kpis.append(_compile_and_eval_confirmed_kpi({"id": kid, "target_value": tv, "priority": pos + 1}, item, pos))
                            continue
                        raise ValueError(
                            item.get("rejection_reason_fa")
                            or f"شاخص «{item.get('name', kid)}» در وضعیت «{item.get('computability_status_fa')}» است و قابل تولید نیست."
                        )
                    cp = dict(item)
                    cp["priority"] = cp.get("priority") or (pos + 1)
                    active_kpis.append(cp)
                if not active_kpis:
                    raise ValueError("هیچ یک از KPIهای انتخاب‌شده معتبر یا قابل محاسبه نبود.")
                explicitly_confirmed_mode = True
        else:
            if custom_resolved:
                active_kpis = custom_resolved
                explicitly_confirmed_mode = True
            elif auto_generate_kpis:
                active_kpis = list(kpi_bundle["selected"])
            else:
                raise ValueError(
                    "انتخاب و تأیید KPIها پیش از تولید داشبورد الزامی است. لطفاً حداقل یک KPI را تأیید کنید یا حالت تولید خودکار را صراحتاً فعال نمایید."
                )

        # Apply target overrides onto active KPIs
        if target_values:
            for k in active_kpis:
                if k["id"] in target_values:
                    k["target_value"] = float(target_values[k["id"]])
                elif k.get("field") and k["field"] in target_values:
                    k["target_value"] = float(target_values[k["field"]])

        # Respect user priority sorting strictly
        active_kpis.sort(key=lambda k: (int(k.get("priority") or 99), -float(k.get("score") or 0)))

        row_count = int(deep_profile.get("row_count") or 0)
        time_cols = [c for c in classifications if c["semantic_class"] == "Date/Time" and c.get("stats", {}).get("temporal")]
        all_date_cols = [c for c in classifications if c["semantic_class"] == "Date/Time"]
        primary_time = time_cols[0] if time_cols else (all_date_cols[0] if all_date_cols else None)
        time_periods = int((primary_time.get("stats", {}).get("temporal") or {}).get("actual_periods") or 0) if primary_time else 0
        time_gran = ((primary_time.get("stats", {}).get("temporal") or {}).get("detected_granularity") or "monthly") if primary_time else "monthly"

        dim_cols = [
            c for c in classifications
            if c["semantic_class"] in ("Category", "Status", "Geographic") and 2 <= int(c["stats"].get("distinct_count") or 0) <= 60
        ]
        # Prefer Customer / Product / Unit / Category / Region dimensions with clean cardinality (3..15)
        def _dim_rank(c: dict[str, Any]) -> tuple[int, int, int]:
            fn_l = c["field_name"].lower()
            biz_dim = 0 if any(t in fn_l for t in ("customer", "product", "unit", "department", "category", "region", "branch", "مشتری", "محصول", "واحد", "دسته‌بندی", "منطقه")) else 1
            card_pref = 0 if 3 <= int(c["stats"].get("distinct_count") or 0) <= 15 else 1
            return (biz_dim, card_pref, int(c["stats"].get("distinct_count") or 0))

        dim_cols.sort(key=_dim_rank)
        geo_cols = [c for c in classifications if c["semantic_class"] == "Geographic" and 2 <= int(c["stats"].get("distinct_count") or 0) <= 80]
        status_cols = [c for c in classifications if c["semantic_class"] == "Status" and 2 <= int(c["stats"].get("distinct_count") or 0) <= 20]
        meas_add = [c for c in classifications if c["sum_allowed"] and c["data_type"] == "numeric"]
        meas_all_num = [c for c in classifications if c["data_type"] == "numeric" and c["semantic_class"] != "Identifier"]
        primary_measure = (
            next((c for c in meas_add if c["semantic_class"] in ("Revenue", "Actual", "Profit", "Currency", "Quantity")), None)
            or (meas_add[0] if meas_add else (meas_all_num[0] if meas_all_num else None))
        )
        secondary_measure = next(
            (c for c in meas_add if primary_measure and c["field_name"] != primary_measure["field_name"]),
            None,
        )
        target_col = next((c for c in classifications if c["semantic_class"] == "Target"), None)

        # Effective filters (if date_range given)
        eff_filters: list[dict[str, Any]] = []
        if date_range and primary_time and (date_range.get("start") or date_range.get("end")):
            eff_filters.append({
                "field_name": date_range.get("field_name") or primary_time["field_name"],
                "operator": "date_range",
                "value": {"start": date_range.get("start"), "end": date_range.get("end")},
                "calendar": calendar_mode,
                "is_active": True,
            })

        # BPMN Process Detection (preserves 100% compatibility with BPMN workflows)
        from app.engines.ai_engine import AiAnalyticsEngine
        cols_for_bpmn = [
            {"name": c["field_name"], "label": c["display_name"], "role": c["semantic_role"], "type": c["raw_type"]}
            for c in classifications
        ]
        bpmn_cfg = AiAnalyticsEngine._infer_bpmn_config_from_cols(cols_for_bpmn)

        # Query verification & deduplication tracker
        seen_widget_sigs: set[str] = set()

        def _probe_widget_query(vspec: dict[str, Any]) -> Optional[dict[str, Any]]:
            try:
                sql_str, q_params, _ = build_visual_query_sql(
                    db, ds, vspec, eff_filters, [], [], user=user, enforce_join_rbac=bool(vspec.get("joins") or vspec.get("multi_source_agg"))
                )
                res = warehouse.execute_query(sql_str, q_params, limit=120)
                if user is not None:
                    sc = build_data_security_context(db, user, ds.id)
                    vis_cols, masked_rows = apply_cls_and_masking(res["columns"], res["rows"], sc)
                    return {"columns": vis_cols, "rows": masked_rows, "sql": sql_str}
                return {"columns": res["columns"], "rows": res["rows"], "sql": sql_str}
            except Exception:
                return None

        def _make_widget(
            *,
            title: str,
            chart_type: str,
            visual_spec: dict[str, Any],
            layout: dict[str, int],
            style: Optional[dict[str, Any]] = None,
            visualization: Optional[dict[str, Any]] = None,
            narrative_fa: str = "",
            provenance: Optional[dict[str, Any]] = None,
            drill_hierarchy: Optional[list[dict[str, str]]] = None,
            drill_through_page_key: Optional[str] = None,
            skip_probe: bool = False,
            allow_empty: bool = False,
            dedup_key: Optional[str] = None,
        ) -> Optional[dict[str, Any]]:
            if dedup_key and dedup_key in seen_widget_sigs:
                return None
            probe = None if skip_probe else _probe_widget_query(visual_spec)
            if not skip_probe:
                if probe is None or (not allow_empty and len(probe["rows"]) == 0):
                    return None
            if dedup_key:
                seen_widget_sigs.add(dedup_key)

            adv = {
                "ai_generated": True,
                "narrative_fa": narrative_fa,
                "sources_used": (provenance or {}).get("sources_used") or [ds.name],
                "domain": domain_info["primary_domain"],
                "domain_fa": domain_info["primary_domain_fa"],
                **(provenance or {}),
            }
            return {
                "title": title,
                "description": narrative_fa[:280] if narrative_fa else "",
                "chart_type": chart_type,
                "dataset_id": ds.id,
                "visual_spec": visual_spec,
                "layout": layout,
                "style": style or {},
                "visualization": visualization or {"show_legend": True, "show_tooltip": True},
                "advanced": adv,
                "drill_hierarchy": drill_hierarchy or [],
                "drill_through_page_key": drill_through_page_key,
                "preview_rows": probe["rows"][:25] if probe else [],
            }

        def _make_time_x(d_col: str) -> tuple[str, str]:
            if calendar_mode == "jalali" and time_gran != "yearly":
                return f"{d_col}_ym", f"SUBSTRING(JALALI_DATE({d_col}), 1, 7)"
            if time_gran == "daily":
                return f"{d_col}_day", f"STRFTIME(TRY_CAST({d_col} AS DATE), '%Y-%m-%d')"
            if time_gran == "yearly":
                return f"{d_col}_yr", f"STRFTIME(DATE_TRUNC('year', TRY_CAST({d_col} AS DATE)), '%Y')"
            return f"{d_col}_ym", f"STRFTIME(DATE_TRUNC('month', TRY_CAST({d_col} AS DATE)), '%Y-%m')"

        # Derive chart measure strictly from confirmed KPIs when user explicitly confirmed KPIs!
        pm_field = primary_measure["field_name"] if primary_measure else "*"
        pm_disp = primary_measure["display_name"] if primary_measure else "تعداد رکورد"
        pm_agg = primary_measure["default_aggregation"] if primary_measure else "COUNT"
        pm_formula_override: Optional[str] = None

        active_ids_set = {k["id"] for k in active_kpis}
        sales_oriented_active = any(
            k["id"] in ("std_total_sales", "std_profit_margin", "std_mom_growth", "std_aov", "std_target_achievement")
            or (primary_measure and k.get("field") == primary_measure["field_name"])
            for k in active_kpis
        )
        if explicitly_confirmed_mode and not sales_oriented_active and active_kpis:
            lead_k = active_kpis[0]
            if lead_k.get("formula") and not lead_k.get("multi_source_agg"):
                pm_formula_override = lead_k["formula"]
                pm_disp = lead_k["name"]
            elif lead_k.get("field"):
                pm_field = lead_k["field"]
                pm_agg = lead_k.get("aggregation", "SUM")
                pm_disp = lead_k["name"]

        def _chart_measure_spec(extra: dict[str, Any]) -> dict[str, Any]:
            if pm_formula_override:
                return {**extra, "formula": pm_formula_override, "calendar_mode": calendar_mode}
            return {**extra, "y_field": pm_field, "aggregation": pm_agg}

        prim_dim = dim_cols[0] if dim_cols else None
        sec_dim = dim_cols[1] if len(dim_cols) > 1 else None
        default_drill_hier = [{"field": d["field_name"]} for d in dim_cols[:3]]
        pages: list[dict[str, Any]] = []

        # ==============================================================
        # PAGE 1: نمای کلی مدیریتی (Executive Overview)
        # ==============================================================
        p1_widgets: list[dict[str, Any]] = []
        kpi_cap = max(len(active_kpis), 12) if explicitly_confirmed_mode else (3 if detail_level == "summary" else (6 if detail_level == "standard" else 9))
        top_kpis = active_kpis[:kpi_cap]
        cur_y = 0

        for idx, k in enumerate(top_kpis):
            vspec: dict[str, Any] = {"joins": k.get("joins") or [], "calendar_mode": calendar_mode, "limit": 1}
            if k.get("multi_source_agg"):
                vspec["multi_source_agg"] = k["multi_source_agg"]
            if k.get("pre_agg_grain"):
                vspec["pre_agg_grain"] = k["pre_agg_grain"]
            if k.get("formula"):
                vspec["formula"] = k["formula"]
            elif k.get("field"):
                vspec["y_field"] = k["field"]
                vspec["aggregation"] = k.get("aggregation", "SUM")
            if k.get("time_field"):
                vspec["time_field"] = k["time_field"]

            eff_fmt = k.get("display_format") or k.get("format_type") or "number"
            eff_comp = k.get("comparison_mode") or "none"
            k_style: dict[str, Any] = {
                "number_format": eff_fmt,
                "decimals": 1 if eff_fmt in ("percentage", "duration") else 0,
                "abbreviate": eff_fmt != "percentage",
                "unit": k.get("unit", ""),
            }
            if eff_comp == "previous_period" and k.get("previous_value") is not None:
                k_style["previous_value"] = k["previous_value"]
            elif eff_comp == "target" and k.get("target_value") is not None:
                k_style["target_value"] = k["target_value"]
            elif not explicitly_confirmed_mode:
                if k.get("previous_value") is not None:
                    k_style["previous_value"] = k["previous_value"]
                if k.get("target_value") is not None:
                    k_style["target_value"] = k["target_value"]
            if k.get("sparkline_values"):
                k_style["sparkline_values"] = k["sparkline_values"]

            # Choose kpi, gauge (for delay rate / SLA / secondary percentage), or bullet (when target set)
            k_chart = "kpi"
            if k["id"] == "std_delay_rate" or "delay" in k["id"] or "sla_breach" in k["id"]:
                k_chart = "gauge"
                k_style["max_value"] = 100
            elif eff_comp == "target" and k.get("target_value") is not None and idx < 2 and template_mode != "operational":
                k_chart = "bullet" if (idx == 0 or not explicitly_confirmed_mode) else "kpi"
                k_style["max_value"] = max(float(k["target_value"]) * 1.25, float(k.get("value") or 0) * 1.1, 100.0)
            elif not explicitly_confirmed_mode and k.get("target_value") is not None and idx == 0 and template_mode != "operational":
                k_chart = "bullet"
                k_style["max_value"] = max(float(k["target_value"]) * 1.25, float(k.get("value") or 0) * 1.1, 100.0)
            elif eff_fmt == "percentage" and k.get("value") is not None and 0 <= float(k["value"]) <= 100 and idx >= 3:
                k_chart = "gauge"
                k_style["max_value"] = 100

            lims_txt = f" محدودیت/هشدار: {' '.join(k['limitations'])}" if k.get("limitations") else ""
            comp_txt = f" | مبنا: {k['comparison_basis']}" if k.get("comparison_basis") else ""
            elig_txt = ""
            if k.get("eligible_definition_fa") and k.get("delayed_definition_fa"):
                elig_txt = f" | واجد شرایط: {k['eligible_definition_fa']} | شرط تأخیر: {k['delayed_definition_fa']}"
            narr = (
                f"{k['name']}: {k.get('formatted_value', '—')} (منبع: {k.get('source', ds.name)}؛ بازه: {k.get('period', 'کل داده')}{comp_txt}{elig_txt}).{lims_txt}"
            )
            kpi_slot = len(p1_widgets)
            w = _make_widget(
                title=k["name"],
                chart_type=k_chart,
                visual_spec=vspec,
                layout={"x": (kpi_slot % 3) * 4, "y": (kpi_slot // 3) * 4, "w": 4, "h": 4},
                style=k_style,
                narrative_fa=narr,
                provenance={
                    "kpi_id": k["id"],
                    "kpi_category": k["category"],
                    "formula": k["formula"],
                    "base_fields": k.get("base_fields") or [],
                    "value": k.get("value"),
                    "comparison_basis": k.get("comparison_basis"),
                    "comparison_mode": eff_comp,
                    "display_format": eff_fmt,
                    "priority": k.get("priority"),
                    "period": k.get("period"),
                    "confidence": k.get("confidence"),
                    "limitations": k.get("limitations", []),
                    "eligible_definition_fa": k.get("eligible_definition_fa"),
                    "delayed_definition_fa": k.get("delayed_definition_fa"),
                    "computed_at": k.get("computed_at"),
                    "sources_used": k.get("source_datasets") or [ds.name],
                },
                drill_hierarchy=default_drill_hier,
                drill_through_page_key="details",
                dedup_key=f"p1_kpi_{k['id']}",
            )
            if w:
                p1_widgets.append(w)

        if p1_widgets:
            cur_y = ((len(p1_widgets) - 1) // 3 + 1) * 4

        # Overview Primary Chart 1: Monthly/Time Trend (using confirmed KPI's measure)
        row_x = 0
        if primary_time and time_periods >= 2 and (primary_measure or pm_formula_override):
            tx_f, tx_expr = _make_time_x(primary_time["field_name"])
            t_spec = _chart_measure_spec({
                "x_field": tx_f,
                "x_formula": tx_expr,
                "sort_by": tx_f,
                "sort_dir": "ASC",
                "limit": 36,
            })
            p_probe = _probe_widget_query(t_spec)
            if p_probe and len(p_probe["rows"]) >= 2:
                narr_t = _build_factual_narrative_fa(
                    analysis_type="trend",
                    title=f"روند {pm_disp}",
                    source_names=[ds.name],
                    x_field=tx_f,
                    y_label=pm_disp,
                    rows=p_probe["rows"],
                    total_records=row_count,
                    calendar_mode=calendar_mode,
                )
                w_tr = _make_widget(
                    title=f"روند زمانی {pm_disp}",
                    chart_type="area" if template_mode == "executive" else "line",
                    visual_spec=t_spec,
                    layout={"x": row_x, "y": cur_y, "w": 6, "h": 8},
                    narrative_fa=narr_t,
                    drill_hierarchy=[{"field": tx_f}] + ([{"field": prim_dim["field_name"]}] if prim_dim else []),
                    drill_through_page_key="details",
                    skip_probe=True,
                    dedup_key=f"trend_{tx_f}_{pm_disp}",
                )
                if w_tr:
                    p1_widgets.append(w_tr)
                    row_x = 6

        if prim_dim:
            b_spec = _chart_measure_spec({
                "x_field": prim_dim["field_name"],
                "limit": 12,
            })
            b_probe = _probe_widget_query(b_spec)
            if b_probe and len(b_probe["rows"]) >= 1:
                narr_b = _build_factual_narrative_fa(
                    analysis_type="ranking",
                    title=f"{pm_disp} به تفکیک {prim_dim['display_name']}",
                    source_names=[ds.name],
                    x_field=prim_dim["field_name"],
                    y_label=pm_disp,
                    rows=b_probe["rows"],
                    total_records=row_count,
                )
                b_chart = "horizontal_bar" if len(b_probe["rows"]) > 6 else "bar"
                w_br = _make_widget(
                    title=f"{pm_disp} به تفکیک {prim_dim['display_name']}",
                    chart_type=b_chart,
                    visual_spec=b_spec,
                    layout={"x": row_x, "y": cur_y, "w": 6 if row_x == 6 else 12, "h": 8},
                    narrative_fa=narr_b,
                    drill_hierarchy=[{"field": prim_dim["field_name"]}] + ([{"field": sec_dim["field_name"]}] if sec_dim else []),
                    drill_through_page_key="details",
                    skip_probe=True,
                    dedup_key=f"ov_bar_{prim_dim['field_name']}_{pm_disp}",
                )
                if w_br:
                    p1_widgets.append(w_br)
                    row_x = 0
                    cur_y += 8

        if row_x == 6:
            row_x = 0
            cur_y += 8

        # Overview Comparative Chart (Sales vs Cost/Profit or Actual vs Target) when confirmed KPIs include them
        cost_col_ov = next((c for c in classifications if c["semantic_class"] == "Cost"), None)
        profit_col_ov = next((c for c in classifications if c["semantic_class"] == "Profit"), None)
        comp_sec_col = cost_col_ov or profit_col_ov or target_col
        if sales_oriented_active and primary_measure and comp_sec_col and comp_sec_col["field_name"] != pm_field and (primary_time and time_periods >= 2 or prim_dim):
            cx_field = f"{primary_time['field_name']}_ym" if (primary_time and time_periods >= 2) else prim_dim["field_name"]  # type: ignore[index]
            cx_formula = _make_time_x(primary_time["field_name"])[1] if (primary_time and time_periods >= 2) else None
            cmp_spec: dict[str, Any] = {
                "x_field": cx_field,
                "y_field": pm_field,
                "aggregation": pm_agg,
                "secondary_y_field": comp_sec_col["field_name"],
                "secondary_aggregation": comp_sec_col["default_aggregation"],
                "sort_by": cx_field if cx_formula else "value",
                "sort_dir": "ASC" if cx_formula else "DESC",
                "limit": 24,
            }
            if cx_formula:
                cmp_spec["x_formula"] = cx_formula
            cmp_pr = _probe_widget_query(cmp_spec)
            if cmp_pr and len(cmp_pr["rows"]) >= 2:
                w_cmp = _make_widget(
                    title=f"مقایسه {pm_disp} در برابر {comp_sec_col['display_name']}",
                    chart_type="combo",
                    visual_spec=cmp_spec,
                    layout={"x": 0, "y": cur_y, "w": 12, "h": 8},
                    narrative_fa=f"نمودار مقایسه‌ای هم‌زمان «{pm_disp}» و «{comp_sec_col['display_name']}» بر اساس شاخص‌های منتخب داشبورد.",
                    drill_through_page_key="details",
                    skip_probe=True,
                    dedup_key=f"ov_cmp_{pm_field}_{comp_sec_col['field_name']}",
                )
                if w_cmp:
                    p1_widgets.append(w_cmp)
                    cur_y += 8

        # Always guarantee BPMN diagram on Overview when process schema detected!
        if bpmn_cfg:
            step_c = bpmn_cfg["step_field"]
            wait_c = bpmn_cfg.get("wait_field") or step_c
            agg_b = "AVG" if bpmn_cfg.get("wait_field") else "COUNT"
            bpmn_cols = [c for c in [step_c, bpmn_cfg.get("wait_field"), bpmn_cfg.get("sla_field"), bpmn_cfg.get("assignee_field"), bpmn_cfg.get("request_id_field"), bpmn_cfg.get("status_field")] if c]
            w_bpmn = _make_widget(
                title="نقشه فرآیند و گلوگاه‌ها (BPMN)",
                chart_type="bpmn",
                visual_spec={"columns": list(dict.fromkeys(bpmn_cols)), "x_field": step_c, "y_field": wait_c, "aggregation": agg_b, "limit": 500},
                layout={"x": 0, "y": cur_y, "w": 12, "h": 10},
                visualization={"bpmn_config": bpmn_cfg, "show_legend": True, "show_tooltip": True},
                narrative_fa=f"نمای فرآیندی بر اساس گام «{step_c}» در «{ds.name}» ({row_count:,} رکورد) جهت شناسایی گلوگاه‌ها و SLA.",
                dedup_key="ov_bpmn_process",
            )
            if w_bpmn:
                p1_widgets.append(w_bpmn)
                cur_y += 10

        if p1_widgets:
            pages.append({
                "title": "نمای کلی مدیریتی",
                "page_order": 0,
                "page_key": "overview",
                "widgets": p1_widgets,
            })

        # ==============================================================
        # PAGE 2: روند زمانی و تغییرات (Time Trends — conditional on valid time >= 2 periods)
        # ==============================================================
        if primary_time and time_periods >= 2 and len(pages) < max_pages:
            p2_widgets: list[dict[str, Any]] = []
            tx_f, tx_expr = _make_time_x(primary_time["field_name"])
            p2_y = 0

            # Multi-measure combo or secondary measure trend
            if secondary_measure:
                c_spec = {
                    "x_field": tx_f, "x_formula": tx_expr,
                    "y_field": pm_field, "aggregation": pm_agg,
                    "secondary_y_field": secondary_measure["field_name"],
                    "secondary_aggregation": secondary_measure["default_aggregation"],
                    "sort_by": tx_f, "sort_dir": "ASC", "limit": 36,
                }
                c_pr = _probe_widget_query(c_spec)
                if c_pr and len(c_pr["rows"]) >= 2:
                    w_c = _make_widget(
                        title=f"مقایسه روند {pm_disp} و {secondary_measure['display_name']}",
                        chart_type="combo",
                        visual_spec=c_spec,
                        layout={"x": 0, "y": p2_y, "w": 6, "h": 8},
                        narrative_fa=f"مقایسه هم‌زمان روند دو شاخص «{pm_disp}» و «{secondary_measure['display_name']}» در {len(c_pr['rows'])} دوره از «{ds.name}».",
                        skip_probe=True,
                        dedup_key=f"p2_combo_{tx_f}_{pm_field}_{secondary_measure['field_name']}",
                    )
                    if w_c:
                        p2_widgets.append(w_c)

            # Stacked area across primary dimension over time
            if prim_dim and prim_dim["stats"]["distinct_count"] <= 10:
                sa_spec = _chart_measure_spec({
                    "x_field": tx_f, "x_formula": tx_expr,
                    "series_field": prim_dim["field_name"],
                    "sort_by": tx_f, "sort_dir": "ASC", "limit": 200,
                })
                sa_pr = _probe_widget_query(sa_spec)
                if sa_pr and len(sa_pr["rows"]) >= 2:
                    w_sa = _make_widget(
                        title=f"روند {pm_disp} به تفکیک {prim_dim['display_name']}",
                        chart_type="stacked_area",
                        visual_spec=sa_spec,
                        layout={"x": 6 if p2_widgets else 0, "y": p2_y, "w": 6 if p2_widgets else 12, "h": 8},
                        narrative_fa=f"تغییرات سهم و روند هر «{prim_dim['display_name']}» در طول زمان بر اساس {len(sa_pr['rows'])} نقطه ثبت‌شده در «{ds.name}».",
                        drill_through_page_key="details",
                        skip_probe=True,
                        dedup_key=f"p2_stacked_area_{tx_f}_{prim_dim['field_name']}",
                    )
                    if w_sa:
                        p2_widgets.append(w_sa)

            if p2_widgets:
                p2_y += 8

            # Period waterfall / cumulative or second measure trend
            if (primary_measure and primary_measure["sum_allowed"]) or pm_formula_override:
                wf_spec = _chart_measure_spec({
                    "x_field": tx_f, "x_formula": tx_expr,
                    "sort_by": tx_f, "sort_dir": "ASC", "limit": 24,
                })
                wf_pr = _probe_widget_query(wf_spec)
                if wf_pr and len(wf_pr["rows"]) >= 3:
                    w_wf = _make_widget(
                        title=f"روند انباشته و تغییرات دوره‌ای {pm_disp}",
                        chart_type="waterfall" if len(wf_pr["rows"]) <= 12 else "bar",
                        visual_spec=wf_spec,
                        layout={"x": 0, "y": p2_y, "w": 12, "h": 8},
                        narrative_fa=_build_factual_narrative_fa(
                            analysis_type="change", title=f"تغییرات دوره‌ای {pm_disp}",
                            source_names=[ds.name], x_field=tx_f, y_label=pm_disp,
                            rows=wf_pr["rows"], total_records=row_count, calendar_mode=calendar_mode,
                        ),
                        drill_through_page_key="details",
                        skip_probe=True,
                        dedup_key=f"p2_wf_{tx_f}_{pm_disp}",
                    )
                    if w_wf:
                        p2_widgets.append(w_wf)
                        p2_y += 8

            if time_gran == "daily" and detail_level != "summary" and (primary_measure or pm_formula_override):
                cal_spec = _chart_measure_spec({
                    "x_field": f"{primary_time['field_name']}_cal",
                    "x_formula": f"STRFTIME(TRY_CAST({primary_time['field_name']} AS DATE), '%Y-%m-%d')",
                    "sort_by": f"{primary_time['field_name']}_cal",
                    "sort_dir": "ASC",
                    "limit": 365,
                })
                cal_pr = _probe_widget_query(cal_spec)
                if cal_pr and len(cal_pr["rows"]) >= 5:
                    w_cal = _make_widget(
                        title=f"نقشه حرارتی تقویمی {pm_disp}",
                        chart_type="calendar_heatmap",
                        visual_spec=cal_spec,
                        layout={"x": 0, "y": p2_y, "w": 12, "h": 7},
                        narrative_fa=f"تراکم روزانه «{pm_disp}» در {len(cal_pr['rows'])} روز ثبت‌شده در «{ds.name}».",
                        drill_through_page_key="details",
                        skip_probe=True,
                        dedup_key=f"p2_cal_{primary_time['field_name']}_{pm_disp}",
                    )
                    if w_cal:
                        p2_widgets.append(w_cal)

            if p2_widgets:
                pages.append({
                    "title": "روند زمانی و تغییرات",
                    "page_order": len(pages),
                    "page_key": "time_trends",
                    "widgets": p2_widgets,
                })

        # ==============================================================
        # PAGE 3: تحلیل تفکیکی و سهم (Dimensional Breakdown & Composition)
        # ==============================================================
        if dim_cols and len(pages) < max_pages:
            p3_widgets: list[dict[str, Any]] = []
            p3_y = 0

            # Donut / Pie share for low-cardinality primary dimension
            if prim_dim and prim_dim["stats"]["distinct_count"] <= 9:
                d_spec = _chart_measure_spec({"x_field": prim_dim["field_name"], "limit": 9})
                d_pr = _probe_widget_query(d_spec)
                if d_pr and len(d_pr["rows"]) >= 2:
                    w_d = _make_widget(
                        title=f"سهم {pm_disp} بر اساس {prim_dim['display_name']}",
                        chart_type="donut",
                        visual_spec=d_spec,
                        layout={"x": 0, "y": p3_y, "w": 6, "h": 8},
                        narrative_fa=_build_factual_narrative_fa(
                            analysis_type="share", title="تحلیل سهم",
                            source_names=[ds.name], x_field=prim_dim["field_name"],
                            y_label=pm_disp, rows=d_pr["rows"], total_records=row_count,
                        ),
                        drill_through_page_key="details",
                        skip_probe=True,
                        dedup_key=f"p3_donut_{prim_dim['field_name']}_{pm_disp}",
                    )
                    if w_d:
                        p3_widgets.append(w_d)

            # Grouped Bar or Stacked Bar when 2 dimensions exist!
            if prim_dim and sec_dim:
                gb_spec = _chart_measure_spec({
                    "x_field": prim_dim["field_name"],
                    "series_field": sec_dim["field_name"],
                    "limit": 80,
                })
                gb_pr = _probe_widget_query(gb_spec)
                if gb_pr and len(gb_pr["rows"]) >= 2:
                    w_gb = _make_widget(
                        title=f"مقایسه خوشه‌ای {pm_disp}: {prim_dim['display_name']} و {sec_dim['display_name']}",
                        chart_type="grouped_bar",
                        visual_spec=gb_spec,
                        layout={"x": 6 if p3_widgets else 0, "y": p3_y, "w": 6 if p3_widgets else 12, "h": 8},
                        narrative_fa=f"تحلیل تقاطعی «{prim_dim['display_name']}» و «{sec_dim['display_name']}» برای سنجش ترکیب «{pm_disp}» در {len(gb_pr['rows'])} گروه.",
                        drill_through_page_key="details",
                        skip_probe=True,
                        dedup_key=f"p3_grouped_{prim_dim['field_name']}_{sec_dim['field_name']}",
                    )
                    if w_gb:
                        p3_widgets.append(w_gb)
            elif sec_dim or (prim_dim and prim_dim["stats"]["distinct_count"] > 6):
                t_dim = sec_dim or prim_dim
                tm_spec = _chart_measure_spec({"x_field": t_dim["field_name"], "limit": 20})
                tm_pr = _probe_widget_query(tm_spec)
                if tm_pr and len(tm_pr["rows"]) >= 2:
                    w_tm = _make_widget(
                        title=f"نمای درختی تمرکز {pm_disp} ({t_dim['display_name']})",
                        chart_type="treemap",
                        visual_spec=tm_spec,
                        layout={"x": 6 if p3_widgets else 0, "y": p3_y, "w": 6 if p3_widgets else 12, "h": 8},
                        narrative_fa=_build_factual_narrative_fa(
                            analysis_type="share", title="نمای درختی",
                            source_names=[ds.name], x_field=t_dim["field_name"],
                            y_label=pm_disp, rows=tm_pr["rows"], total_records=row_count,
                        ),
                        drill_through_page_key="details",
                        skip_probe=True,
                        dedup_key=f"p3_treemap_{t_dim['field_name']}_{pm_disp}",
                    )
                    if w_tm:
                        p3_widgets.append(w_tm)

            if p3_widgets:
                p3_y += 8

            # Geo map if geographic column exists
            if geo_cols and geo_cols[0]["field_name"] != (prim_dim["field_name"] if prim_dim else ""):
                gf = geo_cols[0]
                g_spec = _chart_measure_spec({"x_field": gf["field_name"], "limit": 25})
                g_pr = _probe_widget_query(g_spec)
                if g_pr and len(g_pr["rows"]) >= 2:
                    w_geo = _make_widget(
                        title=f"توزیع جغرافیایی {pm_disp} ({gf['display_name']})",
                        chart_type="geo_map",
                        visual_spec=g_spec,
                        layout={"x": 0, "y": p3_y, "w": 6, "h": 8},
                        narrative_fa=_build_factual_narrative_fa(
                            analysis_type="ranking", title="پراکندگی جغرافیایی",
                            source_names=[ds.name], x_field=gf["field_name"],
                            y_label=pm_disp, rows=g_pr["rows"], total_records=row_count,
                        ),
                        skip_probe=True,
                        dedup_key=f"p3_geo_{gf['field_name']}_{pm_field}",
                    )
                    if w_geo:
                        p3_widgets.append(w_geo)

            # Funnel if status column exists
            if status_cols:
                sf = status_cols[0]
                fn_spec = {"x_field": sf["field_name"], "y_field": "*", "aggregation": "COUNT", "limit": 12}
                fn_pr = _probe_widget_query(fn_spec)
                if fn_pr and len(fn_pr["rows"]) >= 2:
                    w_fn = _make_widget(
                        title=f"پراکندگی وضعیت‌ها ({sf['display_name']})",
                        chart_type="funnel",
                        visual_spec=fn_spec,
                        layout={"x": 6 if any(w["layout"]["y"] == p3_y for w in p3_widgets) else 0, "y": p3_y, "w": 6, "h": 8},
                        narrative_fa=f"تعداد رکوردها در هر وضعیت «{sf['display_name']}» بر اساس {row_count:,} رکورد قابل مشاهده در «{ds.name}».",
                        skip_probe=True,
                        dedup_key=f"p3_funnel_{sf['field_name']}",
                    )
                    if w_fn:
                        p3_widgets.append(w_fn)

            # Multi-source joined dimension chart (if safe join available)
            if any(w["layout"]["y"] == p3_y for w in p3_widgets):
                p3_y += 8
            ms_dim = (kpi_bundle.get("multi_source_analysis") or {}).get("recommended_join_dimension")
            if ms_dim:
                ms_spec = {
                    "joins": ms_dim["joins"],
                    "x_field": ms_dim["field_name"],
                    "y_field": pm_field,
                    "aggregation": pm_agg,
                    "limit": 12,
                }
                ms_pr = _probe_widget_query(ms_spec)
                if ms_pr and len(ms_pr["rows"]) >= 1:
                    w_ms = _make_widget(
                        title=f"تحلیل چندمنبعی {pm_disp} به تفکیک {ms_dim['display_name']} ({ms_dim['dataset_name']})",
                        chart_type="horizontal_bar",
                        visual_spec=ms_spec,
                        layout={"x": 0, "y": p3_y, "w": 12, "h": 8},
                        narrative_fa=f"تحلیل ترکیبی بین «{ds.name}» و «{ms_dim['dataset_name']}» روی «{ms_dim['display_name']}» با حفظ کنترل دسترسی و جلوگیری از تکرار.",
                        provenance={"sources_used": [ds.name, ms_dim["dataset_name"]]},
                        skip_probe=True,
                        dedup_key=f"p3_ms_{ms_dim['field_name']}",
                    )
                    if w_ms:
                        p3_widgets.append(w_ms)

            if p3_widgets:
                pages.append({
                    "title": "تحلیل تفکیکی و سهم",
                    "page_order": len(pages),
                    "page_key": "breakdown",
                    "widgets": p3_widgets,
                })

        # ==============================================================
        # PAGE 4: عملکرد و رتبه‌بندی (Performance & Ranking)
        # ==============================================================
        if prim_dim and primary_measure and len(pages) < max_pages:
            p4_widgets: list[dict[str, Any]] = []
            p4_y = 0

            # Actual vs Target Bullet / Grouped / Progress if target exists
            if target_col and target_col["field_name"] != pm_field:
                tv_spec = {
                    "x_field": prim_dim["field_name"],
                    "y_field": pm_field,
                    "aggregation": pm_agg,
                    "secondary_y_field": target_col["field_name"],
                    "secondary_aggregation": target_col["default_aggregation"],
                    "limit": 12,
                }
                tv_pr = _probe_widget_query(tv_spec)
                if tv_pr and len(tv_pr["rows"]) >= 1:
                    w_tv = _make_widget(
                        title=f"عملکرد واقعی در برابر {target_col['display_name']} ({prim_dim['display_name']})",
                        chart_type="bullet",
                        visual_spec=tv_spec,
                        layout={"x": 0, "y": p4_y, "w": 6, "h": 8},
                        narrative_fa=_build_factual_narrative_fa(
                            analysis_type="target_vs_actual", title="عملکرد در برابر هدف",
                            source_names=[ds.name], x_field=prim_dim["field_name"],
                            y_label=pm_disp, rows=tv_pr["rows"], total_records=row_count,
                        ),
                        skip_probe=True,
                        dedup_key=f"p4_bullet_{prim_dim['field_name']}_{pm_field}",
                    )
                    if w_tv:
                        p4_widgets.append(w_tv)

            # Top vs Bottom ranking table / bar
            rk_spec = {
                "x_field": prim_dim["field_name"],
                "measures": [
                    {"field": pm_field, "aggregation": pm_agg, "alias": "value"},
                    {"formula": "COUNT(*)", "alias": "records_count"},
                ] + ([{"field": secondary_measure["field_name"], "aggregation": secondary_measure["default_aggregation"], "alias": "secondary_value"}] if secondary_measure else []),
                "sort_by": "value",
                "sort_dir": "DESC",
                "limit": 15,
            }
            rk_pr = _probe_widget_query(rk_spec)
            if rk_pr and len(rk_pr["rows"]) >= 2:
                w_rk = _make_widget(
                    title=f"رتبه‌بندی برترین {prim_dim['display_name']}‌ها",
                    chart_type="progress" if len(rk_pr["rows"]) <= 10 and not target_col else "table",
                    visual_spec=rk_spec,
                    layout={"x": 6 if p4_widgets else 0, "y": p4_y, "w": 6 if p4_widgets else 12, "h": 8},
                    narrative_fa=_build_factual_narrative_fa(
                        analysis_type="ranking", title="رتبه‌بندی",
                        source_names=[ds.name], x_field=prim_dim["field_name"],
                        y_label=pm_disp, rows=rk_pr["rows"], total_records=row_count,
                    ),
                    skip_probe=True,
                    dedup_key=f"p4_rank_{prim_dim['field_name']}_{pm_field}",
                )
                if w_rk:
                    p4_widgets.append(w_rk)

            # Radar multi-metric comparison if >= 2 measures and <= 8 categories
            if p4_widgets:
                p4_y += 8
            if secondary_measure and prim_dim["stats"]["distinct_count"] <= 8:
                rd_spec = {
                    "x_field": prim_dim["field_name"],
                    "y_field": pm_field, "aggregation": pm_agg,
                    "secondary_y_field": secondary_measure["field_name"],
                    "secondary_aggregation": secondary_measure["default_aggregation"],
                    "limit": 8,
                }
                rd_pr = _probe_widget_query(rd_spec)
                if rd_pr and len(rd_pr["rows"]) >= 3:
                    w_rd = _make_widget(
                        title=f"نمای راداری چندمعیاره ({prim_dim['display_name']})",
                        chart_type="radar",
                        visual_spec=rd_spec,
                        layout={"x": 0, "y": p4_y, "w": 12, "h": 8},
                        narrative_fa=f"مقایسه هم‌زمان «{pm_disp}» و «{secondary_measure['display_name']}» روی {len(rd_pr['rows'])} گروه اصلی.",
                        skip_probe=True,
                        dedup_key=f"p4_radar_{prim_dim['field_name']}",
                    )
                    if w_rd:
                        p4_widgets.append(w_rd)

            if p4_widgets:
                pages.append({
                    "title": "عملکرد و رتبه‌بندی",
                    "page_order": len(pages),
                    "page_key": "performance",
                    "widgets": p4_widgets,
                })

        # ==============================================================
        # PAGE 5: تحلیل آماری و پراکندگی (Statistical Analysis)
        # ==============================================================
        if primary_measure and row_count >= 8 and len(pages) < max_pages:
            p5_widgets: list[dict[str, Any]] = []
            # Boxplot for dispersion / outliers across primary dimension (or overall)
            bp_x = prim_dim["field_name"] if prim_dim else (all_date_cols[0]["field_name"] if all_date_cols else None)
            if bp_x:
                bp_spec = {"x_field": bp_x, "y_field": pm_field, "aggregation": pm_agg, "limit": 50}
                bp_pr = _probe_widget_query(bp_spec)
                if bp_pr and len(bp_pr["rows"]) >= 4:
                    w_bp = _make_widget(
                        title=f"تحلیل پراکندگی و نقاط پرت ({pm_disp})",
                        chart_type="boxplot",
                        visual_spec=bp_spec,
                        layout={"x": 0, "y": 0, "w": 6, "h": 8},
                        narrative_fa=f"توزیع چارک‌ها و شناسایی نقاط پرت (IQR) برای «{pm_disp}» بر اساس {len(bp_pr['rows'])} گروه.",
                        skip_probe=True,
                        dedup_key=f"p5_box_{bp_x}_{pm_field}",
                    )
                    if w_bp:
                        p5_widgets.append(w_bp)

            # Histogram / binned frequency distribution of primary numeric measure
            mn_v, mx_v = _safe_float(primary_measure["stats"].get("min")), _safe_float(primary_measure["stats"].get("max"))
            if mn_v is not None and mx_v is not None and mx_v > mn_v:
                step = (mx_v - mn_v) / 6.0
                b_expr = (
                    f'CONCAT(\'بازه \', CAST(LEAST(6, GREATEST(1, CAST(FLOOR(("{pm_field}" - {mn_v}) / {step}) AS INTEGER) + 1)) AS VARCHAR))'
                )
                hist_spec = {
                    "x_field": "hist_bin",
                    "x_formula": b_expr,
                    "y_field": "*",
                    "aggregation": "COUNT",
                    "sort_by": "hist_bin",
                    "sort_dir": "ASC",
                    "limit": 10,
                }
                hist_pr = _probe_widget_query(hist_spec)
                if hist_pr and len(hist_pr["rows"]) >= 2:
                    w_hist = _make_widget(
                        title=f"هیستوگرام توزیع فراوانی ({pm_disp})",
                        chart_type="histogram",
                        visual_spec=hist_spec,
                        layout={"x": 6 if p5_widgets else 0, "y": 0, "w": 6 if p5_widgets else 12, "h": 8},
                        narrative_fa=f"توزیع فراوانی مقادیر «{pm_disp}» در بازه {_fmt_num(mn_v)} تا {_fmt_num(mx_v)} در ۶ طبقه متساوی‌الفاصله.",
                        skip_probe=True,
                        dedup_key=f"p5_hist_{pm_field}",
                    )
                    if w_hist:
                        p5_widgets.append(w_hist)

            # Scatter correlation when 2 additive numeric measures exist
            if secondary_measure and bp_x:
                sc_spec = {
                    "x_field": bp_x,
                    "y_field": pm_field, "aggregation": pm_agg,
                    "secondary_y_field": secondary_measure["field_name"],
                    "secondary_aggregation": secondary_measure["default_aggregation"],
                    "limit": 80,
                }
                sc_pr = _probe_widget_query(sc_spec)
                if sc_pr and len(sc_pr["rows"]) >= 3:
                    w_sc = _make_widget(
                        title=f"همبستگی: {pm_disp} در برابر {secondary_measure['display_name']}",
                        chart_type="scatter",
                        visual_spec=sc_spec,
                        layout={"x": 0, "y": 8 if p5_widgets else 0, "w": 12, "h": 8},
                        narrative_fa=f"پراکندگی دوبعدی «{pm_disp}» و «{secondary_measure['display_name']}» (نکته: همبستگی به معنای علیت نیست).",
                        skip_probe=True,
                        dedup_key=f"p5_scatter_{pm_field}_{secondary_measure['field_name']}",
                    )
                    if w_sc:
                        p5_widgets.append(w_sc)

            if p5_widgets:
                pages.append({
                    "title": "تحلیل آماری و توزیع",
                    "page_order": len(pages),
                    "page_key": "statistical",
                    "widgets": p5_widgets,
                })

        # ==============================================================
        # PAGE 6: کیفیت و ممیزی داده (Data Quality — conditional & strictly separated)
        # ==============================================================
        dq_kpis = kpi_bundle.get("data_quality") or []
        if len(classifications) >= 2 and len(pages) < max_pages:
            p6_widgets: list[dict[str, Any]] = []
            dq_y = 0
            for idx_dq, dqk in enumerate(dq_kpis[:3]):
                dq_slot = len(p6_widgets)
                w_dqk = _make_widget(
                    title=dqk["name"],
                    chart_type="gauge" if dqk["id"] == "dq_completeness_pct" else "kpi",
                    visual_spec={"formula": dqk["formula"], "limit": 1},
                    layout={"x": dq_slot * 4, "y": 0, "w": 4, "h": 4},
                    style={
                        "number_format": dqk.get("format_type", "number"),
                        "decimals": 1 if dqk.get("format_type") == "percentage" else 0,
                        "unit": dqk.get("unit", ""),
                        "max_value": 100 if dqk.get("format_type") == "percentage" else None,
                    },
                    narrative_fa=f"شاخص کیفیت داده: {dqk['name']} = {dqk.get('formatted_value', '—')} در «{ds.name}».",
                    provenance={"kpi_id": dqk["id"], "kpi_category": "data_quality", "value": dqk.get("value")},
                    skip_probe=True,
                    dedup_key=f"p6_dq_{dqk['id']}",
                )
                if w_dqk:
                    p6_widgets.append(w_dqk)
            if p6_widgets:
                dq_y = 4

            # Per-column NULL audit using real DuckDB formula across top visible columns
            null_cols = [c for c in classifications if not c.get("is_calculated")][:8]
            if null_cols:
                # Build single UNION ALL visual/SQL or most-null column horizontal audit
                # Use highest-null field or top quality summary table
                top_null = max(null_cols, key=lambda c: c["stats"]["null_pct"])
                if top_null["stats"]["null_pct"] > 0:
                    w_na = _make_widget(
                        title=f"مقادیر خالی در ستون «{top_null['display_name']}» ({top_null['stats']['null_pct']}%)",
                        chart_type="pie",
                        visual_spec={
                            "x_field": "status_flag",
                            "x_formula": f'CASE WHEN "{top_null["field_name"]}" IS NULL THEN \'خالی (NULL)\' ELSE \'دارای مقدار\' END',
                            "y_field": "*",
                            "aggregation": "COUNT",
                            "limit": 5,
                        },
                        layout={"x": 0, "y": dq_y, "w": 6, "h": 7},
                        narrative_fa=f"بررسی کیفیت فیلد «{top_null['display_name']}»: {top_null['stats']['null_count']:,} رکورد خالی ({top_null['stats']['null_pct']}%).",
                        dedup_key=f"p6_null_{top_null['field_name']}",
                    )
                    if w_na:
                        p6_widgets.append(w_na)

            if p6_widgets:
                pages.append({
                    "title": "کیفیت و ممیزی داده",
                    "page_order": len(pages),
                    "page_key": "data_quality",
                    "widgets": p6_widgets,
                })

        # ==============================================================
        # PAGE 7: جزئیات رکوردها و جدول محوری (Details & Pivot)
        # ==============================================================
        if len(pages) < max_pages:
            p7_widgets: list[dict[str, Any]] = []
            det_cols = [c["field_name"] for c in classifications[:8]]
            if det_cols:
                w_tbl = _make_widget(
                    title=f"جدول جزئیات رکوردها ({ds.name})",
                    chart_type="table",
                    visual_spec={"columns": det_cols, "limit": 200},
                    layout={"x": 0, "y": 0, "w": 12, "h": 9},
                    narrative_fa=f"نمایش ردیف‌های جزئی «{ds.name}» با اعمال کامل فیلترهای دسترسی سطری/ستونی ({len(det_cols)} ستون قابل مشاهده).",
                    allow_empty=True,
                    dedup_key="p7_detail_table",
                )
                if w_tbl:
                    p7_widgets.append(w_tbl)

            if prim_dim and sec_dim and primary_measure and detail_level != "summary":
                pv_spec = {
                    "x_field": prim_dim["field_name"],
                    "series_field": sec_dim["field_name"],
                    "y_field": pm_field,
                    "aggregation": pm_agg,
                    "limit": 100,
                }
                w_pv = _make_widget(
                    title=f"جدول محوری {pm_disp}: {prim_dim['display_name']} × {sec_dim['display_name']}",
                    chart_type="pivot_table",
                    visual_spec=pv_spec,
                    layout={"x": 0, "y": 9 if p7_widgets else 0, "w": 12, "h": 8},
                    narrative_fa=f"جدول تقاطعی دو بعدی برای مقایسه هم‌زمان «{prim_dim['display_name']}» و «{sec_dim['display_name']}».",
                    dedup_key=f"p7_pivot_{prim_dim['field_name']}_{sec_dim['field_name']}",
                )
                if w_pv:
                    p7_widgets.append(w_pv)

            if p7_widgets:
                pages.append({
                    "title": "جزئیات و داده‌های پایه",
                    "page_order": len(pages),
                    "page_key": "details",
                    "widgets": p7_widgets,
                })

        # Order pages thoughtfully by template_mode (executive | analytical | operational)
        priority_map = {
            "executive": ["overview", "time_trends", "breakdown", "performance", "statistical", "data_quality", "details"],
            "analytical": ["overview", "statistical", "time_trends", "breakdown", "performance", "data_quality", "details"],
            "operational": ["overview", "time_trends", "data_quality", "details", "breakdown", "performance", "statistical"],
        }.get(template_mode, ["overview", "time_trends", "breakdown", "performance", "statistical", "data_quality", "details"])
        order_lookup = {k: i for i, k in enumerate(priority_map)}
        pages.sort(key=lambda p: order_lookup.get(p.get("page_key", ""), 99))
        for p_idx, p_obj in enumerate(pages):
            p_obj["page_order"] = p_idx

        # Fallback safeguard: always ensure at least 1 page with >=1 widget if dataset has data
        if not pages:
            w_fb = _make_widget(
                title=f"نمای کلی {ds.name}",
                chart_type="table",
                visual_spec={"columns": [c["field_name"] for c in classifications[:6]], "limit": 100},
                layout={"x": 0, "y": 0, "w": 12, "h": 8},
                narrative_fa=f"نمایش داده‌های قابل دسترس از منبع «{ds.name}».",
                allow_empty=True,
            )
            pages.append({
                "title": "نمای کلی مدیریتی",
                "page_order": 0,
                "page_key": "overview",
                "widgets": [w_fb] if w_fb else [],
            })

        # Suggested global dashboard filters
        suggested_filters: list[dict[str, Any]] = []
        if primary_time:
            suggested_filters.append({
                "name": primary_time["display_name"],
                "field_name": primary_time["field_name"],
                "filter_type": "date_range",
                "operator": "date_range",
            })
        for df in dim_cols[:2]:
            suggested_filters.append({
                "name": df["display_name"],
                "field_name": df["field_name"],
                "filter_type": "multi_select",
                "operator": "in",
            })

        return {
            "dataset_id": ds.id,
            "dataset_name": ds.name,
            "deep_profile": deep_profile,
            "field_classifications": classifications,
            "domain_detection": domain_info,
            "kpis": kpi_bundle,
            "active_kpis": active_kpis,
            "pages": pages,
            "suggested_filters": suggested_filters,
            "template_mode": template_mode,
            "detail_level": detail_level,
            "calendar_mode": calendar_mode,
        }

    # ------------------------------------------------------------------
    # PERSIST MULTI-PAGE DASHBOARD TO DATABASE (ORM Models)
    # ------------------------------------------------------------------
    @staticmethod
    def generate_and_persist_dashboard(
        db: Session,
        user: User,
        workspace_id: str,
        ds: Dataset,
        *,
        title: Optional[str] = None,
        template_mode: str = "executive",
        detail_level: str = "standard",
        calendar_mode: str = "jalali",
        date_range: Optional[dict[str, Any]] = None,
        max_pages: int = 7,
        theme_id: Optional[str] = None,
        selected_kpi_ids: Optional[list[str]] = None,
        confirmed_kpis: Optional[list[dict[str, Any]]] = None,
        auto_generate_kpis: bool = False,
        custom_kpis: Optional[list[dict[str, Any]]] = None,
        target_values: Optional[dict[str, float]] = None,
        related_dataset_ids: Optional[list[str]] = None,
        domain_override: Optional[str] = None,
        dashboard_id: Optional[str] = None,
    ) -> tuple[Dashboard, dict[str, Any]]:
        plan = IntelligentKpiEngine.build_intelligent_dashboard_plan(
            db, ds, user=user,
            template_mode=template_mode,
            detail_level=detail_level,
            calendar_mode=calendar_mode,
            date_range=date_range,
            max_pages=max_pages,
            selected_kpi_ids=selected_kpi_ids,
            confirmed_kpis=confirmed_kpis,
            auto_generate_kpis=auto_generate_kpis,
            custom_kpis=custom_kpis,
            target_values=target_values,
            related_dataset_ids=related_dataset_ids,
            domain_override=domain_override,
        )

        # Resolve theme cleanly within org
        chosen_theme = None
        if theme_id:
            chosen_theme = db.query(DashboardTheme).filter(
                DashboardTheme.id == theme_id, DashboardTheme.organization_id == user.organization_id
            ).first()
        if not chosen_theme:
            chosen_theme = db.query(DashboardTheme).filter(DashboardTheme.organization_id == user.organization_id).first()

        domain_fa = plan["domain_detection"]["primary_domain_fa"]
        persisted_kpi_specs = [
            {
                "id": k["id"],
                "name": k["name"],
                "formula": k["formula"],
                "base_fields": k.get("base_fields") or [],
                "display_format": k.get("display_format") or k.get("format_type") or "number",
                "format_type": k.get("display_format") or k.get("format_type") or "number",
                "unit": k.get("unit") or "",
                "priority": int(k.get("priority") or (idx + 1)),
                "comparison_mode": k.get("comparison_mode") or "none",
                "target_value": k.get("target_value"),
                "is_standard": bool(k.get("is_standard", False)),
                "multi_source_agg": k.get("multi_source_agg"),
                "pre_agg_grain": k.get("pre_agg_grain"),
            }
            for idx, k in enumerate(plan["active_kpis"])
        ]
        settings_payload = {
            "ai_generated": True,
            "engine_version": "intelligent_kpi_v3",
            "domain": plan["domain_detection"]["primary_domain"],
            "domain_fa": domain_fa,
            "template_mode": template_mode,
            "detail_level": detail_level,
            "calendar_mode": calendar_mode,
            "kpis_selected_count": len(plan["active_kpis"]),
            "kpi_config": {
                "dataset_id": ds.id,
                "related_dataset_ids": list(related_dataset_ids or []),
                "selected_kpi_ids": [k["id"] for k in plan["active_kpis"]],
                "confirmed_kpis": persisted_kpi_specs,
                "template_mode": template_mode,
                "detail_level": detail_level,
                "calendar_mode": calendar_mode,
                "auto_generate_kpis": bool(auto_generate_kpis),
            },
        }
        dash_desc = (
            f"داشبورد هوشمند ({domain_fa}) | "
            f"{len(plan['active_kpis'])} شاخص تأییدشده در {len(plan['pages'])} صفحه | "
            f"قالب: {template_mode}"
        )

        # Support updating an existing dashboard in-place when user returns to edit KPIs
        dash: Optional[Dashboard] = None
        if dashboard_id:
            from app.models.dashboard import Workspace as WsModel, WidgetFilter
            dash = (
                db.query(Dashboard)
                .join(WsModel, WsModel.id == Dashboard.workspace_id)
                .filter(Dashboard.id == dashboard_id, WsModel.organization_id == user.organization_id)
                .first()
            )
            if not dash:
                raise PermissionError("داشبورد مورد نظر برای ویرایش KPIها یافت نشد یا مجوز دسترسی ندارید.")
            if not permission_resolver.resolve(db, user, "dashboard", dash.id, "edit").allowed:
                raise PermissionError("مجوز ویرایش این داشبورد را ندارید.")

            old_w_ids = [r[0] for r in db.query(Widget.id).filter(Widget.dashboard_id == dash.id).all()]
            old_p_ids = [r[0] for r in db.query(DashboardPage.id).filter(DashboardPage.dashboard_id == dash.id).all()]
            if old_w_ids:
                db.query(DataLineage).filter(DataLineage.target_type == "widget", DataLineage.target_id.in_(old_w_ids)).delete(synchronize_session=False)
                db.query(WidgetFilter).filter(WidgetFilter.widget_id.in_(old_w_ids)).delete(synchronize_session=False)
                db.query(WidgetDrillRule).filter(WidgetDrillRule.widget_id.in_(old_w_ids)).delete(synchronize_session=False)
                db.query(WidgetConfig).filter(WidgetConfig.widget_id.in_(old_w_ids)).delete(synchronize_session=False)
                db.query(WidgetQuery).filter(WidgetQuery.widget_id.in_(old_w_ids)).delete(synchronize_session=False)
                db.query(Widget).filter(Widget.id.in_(old_w_ids)).delete(synchronize_session=False)
            if old_p_ids:
                db.query(DashboardLayout).filter(DashboardLayout.page_id.in_(old_p_ids)).delete(synchronize_session=False)
                db.query(DashboardPage).filter(DashboardPage.id.in_(old_p_ids)).delete(synchronize_session=False)
            db.query(DashboardFilter).filter(DashboardFilter.dashboard_id == dash.id).delete(synchronize_session=False)

            dash.title = title or dash.title
            dash.description = dash_desc
            if theme_id and chosen_theme:
                dash.theme_id = chosen_theme.id
            dash.settings_json = {**(dash.settings_json or {}), **settings_payload}
            db.flush()
        else:
            dash = Dashboard(
                workspace_id=workspace_id,
                title=title or f"داشبورد هوشمند {ds.name}",
                description=dash_desc,
                theme_id=chosen_theme.id if chosen_theme else None,
                settings_json=settings_payload,
                created_by=user.id,
            )
            db.add(dash)
            db.flush()

        # Pass 1: Persist all DashboardPages so drill_through_page_id can reference `details` page ID
        persisted_pages: list[tuple[DashboardPage, dict[str, Any]]] = []
        details_page_id: Optional[str] = None
        for p_idx, page_spec in enumerate(plan["pages"]):
            pkey = page_spec.get("page_key", f"page_{p_idx}")
            is_det = (pkey == "details")
            page = DashboardPage(
                dashboard_id=dash.id,
                title=page_spec["title"],
                page_order=p_idx,
                is_drill_through=is_det,
                settings_json={"page_key": pkey},
            )
            db.add(page)
            db.flush()
            if is_det:
                details_page_id = page.id
            persisted_pages.append((page, page_spec))

        total_widgets = 0
        for page, page_spec in persisted_pages:
            layout_items: list[dict[str, Any]] = []
            for w_spec in page_spec["widgets"]:
                lay = w_spec["layout"]
                w = Widget(
                    dashboard_id=dash.id,
                    page_id=page.id,
                    title=w_spec["title"],
                    description=w_spec.get("description", ""),
                    chart_type=w_spec["chart_type"],
                    dataset_id=ds.id,
                    layout_x=int(lay.get("x", 0)),
                    layout_y=int(lay.get("y", 0)),
                    layout_w=int(lay.get("w", 6)),
                    layout_h=int(lay.get("h", 8)),
                )
                db.add(w)
                db.flush()
                db.add(
                    WidgetQuery(
                        widget_id=w.id,
                        dataset_id=ds.id,
                        query_mode="visual",
                        visual_spec_json=w_spec["visual_spec"],
                    )
                )
                db.add(
                    WidgetConfig(
                        widget_id=w.id,
                        style_json=w_spec.get("style", {}),
                        visualization_json=w_spec.get("visualization", {}),
                        interactions_json={"cross_filter": True, "drill_down": True},
                        advanced_json=w_spec.get("advanced", {"ai_generated": True}),
                    )
                )
                dt_page_id = details_page_id if (w_spec.get("drill_through_page_key") == "details" and details_page_id and page.id != details_page_id) else None
                if w_spec.get("drill_hierarchy") or dt_page_id:
                    db.add(
                        WidgetDrillRule(
                            widget_id=w.id,
                            hierarchy_json=w_spec.get("drill_hierarchy") or [],
                            drill_through_page_id=dt_page_id,
                        )
                    )
                db.add(
                    DataLineage(
                        source_type="dataset",
                        source_id=ds.id,
                        target_type="widget",
                        target_id=w.id,
                        metadata_json={"relation": "ai_powers"},
                    )
                )
                layout_items.append({
                    "i": w.id,
                    "x": w.layout_x,
                    "y": w.layout_y,
                    "w": w.layout_w,
                    "h": w.layout_h,
                })
                total_widgets += 1

            db.add(DashboardLayout(page_id=page.id, layout_json=layout_items))

        # Add suggested Dashboard Filters
        for flt in plan.get("suggested_filters", []):
            db.add(
                DashboardFilter(
                    dashboard_id=dash.id,
                    page_id=None,
                    scope="dashboard",
                    name=flt["name"],
                    field_name=flt["field_name"],
                    dataset_id=ds.id,
                    filter_type=flt["filter_type"],
                    operator=flt["operator"],
                    default_value_json={},
                )
            )

        db.commit()
        db.refresh(dash)
        cache_manager.invalidate_prefix(f"dash:{user.organization_id}:{dash.id}")
        return dash, {
            "pages_count": len(plan["pages"]),
            "widgets_count": total_widgets,
            "kpis_selected": len(plan["active_kpis"]),
            "domain": plan["domain_detection"],
            "kpi_config": settings_payload["kpi_config"],
        }

    # ------------------------------------------------------------------
    # SEMANTIC OVERRIDE & SEMANTIC LAYER PERSISTENCE
    # ------------------------------------------------------------------
    @staticmethod
    def save_field_overrides(
        db: Session,
        user: User,
        ds: Dataset,
        overrides: list[dict[str, Any]],
    ) -> dict[str, Any]:
        updated: list[str] = []
        fields = {f.field_name: f for f in db.query(DatasetField).filter(DatasetField.dataset_id == ds.id).all()}
        sc = build_data_security_context(db, user, ds.id)

        for ov in overrides:
            fname = str(ov.get("field_name") or "").strip()
            if not fname or fname in sc.hidden_columns or fname not in fields:
                continue
            f_orm = fields[fname]
            cur_ov = dict(f_orm.user_override_json or {})
            if ov.get("semantic_class") in SEMANTIC_CLASS_META:
                sem_cls = ov["semantic_class"]
                cur_ov["semantic_class"] = sem_cls
                f_orm.semantic_class = sem_cls
                meta = SEMANTIC_CLASS_META[sem_cls]
                f_orm.aggregation_policy = ov.get("aggregation_policy") or meta["policy"]
                if sem_cls == "Date/Time":
                    f_orm.semantic_role = "time"
                elif sem_cls == "Geographic":
                    f_orm.semantic_role = "geo"
                elif sem_cls == "Identifier":
                    f_orm.semantic_role = "id"
                elif sem_cls in ("Category", "Status", "Boolean"):
                    f_orm.semantic_role = "dimension"
                else:
                    f_orm.semantic_role = "measure"
                f_orm.classification_confidence = 0.99
            if ov.get("aggregation_policy"):
                cur_ov["aggregation_policy"] = str(ov["aggregation_policy"])
                f_orm.aggregation_policy = str(ov["aggregation_policy"])
            if ov.get("display_name"):
                f_orm.display_name = str(ov["display_name"])[:180]
                cur_ov["display_name"] = f_orm.display_name
            if "target_value" in ov:
                t_val = _safe_float(ov.get("target_value"))
                if t_val is not None:
                    cur_ov["target_value"] = t_val
                else:
                    cur_ov.pop("target_value", None)
            f_orm.user_override_json = cur_ov
            f_orm.version = int(f_orm.version or 1) + 1
            updated.append(fname)

        ds.data_version = int(ds.data_version or 1) + 1
        db.commit()
        cache_manager.invalidate_prefix(f"kpi_deep_prof:{ds.organization_id}:{ds.id}")
        return {"updated_fields": updated, "data_version": ds.data_version}

    @staticmethod
    def register_kpis_to_semantic_layer(
        db: Session,
        user: User,
        ds: Dataset,
        kpis: list[dict[str, Any]],
        related_dataset_ids: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        registered: list[dict[str, Any]] = []
        ds_ids = [ds.id, *(related_dataset_ids or [])]
        for idx, k in enumerate(kpis):
            name = str(k.get("name") or f"KPI {idx + 1}").strip()[:160]
            raw_slug = str(k.get("slug") or k.get("id") or name).strip().lower()
            slug_base = re.sub(r"[^a-z0-9_]+", "_", raw_slug).strip("_") or f"kpi_{int(time.time())}_{idx}"
            slug = f"{ds.id[:6]}_{slug_base}"[:115]
            formula = str(k.get("formula") or "").strip()
            if not formula:
                raise ValueError(f"فرمول شاخص «{name}» خالی است")

            res = validate_and_compile_formula(
                db,
                user,
                dataset_ids=ds_ids,
                formula=formula,
                calculation_type="analytical_metric",
                qualify_with_alias=False,
                auto_safe_divide=False,
            )
            existing = db.query(SemanticMetric).filter(
                SemanticMetric.organization_id == user.organization_id,
                SemanticMetric.slug == slug,
            ).first()

            fmt_type = str(k.get("display_format") or k.get("format_type") or "number")
            meta_payload = {
                "limitations": k.get("limitations") or [],
                "comparison_basis": k.get("comparison_basis"),
                "comparison_mode": k.get("comparison_mode") or "none",
                "display_format": fmt_type,
                "priority": int(k.get("priority") or (idx + 1)),
                "base_fields": k.get("base_fields") or [f["field_name"] for f in res["referenced_fields"]],
                "is_standard": bool(k.get("is_standard", False)),
                "computability_status": k.get("computability_status") or "computable",
                "eligible_definition_fa": k.get("eligible_definition_fa"),
                "delayed_definition_fa": k.get("delayed_definition_fa"),
                "source_datasets": k.get("source_datasets") or [ds.name],
                "favorite": bool(k.get("is_favorite", False)),
                "direction": k.get("direction", "higher_is_better"),
                "score": k.get("score"),
            }
            target_v = _safe_float(k.get("target_value"))
            if existing:
                existing.name = name
                existing.formula = formula
                existing.format_type = fmt_type
                existing.unit = str(k.get("unit") or "")
                existing.category = str(k.get("category") or "analytical")
                existing.confidence = float(k.get("confidence") or 1.0)
                existing.target_value = target_v
                existing.kpi_metadata_json = meta_payload
                existing.dependent_datasets_json = res["referenced_datasets"]
                existing.dependent_fields_json = [f["field_name"] for f in res["referenced_fields"]]
                existing.version = int(existing.version or 1) + 1
                sm = existing
            else:
                sm = SemanticMetric(
                    organization_id=user.organization_id,
                    dataset_id=ds.id,
                    name=name,
                    slug=slug,
                    description=str(k.get("description") or k.get("comparison_basis") or ""),
                    formula=formula,
                    format_type=fmt_type,
                    unit=str(k.get("unit") or ""),
                    metric_kind="kpi_engine",
                    output_data_type=res["output_data_type"],
                    dependent_datasets_json=res["referenced_datasets"],
                    dependent_fields_json=[f["field_name"] for f in res["referenced_fields"]],
                    category=str(k.get("category") or "analytical"),
                    confidence=float(k.get("confidence") or 1.0),
                    target_value=target_v,
                    kpi_metadata_json=meta_payload,
                    created_by=user.id,
                )
                db.add(sm)
                db.flush()

            registered.append({"id": sm.id, "slug": sm.slug, "name": sm.name, "formula": sm.formula})
        db.commit()
        return registered
