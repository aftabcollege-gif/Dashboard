import math
import re
from typing import Any, Optional
from sqlalchemy.orm import Session
from app.engines.data_engine import DataProfiler
from app.engines.query_engine import execute_dataset_sql
from app.engines.warehouse import warehouse
from app.models.dashboard import (
    Dashboard,
    DashboardFilter,
    DashboardLayout,
    DashboardPage,
    Widget,
    WidgetConfig,
    WidgetDrillRule,
    WidgetQuery,
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
    def recommend_widgets_for_dataset(db: Session, dataset: Dataset) -> dict[str, Any]:
        fields = db.query(DatasetField).filter(DatasetField.dataset_id == dataset.id).all()
        if not fields:
            # Infer from warehouse table directly
            cols = warehouse.describe_table(dataset.warehouse_table)
            measures = [
                c["column_name"]
                for c in cols
                if any(k in c["column_type"].lower() for k in ("int", "double", "float", "decimal"))
                and not c["column_name"].endswith("_id")
                and c["column_name"] not in ("year", "month", "quarter", "day")
            ]
            times = [
                c["column_name"]
                for c in cols
                if any(k in c["column_name"].lower() for k in ("date", "month", "year", "quarter"))
            ]
            geos = [
                c["column_name"]
                for c in cols
                if any(k in c["column_name"].lower() for k in ("country", "region", "province", "city"))
            ]
            dims = [
                c["column_name"]
                for c in cols
                if c["column_name"] not in measures and c["column_name"] not in times and not c["column_name"].endswith("_id")
            ]
        else:
            measures = [f.field_name for f in fields if f.semantic_role == "measure"]
            times = [f.field_name for f in fields if f.semantic_role == "time"]
            geos = [f.field_name for f in fields if f.semantic_role == "geo"]
            dims = [f.field_name for f in fields if f.semantic_role in ("dimension", "geo")]

        primary_m = measures[0] if measures else "*"
        secondary_m = measures[1] if len(measures) > 1 else None
        primary_t = times[0] if times else None
        primary_d = dims[0] if dims else None
        secondary_d = dims[1] if len(dims) > 1 else (geos[0] if geos else primary_d)

        specs: list[dict[str, Any]] = []
        # Up to 3 KPI cards
        for idx, m in enumerate(measures[:3]):
            specs.append(
                {
                    "title": f"Total {m.replace('_', ' ').title()}",
                    "chart_type": "kpi",
                    "layout": {"x": idx * 4, "y": 0, "w": 4, "h": 3},
                    "visual_spec": {"y_field": m, "aggregation": "SUM"},
                    "style": {"number_format": "currency" if any(k in m for k in ("rev", "cost", "profit", "price", "amount")) else "number"},
                }
            )
        if not measures:
            specs.append(
                {
                    "title": f"Total Records ({dataset.name})",
                    "chart_type": "kpi",
                    "layout": {"x": 0, "y": 0, "w": 4, "h": 3},
                    "visual_spec": {"y_field": "*", "aggregation": "COUNT"},
                    "style": {"number_format": "number"},
                }
            )

        # Time-series trend chart
        if primary_t:
            specs.append(
                {
                    "title": f"{primary_m.replace('_', ' ').title()} Trend Over Time",
                    "chart_type": "area",
                    "layout": {"x": 0, "y": 3, "w": 8, "h": 7},
                    "visual_spec": {
                        "x_field": primary_t,
                        "y_field": primary_m,
                        "aggregation": "SUM" if primary_m != "*" else "COUNT",
                    },
                    "drill_hierarchy": [t for t in ("year", "quarter", "month") if t in (times + dims)] or [primary_t],
                }
            )

        # Category Breakdown Donut / Bar
        if primary_d:
            specs.append(
                {
                    "title": f"{primary_m.replace('_', ' ').title()} by {primary_d.replace('_', ' ').title()}",
                    "chart_type": "bar",
                    "layout": {"x": 8 if primary_t else 0, "y": 3, "w": 4 if primary_t else 6, "h": 7},
                    "visual_spec": {
                        "x_field": primary_d,
                        "y_field": primary_m,
                        "aggregation": "SUM" if primary_m != "*" else "COUNT",
                    },
                }
            )

        if secondary_d:
            specs.append(
                {
                    "title": f"Share by {secondary_d.replace('_', ' ').title()}",
                    "chart_type": "donut",
                    "layout": {"x": 0, "y": 10, "w": 5, "h": 7},
                    "visual_spec": {
                        "x_field": secondary_d,
                        "y_field": primary_m,
                        "aggregation": "SUM" if primary_m != "*" else "COUNT",
                    },
                }
            )

        # Comparison table
        specs.append(
            {
                "title": f"Detailed Breakdown — {dataset.name}",
                "chart_type": "table",
                "layout": {"x": 5 if secondary_d else 0, "y": 10, "w": 7 if secondary_d else 12, "h": 7},
                "visual_spec": {
                    "x_field": primary_d or primary_t,
                    "y_field": primary_m,
                    "secondary_y_field": secondary_m,
                    "aggregation": "SUM" if primary_m != "*" else "COUNT",
                },
            }
        )

        suggested_filters = [
            {"name": d.replace("_", " ").title(), "field_name": d, "filter_type": "multi_select"}
            for d in (dims + geos)[:3]
        ]
        return {
            "profile": DataProfiler.profile_table(dataset.warehouse_table),
            "suggested_widgets": specs,
            "suggested_filters": suggested_filters,
        }

    @staticmethod
    def generate_dashboard_from_dataset(
        db: Session,
        user: User,
        workspace_id: str,
        dataset: Dataset,
        title: Optional[str] = None,
    ) -> Dashboard:
        rec = AiAnalyticsEngine.recommend_widgets_for_dataset(db, dataset)
        dash = Dashboard(
            workspace_id=workspace_id,
            title=title or f"AI Executive Insights — {dataset.name}",
            description=f"Auto-generated AI analytical dashboard from dataset '{dataset.name}'",
            created_by=user.id,
        )
        db.add(dash)
        db.flush()

        page = DashboardPage(dashboard_id=dash.id, title="AI Overview", page_order=0)
        db.add(page)
        db.flush()

        layout_items = []
        for idx, spec in enumerate(rec["suggested_widgets"]):
            lay = spec["layout"]
            w = Widget(
                dashboard_id=dash.id,
                page_id=page.id,
                title=spec["title"],
                chart_type=spec["chart_type"],
                dataset_id=dataset.id,
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
                    style_json=spec.get("style", {}),
                    visualization_json={"show_legend": True, "show_tooltip": True, "data_labels": False},
                    interactions_json={"cross_filter": True},
                    advanced_json={"ai_generated": True},
                )
            )
            db.add(
                WidgetQuery(
                    widget_id=w.id,
                    dataset_id=dataset.id,
                    query_mode="visual",
                    visual_spec_json=spec["visual_spec"],
                )
            )
            if spec.get("drill_hierarchy"):
                db.add(
                    WidgetDrillRule(
                        widget_id=w.id,
                        hierarchy_json=[{"field": f} for f in spec["drill_hierarchy"]],
                        level_rules_json=[
                            {"level": 0, "chart_type": "bar"},
                            {"level": 1, "chart_type": "line"},
                            {"level": 2, "chart_type": "area"},
                        ],
                    )
                )

        db.add(DashboardLayout(page_id=page.id, columns_count=12, layout_json=layout_items))

        for sf in rec["suggested_filters"]:
            db.add(
                DashboardFilter(
                    dashboard_id=dash.id,
                    scope="global",
                    name=sf["name"],
                    field_name=sf["field_name"],
                    dataset_id=dataset.id,
                    filter_type=sf["filter_type"],
                    operator="in",
                )
            )

        db.commit()
        db.refresh(dash)
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
        }
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
        if not chosen_measure:
            chosen_measure = f'{agg}("{num_cols[0]}")' if num_cols else "COUNT(*)"

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
            for candidate in ("region", "department", "category", "month", "year", "product"):
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
        return {
            "question": question,
            "generated_sql": sql,
            "recommended_chart_type": chart_type,
            "x_field": chosen_dim,
            "y_field": "value",
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
    def _extract_chart_type(norm_low: str) -> Optional[str]:
        chart_rules: list[tuple[list[str], str]] = [
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
    def _get_dataset_schema(db: Session, ds: Dataset) -> tuple[list[dict[str, str]], list[str], list[str]]:
        fields = db.query(DatasetField).filter(DatasetField.dataset_id == ds.id).all()
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
                    cinfo, _, _ = AiAnalyticsEngine._get_dataset_schema(db, d)
                    if dim_mention and AiAnalyticsEngine._match_column(dim_mention, cinfo, role_filter="dim"):
                        matching_ds.append(d)
                    elif not dim_mention and AiAnalyticsEngine._match_column(probe_text, cinfo):
                        matching_ds.append(d)
                if len(matching_ds) == 1:
                    chosen_ds = matching_ds[0]

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
        cols_info, num_cols, dim_cols = AiAnalyticsEngine._get_dataset_schema(db, chosen_ds)

        # Question B: Chart type if completely unspecified
        if not chart_type:
            questions.append(
                {
                    "key": "chart_type",
                    "question": "نوع ویجت / نمودار مشخص نیست. چه نوع نمایشی بسازم؟",
                    "options": [
                        {"value": "bar", "label": "نمودار میله‌ای (Bar)"},
                        {"value": "kpi", "label": "کارت شاخص عددی (KPI)"},
                        {"value": "line", "label": "نمودار خطی (Line)"},
                        {"value": "pie", "label": "نمودار دایره‌ای (Pie)"},
                        {"value": "table", "label": "جدول داده (Table)"},
                    ],
                }
            )

        # 6. Resolve dimension (x_field) when chart is not single-value KPI/gauge
        x_field: Optional[str] = clarifications.get("x_field")
        if x_field == "__none__":
            x_field = None
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
            y_lbl = "تعداد" if eff_y == "*" else eff_y.replace("_", " ").title()
            extracted_title = f"{y_lbl} بر اساس {x_field}" if x_field else f"{eff_agg} {y_lbl}"

        visual_spec: dict[str, Any] = {
            "y_field": eff_y,
            "aggregation": eff_agg,
            "limit": 100,
        }
        if x_field and eff_chart not in ("kpi", "gauge"):
            visual_spec["x_field"] = x_field

        is_currency = any(k in eff_y.lower() for k in ("rev", "cost", "profit", "price", "amount", "salary", "budget"))
        style_cfg = {"number_format": "currency" if (is_currency and eff_agg != "COUNT") else "number"}
        viz_cfg = {"show_legend": True, "show_tooltip": True, "data_labels": False}

        sec_ctx = build_data_security_context(db, user, chosen_ds.id)
        sql, params, _ = build_visual_query_sql(
            db, chosen_ds, visual_spec, [], sec_ctx.rls_where_clauses, sec_ctx.rls_params
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
            enforce_permission(db, user, "dashboard", dashboard_id, "add_widget")
            target_page_id = page_id
            if not target_page_id:
                p0 = (
                    db.query(DashboardPage)
                    .filter(DashboardPage.dashboard_id == dashboard_id)
                    .order_by(DashboardPage.page_order.asc())
                    .first()
                )
                target_page_id = p0.id if p0 else None
            if target_page_id:
                existing_widgets = db.query(Widget).filter(
                    Widget.dashboard_id == dashboard_id, Widget.page_id == target_page_id
                ).all()
                next_y = max((w.layout_y + w.layout_h for w in existing_widgets), default=0)
                lay_w = 4 if eff_chart in ("kpi", "gauge") else 6
                lay_h = 3 if eff_chart in ("kpi", "gauge") else 7
                w = Widget(
                    dashboard_id=dashboard_id,
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
                    DataLineage.target_id == dashboard_id,
                ).first():
                    db.add(
                        DataLineage(
                            source_type="dataset",
                            source_id=chosen_ds.id,
                            target_type="dashboard",
                            target_id=dashboard_id,
                        )
                    )
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
            "message_fa": (
                f"ویجت «{extracted_title}» از منبع «{chosen_ds.name}» با موفقیت ساخته شد و به داشبورد اضافه گردید."
                if created_widget
                else f"پیکربندی ویجت «{extracted_title}» از منبع «{chosen_ds.name}» آماده شد."
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

