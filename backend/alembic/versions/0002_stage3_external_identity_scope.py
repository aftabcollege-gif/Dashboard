"""Stage 3: scope ExternalIdentity unique constraint to directory_connection_id

Revision ID: 0002_stage3_external_identity_scope
Revises: 0001_initial_baseline
Create Date: 2026-11-22 16:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_stage3_external_identity_scope"
down_revision: Union[str, None] = "0001_initial_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing_uqs = insp.get_unique_constraints("external_identities")
    old_uq_names = [
        uc["name"]
        for uc in existing_uqs
        if uc.get("name")
        and sorted(uc.get("column_names") or []) == ["external_id", "provider_type"]
    ]
    already_new = any(
        sorted(uc.get("column_names") or []) == ["directory_connection_id", "external_id", "provider_type"]
        for uc in existing_uqs
    )

    with op.batch_alter_table("external_identities", schema=None) as batch_op:
        for old_name in old_uq_names:
            batch_op.drop_constraint(old_name, type_="unique")
        if not already_new:
            batch_op.create_unique_constraint(
                "uq_ext_conn_provider_id",
                ["directory_connection_id", "provider_type", "external_id"],
            )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing_uqs = insp.get_unique_constraints("external_identities")
    has_new = any(uc.get("name") == "uq_ext_conn_provider_id" for uc in existing_uqs)
    has_old = any(
        sorted(uc.get("column_names") or []) == ["external_id", "provider_type"]
        for uc in existing_uqs
    )

    with op.batch_alter_table("external_identities", schema=None) as batch_op:
        if has_new:
            batch_op.drop_constraint("uq_ext_conn_provider_id", type_="unique")
        if not has_old:
            batch_op.create_unique_constraint(
                "uq_ext_provider_id",
                ["provider_type", "external_id"],
            )
