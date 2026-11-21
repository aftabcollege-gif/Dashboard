from typing import Optional
from sqlalchemy import String, Boolean, Integer, Text, ForeignKey, UniqueConstraint, JSON, Index
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base
from app.models.common import TimestampMixin, gen_uuid


class Workspace(Base, TimestampMixin):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class DashboardTheme(Base, TimestampMixin):
    __tablename__ = "dashboard_themes"
    __table_args__ = (UniqueConstraint("organization_id", "slug", name="uq_org_theme_slug"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), default="light", nullable=False)
    tokens_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class Dashboard(Base, TimestampMixin):
    __tablename__ = "dashboards"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(220), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    theme_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("dashboard_themes.id", ondelete="SET NULL"), nullable=True
    )
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_template: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    embed_token: Mapped[Optional[str]] = mapped_column(String(128), unique=True, nullable=True)
    refresh_interval_sec: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    settings_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class DashboardPage(Base, TimestampMixin):
    __tablename__ = "dashboard_pages"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    dashboard_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    page_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_drill_through: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    settings_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class DashboardLayout(Base, TimestampMixin):
    __tablename__ = "dashboard_layouts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    page_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("dashboard_pages.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    columns_count: Mapped[int] = mapped_column(Integer, default=12, nullable=False)
    row_height: Mapped[int] = mapped_column(Integer, default=42, nullable=False)
    gap_px: Mapped[int] = mapped_column(Integer, default=16, nullable=False)
    grid_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    layout_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)


class Widget(Base, TimestampMixin):
    __tablename__ = "widgets"
    __table_args__ = (Index("idx_widgets_dash_page", "dashboard_id", "page_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    dashboard_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False
    )
    page_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("dashboard_pages.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    chart_type: Mapped[str] = mapped_column(String(48), default="bar", nullable=False)
    dataset_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    layout_x: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    layout_y: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    layout_w: Mapped[int] = mapped_column(Integer, default=6, nullable=False)
    layout_h: Mapped[int] = mapped_column(Integer, default=7, nullable=False)


class WidgetConfig(Base, TimestampMixin):
    __tablename__ = "widget_configs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    widget_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("widgets.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    style_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    visualization_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    interactions_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    advanced_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class WidgetQuery(Base, TimestampMixin):
    __tablename__ = "widget_queries"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    widget_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("widgets.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    dataset_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    saved_query_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    query_mode: Mapped[str] = mapped_column(String(32), default="visual", nullable=False)
    sql_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    visual_spec_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    parameters_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class WidgetDrillRule(Base, TimestampMixin):
    __tablename__ = "widget_drill_rules"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    widget_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("widgets.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    hierarchy_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    level_rules_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    drill_through_page_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("dashboard_pages.id", ondelete="SET NULL"), nullable=True
    )


class DashboardFilter(Base, TimestampMixin):
    __tablename__ = "dashboard_filters"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    dashboard_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("dashboard_pages.id", ondelete="CASCADE"), nullable=True
    )
    scope: Mapped[str] = mapped_column(String(24), default="dashboard", nullable=False)  # global|dashboard
    name: Mapped[str] = mapped_column(String(140), nullable=False)
    field_name: Mapped[str] = mapped_column(String(140), nullable=False)
    dataset_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    filter_type: Mapped[str] = mapped_column(String(48), default="multi_select", nullable=False)
    operator: Mapped[str] = mapped_column(String(32), default="in", nullable=False)
    default_value_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    target_widget_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    excluded_widget_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    parent_filter_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    config_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class WidgetFilter(Base, TimestampMixin):
    __tablename__ = "widget_filters"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    widget_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("widgets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    field_name: Mapped[str] = mapped_column(String(140), nullable=False)
    filter_type: Mapped[str] = mapped_column(String(48), default="dropdown", nullable=False)
    operator: Mapped[str] = mapped_column(String(32), default="eq", nullable=False)
    value_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class DashboardVersion(Base, TimestampMixin):
    __tablename__ = "dashboard_versions"
    __table_args__ = (UniqueConstraint("dashboard_id", "version_number", name="uq_dash_ver"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    dashboard_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    snapshot_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class DashboardComment(Base, TimestampMixin):
    __tablename__ = "dashboard_comments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    dashboard_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False, index=True
    )
    widget_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    author_name: Mapped[str] = mapped_column(String(150), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)


class DashboardState(Base, TimestampMixin):
    """Dashboard State Engine: persists per-user filter state, drill stack, active page, chart overrides."""
    __tablename__ = "dashboard_states"
    __table_args__ = (UniqueConstraint("dashboard_id", "user_id", name="uq_dash_user_state"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    dashboard_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    active_page_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    filter_state_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    drill_state_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    widget_overrides_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
