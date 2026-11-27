"""Process Intelligence, Security Log Intelligence & Relationship Confirmation schema

Revision ID: 0006_process_security_intel
Revises: 0005_intelligent_kpi_engine
Create Date: 2026-11-16 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_process_security_intel"
down_revision: Union[str, None] = "0005_intelligent_kpi_engine"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _col_names(insp, table: str) -> set[str]:
    return {c["name"] for c in insp.get_columns(table)}


def _fk_names(insp, table: str) -> set[str]:
    return {fk["name"] for fk in insp.get_foreign_keys(table) if fk.get("name")}


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    if "dataset_relationships" in tables:
        dr_cols = _col_names(insp, "dataset_relationships")
        dr_fks = _fk_names(insp, "dataset_relationships")
        with op.batch_alter_table("dataset_relationships", schema=None) as batch_op:
            if "user_confirmed" not in dr_cols:
                batch_op.add_column(
                    sa.Column("user_confirmed", sa.Boolean(), nullable=False, server_default=sa.false())
                )
            if "confirmation_note" not in dr_cols:
                batch_op.add_column(
                    sa.Column("confirmation_note", sa.Text(), nullable=False, server_default="")
                )
            if "confirmed_by" not in dr_cols:
                batch_op.add_column(sa.Column("confirmed_by", sa.String(length=64), nullable=True))
            if "confirmed_at" not in dr_cols:
                batch_op.add_column(sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True))
            if "fk_ds_rel_confirmed_by" not in dr_fks:
                batch_op.create_foreign_key(
                    "fk_ds_rel_confirmed_by", "users", ["confirmed_by"], ["id"], ondelete="SET NULL"
                )

    if "process_dataset_mappings" not in tables:
        op.create_table(
            "process_dataset_mappings",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("organization_id", sa.String(length=64), nullable=False),
            sa.Column("dataset_id", sa.String(length=64), nullable=False),
            sa.Column("mapping_json", sa.JSON(), nullable=False),
            sa.Column("reference_sequence_json", sa.JSON(), nullable=False),
            sa.Column("user_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("confidence_json", sa.JSON(), nullable=False),
            sa.Column("confirmed_by", sa.String(length=64), nullable=True),
            sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["confirmed_by"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("organization_id", "dataset_id", name="uq_proc_map_org_ds"),
        )

    if "process_corrective_actions" not in tables:
        op.create_table(
            "process_corrective_actions",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("organization_id", sa.String(length=64), nullable=False),
            sa.Column("dataset_id", sa.String(length=64), nullable=False),
            sa.Column("title", sa.String(length=220), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("target_activity", sa.String(length=180), nullable=False),
            sa.Column("kpi_metric", sa.String(length=64), nullable=False),
            sa.Column("baseline_start", sa.String(length=64), nullable=True),
            sa.Column("baseline_end", sa.String(length=64), nullable=True),
            sa.Column("post_start", sa.String(length=64), nullable=True),
            sa.Column("post_end", sa.String(length=64), nullable=True),
            sa.Column("filter_snapshot_json", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("measured_before", sa.Float(), nullable=True),
            sa.Column("measured_after", sa.Float(), nullable=True),
            sa.Column("delta_pct", sa.Float(), nullable=True),
            sa.Column("sample_before", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("sample_after", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("evaluation_notes_json", sa.JSON(), nullable=False),
            sa.Column("created_by", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )

    if "security_collectors" not in tables:
        op.create_table(
            "security_collectors",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("organization_id", sa.String(length=64), nullable=False),
            sa.Column("name", sa.String(length=180), nullable=False),
            sa.Column("source_type", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("retention_days", sa.Integer(), nullable=False, server_default="90"),
            sa.Column("default_timezone", sa.String(length=64), nullable=False, server_default="UTC"),
            sa.Column("config_json", sa.JSON(), nullable=False),
            sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("events_ingested", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("events_deduplicated", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("events_unparsed", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_by", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )

    if "security_events" not in tables:
        op.create_table(
            "security_events",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("organization_id", sa.String(length=64), nullable=False),
            sa.Column("collector_id", sa.String(length=64), nullable=True),
            sa.Column("dedup_hash", sa.String(length=64), nullable=False),
            sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
            sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("source_timezone", sa.String(length=64), nullable=False, server_default="UTC"),
            sa.Column("vendor", sa.String(length=96), nullable=False, server_default=""),
            sa.Column("product", sa.String(length=96), nullable=False, server_default=""),
            sa.Column("device", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("event_type", sa.String(length=96), nullable=False, server_default="generic"),
            sa.Column("severity", sa.String(length=24), nullable=False, server_default="info"),
            sa.Column("action", sa.String(length=32), nullable=False, server_default="Detect"),
            sa.Column("result", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("src_ip", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("src_port", sa.Integer(), nullable=True),
            sa.Column("dst_ip", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("dst_port", sa.Integer(), nullable=True),
            sa.Column("protocol", sa.String(length=24), nullable=False, server_default=""),
            sa.Column("username", sa.String(length=140), nullable=False, server_default=""),
            sa.Column("hostname", sa.String(length=180), nullable=False, server_default=""),
            sa.Column("threat_name", sa.String(length=180), nullable=False, server_default=""),
            sa.Column("file_path", sa.Text(), nullable=False, server_default=""),
            sa.Column("file_hash", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("rule_name", sa.String(length=180), nullable=False, server_default=""),
            sa.Column("policy_name", sa.String(length=180), nullable=False, server_default=""),
            sa.Column("raw_message", sa.Text(), nullable=False, server_default=""),
            sa.Column("extra_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["collector_id"], ["security_collectors.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("organization_id", "dedup_hash", name="uq_sec_event_org_dedup"),
        )
        op.create_index("ix_sec_events_org_time", "security_events", ["organization_id", "event_time"])
        op.create_index("ix_sec_events_org_action", "security_events", ["organization_id", "action"])
        op.create_index("ix_sec_events_org_host_user", "security_events", ["organization_id", "hostname", "username"])

    if "security_unparsed_events" not in tables:
        op.create_table(
            "security_unparsed_events",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("organization_id", sa.String(length=64), nullable=False),
            sa.Column("collector_id", sa.String(length=64), nullable=True),
            sa.Column("source_name", sa.String(length=180), nullable=False),
            sa.Column("line_number", sa.Integer(), nullable=False),
            sa.Column("raw_line", sa.Text(), nullable=False),
            sa.Column("parse_error", sa.Text(), nullable=False),
            sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="quarantined"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["collector_id"], ["security_collectors.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )

    if "security_alert_rules" not in tables:
        op.create_table(
            "security_alert_rules",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("organization_id", sa.String(length=64), nullable=False),
            sa.Column("name", sa.String(length=180), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("rule_type", sa.String(length=32), nullable=False),
            sa.Column("target_metric", sa.String(length=64), nullable=False),
            sa.Column("condition_json", sa.JSON(), nullable=False),
            sa.Column("baseline_window_hours", sa.Integer(), nullable=False, server_default="168"),
            sa.Column("zscore_threshold", sa.Float(), nullable=False, server_default="2.5"),
            sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_by", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )

    if "security_alerts" not in tables:
        op.create_table(
            "security_alerts",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("organization_id", sa.String(length=64), nullable=False),
            sa.Column("rule_id", sa.String(length=64), nullable=True),
            sa.Column("collector_id", sa.String(length=64), nullable=True),
            sa.Column("title", sa.String(length=220), nullable=False),
            sa.Column("severity", sa.String(length=24), nullable=False),
            sa.Column("observed_value", sa.Float(), nullable=False),
            sa.Column("baseline_value", sa.Float(), nullable=False),
            sa.Column("baseline_stddev", sa.Float(), nullable=False),
            sa.Column("delta_value", sa.Float(), nullable=False),
            sa.Column("sample_count", sa.Integer(), nullable=False),
            sa.Column("window_start", sa.DateTime(timezone=True), nullable=True),
            sa.Column("window_end", sa.DateTime(timezone=True), nullable=True),
            sa.Column("evidence_json", sa.JSON(), nullable=False),
            sa.Column("limitations_fa", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("acknowledged_by", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["acknowledged_by"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["collector_id"], ["security_collectors.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["rule_id"], ["security_alert_rules.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )

    if "security_incidents" not in tables:
        op.create_table(
            "security_incidents",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("organization_id", sa.String(length=64), nullable=False),
            sa.Column("title", sa.String(length=220), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("severity", sa.String(length=24), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("alert_ids_json", sa.JSON(), nullable=False),
            sa.Column("recommended_action_fa", sa.Text(), nullable=False),
            sa.Column("human_approved", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("approved_by", sa.String(length=64), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("approval_note", sa.Text(), nullable=False),
            sa.Column("rollback_plan_fa", sa.Text(), nullable=False),
            sa.Column("resolution_note", sa.Text(), nullable=False),
            sa.Column("created_by", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["approved_by"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    for tbl in (
        "security_incidents",
        "security_alerts",
        "security_alert_rules",
        "security_unparsed_events",
        "security_events",
        "security_collectors",
        "process_corrective_actions",
        "process_dataset_mappings",
    ):
        if tbl in tables:
            op.drop_table(tbl)

    if "dataset_relationships" in tables:
        dr_cols = _col_names(insp, "dataset_relationships")
        dr_fks = _fk_names(insp, "dataset_relationships")
        with op.batch_alter_table("dataset_relationships", schema=None) as batch_op:
            if "fk_ds_rel_confirmed_by" in dr_fks:
                batch_op.drop_constraint("fk_ds_rel_confirmed_by", type_="foreignkey")
            for cname in ("confirmed_at", "confirmed_by", "confirmation_note", "user_confirmed"):
                if cname in dr_cols:
                    batch_op.drop_column(cname)
