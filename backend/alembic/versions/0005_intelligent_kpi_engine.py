"""Intelligent KPI Engine semantic classification, aggregation policy, and KPI metadata

Revision ID: 0005_intelligent_kpi_engine
Revises: 0004_descriptive_builder
Create Date: 2026-11-26 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_intelligent_kpi_engine"
down_revision: Union[str, None] = "0004_descriptive_builder"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _col_names(insp, table: str) -> set[str]:
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    df_cols = _col_names(insp, "dataset_fields")
    with op.batch_alter_table("dataset_fields", schema=None) as batch_op:
        if "semantic_class" not in df_cols:
            batch_op.add_column(sa.Column("semantic_class", sa.String(length=48), nullable=False, server_default=""))
        if "classification_confidence" not in df_cols:
            batch_op.add_column(
                sa.Column("classification_confidence", sa.Float(), nullable=False, server_default=sa.text("0.0"))
            )
        if "aggregation_policy" not in df_cols:
            batch_op.add_column(
                sa.Column("aggregation_policy", sa.String(length=32), nullable=False, server_default="")
            )
        if "user_override_json" not in df_cols:
            batch_op.add_column(
                sa.Column("user_override_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
            )

    sm_cols = _col_names(insp, "semantic_metrics")
    with op.batch_alter_table("semantic_metrics", schema=None) as batch_op:
        if "category" not in sm_cols:
            batch_op.add_column(
                sa.Column("category", sa.String(length=48), nullable=False, server_default="analytical")
            )
        if "confidence" not in sm_cols:
            batch_op.add_column(sa.Column("confidence", sa.Float(), nullable=False, server_default=sa.text("1.0")))
        if "target_value" not in sm_cols:
            batch_op.add_column(sa.Column("target_value", sa.Float(), nullable=True))
        if "kpi_metadata_json" not in sm_cols:
            batch_op.add_column(
                sa.Column("kpi_metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
            )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    sm_cols = _col_names(insp, "semantic_metrics")
    with op.batch_alter_table("semantic_metrics", schema=None) as batch_op:
        for cname in ("kpi_metadata_json", "target_value", "confidence", "category"):
            if cname in sm_cols:
                batch_op.drop_column(cname)

    df_cols = _col_names(insp, "dataset_fields")
    with op.batch_alter_table("dataset_fields", schema=None) as batch_op:
        for cname in ("user_override_json", "aggregation_policy", "classification_confidence", "semantic_class"):
            if cname in df_cols:
                batch_op.drop_column(cname)
