"""Descriptive Dashboard Builder schema metadata, formula dependencies, and relationship governance

Revision ID: 0004_descriptive_builder
Revises: 0003_stage4_security_reliability
Create Date: 2026-11-24 10:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_descriptive_builder"
down_revision: Union[str, None] = "0003_stage4_security_reliability"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _col_names(insp, table: str) -> set[str]:
    return {c["name"] for c in insp.get_columns(table)}


def _fk_names(insp, table: str) -> set[str]:
    return {fk["name"] for fk in insp.get_foreign_keys(table) if fk.get("name")}


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    df_cols = _col_names(insp, "dataset_fields")
    df_fks = _fk_names(insp, "dataset_fields")
    with op.batch_alter_table("dataset_fields", schema=None) as batch_op:
        if "description" not in df_cols:
            batch_op.add_column(sa.Column("description", sa.Text(), nullable=False, server_default=""))
        if "calculation_type" not in df_cols:
            batch_op.add_column(
                sa.Column("calculation_type", sa.String(length=32), nullable=False, server_default="physical")
            )
        if "dependent_fields_json" not in df_cols:
            batch_op.add_column(
                sa.Column("dependent_fields_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
            )
        if "access_scope" not in df_cols:
            batch_op.add_column(
                sa.Column("access_scope", sa.String(length=32), nullable=False, server_default="dataset")
            )
        if "version" not in df_cols:
            batch_op.add_column(sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")))
        if "created_by" not in df_cols:
            batch_op.add_column(sa.Column("created_by", sa.String(length=64), nullable=True))
        if "fk_dataset_fields_created_by" not in df_fks:
            batch_op.create_foreign_key(
                "fk_dataset_fields_created_by", "users", ["created_by"], ["id"], ondelete="SET NULL"
            )

    rel_cols = _col_names(insp, "dataset_relationships")
    with op.batch_alter_table("dataset_relationships", schema=None) as batch_op:
        if "is_active" not in rel_cols:
            batch_op.add_column(
                sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true())
            )
        if "metadata_json" not in rel_cols:
            batch_op.add_column(
                sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
            )
        if "validated_at" not in rel_cols:
            batch_op.add_column(sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True))

    sm_cols = _col_names(insp, "semantic_metrics")
    sm_fks = _fk_names(insp, "semantic_metrics")
    with op.batch_alter_table("semantic_metrics", schema=None) as batch_op:
        if "metric_kind" not in sm_cols:
            batch_op.add_column(
                sa.Column("metric_kind", sa.String(length=32), nullable=False, server_default="analytical_metric")
            )
        if "output_data_type" not in sm_cols:
            batch_op.add_column(
                sa.Column("output_data_type", sa.String(length=48), nullable=False, server_default="double")
            )
        if "dependent_datasets_json" not in sm_cols:
            batch_op.add_column(
                sa.Column("dependent_datasets_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
            )
        if "dependent_fields_json" not in sm_cols:
            batch_op.add_column(
                sa.Column("dependent_fields_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
            )
        if "dependent_metrics_json" not in sm_cols:
            batch_op.add_column(
                sa.Column("dependent_metrics_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
            )
        if "access_scope" not in sm_cols:
            batch_op.add_column(
                sa.Column("access_scope", sa.String(length=32), nullable=False, server_default="organization")
            )
        if "version" not in sm_cols:
            batch_op.add_column(sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")))
        if "created_by" not in sm_cols:
            batch_op.add_column(sa.Column("created_by", sa.String(length=64), nullable=True))
        if "fk_semantic_metrics_created_by" not in sm_fks:
            batch_op.create_foreign_key(
                "fk_semantic_metrics_created_by", "users", ["created_by"], ["id"], ondelete="SET NULL"
            )

    dm_cols = _col_names(insp, "data_models")
    dm_fks = _fk_names(insp, "data_models")
    with op.batch_alter_table("data_models", schema=None) as batch_op:
        if "description" not in dm_cols:
            batch_op.add_column(sa.Column("description", sa.Text(), nullable=False, server_default=""))
        if "version" not in dm_cols:
            batch_op.add_column(sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")))
        if "created_by" not in dm_cols:
            batch_op.add_column(sa.Column("created_by", sa.String(length=64), nullable=True))
        if "fk_data_models_created_by" not in dm_fks:
            batch_op.create_foreign_key(
                "fk_data_models_created_by", "users", ["created_by"], ["id"], ondelete="SET NULL"
            )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    dm_cols = _col_names(insp, "data_models")
    dm_fks = _fk_names(insp, "data_models")
    with op.batch_alter_table("data_models", schema=None) as batch_op:
        if "fk_data_models_created_by" in dm_fks:
            batch_op.drop_constraint("fk_data_models_created_by", type_="foreignkey")
        for cname in ("created_by", "version", "description"):
            if cname in dm_cols:
                batch_op.drop_column(cname)

    sm_cols = _col_names(insp, "semantic_metrics")
    sm_fks = _fk_names(insp, "semantic_metrics")
    with op.batch_alter_table("semantic_metrics", schema=None) as batch_op:
        if "fk_semantic_metrics_created_by" in sm_fks:
            batch_op.drop_constraint("fk_semantic_metrics_created_by", type_="foreignkey")
        for cname in (
            "created_by",
            "version",
            "access_scope",
            "dependent_metrics_json",
            "dependent_fields_json",
            "dependent_datasets_json",
            "output_data_type",
            "metric_kind",
        ):
            if cname in sm_cols:
                batch_op.drop_column(cname)

    rel_cols = _col_names(insp, "dataset_relationships")
    with op.batch_alter_table("dataset_relationships", schema=None) as batch_op:
        for cname in ("validated_at", "metadata_json", "is_active"):
            if cname in rel_cols:
                batch_op.drop_column(cname)

    df_cols = _col_names(insp, "dataset_fields")
    df_fks = _fk_names(insp, "dataset_fields")
    with op.batch_alter_table("dataset_fields", schema=None) as batch_op:
        if "fk_dataset_fields_created_by" in df_fks:
            batch_op.drop_constraint("fk_dataset_fields_created_by", type_="foreignkey")
        for cname in ("created_by", "version", "access_scope", "dependent_fields_json", "calculation_type", "description"):
            if cname in df_cols:
                batch_op.drop_column(cname)
