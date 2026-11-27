"""Stage 4: dashboard public override governance, ETL fencing tokens, and bootstrap singleton lock

Revision ID: 0003_stage4_security_reliability
Revises: 0002_stage3_external_identity_scope
Create Date: 2026-11-22 18:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_stage4_security_reliability"
down_revision: Union[str, None] = "0002_stage3_external_identity_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _col_names(insp, table: str) -> set[str]:
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("system_bootstrap_state"):
        op.create_table(
            "system_bootstrap_state",
            sa.Column("id", sa.String(length=32), nullable=False),
            sa.Column("completed", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("bootstrapped_by", sa.String(length=64), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    dash_cols = _col_names(insp, "dashboards")
    with op.batch_alter_table("dashboards", schema=None) as batch_op:
        if "allow_public_restricted" not in dash_cols:
            batch_op.add_column(
                sa.Column("allow_public_restricted", sa.Boolean(), nullable=False, server_default=sa.false())
            )
        if "public_restricted_reason" not in dash_cols:
            batch_op.add_column(sa.Column("public_restricted_reason", sa.Text(), nullable=True))
        if "public_restricted_by" not in dash_cols:
            batch_op.add_column(sa.Column("public_restricted_by", sa.String(length=64), nullable=True))
        if "public_restricted_at" not in dash_cols:
            batch_op.add_column(sa.Column("public_restricted_at", sa.DateTime(timezone=True), nullable=True))

    pipe_cols = _col_names(insp, "etl_pipelines")
    with op.batch_alter_table("etl_pipelines", schema=None) as batch_op:
        if "fence_token" not in pipe_cols:
            batch_op.add_column(
                sa.Column("fence_token", sa.BigInteger(), nullable=False, server_default=sa.text("0"))
            )
        if "active_worker_id" not in pipe_cols:
            batch_op.add_column(sa.Column("active_worker_id", sa.String(length=128), nullable=True))
        if "heartbeat_at" not in pipe_cols:
            batch_op.add_column(sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
        if "lease_expires_at" not in pipe_cols:
            batch_op.add_column(sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))

    run_cols = _col_names(insp, "etl_job_runs")
    with op.batch_alter_table("etl_job_runs", schema=None) as batch_op:
        if "fence_token" not in run_cols:
            batch_op.add_column(
                sa.Column("fence_token", sa.BigInteger(), nullable=False, server_default=sa.text("0"))
            )
        if "worker_id" not in run_cols:
            batch_op.add_column(sa.Column("worker_id", sa.String(length=128), nullable=True))
        if "heartbeat_at" not in run_cols:
            batch_op.add_column(sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
        if "lease_expires_at" not in run_cols:
            batch_op.add_column(sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    run_cols = _col_names(insp, "etl_job_runs")
    with op.batch_alter_table("etl_job_runs", schema=None) as batch_op:
        for cname in ("lease_expires_at", "heartbeat_at", "worker_id", "fence_token"):
            if cname in run_cols:
                batch_op.drop_column(cname)

    pipe_cols = _col_names(insp, "etl_pipelines")
    with op.batch_alter_table("etl_pipelines", schema=None) as batch_op:
        for cname in ("lease_expires_at", "heartbeat_at", "active_worker_id", "fence_token"):
            if cname in pipe_cols:
                batch_op.drop_column(cname)

    dash_cols = _col_names(insp, "dashboards")
    with op.batch_alter_table("dashboards", schema=None) as batch_op:
        for cname in (
            "public_restricted_at",
            "public_restricted_by",
            "public_restricted_reason",
            "allow_public_restricted",
        ):
            if cname in dash_cols:
                batch_op.drop_column(cname)

    if insp.has_table("system_bootstrap_state"):
        op.drop_table("system_bootstrap_state")
