import math
import re
from typing import Any, Optional
from sqlalchemy.orm import Session
from app.core.rls_cls import build_data_security_context
from app.engines.data_engine import DataProfiler
from app.engines.query_engine import execute_dataset_sql
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
from app.models.data import Dataset, DatasetField, SemanticMetric
from app.models.identity import User


class AiAnalyticsEngine:
    """
    AI Analytics Engine (Section 14):
    1. Schema profiling & automatic KPI + Chart + Filter dashboard generation
    2. Natural Language Query (NLQ -> SQL + Optimal Chart Recommendation) in EN & FA
    3. Statistical Anomaly Detection (Z-Score) & Trend Forecasting
    """

    @staticmethod
    def recommend_widgets_for_dataset(
        db: Session,
        dataset: Dataset,
        user: Optional[User] = None,
        include_profile: bool = True,
        *,
        related_dataset_ids: Optional[list[str]] = None,
        calendar_mode: str = "jalali",
        domain_override: Optional[str] = None,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        from app.engines.kpi_engine import IntelligentKpiEngine

        plan = IntelligentKpiEngine.build_intelligent_dashboard_plan(
            db,
            dataset,
            user=user,
            calendar_mode=calendar_mode,
            related_dataset_ids=related_dataset_ids,
            domain_override=domain_override,
            auto_generate_kpis=True,
            force_refresh=force_refresh,
        )
        # Flatten pages into backward-compatible suggested_widgets list while exposing full 5-stage analysis
        suggested_widgets: list[dict[str, Any]] = []
        for page in plan["pages"]:
            for w in page["widgets"]:
                suggested_widgets.append({
                    "title": w["title"],
                    "description": w.get("description", ""),
                    "chart_type": w["chart_type"],
                    "layout": w["layout"],
                    "visual_spec": w["visual_spec"],
                    "style": w.get("style", {}),
                    "visualization": w.get("visualization", {}),
                    "advanced": w.get("advanced", {}),
                    "drill_hierarchy": [d["field"] for d in (w.get("drill_hierarchy") or []) if isinstance(d, dict) and d.get("field")],
                    "page_title": page["title"],
                    "page_key": page.get("page_key"),
                })

        sec_ctx = build_data_security_context(db, user, dataset.id) if user is not None else None
        legacy_profile = None
        if include_profile:
            legacy_profile = {
                "table_name": dataset.warehouse_table,
                "row_count": plan["deep_profile"]["row_count"],
                "fields": [
                    {
                        "field_name": f["field_name"],
                        "raw_type": f["raw_type"],
                        "distinct_count": f["distinct_count"],
                        "null_count": f["null_count"],
                        "min": f["min"],
                        "max": f["max"],
                        "avg": f["avg"],
                        "median": f["median"],
                    }
                    for f in plan["deep_profile"]["fields"]
                ],
            }

        return {
            "dataset_id": dataset.id,
            "dataset_name": dataset.name,
            "profile": legacy_profile,
            "deep_profile": plan["deep_profile"],
            "field_classifications": plan["field_classifications"],
            "domain_detection": plan["domain_detection"],
            "kpis": plan["kpis"],
            "recommended_pages": [
                {
                    "title": p["title"],
                    "page_key": p.get("page_key"),
                    "widgets_count": len(p["widgets"]),
                    "chart_types": [w["chart_type"] for w in p["widgets"]],
                }
                for p in plan["pages"]
            ],
            "suggested_widgets": suggested_widgets,
            "suggested_filters": plan["suggested_filters"],
            "multi_source_analysis": plan["kpis"].get("multi_source_analysis", {}),
        }

    @staticmethod
    def generate_dashboard_from_dataset(
        db: Session,
        user: User,
        workspace_id: str,
        dataset: Dataset,
        title: Optional[str] = None,
        *,
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
        dashboard_id: Optional[str] = None,
    ) -> Dashboard:
        from app.engines.kpi_engine import IntelligentKpiEngine

        dash, _meta = IntelligentKpiEngine.generate_and_persist_dashboard(
            db,
            user,
            workspace_id,
            dataset,
            title=title,
            template_mode=template_mode,
            detail_level=detail_level,
            calendar_mode=calendar_mode,
            date_range=date_range,
            max_pages=max_pages,
            theme_id=theme_id,
            selected_kpi_ids=selected_kpi_ids,
            confirmed_kpis=confirmed_kpis,
            auto_generate_kpis=auto_generate_kpis,
            custom_kpis=custom_kpis,
            target_values=target_values,
            related_dataset_ids=related_dataset_ids,
            dashboard_id=dashboard_id,
        )
        return dash

    @staticmethod
    def natural_language_query(
        db: Session,
        user: User,
        question: str,
        dataset: Dataset,
    ) -> dict[str, Any]:
        """
        Translates English or Persian natural-language analytics questions into DuckDB SQL,
        executes them securely with RLS/CLS, and recommends the best visualization.
        """
        q_low = question.lower()
        cols = warehouse.describe_table(dataset.warehouse_table)
        col_names = [c["column_name"] for c in cols]

        # Aggregation keyword detection (EN + FA)
        agg = "SUM"
        if any(k in q_low for k in ("avg", "average", "mean", "میانگین")):
            agg = "AVG"
        elif any(k in q_low for k in ("count", "how many", "تعداد")):
            agg = "COUNT"
        elif any(k in q_low for k in ("max", "highest", "بیشترین")):
            agg = "MAX"
        elif any(k in q_low for k in ("min", "lowest", "کمترین")):
            agg = "MIN"

        # Top N detection
        top_n: Optional[int] = None
        m_top = re.search(r"\b(top|برترین|اول)\s*(\d+)", q_low)
        if m_top:
            top_n = int(m_top.group(2))

        # Match semantic metric or numeric measure
        metrics = db.query(SemanticMetric).filter(SemanticMetric.dataset_id == dataset.id).all()
        chosen_measure: Optional[str] = None
        for sm in metrics:
            if sm.name.lower() in q_low or sm.slug.lower() in q_low:
                chosen_measure = sm.formula
                break

        num_cols = [
            c["column_name"]
            for c in cols
            if any(t in c["column_type"].lower() for t in ("int", "double", "float", "decimal"))
            and not c["column_name"].endswith("_id")
            and c["column_name"] not in ("year", "month", "quarter")
        ]
        fa_measure_hints = {
            "فروش": ["revenue", "sales", "amount", "total"],
            "درآمد": ["revenue", "income", "sales"],
            "هزینه": ["cost", "expense", "amount"],
            "سود": ["profit", "net_profit", "margin"],
            "تعداد": ["quantity", "units", "count"],
            "انتظار": ["wait_hours", "wait", "delay", "duration"],
            "حقوق": ["salary", "payroll", "wage"],
            "مالیات": ["tax", "vat"],
            "تخفیف": ["discount"],
            "موجودی": ["inventory", "stock", "balance"],
            "بیمار": ["patient"],
        }
        explicit_metric_concept: Optional[str] = None
        for fa_k in fa_measure_hints:
            if fa_k in q_low:
                explicit_metric_concept = fa_k
                break

        if not chosen_measure:
            for c in num_cols:
                if c.replace("_", " ") in q_low or c in q_low:
                    chosen_measure = f'{agg}("{c}")'
                    break
        if not chosen_measure:
            for fa_k, candidates in fa_measure_hints.items():
                if fa_k in q_low:
                    for cand in candidates:
                        for c in num_cols:
                            if cand in c:
                                chosen_measure = f'{agg}("{c}")'
                                break
                        if chosen_measure:
                            break
        if not chosen_measure and explicit_metric_concept and explicit_metric_concept != "تعداد":
            return {
                "status": "needs_data",
                "question": question,
                "answer_fa": (
                    f"در مجموعه‌داده «{dataset.name}» هیچ ستون یا شاخصی متناظر با «{explicit_metric_concept}» یافت نشد. "
                    f"برای جلوگیری از پاسخ نادرست، محاسبه‌ای انجام نشد. می‌توانید از ستون‌های موجود استفاده کنید: "
                    f"{', '.join(num_cols[:8]) or 'ندارد'}."
                ),
                "missing_concept": explicit_metric_concept,
                "available_measures": num_cols,
                "available_dimensions": [c for c in col_names if c not in num_cols],
                "source_datasets": [dataset.name],
                "generated_sql": None,
                "recommended_chart_type": "kpi",
                "result": {"columns": [], "rows": [], "row_count": 0},
            }
        if not chosen_measure:
            chosen_measure = f'{agg}("{num_cols[0]}")' if (num_cols and agg != "COUNT") else "COUNT(*)"

        # Dimension matching (EN + FA)
        dim_cols = [c["column_name"] for c in cols if c["column_name"] not in num_cols and not c["column_name"].endswith("_id")]
        fa_dim_hints = {
            "منطقه": ["region", "area"],
            "استان": ["province", "state", "region"],
            "شهر": ["city"],
            "دپارتمان": ["department", "dept"],
            "بخش": ["department", "division"],
            "محصول": ["product", "category"],
            "دسته": ["category"],
            "ماه": ["month", "month_name", "order_date"],
            "سال": ["year"],
            "تاریخ": ["order_date", "transaction_date", "date"],
            "کشور": ["country"],
            "گام": ["step_name", "activity"],
            "مرحله": ["step_name", "activity"],
            "کارتابل": ["assignee_name", "assignee"],
            "فرآیند": ["process_name"],
        }
        chosen_dim: Optional[str] = None
        for c in dim_cols:
            if c.replace("_", " ") in q_low or c in q_low:
                chosen_dim = c
                break
        if not chosen_dim:
            for fa_k, candidates in fa_dim_hints.items():
                if fa_k in q_low:
                    for cand in candidates:
                        for c in dim_cols:
                            if cand in c:
                                chosen_dim = c
                                break
                        if chosen_dim:
                            break

        if not chosen_dim and any(k in q_low for k in ("by ", "بر اساس", "به تفکیک", "per ", "trend", "روند")):
            for candidate in ("region", "department", "category", "step_name", "month", "year", "product"):
                if candidate in col_names:
                    chosen_dim = candidate
                    break

        tname = dataset.warehouse_table
        limit_val = top_n or 50
        if chosen_dim:
            sql = (
                f'SELECT "{chosen_dim}", {chosen_measure} AS "value" '
                f'FROM "{tname}" GROUP BY "{chosen_dim}" '
                f'ORDER BY "value" DESC LIMIT {limit_val}'
            )
            chart_type = "line" if any(k in chosen_dim for k in ("date", "month", "year", "quarter")) else "bar"
        else:
            sql = f'SELECT {chosen_measure} AS "value" FROM "{tname}"'
            chart_type = "kpi"

        result = execute_dataset_sql(db, user, sql, dataset=dataset, use_cache=False)
        rows = result.get("rows") or []
        if not rows:
            answer_fa = f"در مجموعه‌داده «{dataset.name}» هیچ داده‌ای مطابق با این پرسش یافت نشد."
        elif chosen_dim:
            top_items = [f"{r.get(chosen_dim)}: {r.get('value')}" for r in rows[:3]]
            answer_fa = f"بر اساس منبع «{dataset.name}» ({result.get('row_count', len(rows))} گروه): " + " | ".join(top_items)
        else:
            val = rows[0].get("value")
            answer_fa = f"مقدار محاسبه‌شده از منبع «{dataset.name}»: {val}"

        return {
            "status": "answered",
            "question": question,
            "answer_fa": answer_fa,
            "generated_sql": sql,
            "recommended_chart_type": chart_type,
            "x_field": chosen_dim,
            "y_field": "value",
            "source_datasets": [dataset.name],
            "confidence": 0.92,
            "limitations": [],
            "result": result,
        }

    @staticmethod
    def detect_anomalies_and_forecast(
        values: list[float],
        labels: list[str],
        forecast_periods: int = 3,
    ) -> dict[str, Any]:
        if len(values) < 3:
            return {"anomalies": [], "forecast": [], "summary": "Insufficient points for statistical analysis"}

        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        std = math.sqrt(variance) if variance > 0 else 1.0

        anomalies = []
        for idx, val in enumerate(values):
            z = (val - mean) / std
            if abs(z) >= 1.75:
                anomalies.append(
                    {
                        "index": idx,
                        "label": labels[idx] if idx < len(labels) else str(idx),
                        "value": val,
                        "z_score": round(z, 2),
                        "direction": "spike" if z > 0 else "drop",
                    }
                )

        # Simple Ordinary Least Squares Linear Trend Forecast
        n = len(values)
        xs = list(range(n))
        sum_x = sum(xs)
        sum_y = sum(values)
        sum_xy = sum(x * y for x, y in zip(xs, values))
        sum_x2 = sum(x * x for x in xs)
        denom = n * sum_x2 - sum_x * sum_x
        slope = (n * sum_xy - sum_x * sum_y) / denom if denom != 0 else 0.0
        intercept = (sum_y - slope * sum_x) / n

        forecast = []
        for step in range(1, forecast_periods + 1):
            fx = n - 1 + step
            fy = max(0.0, round(intercept + slope * fx, 2))
            forecast.append({"label": f"Forecast +{step}", "value": fy})

        trend_dir = "upward" if slope > 0 else ("downward" if slope < 0 else "flat")
        return {
            "mean": round(mean, 2),
            "std_dev": round(std, 2),
            "trend_slope": round(slope, 3),
            "trend_direction": trend_dir,
            "anomalies": anomalies,
            "forecast": forecast,
        }

    @staticmethod
    def _normalize_text(text: str) -> str:
        s = (text or "").strip()
        for a, b in (
            ("ي", "ی"),
            ("ى", "ی"),
            ("ك", "ک"),
            ("\u200c", " "),
            ("۰", "0"),
            ("۱", "1"),
            ("۲", "2"),
            ("۳", "3"),
            ("۴", "4"),
            ("۵", "5"),
            ("۶", "6"),
            ("۷", "7"),
            ("۸", "8"),
            ("۹", "9"),
        ):
            s = s.replace(a, b)
        s = re.sub(r"\s+", " ", s)
        return s

    @staticmethod
    def _norm_col_text(val: Any) -> str:
        s = str(val or "").strip().lower()
        for a, b in (
            ("ي", "ی"),
            ("ى", "ی"),
            ("ك", "ک"),
            ("آ", "ا"),
            ("أ", "ا"),
            ("إ", "ا"),
            ("\u200c", " "),
            ("\u200f", ""),
            ("_", " "),
            ("-", " "),
        ):
            s = s.replace(a, b)
        return re.sub(r"\s+", " ", s).strip()

    @staticmethod
    def _infer_bpmn_config_from_cols(cols_info: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
        """Detects BPMN process columns (process, step, wait time, SLA, assignee, status, timestamps) from dataset columns."""
        if not cols_info:
            return None

        norm_cols: list[dict[str, str]] = []
        for c in cols_info:
            cname = c.get("name") or c.get("field_name") or c.get("column_name") or ""
            if not cname:
                continue
            clabel = c.get("label") or c.get("display_name") or cname
            norm_cols.append({
                "name": str(cname),
                "n_name": AiAnalyticsEngine._norm_col_text(cname),
                "n_label": AiAnalyticsEngine._norm_col_text(clabel),
            })

        if not norm_cols:
            return None

        def _pick(
            hints: tuple[str, ...],
            *,
            exclude_names: set[str] | None = None,
            exclude_tokens: tuple[str, ...] = (),
        ) -> Optional[str]:
            ex = exclude_names or set()
            norm_hints = [AiAnalyticsEngine._norm_col_text(h) for h in hints]
            norm_ex_toks = [AiAnalyticsEngine._norm_col_text(t) for t in exclude_tokens if t]

            def _blocked(item: dict[str, str], hint: str) -> bool:
                if item["name"] in ex:
                    return True
                hay = f"{item['n_name']} {item['n_label']}"
                for t in norm_ex_toks:
                    if t in hay and t not in hint:
                        return True
                return False

            # Pass 1: exact match on normalized name or label
            for h in norm_hints:
                for item in norm_cols:
                    if _blocked(item, h):
                        continue
                    if item["n_name"] == h or item["n_label"] == h:
                        return item["name"]

            # Pass 2: phrase substring match
            for h in norm_hints:
                for item in norm_cols:
                    if _blocked(item, h):
                        continue
                    if h in item["n_name"] or h in item["n_label"]:
                        return item["name"]
            return None

        # 1) Process name column (must NOT match step/stage/activity/code/time columns like "مرحله فرایند" or "گام فرایند")
        process_col = _pick(
            (
                "process_name", "workflow_name", "process_title", "process_type",
                "نام فرایندهای سازمان", "نام فرایند سازمان", "فرایندهای سازمان",
                "نام فرایند", "عنوان فرایند", "نوع فرایند", "فرایند",
                "process", "workflow",
            ),
            exclude_tokens=("step", "stage", "activity", "task", "node", "گام", "مرحله", "فعالیت", "کد", "شناسه", "id", "code", "مدت", "زمان", "تاریخ", "wait", "sla", "time"),
        )

        # 2) Step / activity column (must exclude process_col if found)
        used = {process_col} if process_col else set()
        step_col = _pick(
            (
                "step_name", "activity_name", "stage_name", "task_name",
                "گامهای هر فرایند", "گام های هر فرایند", "نام گام", "نام مرحله", "گام فرایند", "مرحله فرایند",
                "گام", "مرحله", "فعالیت", "step", "stage", "activity", "task",
            ),
            exclude_names=used,
            exclude_tokens=("کد", "شناسه", "id", "code", "مدت", "زمان", "تاریخ", "wait", "sla", "تعداد"),
        )
        if not step_col:
            step_col = _pick(("step_name", "step", "stage", "activity", "task", "گام", "مرحله", "فعالیت"), exclude_names=used)
        if step_col:
            used.add(step_col)

        # 3) Wait / duration / delay column
        wait_col = _pick(
            (
                "wait_hours", "wait_time", "waiting_hours", "duration_hours", "cycle_hours", "delay_hours",
                "مدت انتظار", "زمان انتظار", "میزان انتظار", "تاخیر", "توقف", "مدت زمان", "مدت",
                "wait", "duration", "delay", "latency",
            ),
            exclude_names=used,
        )

        # 4) Assignee / cartable owner / person column (NEVER steal process_col or step_col!)
        assignee_col = _pick(
            (
                "assignee_name", "assignee", "actor_name", "actor", "handler", "operator", "agent",
                "employee_name", "employee", "staff", "owner_name", "owner", "person", "username", "user_name", "user",
                "نام کارشناس", "صاحب کارتابل", "در کارتابل", "کارتابل", "نام مجری", "مجری",
                "اقدام کننده", "کاربر اقدام کننده", "تحویل گیرنده", "مسئول کارتابل", "نام مسئول", "مسئول",
                "نام و نام خانوادگی", "نام خانوادگی", "نام کامل", "نام همکار", "نام کارمند", "نام شخص", "نام فرد", "نام کاربر",
                "کارشناس", "متصدی", "عامل", "کاربر", "شخص", "پشتیبان", "درخواست دهنده",
            ),
            exclude_names=used,
            exclude_tokens=("فرایند", "فرآیند", "گام", "مرحله", "واحد", "بخش", "دپارتمان", "شرکت", "سمت", "نقش", "نقش کارتابل"),
        )

        sla_col = _pick(("sla_hours", "sla", "target_hours", "max_hours", "سقف مجاز", "سقف", "مهلت", "مجاز", "هدف"), exclude_names=used)
        status_col = _pick(("status", "state", "وضعیت درخواست", "وضعیت گام", "وضعیت"), exclude_names=used)
        req_col = _pick(
            (
                "request_code", "request_id", "case_id", "instance_id", "process_instance_id", "ticket_id", "ticket_code",
                "کد درخواست", "شماره درخواست", "شناسه درخواست", "شماره پرونده", "کد پرونده", "شناسه پرونده", "کد رهگیری",
                "درخواست", "پرونده", "case", "request", "ticket",
            ),
            exclude_names=used,
        )
        req_title_col = _pick(("request_title", "case_title", "title", "subject", "description", "عنوان درخواست", "موضوع درخواست", "عنوان", "موضوع", "شرح"), exclude_names=used)
        entered_at_col = _pick(("entered_at", "event_time", "start_time", "created_at", "timestamp", "date", "تاریخ ورود", "زمان ورود", "تاریخ ثبت", "تاریخ", "زمان"), exclude_names=used)
        dept_col = _pick(("department", "unit", "org_unit", "واحد سازمانی", "واحد", "بخش", "دپارتمان", "اداره", "معاونت"), exclude_names=used)
        role_col = _pick(("assignee_role", "actor_role", "role", "position", "سمت", "نقش", "پست"), exclude_names=used)
        step_id_col = _pick(("step_id", "node_id", "task_id", "کد گام", "کد مرحله", "شناسه گام"), exclude_names=used)

        # Must look like a process dataset (step + any of process / wait / assignee / req)
        if not step_col or (not process_col and not wait_col and not assignee_col and not req_col):
            return None

        return {
            "process_field": process_col,
            "step_field": step_col,
            "step_id_field": step_id_col,
            "wait_field": wait_col,
            "sla_field": sla_col,
            "assignee_field": assignee_col,
            "assignee_role_field": role_col,
            "request_id_field": req_col,
            "request_title_field": req_title_col,
            "entered_at_field": entered_at_col,
            "status_field": status_col,
            "department_field": dept_col,
            "heatmap_enabled": True,
            "heatmap_metric": "avg_wait" if wait_col else "pending_count",
        }

    @staticmethod
    def _extract_chart_type(norm_low: str) -> Optional[str]:
        chart_rules: list[tuple[list[str], str]] = [
            (["bpmn", "گلوگاه", "گردش کار", "نقشه فرآیند", "نقشه فرایند", "نمودار فرآیند", "نمودار فرایند", "کارتابل فرآیند", "فرآیند خرید", "فرایند خرید"], "bpmn"),
            (["میله ای افقی", "نمودار افقی", "ستونی افقی", "horizontal bar", "horizontal_bar"], "horizontal_bar"),
            (["میله ای پشته", "ستونی پشته", "پشته ای", "stacked bar", "stacked_bar"], "stacked_bar"),
            (["میله ای", "ستونی", "نمودار میله", "ویجت میله", "bar chart", "bar"], "bar"),
            (["مساحتی پشته", "stacked area", "stacked_area"], "stacked_area"),
            (["مساحتی", "سطحی", "area chart", "area"], "area"),
            (["خطی", "روند", "نمودار خط", "line chart", "line"], "line"),
            (["دونات", "حلقوی", "donut"], "donut"),
            (["دایره ای", "پای", "نمودار دایره", "pie chart", "pie"], "pie"),
            (["گیج", "عقربه ای", "سرعت سنج", "gauge"], "gauge"),
            (["جدول محوری", "پیوت", "pivot"], "pivot_table"),
            (["جدول", "لیست", "table"], "table"),
            (["آبشاری", "waterfall"], "waterfall"),
            (["قیف", "فانل", "funnel"], "funnel"),
            (["رادار", "عنکبوتی", "radar"], "radar"),
            (["پراکندگی", "scatter"], "scatter"),
            (["حبابی", "bubble"], "bubble"),
            (["درختی", "treemap"], "treemap"),
            (["نقشه", "جغرافیا", "geo_map", "map"], "geo_map"),
            (["حرارتی", "هیت مپ", "heatmap"], "heatmap"),
            (["ترکیبی", "combo"], "combo"),
            (["کارت شاخص", "کارت عدد", "تک عددی", "شاخص کل", "کارت kpi", "kpi"], "kpi"),
        ]
        for keywords, ct in chart_rules:
            for kw in keywords:
                if kw in norm_low:
                    return ct
        return None

    @staticmethod
    def _get_dataset_schema(
        db: Session, ds: Dataset, user: Optional[User] = None
    ) -> tuple[list[dict[str, str]], list[str], list[str]]:
        sec_ctx = build_data_security_context(db, user, ds.id) if user is not None else None
        hidden = sec_ctx.hidden_columns if sec_ctx else set()
        fields = [
            f
            for f in db.query(DatasetField).filter(DatasetField.dataset_id == ds.id).all()
            if f.field_name not in hidden
        ]
        cols_info: list[dict[str, str]] = []
        if fields:
            for f in fields:
                cols_info.append(
                    {
                        "name": f.field_name,
                        "label": AiAnalyticsEngine._normalize_text(f.display_name or f.field_name),
                        "role": f.semantic_role or "dimension",
                        "type": f.data_type or "varchar",
                    }
                )
        else:
            raw_cols = warehouse.describe_table(ds.warehouse_table)
            for c in raw_cols:
                cname = c["column_name"]
                if cname in hidden:
                    continue
                ctype = (c.get("column_type") or "").lower()
                is_num = (
                    any(k in ctype for k in ("int", "double", "float", "decimal", "numeric"))
                    and not cname.endswith("_id")
                    and cname not in ("year", "month", "quarter", "day")
                )
                role = "measure" if is_num else ("time" if cname in ("year", "quarter", "month", "day", "date") else "dimension")
                cols_info.append(
                    {
                        "name": cname,
                        "label": cname.replace("_", " "),
                        "role": role,
                        "type": ctype,
                    }
                )
        num_cols = [c["name"] for c in cols_info if c["role"] == "measure"]
        dim_cols = [
            c["name"]
            for c in cols_info
            if c["role"] != "measure" and not c["name"].endswith("_id") and c["name"] != "id"
        ]
        return cols_info, num_cols, dim_cols

    @staticmethod
    def _match_column(mention: str, cols_info: list[dict[str, str]], role_filter: Optional[str] = None) -> Optional[str]:
        m_norm = AiAnalyticsEngine._normalize_text(mention).lower().strip(" «»\"'،,.؟?")
        if not m_norm:
            return None

        candidates = cols_info
        if role_filter == "measure":
            candidates = [c for c in cols_info if c["role"] == "measure"]
        elif role_filter == "dim":
            candidates = [c for c in cols_info if c["role"] != "measure" and not c["name"].endswith("_id") and c["name"] != "id"]

        # Direct exact / substring match on column name or display label
        for c in candidates:
            cn = c["name"].lower()
            cl = c["label"].lower()
            if m_norm == cn or m_norm == cn.replace("_", " ") or m_norm == cl:
                return c["name"]
        for c in candidates:
            cn = c["name"].lower().replace("_", " ")
            cl = c["label"].lower()
            if len(m_norm) >= 2 and (m_norm in cn or cn in m_norm or m_norm in cl or cl in m_norm):
                return c["name"]

        # Bilingual synonym matching (Persian <-> English)
        fa_en_synonyms: dict[str, list[str]] = {
            "فروش": ["revenue", "sales", "total_sales"],
            "درامد": ["revenue", "income", "sales"],
            "درآمد": ["revenue", "income", "sales"],
            "سود": ["profit", "net_profit", "margin"],
            "هزینه": ["cost", "expense", "amount", "budget"],
            "بودجه": ["budget", "planned"],
            "تعداد": ["quantity", "qty", "units", "count", "headcount", "employees"],
            "مقدار": ["quantity", "amount", "value"],
            "مبلغ": ["amount", "revenue", "cost", "price", "total"],
            "حقوق": ["salary", "payroll", "wage"],
            "پیمانکار": ["contractor", "vendor", "supplier", "partner", "پیمانکار"],
            "نام پیمانکار": ["contractor_name", "contractor", "vendor_name", "vendor", "supplier", "پیمانکار"],
            "مشتری": ["customer", "client", "account"],
            "منطقه": ["region", "area", "zone"],
            "استان": ["province", "state", "region"],
            "شهر": ["city"],
            "کشور": ["country"],
            "محصول": ["product", "item"],
            "دسته": ["category", "group"],
            "دسته بندی": ["category"],
            "دپارتمان": ["department", "dept"],
            "بخش": ["department", "division", "unit"],
            "کانال": ["channel"],
            "سال": ["year"],
            "فصل": ["quarter"],
            "ماه": ["month", "month_name"],
            "روز": ["day"],
            "تاریخ": ["order_date", "transaction_date", "date", "created_at"],
            "کارمند": ["employee", "staff", "name"],
            "پیمانکار": ["contractor", "vendor", "supplier", "پیمانکار"],
            "نام پیمانکار": ["contractor_name", "contractor", "پیمانکار"],
            "حوزه پروژه": ["project_type", "حوزه"],
            "تعداد پروژه": ["project_count", "تعداد پروژه"],
            "کار انجام شده": ["completed_tasks", "انجام"],
            "امتیاز عملکرد": ["performance_score", "امتیاز"],
            "امتیاز": ["performance_score", "score", "امتیاز"],
            "مبلغ قرارداد": ["contract_value", "مبلغ قرارداد"],
            "عملکرد": ["performance_score", "performance", "score", "revenue", "efficiency"],
            "گام": ["step_name", "step", "stage", "گام", "مرحله"],
            "مرحله": ["step_name", "step", "stage", "گام", "مرحله"],
            "فرآیند": ["process_name", "step_name", "فرآیند"],
            "فرایند": ["process_name", "step_name", "فرآیند"],
            "زمان انتظار": ["wait_hours", "wait_time", "wait", "انتظار"],
            "انتظار": ["wait_hours", "wait_time", "wait", "انتظار"],
            "تاخیر": ["wait_hours", "delay", "تاخیر"],
            "کارتابل": ["assignee_name", "assignee", "کارتابل", "مسئول"],
            "مسئول": ["assignee_name", "assignee", "owner", "مسئول"],
            "وضعیت": ["status", "state", "وضعیت"],
            "درخواست": ["request_code", "request_id", "درخواست"],
        }
        for fa_key, en_list in fa_en_synonyms.items():
            if fa_key in m_norm or m_norm in fa_key:
                for en_kw in en_list:
                    for c in candidates:
                        if en_kw in c["name"].lower() or en_kw in c["label"].lower():
                            return c["name"]
        return None

    @staticmethod
    def plan_or_create_widget_from_text(
        db: Session,
        user: User,
        prompt: str,
        dashboard_id: Optional[str] = None,
        page_id: Optional[str] = None,
        dataset_id: Optional[str] = None,
        clarifications: Optional[dict[str, Any]] = None,
        create_widget: bool = True,
    ) -> dict[str, Any]:
        """
        Conversational Persian & English Text-to-Widget/KPI Builder:
        Parses natural-language widget creation requests, detects ambiguities (missing/unknown
        dataset, dimension column, measure/aggregation, or chart type), returns Persian Q&A
        clarification prompts when ambiguous, or creates & previews the widget immediately.
        """
        from app.api.deps import enforce_permission
        from app.core.rls_cls import apply_cls_and_masking, build_data_security_context
        from app.engines.query_engine import build_visual_query_sql
        from app.models.data import DataLineage

        clarifications = dict(clarifications or {})
        raw_prompt = (prompt or "").strip()
        free_reply = AiAnalyticsEngine._normalize_text((clarifications.get("free_text") or "").strip())
        norm = AiAnalyticsEngine._normalize_text(raw_prompt)
        norm_low = norm.lower()

        datasets = db.query(Dataset).filter(Dataset.organization_id == user.organization_id).all()
        if not datasets:
            return {
                "status": "needs_clarification",
                "message_fa": "هیچ منبع داده (دیتاست) در سیستم ثبت نشده است. لطفاً ابتدا یک فایل Excel/CSV یا منبع داده را در بخش منابع داده بارگذاری کنید.",
                "questions": [],
                "parsed": {},
            }

        # 1. Extract explicit widget title ("تحت عنوان X", "با عنوان X", "title: X") from primary prompt
        extracted_title: Optional[str] = clarifications.get("title")
        if not extracted_title:
            m_title = re.search(
                r"(?:تحت\s+عنوان|با\s+عنوان|به\s+نام|با\s+نام|عنوان\s*[:：]|title\s*[:：])\s*[\"«']?([^\"»'\n]+?)[\"»']?(?=\s*(?:بساز|ایجاد\s*کن|تولید\s*کن|درست\s*کن|اضافه\s*کن|بگذار|نشان\s*بده|نمایش\s*بده|$))",
                norm,
                flags=re.IGNORECASE,
            )
            if m_title:
                extracted_title = m_title.group(1).strip(" ،,.«»\"'")

        # 2. Extract explicit source mention ("از روی منبع X", "از دیتاست X", "from source X")
        source_mention: Optional[str] = None
        m_src = re.search(
            r"(?:از\s+روی|از|روی)\s+(?:منبع\s+داده|منبع|دیتاست|جدول|source|dataset|table)\s+[\"«']?(.+?)[\"»']?(?=\s+(?:یک|یه|ویجت|نمودار|کارت|شاخص|جدول|بر\s+اساس|به\s+تفکیک|بر\s+حسب|تحت\s+عنوان|با\s+عنوان|بساز|ایجاد|تولید|by|per|$))",
            norm,
            flags=re.IGNORECASE,
        )
        if m_src:
            source_mention = m_src.group(1).strip(" ،,.«»\"'")

        # Remove source/title phrases before scanning for aggregation/dimension keywords so
        # e.g. "از روی منبع تعداد ... تحت عنوان عملکرد پیمانکار" doesn't misread "تعداد" as COUNT!
        semantic_body = norm
        if m_src:
            semantic_body = semantic_body.replace(m_src.group(0), " ")
        if extracted_title:
            semantic_body = re.sub(
                r"(?:تحت\s+عنوان|با\s+عنوان|به\s+نام|با\s+نام|عنوان\s*[:：]|title\s*[:：])\s*[\"«']?"
                + re.escape(extracted_title)
                + r"[\"»']?",
                " ",
                semantic_body,
                flags=re.IGNORECASE,
            )
        if free_reply:
            semantic_body = f"{semantic_body} {free_reply}".strip()
        semantic_low = AiAnalyticsEngine._normalize_text(semantic_body).lower()

        # 3. Extract dimension phrase ("بر اساس X", "به تفکیک X", "by X")
        dim_mention: Optional[str] = None
        m_dim = re.search(
            r"(?:بر\s+اساس|به\s+تفکیک|بر\s+حسب|برای\s+هر|روی\s+محور|by|per)\s+[\"«']?(.+?)[\"»']?(?=\s+(?:بساز|ایجاد|تولید|درست|اضافه|نشان|نمایش|تحت\s+عنوان|با\s+عنوان|به\s+نام|با\s+نام|از\s+روی|از\s+منبع|title|$))",
            semantic_body,
            flags=re.IGNORECASE,
        )
        if m_dim:
            dim_mention = m_dim.group(1).strip(" ،,.«»\"'")

        # 4. Resolve dataset
        chosen_ds: Optional[Dataset] = None
        ds_id_override = clarifications.get("dataset_id") or dataset_id
        if ds_id_override:
            chosen_ds = next((d for d in datasets if d.id == ds_id_override), None)

        source_not_found_name: Optional[str] = None
        if not chosen_ds and source_mention:
            sm_low = source_mention.lower()
            exact_hits = [
                d
                for d in datasets
                if sm_low == d.name.lower()
                or sm_low in d.name.lower()
                or d.name.lower() in sm_low
                or sm_low in d.warehouse_table.lower()
            ]
            # Bilingual source hints
            if not exact_hits:
                src_synonyms = {
                    "فروش": ["sales", "revenue", "فروش"],
                    "هزینه": ["expense", "cost", "budget", "هزینه"],
                    "منابع انسانی": ["hr", "workforce", "employee", "پرسنل"],
                    "نیروی انسانی": ["hr", "workforce", "employee", "پرسنل"],
                    "کارکنان": ["hr", "workforce", "employee"],
                    "فرآیند": ["bpmn", "process", "فرآیند"],
                    "فرایند": ["bpmn", "process", "فرآیند"],
                    "bpmn": ["bpmn", "process", "فرآیند"],
                    "گلوگاه": ["bpmn", "process", "فرآیند"],
                    "کارتابل": ["bpmn", "process", "فرآیند"],
                }
                for sk, targets in src_synonyms.items():
                    if sk in sm_low:
                        exact_hits = [
                            d for d in datasets if any(tg in d.name.lower() or tg in d.warehouse_table.lower() for tg in targets)
                        ]
                        if exact_hits:
                            break
            if len(exact_hits) == 1:
                chosen_ds = exact_hits[0]
            elif len(exact_hits) == 0:
                source_not_found_name = source_mention

        # Try inferring unique dataset from explicit dimension or measure if no source mention failed
        if not chosen_ds and not source_not_found_name:
            if len(datasets) == 1:
                chosen_ds = datasets[0]
            else:
                matching_ds: list[Dataset] = []
                probe_text = dim_mention or semantic_low
                for d in datasets:
                    cinfo, _, _ = AiAnalyticsEngine._get_dataset_schema(db, d, user=user)
                    if dim_mention and AiAnalyticsEngine._match_column(dim_mention, cinfo, role_filter="dim"):
                        matching_ds.append(d)
                    elif not dim_mention and AiAnalyticsEngine._match_column(probe_text, cinfo):
                        matching_ds.append(d)
                if len(matching_ds) == 1:
                    chosen_ds = matching_ds[0]
                elif not dim_mention:
                    chosen_ds = AiAnalyticsEngine._match_dataset_from_text(datasets, semantic_low)

        # 5. Detect chart type & aggregation from semantic body
        chart_type = clarifications.get("chart_type") or AiAnalyticsEngine._extract_chart_type(semantic_low)
        agg: Optional[str] = clarifications.get("aggregation")
        if not agg:
            if any(k in semantic_low for k in ("میانگین", "متوسط", "avg", "average", "mean")):
                agg = "AVG"
            elif any(k in semantic_low for k in ("بیشترین", "حداکثر", "ماکزیمم", "max", "highest")):
                agg = "MAX"
            elif any(k in semantic_low for k in ("کمترین", "حداقل", "مینیمم", "min", "lowest")):
                agg = "MIN"
            elif any(k in semantic_low for k in ("مجموع", "جمع", "سرجمع", "sum", "total")):
                agg = "SUM"
            elif any(k in semantic_low for k in ("شمارش", "تعداد رکورد", "تعداد کل", "چند تا", "count", "how many")):
                agg = "COUNT"
            elif "تعداد" in semantic_low and not any(
                col_phrase in semantic_low for col_phrase in ("تعداد پروژه", "تعداد کار", "تعداد قرارداد")
            ):
                agg = "COUNT"

        questions: list[dict[str, Any]] = []

        # Question A: Ambiguous or missing dataset
        if not chosen_ds:
            q_text = (
                f"منبع داده «{source_not_found_name}» در سیستم پیدا نشد. لطفاً مشخص کنید از کدام منبع داده استفاده کنم؟"
                if source_not_found_name
                else "چند منبع داده در سیستم وجود دارد؛ کدام منبع داده (دیتاست) را برای ساخت این ویجت انتخاب می‌کنید؟"
            )
            questions.append(
                {
                    "key": "dataset_id",
                    "question": q_text,
                    "options": [
                        {"value": d.id, "label": d.name, "hint": f"{d.row_count} ردیف"}
                        for d in datasets
                    ],
                }
            )
            return {
                "status": "needs_clarification",
                "message_fa": "برای ساخت دقیق ویجت، ابتدا منبع داده را مشخص کنید:",
                "questions": questions,
                "parsed": {
                    "title": extracted_title,
                    "chart_type": chart_type,
                    "source_mention": source_mention,
                    "dim_mention": dim_mention,
                    "aggregation": agg,
                },
            }

        # Now we have chosen_ds -> inspect its real columns!
        cols_info, num_cols, dim_cols = AiAnalyticsEngine._get_dataset_schema(db, chosen_ds, user=user)

        # Question B: Chart type if completely unspecified
        if not chart_type:
            questions.append(
                {
                    "key": "chart_type",
                    "question": "نوع ویجت / نمودار مشخص نیست. چه نوع نمایشی بسازم؟",
                    "options": [
                        {"value": "bar", "label": "نمودار میله‌ای (Bar)"},
                        {"value": "bpmn", "label": "فرآیند BPMN و کشف گلوگاه‌ها"},
                        {"value": "kpi", "label": "کارت شاخص عددی (KPI)"},
                        {"value": "line", "label": "نمودار خطی (Line)"},
                        {"value": "pie", "label": "نمودار دایره‌ای (Pie)"},
                        {"value": "table", "label": "جدول داده (Table)"},
                    ],
                }
            )

        inferred_bpmn = AiAnalyticsEngine._infer_bpmn_config_from_cols(cols_info)

        # 6. Resolve dimension (x_field) when chart is not single-value KPI/gauge
        x_field: Optional[str] = clarifications.get("x_field")
        if x_field == "__none__":
            x_field = None
        elif not x_field and chart_type == "bpmn":
            x_field = (inferred_bpmn or {}).get("step_field") or (dim_cols[0] if dim_cols else None)
        elif not x_field and chart_type not in ("kpi", "gauge"):
            if dim_mention:
                x_field = AiAnalyticsEngine._match_column(dim_mention, cols_info, role_filter="dim")
                if not x_field:
                    # Maybe user named a numeric/year column as dimension
                    x_field = AiAnalyticsEngine._match_column(dim_mention, cols_info)
                if not x_field:
                    questions.append(
                        {
                            "key": "x_field",
                            "question": f"فیلد «{dim_mention}» در منبع «{chosen_ds.name}» پیدا نشد. کدام ستون را برای دسته‌بندی (محور X) انتخاب می‌کنید؟",
                            "options": [
                                {"value": c["name"], "label": c["label"], "hint": c["name"]}
                                for c in cols_info
                                if c["name"] in dim_cols
                            ][:10],
                        }
                    )
            else:
                # No dimension mentioned in text for a non-KPI chart
                x_field = AiAnalyticsEngine._match_column(semantic_low, cols_info, role_filter="dim")
                if not x_field and dim_cols and chart_type != "table":
                    questions.append(
                        {
                            "key": "x_field",
                            "question": f"نمودار بر اساس کدام ستون از «{chosen_ds.name}» گروه‌بندی شود؟",
                            "options": [
                                {"value": c["name"], "label": c["label"], "hint": c["name"]}
                                for c in cols_info
                                if c["name"] in dim_cols
                            ][:10],
                        }
                    )

        # 7. Resolve measure (y_field) + aggregation
        y_field: Optional[str] = clarifications.get("y_field")
        if not y_field:
            # Scan semantic body (excluding dimension mention) for measure column
            measure_probe = semantic_low
            if dim_mention:
                measure_probe = measure_probe.replace(dim_mention.lower(), " ")
            y_field = AiAnalyticsEngine._match_column(measure_probe, cols_info, role_filter="measure")

            # Also check semantic metrics
            if not y_field:
                metrics = db.query(SemanticMetric).filter(SemanticMetric.dataset_id == chosen_ds.id).all()
                for sm in metrics:
                    if sm.name.lower() in measure_probe or sm.slug.lower() in measure_probe:
                        # Extract column inside formula if possible e.g. SUM("revenue")
                        m_col = re.search(r'"([A-Za-z0-9_]+)"', sm.formula or "")
                        if m_col and m_col.group(1) in num_cols:
                            y_field = m_col.group(1)
                            break

        # If user specified COUNT without a numeric field, COUNT(*) is completely clear!
        if agg == "COUNT" and not y_field:
            y_field = "*"

        if chart_type == "bpmn" and not y_field:
            y_field = (inferred_bpmn or {}).get("wait_field") or (num_cols[0] if num_cols else "*")
            if y_field != "*" and not agg:
                agg = "AVG"

        # If measure is still missing/ambiguous
        if not y_field:
            if len(num_cols) == 1 and agg in ("SUM", "AVG", "MAX", "MIN"):
                y_field = num_cols[0]
            elif len(num_cols) == 0:
                agg = agg or "COUNT"
                y_field = "*"
            else:
                measure_opts = [{"value": "__COUNT__", "label": "تعداد رکوردها (شمارش / COUNT)", "hint": "COUNT(*)"}]
                for nc in num_cols[:6]:
                    lbl = next((c["label"] for c in cols_info if c["name"] == nc), nc)
                    is_avg_metric = any(k in nc.lower() for k in ("score", "pct", "rate", "ratio", "margin"))
                    if is_avg_metric:
                        measure_opts.append({"value": f"AVG:{nc}", "label": f"میانگین {lbl}", "hint": f"AVG({nc})"})
                    else:
                        measure_opts.append({"value": nc, "label": f"مجموع {lbl}", "hint": f"SUM({nc})"})
                questions.append(
                    {
                        "key": "y_field",
                        "question": f"کدام شاخص/ستون عددی از منبع «{chosen_ds.name}» محاسبه شود؟",
                        "options": measure_opts,
                    }
                )

        if y_field == "__COUNT__":
            y_field = "*"
            agg = "COUNT"
        elif isinstance(y_field, str) and y_field.startswith("AVG:"):
            y_field = y_field[4:]
            agg = "AVG"
        elif isinstance(y_field, str) and y_field.startswith("SUM:"):
            y_field = y_field[4:]
            agg = "SUM"
        elif y_field and not agg:
            if any(k in y_field.lower() for k in ("score", "pct", "rate", "ratio", "margin")):
                agg = "AVG"
            else:
                agg = "SUM" if y_field != "*" else "COUNT"

        if questions:
            return {
                "status": "needs_clarification",
                "message_fa": "برای تکمیل و ساخت دقیق ویجت، لطفاً موارد زیر را مشخص کنید:",
                "questions": questions,
                "parsed": {
                    "dataset_id": chosen_ds.id,
                    "dataset_name": chosen_ds.name,
                    "title": extracted_title,
                    "chart_type": chart_type,
                    "x_field": x_field,
                    "y_field": y_field if y_field != "__COUNT__" else "*",
                    "aggregation": agg,
                },
            }

        # Everything resolved! Build final widget spec & execute live preview
        eff_chart = chart_type or "bar"
        eff_agg = agg or ("COUNT" if y_field == "*" else "SUM")
        eff_y = y_field or "*"
        if not extracted_title:
            col_lbl_map = {c["name"]: c["label"] for c in cols_info}
            y_lbl = "تعداد" if eff_y == "*" else col_lbl_map.get(eff_y, eff_y.replace("_", " ").title())
            x_lbl = col_lbl_map.get(x_field, x_field) if x_field else ""
            extracted_title = f"{y_lbl} بر اساس {x_lbl}" if x_field else f"{eff_agg} {y_lbl}"

        visual_spec: dict[str, Any] = {
            "y_field": eff_y,
            "aggregation": eff_agg,
            "limit": 100,
        }
        if x_field and eff_chart not in ("kpi", "gauge"):
            visual_spec["x_field"] = x_field
        if eff_chart == "bpmn":
            bpmn_cfg = dict(inferred_bpmn or {})
            if x_field and not bpmn_cfg.get("step_field"):
                bpmn_cfg["step_field"] = x_field
            if eff_y != "*" and not bpmn_cfg.get("wait_field"):
                bpmn_cfg["wait_field"] = eff_y
            bpmn_cfg["heatmap_enabled"] = True
            bpmn_cfg["heatmap_metric"] = "avg_wait"
            bpmn_cfg["process_name"] = extracted_title
            if "bpmn" in (chosen_ds.warehouse_table or "").lower():
                from app.seed.bootstrap import DEFAULT_BPMN_GRAPH_PAYLOAD
                bpmn_cfg["nodes"] = DEFAULT_BPMN_GRAPH_PAYLOAD["nodes"]
                bpmn_cfg["edges"] = DEFAULT_BPMN_GRAPH_PAYLOAD["edges"]
            visual_spec["bpmn_config"] = bpmn_cfg

        is_currency = any(k in eff_y.lower() for k in ("rev", "cost", "profit", "price", "amount", "salary", "budget"))
        style_cfg = {"number_format": "currency" if (is_currency and eff_agg != "COUNT") else "number"}
        viz_cfg = {"show_legend": True, "show_tooltip": True, "data_labels": False}

        sec_ctx = build_data_security_context(db, user, chosen_ds.id)
        if eff_chart == "bpmn":
            from app.engines.bpmn_analytics import execute_bpmn_widget_sql

            bpmn_res = execute_bpmn_widget_sql(
                db,
                chosen_ds,
                visual_spec,
                viz_cfg,
                [],
                sec_ctx.rls_where_clauses,
                sec_ctx.rls_params,
                user=user,
            )
            sql = bpmn_res.get("generated_sql", "")
            preview_data = {
                "columns": bpmn_res["columns"],
                "rows": bpmn_res["rows"],
                "row_count": bpmn_res["row_count"],
                "bpmn_analytics": bpmn_res.get("bpmn_analytics"),
            }
        else:
            sql, params, _ = build_visual_query_sql(
                db, chosen_ds, visual_spec, [], sec_ctx.rls_where_clauses, sec_ctx.rls_params,
                raw_mode=False,
                user=user,
            )
            raw_preview = warehouse.execute_query(sql, params)
            vis_cols, vis_rows = apply_cls_and_masking(raw_preview["columns"], raw_preview["rows"], sec_ctx)
            preview_data = {
                "columns": vis_cols,
                "rows": vis_rows,
                "row_count": len(vis_rows),
            }

        created_widget = None
        if create_widget and dashboard_id:
            from app.core.cache import cache_manager
            from app.core.tenant import get_dashboard_for_org

            dash_obj = get_dashboard_for_org(db, dashboard_id, user.organization_id)
            enforce_permission(db, user, "dashboard", dash_obj.id, "add_widget")
            target_page_id = page_id
            if not target_page_id:
                p0 = (
                    db.query(DashboardPage)
                    .filter(DashboardPage.dashboard_id == dash_obj.id)
                    .order_by(DashboardPage.page_order.asc())
                    .first()
                )
                target_page_id = p0.id if p0 else None
            if target_page_id:
                page_obj = (
                    db.query(DashboardPage)
                    .filter(DashboardPage.id == target_page_id, DashboardPage.dashboard_id == dash_obj.id)
                    .first()
                )
                if not page_obj:
                    target_page_id = None
            if target_page_id:
                existing_widgets = db.query(Widget).filter(
                    Widget.dashboard_id == dash_obj.id, Widget.page_id == target_page_id
                ).all()
                next_y = max((w.layout_y + w.layout_h for w in existing_widgets), default=0)
                lay_w = 12 if eff_chart == "bpmn" else (4 if eff_chart in ("kpi", "gauge") else 6)
                lay_h = 10 if eff_chart == "bpmn" else (3 if eff_chart in ("kpi", "gauge") else 7)
                w = Widget(
                    dashboard_id=dash_obj.id,
                    page_id=target_page_id,
                    title=extracted_title,
                    description=f"AI Text-to-Widget ({chosen_ds.name})",
                    chart_type=eff_chart,
                    dataset_id=chosen_ds.id,
                    layout_x=0,
                    layout_y=next_y,
                    layout_w=lay_w,
                    layout_h=lay_h,
                )
                db.add(w)
                db.flush()
                db.add(
                    WidgetConfig(
                        widget_id=w.id,
                        style_json=style_cfg,
                        visualization_json=viz_cfg,
                        interactions_json={"cross_filter": True},
                        advanced_json={"ai_text_prompt": f"{raw_prompt} {free_reply}".strip()},
                    )
                )
                db.add(
                    WidgetQuery(
                        widget_id=w.id,
                        dataset_id=chosen_ds.id,
                        query_mode="visual",
                        visual_spec_json=visual_spec,
                    )
                )
                if not db.query(DataLineage).filter(
                    DataLineage.source_type == "dataset",
                    DataLineage.source_id == chosen_ds.id,
                    DataLineage.target_type == "dashboard",
                    DataLineage.target_id == dash_obj.id,
                ).first():
                    db.add(
                        DataLineage(
                            source_type="dataset",
                            source_id=chosen_ds.id,
                            target_type="dashboard",
                            target_id=dash_obj.id,
                        )
                    )
                cache_manager.invalidate_dashboard(dash_obj.id)
                db.commit()
                created_widget = {
                    "id": w.id,
                    "page_id": target_page_id,
                    "title": w.title,
                    "chart_type": w.chart_type,
                    "dataset_id": w.dataset_id,
                    "layout": {"x": w.layout_x, "y": w.layout_y, "w": w.layout_w, "h": w.layout_h},
                }

        return {
            "status": "created" if created_widget else "ready",
            "action": "add_widget",
            "message_fa": (
                f"نمودار «{extracted_title}» با موفقیت ساخته شد و به داشبورد اضافه گردید."
                if created_widget
                else f"پیکربندی نمودار «{extracted_title}» آماده شد."
            ),
            "widget": created_widget,
            "widget_spec": {
                "title": extracted_title,
                "chart_type": eff_chart,
                "dataset_id": chosen_ds.id,
                "dataset_name": chosen_ds.name,
                "visual_spec": visual_spec,
                "style": style_cfg,
                "visualization": viz_cfg,
            },
            "generated_sql": sql,
            "preview_data": preview_data,
        }

    CHART_LABELS_FA: dict[str, str] = {
        "bar": "نمودار میله‌ای",
        "horizontal_bar": "نمودار میله‌ای افقی",
        "stacked_bar": "نمودار ستونی انباشته",
        "line": "نمودار خطی (روندی)",
        "area": "نمودار مساحتی",
        "pie": "نمودار دایره‌ای",
        "donut": "نمودار حلقوی",
        "kpi": "کارت عدد خلاصه",
        "gauge": "عقربه شاخص",
        "table": "جدول اطلاعات",
        "pivot_table": "جدول مقایسه‌ای",
        "bpmn": "فرآیند BPMN و گلوگاه‌ها",
    }

    @staticmethod
    def _friendly_dataset_label(ds: Dataset) -> tuple[str, str]:
        wt = (ds.warehouse_table or "").lower()
        nm = (ds.name or "").lower()
        if "bpmn" in wt or "bpmn" in nm or "فرآیند" in nm:
            return ("فرآیندهای BPMN و کارتابل درخواست‌ها (کشف گلوگاه)", f"{ds.row_count} رویداد فرآیندی")
        if "contractor" in wt or ds.name == "تعداد":
            return ("گزارش عملکرد پیمانکاران و پروژه‌ها (تعداد)", f"{ds.row_count} رکورد آماده")
        if "sales" in wt or "sales" in nm:
            return ("فروش، درآمد و سود مناطق", f"{ds.row_count} سفارش")
        if "expense" in wt or "expense" in nm:
            return ("هزینه‌ها و بودجه بخش‌ها", f"{ds.row_count} سند مالی")
        if "hr" in wt or "workforce" in nm:
            return ("اطلاعات پرسنل و منابع انسانی", f"{ds.row_count} پرسنل")
        return (ds.name, f"{ds.row_count} ردیف اطلاعات")

    @staticmethod
    def _match_dataset_from_text(datasets: list[Dataset], text_low: str) -> Optional[Dataset]:
        if not datasets or not text_low:
            return None
        for d in datasets:
            if d.name.lower() in text_low or d.warehouse_table.lower() in text_low:
                return d
        syn_groups: list[tuple[list[str], list[str]]] = [
            (["bpmn", "گلوگاه", "کارتابل", "فرآیند", "فرایند", "انتظار", "گردش کار", "process"], ["bpmn", "process", "فرآیند"]),
            (["پیمانکار", "تعداد پروژه", "پروژه", "contractor"], ["contractor", "تعداد"]),
            (["فروش", "درآمد", "سود", "سفارش", "محصول", "مناطق", "منطقه", "sales", "revenue"], ["sales", "revenue"]),
            (["هزینه", "بودجه", "خرج", "مالی", "expense", "budget", "cost"], ["expense", "cost", "budget"]),
            (["پرسنل", "کارمند", "کارکنان", "حقوق", "منابع انسانی", "نیروی انسانی", "hr", "employee"], ["hr", "workforce"]),
            (["لاگ", "خطا", "سرور", "درخواست", "log", "error", "request", "access", "nginx"], ["log", "لاگ", "access", "error"]),
        ]
        for kws, targets in syn_groups:
            if any(k in text_low for k in kws):
                for d in datasets:
                    dn = d.name.lower()
                    wt = d.warehouse_table.lower()
                    if any(tg in dn or tg in wt for tg in targets):
                        return d
        return None

    @staticmethod
    def _resolve_theme_from_text(db: Session, org_id: str, text_low: str) -> Optional[DashboardTheme]:
        themes = (
            db.query(DashboardTheme)
            .filter((DashboardTheme.is_builtin == True) | (DashboardTheme.organization_id == org_id))
            .all()
        )
        if not themes:
            return None
        for th in themes:
            if th.id == text_low or th.slug.lower() in text_low or th.name.lower() in text_low:
                return th
        theme_hints = [
            (["شیشه", "glass", "بلور", "شیک"], ["glass"]),
            (["نئون", "neon", "درخشان", "رنگی"], ["neon"]),
            (["تیره", "مشکی", "دارک", "dark", "تم شب", "حالت شب"], ["dark"]),
            (["اداری", "رسمی", "سازمانی", "corporate", "آبی"], ["corporate"]),
            (["مینیمال", "ساده", "minimal"], ["minimal"]),
            (["روشن", "سفید", "تم روز", "حالت روز", "light"], ["light", "corporate"]),
            (["متریال", "material"], ["material"]),
        ]
        for kws, slugs in theme_hints:
            if any(k in text_low for k in kws):
                for slug in slugs:
                    hit = next((th for th in themes if slug in th.slug.lower() or slug in th.name.lower()), None)
                    if hit:
                        return hit
        return None

    @staticmethod
    def _build_full_dashboard_from_intent(
        db: Session,
        user: User,
        ds: Dataset,
        title: str,
        layout_preset: str,
        theme: Optional[DashboardTheme],
    ) -> dict[str, Any]:
        ws = db.query(Workspace).filter(Workspace.organization_id == user.organization_id).first()
        if not ws:
            ws = Workspace(organization_id=user.organization_id, name="Primary Analytics Workspace")
            db.add(ws)
            db.flush()

        cols_info, num_cols, dim_cols = AiAnalyticsEngine._get_dataset_schema(db, ds, user=user)
        col_label_map = {c["name"]: c["label"] for c in cols_info}

        def _lbl(col: Optional[str]) -> str:
            if not col or col == "*":
                return "رکوردها"
            return col_label_map.get(col, col.replace("_", " ").title())

        # Prioritize meaningful dimensions & measures
        pref_dims = [c for c in ("contractor_name", "region", "project_type", "category", "department", "product", "channel", "city", "level", "status_code", "service", "method", "logger", "host") if c in dim_cols]
        rest_dims = [c for c in dim_cols if c not in pref_dims and not c.endswith("_code")]
        ordered_dims = pref_dims + rest_dims
        primary_dim = ordered_dims[0] if ordered_dims else None
        secondary_dim = ordered_dims[1] if len(ordered_dims) > 1 else primary_dim
        time_col = next((c["name"] for c in cols_info if c.get("role") == "time"), None)

        pref_nums = [c for c in ("project_count", "revenue", "profit", "actual_amount", "performance_score", "contract_value", "salary", "latency_ms", "duration_ms", "response_bytes", "bytes") if c in num_cols]
        rest_nums = [c for c in num_cols if c not in pref_nums]
        ordered_nums = pref_nums + rest_nums
        primary_num = ordered_nums[0] if ordered_nums else "*"
        secondary_num = ordered_nums[1] if len(ordered_nums) > 1 else primary_num

        def _agg_for(col: str) -> str:
            if col == "*":
                return "COUNT"
            if any(k in col.lower() for k in ("score", "pct", "rate", "ratio", "margin")):
                return "AVG"
            return "SUM"

        p_agg = _agg_for(primary_num)
        s_agg = _agg_for(secondary_num)
        is_curr_p = any(k in primary_num.lower() for k in ("rev", "cost", "profit", "price", "amount", "salary", "budget", "value"))

        dash = Dashboard(
            workspace_id=ws.id,
            title=title,
            description=f"ساخته‌شده با دستیار هوشمند فارسی از داده‌های «{ds.name}»",
            theme_id=theme.id if theme else None,
            created_by=user.id,
        )
        db.add(dash)
        db.flush()

        page = DashboardPage(dashboard_id=dash.id, title="نمای کلی", page_order=0)
        db.add(page)
        db.flush()

        widget_specs: list[dict[str, Any]] = []
        if layout_preset in ("executive_full", "kpi_and_pie"):
            widget_specs.append(
                {
                    "title": f"تعداد کل {_lbl(primary_dim) if primary_dim else 'اطلاعات'}",
                    "chart_type": "kpi",
                    "layout": {"x": 0, "y": 0, "w": 4, "h": 3},
                    "visual_spec": {"y_field": "*", "aggregation": "COUNT"},
                    "style": {"number_format": "number"},
                }
            )
            widget_specs.append(
                {
                    "title": f"{'میانگین' if p_agg == 'AVG' else 'مجموع'} {_lbl(primary_num)}",
                    "chart_type": "kpi",
                    "layout": {"x": 4, "y": 0, "w": 4, "h": 3},
                    "visual_spec": {"y_field": primary_num, "aggregation": p_agg},
                    "style": {"number_format": "currency" if is_curr_p and p_agg != "COUNT" else "number"},
                }
            )
            if secondary_num != primary_num:
                widget_specs.append(
                    {
                        "title": f"{'میانگین' if s_agg == 'AVG' else 'مجموع'} {_lbl(secondary_num)}",
                        "chart_type": "kpi",
                        "layout": {"x": 8, "y": 0, "w": 4, "h": 3},
                        "visual_spec": {"y_field": secondary_num, "aggregation": s_agg},
                        "style": {"number_format": "number"},
                    }
                )

        chart_top_y = 3 if widget_specs else 0
        bpmn_inferred = AiAnalyticsEngine._infer_bpmn_config_from_cols(cols_info)
        if bpmn_inferred and (bpmn_inferred.get("step_field") or "bpmn" in (ds.warehouse_table or "").lower()):
            bpmn_cfg = {**bpmn_inferred, "process_name": title, "heatmap_enabled": True}
            if "bpmn" in (ds.warehouse_table or "").lower():
                from app.seed.bootstrap import DEFAULT_BPMN_GRAPH_PAYLOAD
                bpmn_cfg["nodes"] = DEFAULT_BPMN_GRAPH_PAYLOAD["nodes"]
                bpmn_cfg["edges"] = DEFAULT_BPMN_GRAPH_PAYLOAD["edges"]
            w_f = bpmn_cfg.get("wait_field") or primary_num
            s_f = bpmn_cfg.get("step_field") or primary_dim
            widget_specs.append(
                {
                    "title": f"نقشه فرآیند BPMN و گلوگاه‌ها — {ds.name}",
                    "chart_type": "bpmn",
                    "layout": {"x": 0, "y": chart_top_y, "w": 12, "h": 10},
                    "visual_spec": {
                        "x_field": s_f,
                        "y_field": w_f,
                        "aggregation": "AVG" if w_f != "*" else "COUNT",
                        "bpmn_config": bpmn_cfg,
                    },
                    "style": {"number_format": "number"},
                }
            )
            chart_top_y += 10

        if primary_dim and layout_preset in ("executive_full", "charts_only"):
            widget_specs.append(
                {
                    "title": f"مقایسه {_lbl(primary_num)} بر اساس {_lbl(primary_dim)}",
                    "chart_type": "bar",
                    "layout": {"x": 0, "y": chart_top_y, "w": 6, "h": 7},
                    "visual_spec": {"x_field": primary_dim, "y_field": primary_num, "aggregation": p_agg, "limit": 50},
                    "style": {"number_format": "currency" if is_curr_p and p_agg != "COUNT" else "number"},
                }
            )

        if secondary_dim and layout_preset in ("executive_full", "kpi_and_pie"):
            widget_specs.append(
                {
                    "title": f"سهم بر اساس {_lbl(secondary_dim)}",
                    "chart_type": "pie" if layout_preset == "kpi_and_pie" else "donut",
                    "layout": {"x": 6 if (primary_dim and layout_preset == "executive_full") else 0, "y": chart_top_y, "w": 6, "h": 7},
                    "visual_spec": {"x_field": secondary_dim, "y_field": primary_num, "aggregation": p_agg, "limit": 30},
                    "style": {"number_format": "number"},
                }
            )

        if layout_preset == "charts_only" and (time_col or secondary_dim):
            x_t = time_col or secondary_dim
            widget_specs.append(
                {
                    "title": f"روند {_lbl(secondary_num)} بر اساس {_lbl(x_t)}",
                    "chart_type": "line",
                    "layout": {"x": 6, "y": chart_top_y, "w": 6, "h": 7},
                    "visual_spec": {"x_field": x_t, "y_field": secondary_num, "aggregation": s_agg, "limit": 50},
                    "style": {"number_format": "number"},
                }
            )

        if layout_preset == "executive_full" and primary_dim:
            widget_specs.append(
                {
                    "title": f"جدول جزئیات {_lbl(primary_dim)}",
                    "chart_type": "table",
                    "layout": {"x": 0, "y": chart_top_y + 7, "w": 12, "h": 7},
                    "visual_spec": {
                        "x_field": primary_dim,
                        "y_field": primary_num,
                        "secondary_y_field": secondary_num if secondary_num != primary_num else None,
                        "aggregation": p_agg,
                        "limit": 50,
                    },
                    "style": {"number_format": "number"},
                }
            )

        created_widgets = []
        layout_items = []
        for sp in widget_specs:
            lay = sp["layout"]
            w = Widget(
                dashboard_id=dash.id,
                page_id=page.id,
                title=sp["title"],
                chart_type=sp["chart_type"],
                dataset_id=ds.id,
                layout_x=lay["x"],
                layout_y=lay["y"],
                layout_w=lay["w"],
                layout_h=lay["h"],
            )
            db.add(w)
            db.flush()
            layout_items.append({"i": w.id, **lay})
            db.add(
                WidgetConfig(
                    widget_id=w.id,
                    style_json=sp.get("style", {}),
                    visualization_json={"show_legend": True, "show_tooltip": True, "data_labels": False},
                    interactions_json={"cross_filter": True},
                    advanced_json={"ai_generated": True},
                )
            )
            db.add(
                WidgetQuery(
                    widget_id=w.id,
                    dataset_id=ds.id,
                    query_mode="visual",
                    visual_spec_json=sp["visual_spec"],
                )
            )
            created_widgets.append(
                {
                    "id": w.id,
                    "title": w.title,
                    "chart_type": w.chart_type,
                    "chart_label_fa": AiAnalyticsEngine.CHART_LABELS_FA.get(w.chart_type, w.chart_type),
                }
            )

        db.add(DashboardLayout(page_id=page.id, columns_count=12, layout_json=layout_items))

        # Add automatic friendly filter
        filter_col = next((c for c in ("region", "project_type", "department", "category", "year") if c in dim_cols), primary_dim)
        if filter_col:
            db.add(
                DashboardFilter(
                    dashboard_id=dash.id,
                    name=_lbl(filter_col),
                    field_name=filter_col,
                    dataset_id=ds.id,
                    filter_type="multi_select",
                    operator="in",
                    scope="dashboard",
                    default_value_json=[],
                )
            )

        from app.models.data import DataLineage

        db.add(DataLineage(source_type="dataset", source_id=ds.id, target_type="dashboard", target_id=dash.id))
        db.commit()

        return {
            "status": "dashboard_created",
            "action": "create_dashboard",
            "message_fa": f"داشبورد «{dash.title}» با {len(created_widgets)} گزارش و نمودار آماده با موفقیت ساخته شد!",
            "created_dashboard": {
                "id": dash.id,
                "title": dash.title,
                "description": dash.description,
                "theme_id": dash.theme_id,
                "theme_name": theme.name if theme else "پیش‌فرض",
                "dataset_id": ds.id,
                "dataset_name": ds.name,
                "widget_count": len(created_widgets),
                "widgets": created_widgets,
            },
        }

    @staticmethod
    def _handle_create_dashboard_chat(
        db: Session,
        user: User,
        prompt: str,
        clarifications: dict[str, Any],
    ) -> dict[str, Any]:
        from app.api.deps import enforce_permission

        enforce_permission(db, user, "dashboard", "*", "add")
        free_reply = AiAnalyticsEngine._normalize_text(str(clarifications.get("free_text") or ""))
        norm = AiAnalyticsEngine._normalize_text(prompt)
        combined_low = AiAnalyticsEngine._normalize_text(f"{norm} {free_reply}").lower()

        datasets = db.query(Dataset).filter(Dataset.organization_id == user.organization_id).all()
        if not datasets:
            return {
                "status": "needs_clarification",
                "action": "create_dashboard",
                "message_fa": "هنوز هیچ منبع داده یا فایل اکسل در سیستم ثبت نشده است. لطفاً ابتدا یک فایل اکسل از بخش «منابع داده» بارگذاری کنید.",
                "questions": [],
                "parsed": {"mode": "create_dashboard"},
            }

        # Extract custom dashboard title
        dash_title: Optional[str] = clarifications.get("title")
        if not dash_title:
            m_t = re.search(
                r"(?:با\s+عنوان|تحت\s+عنوان|به\s+نام|با\s+نام|اسمش\s+رو\s+بذار|عنوان)\s+[\"«']?([^\"»'\n]+?)[\"»']?(?=\s*(?:بساز|ایجاد|تولید|درست|$))",
                norm,
                flags=re.IGNORECASE,
            )
            if m_t:
                dash_title = m_t.group(1).strip(" ،,.«»\"'")

        # Resolve dataset
        chosen_ds: Optional[Dataset] = None
        if clarifications.get("dataset_id"):
            chosen_ds = next((d for d in datasets if d.id == clarifications["dataset_id"]), None)
        if not chosen_ds:
            chosen_ds = AiAnalyticsEngine._match_dataset_from_text(datasets, combined_low)

        # Resolve theme
        chosen_theme: Optional[DashboardTheme] = None
        themes = (
            db.query(DashboardTheme)
            .filter((DashboardTheme.is_builtin == True) | (DashboardTheme.organization_id == user.organization_id))
            .all()
        )
        if clarifications.get("theme_id"):
            if clarifications["theme_id"] == "__default__":
                chosen_theme = themes[0] if themes else None
            else:
                chosen_theme = next((t for t in themes if t.id == clarifications["theme_id"]), None)
        if not chosen_theme:
            chosen_theme = AiAnalyticsEngine._resolve_theme_from_text(db, user.organization_id, combined_low)

        # Resolve layout preset
        layout_preset: Optional[str] = clarifications.get("layout_preset")
        if not layout_preset:
            if any(k in combined_low for k in ("کامل", "همه", "مدیریتی", "جامع", "executive", "full")):
                layout_preset = "executive_full"
            elif any(k in combined_low for k in ("روند", "خطی", "فقط نمودار", "charts")):
                layout_preset = "charts_only"
            elif any(k in combined_low for k in ("خلاصه", "سهم", "دایره", "سریع", "کارت")):
                layout_preset = "kpi_and_pie"

        questions: list[dict[str, Any]] = []
        if not chosen_ds:
            ds_opts = []
            for d in datasets:
                lbl, hint = AiAnalyticsEngine._friendly_dataset_label(d)
                ds_opts.append({"value": d.id, "label": lbl, "hint": hint})
            questions.append(
                {
                    "key": "dataset_id",
                    "question": "این داشبورد قرار است گزارش‌های کدام بخش یا موضوع را نشان دهد؟",
                    "options": ds_opts,
                }
            )

        if not layout_preset:
            questions.append(
                {
                    "key": "layout_preset",
                    "question": "دوست دارید چه نوع گزارش‌هایی داخل داشبورد ساخته شود؟",
                    "options": [
                        {
                            "value": "executive_full",
                            "label": "بسته کامل مدیریتی (توصیه‌شده)",
                            "hint": "کارت‌های خلاصه آمار + نمودار میله‌ای + دایره‌ای + جدول جزئیات",
                        },
                        {
                            "value": "charts_only",
                            "label": "نمودارهای مقایسه‌ای و روندی",
                            "hint": "نمودار میله‌ای و خطی برای مقایسه و بررسی روند",
                        },
                        {
                            "value": "kpi_and_pie",
                            "label": "نمای سریع و خلاصه",
                            "hint": "اعداد کلیدی و نمودار سهم دایره‌ای",
                        },
                    ],
                }
            )

        if not chosen_theme and themes:
            theme_name_fa = {
                "light": "روشن و تمیز",
                "dark": "تیره و مدرن",
                "corporate": "اداری و رسمی (آبی)",
                "glass": "شیشه‌ای و حرفه‌ای",
                "neon": "مدرن و پرکنتراست",
                "minimal": "ساده و مینیمال",
                "material": "مدرن متریال",
            }
            questions.append(
                {
                    "key": "theme_id",
                    "question": "ظاهر و رنگ داشبورد چطور باشد؟",
                    "options": [
                        {"value": th.id, "label": theme_name_fa.get(th.slug, th.name)}
                        for th in themes[:5]
                    ],
                }
            )

        if questions:
            return {
                "status": "needs_clarification",
                "action": "create_dashboard",
                "message_fa": "برای اینکه داشبورد دقیقاً مطابق سلیقه شما ساخته شود، لطفاً به این موارد پاسخ دهید:",
                "questions": questions,
                "parsed": {
                    "mode": "create_dashboard",
                    "dataset_id": chosen_ds.id if chosen_ds else None,
                    "layout_preset": layout_preset,
                    "theme_id": chosen_theme.id if chosen_theme else None,
                    "title": dash_title,
                },
            }

        if not dash_title:
            if chosen_ds:
                friendly, _ = AiAnalyticsEngine._friendly_dataset_label(chosen_ds)
                dash_title = f"داشبورد {friendly.split('(')[0].strip()}"
            else:
                dash_title = "داشبورد هوشمند جدید"

        return AiAnalyticsEngine._build_full_dashboard_from_intent(
            db=db,
            user=user,
            ds=chosen_ds,
            title=dash_title,
            layout_preset=layout_preset or "executive_full",
            theme=chosen_theme,
        )

    @staticmethod
    def _handle_edit_chat(
        db: Session,
        user: User,
        prompt: str,
        dashboard_id: Optional[str],
        widget_id: Optional[str],
        clarifications: dict[str, Any],
    ) -> dict[str, Any]:
        from app.api.deps import enforce_permission
        from app.core.rls_cls import apply_cls_and_masking, build_data_security_context
        from app.engines.query_engine import build_visual_query_sql

        free_reply = AiAnalyticsEngine._normalize_text(str(clarifications.get("free_text") or ""))
        norm = AiAnalyticsEngine._normalize_text(prompt)
        combined_text = AiAnalyticsEngine._normalize_text(f"{norm} {free_reply}")
        combined_low = combined_text.lower()

        dashboards = (
            db.query(Dashboard)
            .join(Workspace, Dashboard.workspace_id == Workspace.id)
            .filter(Workspace.organization_id == user.organization_id, Dashboard.is_template == False)
            .order_by(Dashboard.updated_at.desc())
            .all()
        )
        if not dashboards:
            dashboards = (
                db.query(Dashboard)
                .join(Workspace, Dashboard.workspace_id == Workspace.id)
                .filter(Workspace.organization_id == user.organization_id)
                .all()
            )
        if not dashboards:
            return {
                "status": "needs_clarification",
                "action": "edit",
                "message_fa": "هنوز داشبوردی در سیستم وجود ندارد که ویرایش شود. آیا می‌خواهید یک داشبورد جدید بسازیم؟",
                "questions": [
                    {
                        "key": "mode",
                        "question": "چطور شروع کنیم؟",
                        "options": [{"value": "create_dashboard", "label": "ساخت داشبورد جدید با راهنمای فارسی"}],
                    }
                ],
                "parsed": {"mode": "edit"},
            }

        target_dash: Optional[Dashboard] = None
        ds_id_val = clarifications.get("dashboard_id") or dashboard_id
        if ds_id_val:
            target_dash = next((d for d in dashboards if d.id == ds_id_val), None)
        if not target_dash:
            for d in dashboards:
                if d.title.lower() in combined_low:
                    target_dash = d
                    break
        if not target_dash and len(dashboards) == 1:
            target_dash = dashboards[0]

        if not target_dash:
            return {
                "status": "needs_clarification",
                "action": "edit",
                "message_fa": "لطفاً مشخص کنید کدام داشبورد را می‌خواهید تغییر دهید:",
                "questions": [
                    {
                        "key": "dashboard_id",
                        "question": "کدام داشبورد را ویرایش کنیم؟",
                        "options": [{"value": d.id, "label": d.title} for d in dashboards[:8]],
                    }
                ],
                "parsed": {"mode": "edit"},
            }

        enforce_permission(db, user, "dashboard", target_dash.id, "edit")
        widgets = db.query(Widget).filter(Widget.dashboard_id == target_dash.id).all()

        # Check explicit target widget or dashboard-level scope
        target_scope: Optional[str] = clarifications.get("target_widget_id") or widget_id
        target_widget: Optional[Widget] = None

        if target_scope and target_scope != "__dashboard__":
            target_widget = next((w for w in widgets if w.id == target_scope), None)

        if not target_widget and target_scope != "__dashboard__":
            # Match widget by title or ordinal or chart type
            for w in widgets:
                if w.title.lower() in combined_low:
                    target_widget = w
                    break
            if not target_widget and widgets:
                if any(k in combined_low for k in ("اول", "نخست")):
                    target_widget = widgets[0]
                elif "دوم" in combined_low and len(widgets) >= 2:
                    target_widget = widgets[1]
                elif "سوم" in combined_low and len(widgets) >= 3:
                    target_widget = widgets[2]
                elif "آخر" in combined_low:
                    target_widget = widgets[-1]

        # Check if request clearly refers to dashboard-level settings (theme, dashboard title, adding filter)
        is_dash_level_intent = target_scope == "__dashboard__" or (
            not target_widget
            and (
                any(k in combined_low for k in ("تم داشبورد", "تم ", "ظاهر داشبورد", "ظاهرش", "رنگ داشبورد", "قالب داشبورد", "تیره کن", "روشن کن", "شیشه ای کن"))
                or any(k in combined_low for k in ("عنوان داشبورد", "اسم داشبورد", "نام داشبورد"))
                or ("فیلتر" in combined_low and any(k in combined_low for k in ("اضافه", "بذار", "قرار بده")))
            )
        )

        if is_dash_level_intent:
            # 1) Theme change
            resolved_theme = None
            if clarifications.get("theme_id"):
                resolved_theme = (
                    db.query(DashboardTheme)
                    .filter(
                        DashboardTheme.id == clarifications["theme_id"],
                        (DashboardTheme.is_builtin == True) | (DashboardTheme.organization_id == user.organization_id),
                    )
                    .first()
                )
            if not resolved_theme:
                resolved_theme = AiAnalyticsEngine._resolve_theme_from_text(db, user.organization_id, combined_low)

            if resolved_theme:
                target_dash.theme_id = resolved_theme.id
                db.commit()
                return {
                    "status": "dashboard_updated",
                    "action": "edit_dashboard",
                    "message_fa": f"ظاهر داشبورد «{target_dash.title}» با موفقیت به «{resolved_theme.name}» تغییر یافت.",
                    "updated_dashboard": {
                        "id": target_dash.id,
                        "title": target_dash.title,
                        "theme_id": resolved_theme.id,
                        "theme_name": resolved_theme.name,
                    },
                }

            # 2) Dashboard title change
            m_dtitle = re.search(
                r"(?:عنوان\s+داشبورد|اسم\s+داشبورد|نام\s+داشبورد|عنوانش|اسمش|نامش)\s*(?:را|رو)?\s*(?:تغییر\s+بده\s+به|عوض\s+کن\s+به|تغییر\s+بده|عوض\s+کن|بگذار|بذار|بکن|به)\s*[\"«']?([^\"»'\n]+?)[\"»']?(?=\s*$)",
                combined_text,
                flags=re.IGNORECASE,
            )
            new_dtitle = clarifications.get("new_title") or (m_dtitle.group(1).strip(" ،,.«»\"'") if m_dtitle else None)
            if new_dtitle and len(new_dtitle) >= 2 and not any(k in new_dtitle for k in ("عوض کن", "تغییر بده")):
                target_dash.title = new_dtitle
                db.commit()
                return {
                    "status": "dashboard_updated",
                    "action": "edit_dashboard",
                    "message_fa": f"نام داشبورد با موفقیت به «{new_dtitle}» تغییر یافت.",
                    "updated_dashboard": {"id": target_dash.id, "title": target_dash.title, "theme_id": target_dash.theme_id},
                }

            # 3) Add dashboard filter
            if "فیلتر" in combined_low:
                ds_candidates = db.query(Dataset).filter(Dataset.organization_id == user.organization_id).all()
                matched_col = None
                matched_ds = None
                for w in widgets:
                    if w.dataset_id:
                        d_obj = next((x for x in ds_candidates if x.id == w.dataset_id), None)
                        if d_obj:
                            cinfo, _, _ = AiAnalyticsEngine._get_dataset_schema(db, d_obj, user=user)
                            col = AiAnalyticsEngine._match_column(combined_low, cinfo, role_filter="dim")
                            if col:
                                matched_col = col
                                matched_ds = d_obj
                                break
                if matched_col and matched_ds:
                    cinfo, _, _ = AiAnalyticsEngine._get_dataset_schema(db, matched_ds, user=user)
                    lbl = next((c["label"] for c in cinfo if c["name"] == matched_col), matched_col)
                    db.add(
                        DashboardFilter(
                            dashboard_id=target_dash.id,
                            name=lbl,
                            field_name=matched_col,
                            dataset_id=matched_ds.id,
                            filter_type="multi_select",
                            operator="in",
                            scope="dashboard",
                            default_value_json=[],
                        )
                    )
                    db.commit()
                    return {
                        "status": "dashboard_updated",
                        "action": "edit_dashboard",
                        "message_fa": f"فیلتر «{lbl}» به داشبورد «{target_dash.title}» اضافه شد.",
                        "updated_dashboard": {"id": target_dash.id, "title": target_dash.title, "theme_id": target_dash.theme_id},
                    }

            # Ask which dashboard-level setting to change
            themes = db.query(DashboardTheme).filter(DashboardTheme.organization_id == user.organization_id).all()
            return {
                "status": "needs_clarification",
                "action": "edit",
                "message_fa": f"چه تغییری روی تنظیمات کلی داشبورد «{target_dash.title}» انجام شود؟",
                "questions": [
                    {
                        "key": "theme_id",
                        "question": "می‌توانید یکی از ظاهرهای زیر را انتخاب کنید یا نام جدید داشبورد را بنویسید:",
                        "options": [{"value": th.id, "label": f"تغییر ظاهر به: {th.name}"} for th in themes[:5]],
                    }
                ],
                "parsed": {"mode": "edit", "dashboard_id": target_dash.id, "target_widget_id": "__dashboard__"},
            }

        # Widget-level edit: if widget is not yet identified
        if not target_widget:
            opts = [{"value": "__dashboard__", "label": "تغییر کلی داشبورد (نام، رنگ/تم، یا فیلتر)"}]
            for w in widgets[:9]:
                ct_fa = AiAnalyticsEngine.CHART_LABELS_FA.get(w.chart_type, w.chart_type)
                opts.append({"value": w.id, "label": f"{w.title}", "hint": ct_fa})
            return {
                "status": "needs_clarification",
                "action": "edit",
                "message_fa": f"در داشبورد «{target_dash.title}»، کدام بخش یا نمودار را می‌خواهید تغییر دهید؟",
                "questions": [
                    {
                        "key": "target_widget_id",
                        "question": "مورد نظر را انتخاب کنید:",
                        "options": opts,
                    }
                ],
                "parsed": {"mode": "edit", "dashboard_id": target_dash.id},
            }

        # We have target_widget! Check what modification the user asked for.
        edit_action = clarifications.get("edit_action")
        new_chart_type = clarifications.get("chart_type") or AiAnalyticsEngine._extract_chart_type(combined_low)
        if isinstance(edit_action, str) and edit_action.startswith("chart:"):
            new_chart_type = edit_action.split(":", 1)[1]

        is_delete = edit_action == "delete" or any(k in combined_low for k in ("حذف", "پاک کن", "بردار", "delete", "remove"))
        if is_delete:
            from app.core.cache import cache_manager

            enforce_permission(db, user, "dashboard", target_dash.id, "delete_widget")
            w_title = target_widget.title
            w_id = target_widget.id
            db.delete(target_widget)
            cache_manager.invalidate_dashboard(target_dash.id)
            db.commit()
            return {
                "status": "widget_deleted",
                "action": "delete_widget",
                "message_fa": f"نمودار «{w_title}» با موفقیت از داشبورد حذف شد.",
                "deleted_widget_id": w_id,
                "dashboard_id": target_dash.id,
            }

        # Extract new widget title if requested
        new_w_title: Optional[str] = clarifications.get("new_title")
        if not new_w_title:
            m_wt = re.search(
                r"(?:عنوان|اسم|نام)ش?\s*(?:ویجت|نمودار|کارت)?\s*(?:را|رو)?\s*(?:تغییر\s+بده\s+به|عوض\s+کن\s+به|تغییر\s+بده|عوض\s+کن|بگذار|بذار|بکن|به)\s*[\"«']?([^\"»'\n]+?)[\"»']?(?=\s*(?:و\s+(?:نوعش|تبدیل)|$))",
                combined_text,
                flags=re.IGNORECASE,
            )
            if m_wt:
                cand_t = m_wt.group(1).strip(" ،,.«»\"'")
                if len(cand_t) >= 2 and not any(k in cand_t for k in ("عوض کن", "تغییر بده", "نمودار را")):
                    new_w_title = cand_t

        # Extract updated dimension or measure if mentioned
        wq = db.query(WidgetQuery).filter(WidgetQuery.widget_id == target_widget.id).first()
        ds_obj = db.query(Dataset).filter(Dataset.id == target_widget.dataset_id).first() if target_widget.dataset_id else None
        new_x: Optional[str] = None
        new_y: Optional[str] = None
        new_agg: Optional[str] = None
        if ds_obj:
            cinfo, num_cols, _ = AiAnalyticsEngine._get_dataset_schema(db, ds_obj, user=user)
            m_dim = re.search(
                r"(?:بر\s+اساس|به\s+تفکیک|بر\s+حسب|روی\s+محور)\s+[\"«']?([^\"»'\n]+?)[\"»']?(?=\s*(?:نشان|نمایش|تبدیل|کن|بده|$))",
                combined_text,
                flags=re.IGNORECASE,
            )
            if m_dim:
                new_x = AiAnalyticsEngine._match_column(m_dim.group(1), cinfo, role_filter="dim")
            # Check if user mentioned a specific new metric in reply
            measure_hit = AiAnalyticsEngine._match_column(combined_low, cinfo, role_filter="measure")
            if measure_hit and measure_hit != (wq.visual_spec_json or {}).get("y_field"):
                new_y = measure_hit
                if any(k in combined_low for k in ("میانگین", "avg")) or any(k in measure_hit.lower() for k in ("score", "pct", "rate")):
                    new_agg = "AVG"
                elif any(k in combined_low for k in ("تعداد رکورد", "شمارش", "count")):
                    new_agg = "COUNT"
                    new_y = "*"
                else:
                    new_agg = "SUM"

        has_any_change = bool(new_chart_type or new_w_title or new_x or new_y)
        if not has_any_change:
            return {
                "status": "needs_clarification",
                "action": "edit",
                "message_fa": f"چه تغییری می‌خواهید روی «{target_widget.title}» انجام شود؟",
                "questions": [
                    {
                        "key": "edit_action",
                        "question": "می‌توانید یکی از گزینه‌ها را بزنید یا تغییر دلخواه خود را به فارسی تایپ کنید (مثلاً: عنوانش را بگذار آمار جدید):",
                        "options": [
                            {"value": "chart:bar", "label": "تبدیل به نمودار میله‌ای"},
                            {"value": "chart:line", "label": "تبدیل به نمودار خطی (روندی)"},
                            {"value": "chart:pie", "label": "تبدیل به نمودار دایره‌ای"},
                            {"value": "chart:table", "label": "نمایش به صورت جدول"},
                            {"value": "chart:kpi", "label": "نمایش به شکل کارت عدد خلاصه"},
                            {"value": "delete", "label": "حذف این نمودار از داشبورد"},
                        ],
                    }
                ],
                "parsed": {
                    "mode": "edit",
                    "dashboard_id": target_dash.id,
                    "target_widget_id": target_widget.id,
                },
            }

        # Apply widget edits
        if new_chart_type:
            target_widget.chart_type = new_chart_type
        if new_w_title:
            target_widget.title = new_w_title

        visual_spec = dict((wq.visual_spec_json or {}) if wq else {})
        if new_x:
            visual_spec["x_field"] = new_x
        if new_y:
            visual_spec["y_field"] = new_y
        if new_agg:
            visual_spec["aggregation"] = new_agg
        if target_widget.chart_type == "bpmn" and not visual_spec.get("bpmn_config") and ds_obj:
            cinfo_e, _, _ = AiAnalyticsEngine._get_dataset_schema(db, ds_obj, user=user)
            inferred_e = AiAnalyticsEngine._infer_bpmn_config_from_cols(cinfo_e)
            if inferred_e:
                visual_spec["bpmn_config"] = {**inferred_e, "heatmap_enabled": True}
        if wq:
            wq.visual_spec_json = visual_spec

        from app.core.cache import cache_manager
        cache_manager.invalidate_dashboard(target_dash.id)
        db.commit()

        preview_data = None
        generated_sql = None
        if ds_obj and visual_spec:
            sec_ctx = build_data_security_context(db, user, ds_obj.id)
            generated_sql, params, _ = build_visual_query_sql(
                db, ds_obj, visual_spec, [], sec_ctx.rls_where_clauses, sec_ctx.rls_params,
                raw_mode=(target_widget.chart_type == "bpmn"),
                user=user,
            )
            raw_prev = warehouse.execute_query(generated_sql, params)
            vis_cols, vis_rows = apply_cls_and_masking(raw_prev["columns"], raw_prev["rows"], sec_ctx)
            preview_data = {"columns": vis_cols, "rows": vis_rows, "row_count": len(vis_rows)}

        ct_fa = AiAnalyticsEngine.CHART_LABELS_FA.get(target_widget.chart_type, target_widget.chart_type)
        return {
            "status": "widget_updated",
            "action": "edit_widget",
            "message_fa": f"«{target_widget.title}» با موفقیت به‌روزرسانی شد ({ct_fa}).",
            "widget": {
                "id": target_widget.id,
                "dashboard_id": target_dash.id,
                "page_id": target_widget.page_id,
                "title": target_widget.title,
                "chart_type": target_widget.chart_type,
                "dataset_id": target_widget.dataset_id,
                "visual_spec": visual_spec,
            },
            "widget_spec": {
                "title": target_widget.title,
                "chart_type": target_widget.chart_type,
                "visual_spec": visual_spec,
                "style": {},
                "visualization": {"show_legend": True, "show_tooltip": True},
            },
            "generated_sql": generated_sql,
            "preview_data": preview_data,
        }

    @staticmethod
    def _handle_qa_chat(
        db: Session,
        user: User,
        prompt: str,
        clarifications: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        clar = dict(clarifications or {})
        datasets = db.query(Dataset).filter(Dataset.organization_id == user.organization_id).all()
        if not datasets:
            return {
                "status": "needs_data",
                "action": "qa",
                "message_fa": "هنوز هیچ منبع داده‌ای در سامانه ثبت نشده است. لطفاً ابتدا فایل اکسل یا منبع داده وارد کنید.",
                "source_datasets": [],
            }

        chosen_ds = None
        if clar.get("dataset_id"):
            chosen_ds = next((d for d in datasets if d.id == clar["dataset_id"]), None)
        if not chosen_ds:
            low = AiAnalyticsEngine._normalize_text(prompt).lower()
            for d in datasets:
                if d.name.lower() in low or any(tok in low for tok in d.name.lower().split() if len(tok) >= 3):
                    chosen_ds = d
                    break
        if not chosen_ds and len(datasets) == 1:
            chosen_ds = datasets[0]
        if not chosen_ds:
            return {
                "status": "needs_clarification",
                "action": "qa",
                "message_fa": "این پرسش باید از روی کدام منبع داده پاسخ داده شود؟",
                "questions": [
                    {
                        "key": "dataset_id",
                        "question": "منبع داده مورد نظر را انتخاب کنید:",
                        "options": [
                            {"value": d.id, "label": d.name, "hint": f"{d.row_count or 0} ردیف"}
                            for d in datasets[:8]
                        ],
                    }
                ],
                "parsed": {"mode": "qa"},
            }

        nlq = AiAnalyticsEngine.natural_language_query(db, user, prompt, chosen_ds)
        if nlq.get("status") == "needs_data":
            return {
                "status": "needs_data",
                "action": "qa",
                "message_fa": nlq["answer_fa"],
                "available_measures": nlq.get("available_measures", []),
                "available_dimensions": nlq.get("available_dimensions", []),
                "source_datasets": nlq.get("source_datasets", [chosen_ds.name]),
            }
        return {
            "status": "qa_answered",
            "action": "qa",
            "message_fa": nlq["answer_fa"],
            "generated_sql": nlq["generated_sql"],
            "recommended_chart_type": nlq["recommended_chart_type"],
            "x_field": nlq.get("x_field"),
            "y_field": nlq.get("y_field"),
            "source_datasets": nlq.get("source_datasets", [chosen_ds.name]),
            "confidence": nlq.get("confidence", 0.9),
            "limitations": nlq.get("limitations", []),
            "preview_data": nlq.get("result"),
        }

    @staticmethod
    def handle_conversational_request(
        db: Session,
        user: User,
        prompt: str,
        dashboard_id: Optional[str] = None,
        page_id: Optional[str] = None,
        widget_id: Optional[str] = None,
        mode: Optional[str] = None,
        clarifications: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        clar = dict(clarifications or {})
        eff_mode = clar.get("mode") or (mode if mode and mode != "auto" else None)
        norm_low = AiAnalyticsEngine._normalize_text(prompt).lower()

        if not eff_mode:
            is_edit_kw = any(
                k in norm_low
                for k in (
                    "ویرایش",
                    "تغییر",
                    "عوض کن",
                    "تبدیل کن",
                    "تبدیل به",
                    "بکن ",
                    "حذف",
                    "پاک کن",
                    "بردار",
                    "اسمش رو",
                    "تم داشبورد",
                    "ظاهر داشبورد",
                    "رنگ داشبورد",
                    "فیلتر",
                    "تیره کن",
                    "روشن کن",
                )
            )
            has_dash_word = any(k in norm_low for k in ("داشبورد", "dashboard", "صفحه گزارش"))
            has_widget_word = any(k in norm_low for k in ("ویجت", "نمودار", "کارت", "جدول", "شاخص", "kpi", "chart", "widget", "bpmn", "گلوگاه", "گردش کار"))
            has_create_verb = any(k in norm_low for k in ("بساز", "ایجاد کن", "بکش", "اضافه کن", "بیفزا", "create", "build", "add"))
            is_qa_kw = any(k in norm_low for k in ("چقدر", "کدام", "چند ", "میانگین", "مجموع", "جمع ", "بیشترین", "کمترین", "چرا", "چه کسی", "how many", "what is", "which", "؟", "?"))

            if widget_id or is_edit_kw:
                eff_mode = "edit"
            elif has_dash_word and not has_widget_word:
                eff_mode = "create_dashboard"
            elif is_qa_kw and not has_create_verb and not has_widget_word:
                eff_mode = "qa"
            elif has_widget_word or any(k in norm_low for k in ("از روی منبع", "بر اساس", "به تفکیک")):
                eff_mode = "add_widget"

        if not eff_mode:
            return {
                "status": "needs_clarification",
                "action": "choose_mode",
                "message_fa": "خوش آمدید! می‌خواهید با هم چه کاری انجام دهیم؟",
                "questions": [
                    {
                        "key": "mode",
                        "question": "یکی از کارهای زیر را انتخاب کنید:",
                        "options": [
                            {"value": "qa", "label": "پرسش و تحلیل مستقیم از داده‌ها (Q&A)", "hint": "محاسبه واقعی از SQL با منبع و عدم توهم"},
                            {"value": "create_dashboard", "label": "ساخت یک داشبورد کامل جدید", "hint": "با چند نمودار و کارت خلاصه آماده"},
                            {"value": "add_widget", "label": "افزودن یک نمودار یا عدد آماری جدید", "hint": "به داشبورد فعلی یا دلخواه"},
                            {"value": "edit", "label": "ویرایش داشبورد یا نمودارهای موجود", "hint": "تغییر رنگ/ظاهر، نام، نوع نمودار یا حذف"},
                        ],
                    }
                ],
                "parsed": {},
            }

        if eff_mode == "qa":
            return AiAnalyticsEngine._handle_qa_chat(db, user, prompt, clar)
        if eff_mode == "create_dashboard":
            return AiAnalyticsEngine._handle_create_dashboard_chat(db, user, prompt, clar)
        if eff_mode == "edit":
            return AiAnalyticsEngine._handle_edit_chat(db, user, prompt, dashboard_id, widget_id, clar)

        # Default: add_widget
        return AiAnalyticsEngine.plan_or_create_widget_from_text(
            db=db,
            user=user,
            prompt=prompt,
            dashboard_id=dashboard_id,
            page_id=page_id,
            dataset_id=clar.get("dataset_id"),
            clarifications=clar,
            create_widget=True,
        )


