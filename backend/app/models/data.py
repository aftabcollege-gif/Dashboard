from datetime import datetime
from typing import Optional
from sqlalchemy import (
    String, Boolean, Integer, BigInteger, Float, Text, DateTime, ForeignKey, UniqueConstraint, JSON
)
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base
from app.models.common import TimestampMixin, gen_uuid


class DataSource(Base, TimestampMixin):
    __tablename__ = "data_sources"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    source_type: Mapped[str] = mapped_column(String(48), nullable=False)
    read_only: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    config_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    refresh_policy: Mapped[str] = mapped_column(String(32), default="scheduled", nullable=False)
    last_tested_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    health_status: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class DataSourceCredential(Base, TimestampMixin):
    __tablename__ = "data_source_credentials"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    data_source_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("data_sources.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    encrypted_payload: Mapped[str] = mapped_column(Text, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class Dataset(Base, TimestampMixin):
    __tablename__ = "datasets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    data_source_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("data_sources.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    warehouse_table: Mapped[str] = mapped_column(String(180), nullable=False, unique=True)
    schema_type: Mapped[str] = mapped_column(String(32), default="fact", nullable=False)
    refresh_policy: Mapped[str] = mapped_column(String(32), default="cached", nullable=False)
    data_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    row_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    last_refreshed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    freshness_status: Mapped[str] = mapped_column(String(32), default="fresh", nullable=False)
    profile_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class DatasetField(Base, TimestampMixin):
    __tablename__ = "dataset_fields"
    __table_args__ = (UniqueConstraint("dataset_id", "field_name", name="uq_ds_field"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    dataset_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    field_name: Mapped[str] = mapped_column(String(140), nullable=False)
    display_name: Mapped[str] = mapped_column(String(180), nullable=False)
    data_type: Mapped[str] = mapped_column(String(48), nullable=False)
    semantic_role: Mapped[str] = mapped_column(String(32), default="dimension", nullable=False)
    is_primary_key: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_calculated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    expression: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    format_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    stats_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    calculation_type: Mapped[str] = mapped_column(String(32), default="physical", nullable=False)
    dependent_fields_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    access_scope: Mapped[str] = mapped_column(String(32), default="dataset", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    semantic_class: Mapped[str] = mapped_column(String(48), default="", nullable=False)
    classification_confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    aggregation_policy: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    user_override_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class DatasetRelationship(Base, TimestampMixin):
    __tablename__ = "dataset_relationships"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    left_dataset_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    left_field: Mapped[str] = mapped_column(String(140), nullable=False)
    right_dataset_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    right_field: Mapped[str] = mapped_column(String(140), nullable=False)
    join_type: Mapped[str] = mapped_column(String(24), default="left", nullable=False)
    cardinality: Mapped[str] = mapped_column(String(24), default="many_to_one", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    user_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    confirmation_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    confirmed_by: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    validated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class SemanticMetric(Base, TimestampMixin):
    """Semantic Layer: canonical metric formulas shared across dashboards."""
    __tablename__ = "semantic_metrics"
    __table_args__ = (UniqueConstraint("organization_id", "slug", name="uq_org_semantic_slug"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    dataset_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    formula: Mapped[str] = mapped_column(Text, nullable=False)
    format_type: Mapped[str] = mapped_column(String(32), default="number", nullable=False)
    unit: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    metric_kind: Mapped[str] = mapped_column(String(32), default="analytical_metric", nullable=False)
    output_data_type: Mapped[str] = mapped_column(String(48), default="double", nullable=False)
    dependent_datasets_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    dependent_fields_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    dependent_metrics_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    access_scope: Mapped[str] = mapped_column(String(32), default="organization", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    category: Mapped[str] = mapped_column(String(48), default="analytical", nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    target_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    kpi_metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class SavedQuery(Base, TimestampMixin):
    __tablename__ = "queries"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    dataset_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("datasets.id", ondelete="SET NULL"), nullable=True
    )
    query_mode: Mapped[str] = mapped_column(String(24), default="sql", nullable=False)
    sql_text: Mapped[str] = mapped_column(Text, nullable=False)
    visual_spec_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class QueryParameter(Base, TimestampMixin):
    __tablename__ = "query_parameters"
    __table_args__ = (UniqueConstraint("query_id", "param_name", name="uq_query_param"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    query_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("queries.id", ondelete="CASCADE"), nullable=False
    )
    param_name: Mapped[str] = mapped_column(String(80), nullable=False)
    param_type: Mapped[str] = mapped_column(String(32), default="string", nullable=False)
    default_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class DataModel(Base, TimestampMixin):
    __tablename__ = "data_models"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    model_type: Mapped[str] = mapped_column(String(32), default="star_schema", nullable=False)
    definition_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class DataTransformation(Base, TimestampMixin):
    __tablename__ = "data_transformations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    dataset_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    transform_type: Mapped[str] = mapped_column(String(48), nullable=False)
    config_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class DataLineage(Base, TimestampMixin):
    __tablename__ = "data_lineage"
    __table_args__ = (
        UniqueConstraint("source_type", "source_id", "target_type", "target_id", name="uq_lineage_edge"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    source_type: Mapped[str] = mapped_column(String(48), nullable=False)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(48), nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
