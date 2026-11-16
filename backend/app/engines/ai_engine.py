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
