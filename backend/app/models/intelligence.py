from datetime import datetime
from typing import Optional
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base
from app.models.common import TimestampMixin, gen_uuid


class ProcessDatasetMapping(Base, TimestampMixin):
    """Persisted user-confirmed Event Log column mapping for Process Intelligence."""

    __tablename__ = "process_dataset_mappings"
    __table_args__ = (UniqueConstraint("organization_id", "dataset_id", name="uq_proc_map_org_ds"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    dataset_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    mapping_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    reference_sequence_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    user_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    confidence_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    confirmed_by: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ProcessCorrectiveAction(Base, TimestampMixin):
    """Tracked process corrective action with reproducible before/after KPI measurements."""

    __tablename__ = "process_corrective_actions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    dataset_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(220), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    target_activity: Mapped[str] = mapped_column(String(180), default="", nullable=False)
    kpi_metric: Mapped[str] = mapped_column(String(64), default="avg_cycle_hours", nullable=False)
    baseline_start: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    baseline_end: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    post_start: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    post_end: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    filter_snapshot_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="planned", nullable=False)
    measured_before: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    measured_after: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    delta_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sample_before: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sample_after: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    evaluation_notes_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class SecurityCollector(Base, TimestampMixin):
    """Configured security log collector with health, retry, deduplication & retention metadata."""

    __tablename__ = "security_collectors"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), default="syslog_security_jsonl", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    retention_days: Mapped[int] = mapped_column(Integer, default=90, nullable=False)
    default_timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    config_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    events_ingested: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    events_deduplicated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    events_unparsed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class SecurityEvent(Base, TimestampMixin):
    """Normalized security event retaining raw message separately behind security_log:view_raw RBAC."""

    __tablename__ = "security_events"
    __table_args__ = (
        UniqueConstraint("organization_id", "dedup_hash", name="uq_sec_event_org_dedup"),
        Index("ix_sec_events_org_time", "organization_id", "event_time"),
        Index("ix_sec_events_org_action", "organization_id", "action"),
        Index("ix_sec_events_org_host_user", "organization_id", "hostname", "username"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    collector_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("security_collectors.id", ondelete="SET NULL"), nullable=True
    )
    dedup_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    vendor: Mapped[str] = mapped_column(String(96), default="", nullable=False)
    product: Mapped[str] = mapped_column(String(96), default="", nullable=False)
    device: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    event_type: Mapped[str] = mapped_column(String(96), default="generic", nullable=False)
    severity: Mapped[str] = mapped_column(String(24), default="info", nullable=False)
    action: Mapped[str] = mapped_column(String(32), default="Detect", nullable=False)
    result: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    src_ip: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    src_port: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    dst_ip: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    dst_port: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    protocol: Mapped[str] = mapped_column(String(24), default="", nullable=False)
    username: Mapped[str] = mapped_column(String(140), default="", nullable=False)
    hostname: Mapped[str] = mapped_column(String(180), default="", nullable=False)
    threat_name: Mapped[str] = mapped_column(String(180), default="", nullable=False)
    file_path: Mapped[str] = mapped_column(Text, default="", nullable=False)
    file_hash: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    rule_name: Mapped[str] = mapped_column(String(180), default="", nullable=False)
    policy_name: Mapped[str] = mapped_column(String(180), default="", nullable=False)
    raw_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    extra_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class SecurityUnparsedEvent(Base, TimestampMixin):
    """Quarantined raw events that failed schema parsing so nothing is silently dropped."""

    __tablename__ = "security_unparsed_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    collector_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("security_collectors.id", ondelete="SET NULL"), nullable=True
    )
    source_name: Mapped[str] = mapped_column(String(180), default="", nullable=False)
    line_number: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    raw_line: Mapped[str] = mapped_column(Text, nullable=False)
    parse_error: Mapped[str] = mapped_column(Text, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="quarantined", nullable=False)


class SecurityAlertRule(Base, TimestampMixin):
    """Threshold and statistical-baseline alert rule definition."""

    __tablename__ = "security_alert_rules"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    rule_type: Mapped[str] = mapped_column(String(32), default="statistical_baseline", nullable=False)
    target_metric: Mapped[str] = mapped_column(String(64), default="failed_auth_count", nullable=False)
    condition_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    baseline_window_hours: Mapped[int] = mapped_column(Integer, default=168, nullable=False)
    zscore_threshold: Mapped[float] = mapped_column(Float, default=2.5, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class SecurityAlert(Base, TimestampMixin):
    """Explainable security alert with observed vs baseline evidence and limitations."""

    __tablename__ = "security_alerts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    rule_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("security_alert_rules.id", ondelete="SET NULL"), nullable=True
    )
    collector_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("security_collectors.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(220), nullable=False)
    severity: Mapped[str] = mapped_column(String(24), default="medium", nullable=False)
    observed_value: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    baseline_value: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    baseline_stddev: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    delta_value: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    window_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    window_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    evidence_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    limitations_fa: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="open", nullable=False)
    acknowledged_by: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class SecurityIncident(Base, TimestampMixin):
    """Advisory security incident lifecycle; never auto-executes blocking/destructive actions."""

    __tablename__ = "security_incidents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(220), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    severity: Mapped[str] = mapped_column(String(24), default="medium", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="open", nullable=False)
    alert_ids_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    recommended_action_fa: Mapped[str] = mapped_column(Text, default="", nullable=False)
    human_approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    approved_by: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    approval_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    rollback_plan_fa: Mapped[str] = mapped_column(Text, default="", nullable=False)
    resolution_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
