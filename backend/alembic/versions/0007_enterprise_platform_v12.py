"""Enterprise platform v1.2.0 schema upgrade (Shared Data Sources, Background Jobs, Named Layouts, BPMN Rules/Layouts, SOC Tier 1/2 + CVE, Action Management)

Revision ID: 0007_enterprise_platform_v12
Revises: 0006_process_security_intel
Create Date: 2026-11-16 14:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007_enterprise_platform_v12"
down_revision: Union[str, None] = "0006_process_security_intel"
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

    if "composite_datasets" not in tables:
        op.create_table(
            "composite_datasets",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("organization_id", sa.String(length=64), nullable=False),
            sa.Column("data_source_id", sa.String(length=64), nullable=False),
            sa.Column("dataset_id", sa.String(length=64), nullable=True),
            sa.Column("name", sa.String(length=180), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("definition_json", sa.JSON(), nullable=False),
            sa.Column("refresh_schedule", sa.String(length=32), nullable=False, server_default="manual"),
            sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_by", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["data_source_id"], ["data_sources.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )

    if "named_dashboard_layouts" not in tables:
        op.create_table(
            "named_dashboard_layouts",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("dashboard_id", sa.String(length=64), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("layout_json", sa.JSON(), nullable=False),
            sa.Column("created_by", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["dashboard_id"], ["dashboards.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("dashboard_id", "name", name="uq_dash_named_layout"),
        )
        op.create_index("ix_named_dashboard_layouts_dashboard_id", "named_dashboard_layouts", ["dashboard_id"])

    if "background_jobs" not in tables:
        op.create_table(
            "background_jobs",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("organization_id", sa.String(length=64), nullable=False),
            sa.Column("job_type", sa.String(length=48), nullable=False),
            sa.Column("title", sa.String(length=220), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
            sa.Column("progress_pct", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("processed_items", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("total_items", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("stage_label", sa.String(length=160), nullable=False, server_default="queued"),
            sa.Column("result_json", sa.JSON(), nullable=False),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_by", sa.String(length=64), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_background_jobs_organization_id", "background_jobs", ["organization_id"])

    if "security_events" in tables:
        cols = _col_names(insp, "security_events")
        with op.batch_alter_table("security_events", schema=None) as batch_op:
            if "threat_category" not in cols:
                batch_op.add_column(sa.Column("threat_category", sa.String(length=96), nullable=False, server_default=""))
            if "threat_status" not in cols:
                batch_op.add_column(sa.Column("threat_status", sa.String(length=48), nullable=False, server_default=""))
            if "extraction_reason" not in cols:
                batch_op.add_column(sa.Column("extraction_reason", sa.String(length=220), nullable=False, server_default=""))
            if "software_name" not in cols:
                batch_op.add_column(sa.Column("software_name", sa.String(length=120), nullable=False, server_default=""))
            if "software_version" not in cols:
                batch_op.add_column(sa.Column("software_version", sa.String(length=64), nullable=False, server_default=""))

    if "security_alerts" in tables:
        cols = _col_names(insp, "security_alerts")
        fks = _fk_names(insp, "security_alerts")
        with op.batch_alter_table("security_alerts", schema=None) as batch_op:
            if "tier" not in cols:
                batch_op.add_column(sa.Column("tier", sa.Integer(), nullable=False, server_default="1"))
            if "duplicate_count" not in cols:
                batch_op.add_column(sa.Column("duplicate_count", sa.Integer(), nullable=False, server_default="1"))
            if "false_positive" not in cols:
                batch_op.add_column(sa.Column("false_positive", sa.Boolean(), nullable=False, server_default=sa.false()))
            if "false_positive_reason" not in cols:
                batch_op.add_column(sa.Column("false_positive_reason", sa.Text(), nullable=False, server_default=""))
            if "assigned_to" not in cols:
                batch_op.add_column(sa.Column("assigned_to", sa.String(length=64), nullable=True))
            if "notes_json" not in cols:
                batch_op.add_column(sa.Column("notes_json", sa.JSON(), nullable=False, server_default="[]"))
            if "escalated_at" not in cols:
                batch_op.add_column(sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True))
            if "fk_sec_alert_assigned_to" not in fks:
                batch_op.create_foreign_key(
                    "fk_sec_alert_assigned_to", "users", ["assigned_to"], ["id"], ondelete="SET NULL"
                )

    if "security_incidents" in tables:
        cols = _col_names(insp, "security_incidents")
        fks = _fk_names(insp, "security_incidents")
        with op.batch_alter_table("security_incidents", schema=None) as batch_op:
            if "assigned_to" not in cols:
                batch_op.add_column(sa.Column("assigned_to", sa.String(length=64), nullable=True))
            if "started_at" not in cols:
                batch_op.add_column(sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
            if "escalated_at" not in cols:
                batch_op.add_column(sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True))
            if "closed_at" not in cols:
                batch_op.add_column(sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True))
            if "mtta_minutes" not in cols:
                batch_op.add_column(sa.Column("mtta_minutes", sa.Float(), nullable=True))
            if "mttr_minutes" not in cols:
                batch_op.add_column(sa.Column("mttr_minutes", sa.Float(), nullable=True))
            if "timeline_json" not in cols:
                batch_op.add_column(sa.Column("timeline_json", sa.JSON(), nullable=False, server_default="[]"))
            if "blast_radius_json" not in cols:
                batch_op.add_column(sa.Column("blast_radius_json", sa.JSON(), nullable=False, server_default="{}"))
            if "hypothesis_notes_fa" not in cols:
                batch_op.add_column(sa.Column("hypothesis_notes_fa", sa.Text(), nullable=False, server_default=""))
            if "evidence_json" not in cols:
                batch_op.add_column(sa.Column("evidence_json", sa.JSON(), nullable=False, server_default="[]"))
            if "final_report_fa" not in cols:
                batch_op.add_column(sa.Column("final_report_fa", sa.Text(), nullable=False, server_default=""))
            if "fk_sec_inc_assigned_to" not in fks:
                batch_op.create_foreign_key(
                    "fk_sec_inc_assigned_to", "users", ["assigned_to"], ["id"], ondelete="SET NULL"
                )

    if "bpmn_bottleneck_rules" not in tables:
        op.create_table(
            "bpmn_bottleneck_rules",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("organization_id", sa.String(length=64), nullable=False),
            sa.Column("dataset_id", sa.String(length=64), nullable=True),
            sa.Column("process_name", sa.String(length=180), nullable=False, server_default="*"),
            sa.Column("step_name", sa.String(length=180), nullable=False, server_default="*"),
            sa.Column("name", sa.String(length=180), nullable=False),
            sa.Column("formula_expression", sa.Text(), nullable=False),
            sa.Column("warning_threshold", sa.Float(), nullable=False, server_default="40.0"),
            sa.Column("critical_threshold", sa.Float(), nullable=False, server_default="70.0"),
            sa.Column("metric_weights_json", sa.JSON(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_by", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_bpmn_bottleneck_rules_organization_id", "bpmn_bottleneck_rules", ["organization_id"])

    if "bpmn_layout_configs" not in tables:
        op.create_table(
            "bpmn_layout_configs",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("organization_id", sa.String(length=64), nullable=False),
            sa.Column("scope_key", sa.String(length=160), nullable=False),
            sa.Column("node_positions_json", sa.JSON(), nullable=False),
            sa.Column("manual_order_json", sa.JSON(), nullable=False),
            sa.Column("display_config_json", sa.JSON(), nullable=False),
            sa.Column("updated_by", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("organization_id", "scope_key", name="uq_bpmn_layout_org_scope"),
        )

    if "security_classification_rules" not in tables:
        op.create_table(
            "security_classification_rules",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("organization_id", sa.String(length=64), nullable=False),
            sa.Column("rule_kind", sa.String(length=32), nullable=False, server_default="status_map"),
            sa.Column("name", sa.String(length=180), nullable=False),
            sa.Column("pattern", sa.Text(), nullable=False),
            sa.Column("target_value", sa.String(length=96), nullable=False),
            sa.Column("severity_override", sa.String(length=24), nullable=True),
            sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_by", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_security_classification_rules_organization_id", "security_classification_rules", ["organization_id"])

    if "security_vulnerabilities" not in tables:
        op.create_table(
            "security_vulnerabilities",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("cve_id", sa.String(length=48), nullable=False),
            sa.Column("software_product", sa.String(length=160), nullable=False, server_default=""),
            sa.Column("affected_version_pattern", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("cvss_score", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("severity", sa.String(length=24), nullable=False, server_default="medium"),
            sa.Column("summary", sa.Text(), nullable=False, server_default=""),
            sa.Column("source", sa.String(length=64), nullable=False, server_default="local_catalog"),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("references_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("cve_id", name="uq_sec_vuln_cve_id"),
        )

    if "security_asset_exposures" not in tables:
        op.create_table(
            "security_asset_exposures",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("organization_id", sa.String(length=64), nullable=False),
            sa.Column("asset_name", sa.String(length=180), nullable=False),
            sa.Column("software_name", sa.String(length=160), nullable=False, server_default="unknown"),
            sa.Column("software_version", sa.String(length=64), nullable=False, server_default="unknown"),
            sa.Column("cve_id", sa.String(length=48), nullable=False, server_default="NEEDS_REVIEW"),
            sa.Column("cvss_score", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("risk_level", sa.String(length=24), nullable=False, server_default="needs_review"),
            sa.Column("remediation_status", sa.String(length=32), nullable=False, server_default="open"),
            sa.Column("notes", sa.Text(), nullable=False, server_default=""),
            sa.Column("related_event_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("organization_id", "asset_name", "cve_id", name="uq_sec_asset_cve"),
        )
        op.create_index("ix_security_asset_exposures_organization_id", "security_asset_exposures", ["organization_id"])

    if "action_items" not in tables:
        op.create_table(
            "action_items",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("organization_id", sa.String(length=64), nullable=False),
            sa.Column("domain", sa.String(length=32), nullable=False, server_default="process"),
            sa.Column("source_ref", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("title", sa.String(length=220), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("priority", sa.String(length=24), nullable=False, server_default="medium"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
            sa.Column("assignee_id", sa.String(length=64), nullable=True),
            sa.Column("assignee_name", sa.String(length=160), nullable=False, server_default=""),
            sa.Column("due_date", sa.String(length=64), nullable=True),
            sa.Column("evidence_before_json", sa.JSON(), nullable=False),
            sa.Column("evidence_after_json", sa.JSON(), nullable=False),
            sa.Column("impact_summary", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_by", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["assignee_id"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_action_items_organization_id", "action_items", ["organization_id"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    for tbl in (
        "action_items",
        "security_asset_exposures",
        "security_vulnerabilities",
        "security_classification_rules",
        "bpmn_layout_configs",
        "bpmn_bottleneck_rules",
        "background_jobs",
        "named_dashboard_layouts",
        "composite_datasets",
    ):
        if tbl in tables:
            op.drop_table(tbl)

    if "security_incidents" in tables:
        cols = _col_names(insp, "security_incidents")
        with op.batch_alter_table("security_incidents", schema=None) as batch_op:
            for c in (
                "final_report_fa",
                "evidence_json",
                "hypothesis_notes_fa",
                "blast_radius_json",
                "timeline_json",
                "mttr_minutes",
                "mtta_minutes",
                "closed_at",
                "escalated_at",
                "started_at",
                "assigned_to",
            ):
                if c in cols:
                    batch_op.drop_column(c)

    if "security_alerts" in tables:
        cols = _col_names(insp, "security_alerts")
        with op.batch_alter_table("security_alerts", schema=None) as batch_op:
            for c in (
                "escalated_at",
                "notes_json",
                "assigned_to",
                "false_positive_reason",
                "false_positive",
                "duplicate_count",
                "tier",
            ):
                if c in cols:
                    batch_op.drop_column(c)

    if "security_events" in tables:
        cols = _col_names(insp, "security_events")
        with op.batch_alter_table("security_events", schema=None) as batch_op:
            for c in (
                "software_version",
                "software_name",
                "extraction_reason",
                "threat_status",
                "threat_category",
            ):
                if c in cols:
                    batch_op.drop_column(c)
