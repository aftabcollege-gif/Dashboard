from datetime import datetime
from typing import Optional
from sqlalchemy import String, Boolean, Integer, BigInteger, Float, Text, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base
from app.models.common import TimestampMixin, gen_uuid, utcnow


class CacheMetadata(Base, TimestampMixin):
    __tablename__ = "cache_metadata"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    cache_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    organization_id: Mapped[str] = mapped_column(String(64), nullable=False)
    dashboard_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    widget_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    dataset_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    data_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    permission_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    filter_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hit_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MaterializedViewMetadata(Base, TimestampMixin):
    __tablename__ = "materialized_view_metadata"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    dataset_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    view_name: Mapped[str] = mapped_column(String(180), nullable=False, unique=True)
    granularity: Mapped[str] = mapped_column(String(32), nullable=False)
    definition_sql: Mapped[str] = mapped_column(Text, nullable=False)
    row_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    last_refreshed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class QueryExecutionLog(Base):
    __tablename__ = "query_execution_logs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    dataset_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    widget_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    execution_ms: Mapped[float] = mapped_column(Float, nullable=False)
    rows_returned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="success", nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )


class DashboardPerformanceLog(Base):
    __tablename__ = "dashboard_performance_logs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    dashboard_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    load_ms: Mapped[float] = mapped_column(Float, nullable=False)
    widget_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cache_hits: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cache_misses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )


class WidgetPerformanceLog(Base):
    __tablename__ = "widget_performance_logs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    widget_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    dashboard_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    load_ms: Mapped[float] = mapped_column(Float, nullable=False)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    rows_returned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
