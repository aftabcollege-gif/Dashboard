from typing import Any, Optional
from sqlalchemy.orm import Session
from app.core.permissions import permission_resolver
from app.models.identity import User
from app.models.dashboard import Widget, WidgetDrillRule

DEFAULT_HIERARCHIES: dict[str, list[str]] = {
    "time": ["year", "quarter", "month", "day"],
    "geography": ["country", "region", "province", "city"],
    "organization": ["organization", "division", "department", "unit"],
}


def resolve_drill_state(
    db: Session,
    user: User,
    widget: Widget,
    drill_rule: Optional[WidgetDrillRule],
    drill_stack: list[dict[str, Any]],
    requested_level: Optional[int] = None,
) -> dict[str, Any]:
    """
    Smart Drill-Down & Drill-Through Engine (Section 6) with strict Backend RBAC (Section 20D).
    - Checks independent 'drill_down' permission on widget/drill_down scope.
    - Checks level-specific permissions ('drill_level_1', 'drill_level_2', 'drill_level_3', 'view_raw_records').
    - Resolves active dimension field, cumulative breadcrumb filters, and auto chart-type switch rules!
    """
    level = requested_level if requested_level is not None else len(drill_stack)
    hierarchy = (drill_rule.hierarchy_json if drill_rule else []) or []
    level_rules = (drill_rule.level_rules_json if drill_rule else []) or []

    parent_chain = [("dashboard", widget.dashboard_id), ("dashboard_page", widget.page_id), ("widget", widget.id)]

    # If user requested drill down (level > 0), enforce backend drill permissions strictly!
    if level > 0:
        d_main = permission_resolver.resolve(
            db, user, "drill_down", widget.id, "drill_down", parent_chain=parent_chain
        )
        if not d_main.allowed:
            raise PermissionError("Drill-down permission denied for this widget")

        if level == 1:
            d_lvl = permission_resolver.resolve(db, user, "drill_down", widget.id, "drill_level_1", parent_chain)
            if not d_lvl.allowed:
                raise PermissionError("Drill level 1 permission denied")
        elif level == 2:
            d_lvl = permission_resolver.resolve(db, user, "drill_down", widget.id, "drill_level_2", parent_chain)
            if not d_lvl.allowed:
                raise PermissionError("Drill level 2 permission denied")
        elif level >= 3:
            d_lvl = permission_resolver.resolve(db, user, "drill_down", widget.id, "drill_level_3", parent_chain)
            if not d_lvl.allowed:
                raise PermissionError("Drill level 3 permission denied")

    # Determine active dimension column & effective chart_type from level_rules
    effective_dimension: Optional[str] = None
    effective_chart_type: str = widget.chart_type
    is_raw_level = False

    if hierarchy and 0 <= level < len(hierarchy):
        step = hierarchy[level]
        effective_dimension = step.get("field") if isinstance(step, dict) else str(step)
    elif hierarchy and level >= len(hierarchy):
        # Raw record drill level
        d_raw = permission_resolver.resolve(db, user, "drill_down", widget.id, "view_raw_records", parent_chain)
        if not d_raw.allowed:
            raise PermissionError("Raw transaction record access denied")
        is_raw_level = True
        effective_chart_type = "table"

    for lr in level_rules:
        if isinstance(lr, dict) and int(lr.get("level", -1)) == level:
            if lr.get("chart_type"):
                effective_chart_type = str(lr["chart_type"])
            if lr.get("field"):
                effective_dimension = str(lr["field"])

    # Convert drill_stack entries into cumulative context filters
    drill_filters: list[dict[str, Any]] = []
    breadcrumbs: list[dict[str, Any]] = [{"level": 0, "label": "All", "field": None, "value": None}]
    for idx, item in enumerate(drill_stack):
        f_name = item.get("field")
        f_val = item.get("value")
        if f_name and f_val is not None:
            drill_filters.append(
                {"field_name": f_name, "operator": "eq", "value": f_val, "is_active": True}
            )
            breadcrumbs.append(
                {"level": idx + 1, "label": f"{f_name}: {f_val}", "field": f_name, "value": f_val}
            )

    return {
        "level": level,
        "hierarchy": hierarchy,
        "effective_dimension": effective_dimension,
        "effective_chart_type": effective_chart_type,
        "is_raw_level": is_raw_level,
        "drill_filters": drill_filters,
        "breadcrumbs": breadcrumbs,
        "can_drill_down": (
            level < len(hierarchy)
            and permission_resolver.resolve(
                db, user, "drill_down", widget.id, "drill_down", parent_chain=parent_chain
            ).allowed
        ),
        "drill_through_page_id": drill_rule.drill_through_page_id if drill_rule else None,
    }
