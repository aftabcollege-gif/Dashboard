from datetime import datetime
from typing import Optional
from sqlalchemy import String, Boolean, Integer, BigInteger, Float, Text, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base
from app.models.common import TimestampMixin, gen_uuid, utcnow


class EtlPipeline(Base, TimestampMixin):
    __tablename__ = "etl_pipelines"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    data_source_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("data_sources.id", ondelete="SET NULL"), nullable=True
    )
    target_dataset_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("datasets.id", ondelete="SET NULL"), nullable=True
    )
    extract_mode: Mapped[str] = mapped_column(String(32), default="incremental", nullable=False)
    incremental_column: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    primary_key_column: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    source_query: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retry_policy_json: Mapped[dict] = mapped_column(
        JSON,
        default=lambda: {"max_retries": 3, "initial_backoff_sec": 1.0, "backoff_multiplier": 2.0},
        nullable=False,
    )
    priority: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_running: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    fence_token: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    active_worker_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class EtlPipelineStep(Base, TimestampMixin):
    __tablename__ = "etl_pipeline_steps"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    pipeline_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("etl_pipelines.id", ondelete="CASCADE"), nullable=False
    )
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    step_type: Mapped[str] = mapped_column(String(48), nullable=False)
    config_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class EtlSchedule(Base, TimestampMixin):
    __tablename__ = "etl_schedules"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    pipeline_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("etl_pipelines.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    schedule_type: Mapped[str] = mapped_column(String(32), default="interval", nullable=False)
    interval_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cron_expression: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    allowed_window_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    depends_on_pipeline_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    next_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class EtlJob(Base, TimestampMixin):
    __tablename__ = "etl_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    pipeline_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("etl_pipelines.id", ondelete="CASCADE"), nullable=False
    )
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class EtlJobRun(Base, TimestampMixin):
    __tablename__ = "etl_job_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    job_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("etl_jobs.id", ondelete="CASCADE"), nullable=False
    )
    pipeline_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("etl_pipelines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    fence_token: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    worker_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    rows_extracted: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    rows_inserted: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    rows_updated: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    rows_unchanged: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    log_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class EtlCheckpoint(Base, TimestampMixin):
    __tablename__ = "etl_checkpoints"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    pipeline_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("etl_pipelines.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    last_cursor_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_success_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    row_hashes_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class EtlError(Base, TimestampMixin):
    __tablename__ = "etl_errors"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    pipeline_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("etl_pipelines.id", ondelete="CASCADE"), nullable=False
    )
    job_run_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("etl_job_runs.id", ondelete="SET NULL"), nullable=True
    )
    error_type: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    traceback: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class EtlMetric(Base, TimestampMixin):
    __tablename__ = "etl_metrics"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    pipeline_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("etl_pipelines.id", ondelete="CASCADE"), nullable=False
    )
    job_run_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("etl_job_runs.id", ondelete="SET NULL"), nullable=True
    )
    cpu_percent: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    memory_mb: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    storage_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    throughput_rows_sec: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)


class ScheduledReport(Base, TimestampMixin):
    __tablename__ = "scheduled_reports"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    dashboard_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    format: Mapped[str] = mapped_column(String(16), default="pdf", nullable=False)  # pdf|xlsx|csv
    cron_expression: Mapped[str] = mapped_column(String(120), default="0 8 * * 1", nullable=False)
    recipients_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_file_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
