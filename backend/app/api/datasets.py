import os
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.api.deps import enforce_permission, get_current_user
from app.core.cache import cache_manager
from app.core.permissions import permission_resolver
from app.core.rls_cls import build_data_security_context
from app.core.tenant import get_dataset_for_org
from app.database import get_db
from app.engines.data_engine import DataProfiler
from app.engines.filter_engine import get_cascading_filter_options
from app.engines.query_engine import execute_dataset_sql
from app.engines.warehouse import sanitize_identifier, warehouse
from app.models.data import (
    DataLineage,
    DataSource,
    Dataset,
    DatasetField,
    DatasetRelationship,
    DataTransformation,
    SemanticMetric,
)
from app.models.identity import AccessPolicy, User

router = APIRouter(prefix="/datasets", tags=["Datasets, Semantic Layer & Lineage"])


class FilterOptionsRequest(BaseModel):
    field_name: str
    parent_filters: list[dict[str, Any]] = []
    search: Optional[str] = None
    limit: int = 200


class RelationshipCreate(BaseModel):
    left_dataset_id: str
    left_field: str
    right_dataset_id: str
    right_field: str
    join_type: str = "left"
    cardinality: str = "many_to_one"


class CalculatedFieldCreate(BaseModel):
    field_name: str
    display_name: str
    data_type: str = "float"
    semantic_role: str = "measure"
    expression: str


class SemanticMetricCreate(BaseModel):
    dataset_id: str
    name: str
    slug: str
    description: str = ""
    formula: str
    format_type: str = "number"
    unit: str = ""


class TransformationCreate(BaseModel):
    transform_type: str
    config_json: dict[str, Any]


@router.get("")
def list_datasets(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "dataset", "*", "view")
    rows = db.query(Dataset).filter(Dataset.organization_id == user.organization_id).all()
    out = []
    for d in rows:
        if not permission_resolver.resolve(db, user, "dataset", d.id, "view").allowed:
            continue
        sec_ctx = build_data_security_context(db, user, d.id)
        fields = db.query(DatasetField).filter(DatasetField.dataset_id == d.id).all()
        visible_fields = [f for f in fields if f.field_name not in sec_ctx.hidden_columns]
        out.append({
            "id": d.id,
            "name": d.name,
            "description": d.description,
            "data_source_id": d.data_source_id,
            "warehouse_table": d.warehouse_table,
            "schema_type": d.schema_type,
            "refresh_policy": d.refresh_policy,
            "data_version": d.data_version,
            "row_count": d.row_count,
            "columns_count": len(visible_fields),
            "last_refreshed_at": d.last_refreshed_at,
            "freshness_status": d.freshness_status,
            "is_uploaded_excel": bool(d.warehouse_table and d.warehouse_table.startswith("wh_file_")),
            "fields": [
                {
                    "id": f.id, "field_name": f.field_name, "display_name": f.display_name,
                    "data_type": f.data_type, "semantic_role": f.semantic_role,
                    "is_primary_key": f.is_primary_key, "is_calculated": f.is_calculated, "expression": f.expression
                }
                for f in visible_fields
            ],
        })
    return out


@router.get("/semantic-metrics")
def list_semantic_metrics(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "dataset", "*", "view")
    rows = db.query(SemanticMetric).filter(SemanticMetric.organization_id == user.organization_id).all()
    return [
        {
            "id": m.id, "dataset_id": m.dataset_id, "name": m.name, "slug": m.slug,
            "description": m.description, "formula": m.formula, "format_type": m.format_type, "unit": m.unit
        }
        for m in rows
    ]


@router.post("/semantic-metrics")
def create_semantic_metric(
    body: SemanticMetricCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    ds = get_dataset_for_org(db, body.dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", ds.id, "edit")
    sm = SemanticMetric(
        organization_id=user.organization_id,
        dataset_id=ds.id,
        name=body.name,
        slug=body.slug,
        description=body.description,
        formula=body.formula,
        format_type=body.format_type,
        unit=body.unit,
    )
    db.add(sm)
    db.commit()
    return {"id": sm.id, "slug": sm.slug}


@router.get("/relationships")
def list_relationships(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "dataset", "*", "view")
    org_ds_ids = {
        d.id for d in db.query(Dataset.id).filter(Dataset.organization_id == user.organization_id).all()
    }
    rows = (
        db.query(DatasetRelationship)
        .filter(
            DatasetRelationship.left_dataset_id.in_(org_ds_ids),
            DatasetRelationship.right_dataset_id.in_(org_ds_ids),
        )
        .all()
        if org_ds_ids
        else []
    )
    return [
        {
            "id": r.id, "left_dataset_id": r.left_dataset_id, "left_field": r.left_field,
            "right_dataset_id": r.right_dataset_id, "right_field": r.right_field,
            "join_type": r.join_type, "cardinality": r.cardinality
        }
        for r in rows
    ]


@router.post("/relationships")
def create_relationship(
    body: RelationshipCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    left_ds = get_dataset_for_org(db, body.left_dataset_id, user.organization_id)
    right_ds = get_dataset_for_org(db, body.right_dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", left_ds.id, "edit")
    enforce_permission(db, user, "dataset", right_ds.id, "view")
    rel = DatasetRelationship(
        left_dataset_id=left_ds.id,
        left_field=sanitize_identifier(body.left_field),
        right_dataset_id=right_ds.id,
        right_field=sanitize_identifier(body.right_field),
        join_type=body.join_type,
        cardinality=body.cardinality,
    )
    db.add(rel)
    db.commit()
    return {"id": rel.id}


@router.get("/{dataset_id}/profile")
def profile_dataset(dataset_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ds = get_dataset_for_org(db, dataset_id, user.organization_id)
    if not permission_resolver.resolve(db, user, "dataset", ds.id, "view_schema").allowed:
        enforce_permission(db, user, "dataset", ds.id, "view")
    else:
        dec = permission_resolver.resolve(db, user, "dataset", ds.id, "view")
        if not dec.allowed and dec.reason.startswith("explicit_deny"):
            enforce_permission(db, user, "dataset", ds.id, "view")
    sec_ctx = build_data_security_context(db, user, ds.id)
    return DataProfiler.profile_table(
        ds.warehouse_table,
        sec_ctx=sec_ctx,
    )


@router.get("/{dataset_id}/preview")
def preview_dataset(
    dataset_id: str, limit: int = 50, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    ds = get_dataset_for_org(db, dataset_id, user.organization_id)
    if not permission_resolver.resolve(db, user, "dataset", ds.id, "view_data").allowed:
        enforce_permission(db, user, "dataset", ds.id, "view")
    else:
        dec = permission_resolver.resolve(db, user, "dataset", ds.id, "view")
        if not dec.allowed and dec.reason.startswith("explicit_deny"):
            enforce_permission(db, user, "dataset", ds.id, "view")
    bounded = max(1, min(int(limit), 500))
    return execute_dataset_sql(
        db, user, f'SELECT * FROM "{ds.warehouse_table}" LIMIT {bounded}', dataset=ds, limit=bounded, use_cache=False
    )


@router.post("/{dataset_id}/filter-options")
def cascading_filter_options(
    dataset_id: str,
    body: FilterOptionsRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ds = get_dataset_for_org(db, dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", ds.id, "view")
    sec_ctx = build_data_security_context(db, user, ds.id)
    if body.field_name in sec_ctx.hidden_columns:
        return {"values": []}
    try:
        vals = get_cascading_filter_options(
            ds.warehouse_table,
            body.field_name,
            body.parent_filters,
            sec_ctx.rls_where_clauses,
            sec_ctx.rls_params,
            search=body.search,
            limit=body.limit,
            sec_ctx=sec_ctx,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"field_name": body.field_name, "values": vals}


@router.post("/{dataset_id}/calculated-fields")
def add_calculated_field(
    dataset_id: str,
    body: CalculatedFieldCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ds = get_dataset_for_org(db, dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", ds.id, "edit")
    f = DatasetField(
        dataset_id=ds.id,
        field_name=sanitize_identifier(body.field_name),
        display_name=body.display_name,
        data_type=body.data_type,
        semantic_role=body.semantic_role,
        is_calculated=True,
        expression=body.expression,
    )
    db.add(f)
    db.commit()
    return {"id": f.id, "field_name": f.field_name}


@router.get("/{dataset_id}/lineage")
def get_dataset_lineage(dataset_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ds = get_dataset_for_org(db, dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", ds.id, "view")
    upstream = db.query(DataLineage).filter(DataLineage.target_type == "dataset", DataLineage.target_id == ds.id).all()
    downstream = db.query(DataLineage).filter(DataLineage.source_type == "dataset", DataLineage.source_id == ds.id).all()
    return {
        "dataset": {
            "id": ds.id, "name": ds.name, "warehouse_table": ds.warehouse_table,
            "freshness_status": ds.freshness_status, "last_refreshed_at": ds.last_refreshed_at, "data_version": ds.data_version
        },
        "upstream": [{"source_type": e.source_type, "source_id": e.source_id, "meta": e.metadata_json} for e in upstream],
        "downstream": [{"target_type": e.target_type, "target_id": e.target_id, "meta": e.metadata_json} for e in downstream],
    }


@router.post("/{dataset_id}/transformations")
def add_transformation(
    dataset_id: str,
    body: TransformationCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ds = get_dataset_for_org(db, dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", ds.id, "edit")
    cnt = db.query(DataTransformation).filter(DataTransformation.dataset_id == ds.id).count()
    tr = DataTransformation(
        dataset_id=ds.id,
        step_order=cnt + 1,
        transform_type=body.transform_type,
        config_json=body.config_json,
    )
    db.add(tr)
    db.commit()
    return {"id": tr.id, "step_order": tr.step_order}


@router.delete("/{dataset_id}")
def delete_dataset(dataset_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ds = get_dataset_for_org(db, dataset_id, user.organization_id)
    if not permission_resolver.resolve(db, user, "dataset", ds.id, "delete").allowed:
        enforce_permission(db, user, "dataset", ds.id, "edit")

    # Remove DuckDB table if not shared by another dataset
    other_ref = (
        db.query(Dataset)
        .filter(Dataset.warehouse_table == ds.warehouse_table, Dataset.id != ds.id)
        .first()
    )
    if not other_ref and ds.warehouse_table:
        try:
            warehouse.drop_table(ds.warehouse_table)
        except Exception:
            pass

    # Clean up uploaded file if this dataset's data source owns an uploaded file exclusively
    if ds.data_source_id:
        src = db.query(DataSource).filter(DataSource.id == ds.data_source_id).first()
        other_ds_on_src = (
            db.query(Dataset).filter(Dataset.data_source_id == ds.data_source_id, Dataset.id != ds.id).first()
        )
        if src and not other_ds_on_src and src.source_type in (
            "excel", "csv", "tsv", "json", "jsonl", "ndjson", "log", "txt", "xml"
        ):
            fpath = (src.config_json or {}).get("file_path")
            if fpath and os.path.exists(fpath):
                try:
                    os.remove(fpath)
                except OSError:
                    pass

    db.query(DatasetField).filter(DatasetField.dataset_id == ds.id).delete()
    db.query(DataTransformation).filter(DataTransformation.dataset_id == ds.id).delete()
    db.query(SemanticMetric).filter(SemanticMetric.dataset_id == ds.id).delete()
    db.query(DatasetRelationship).filter(
        (DatasetRelationship.left_dataset_id == ds.id) | (DatasetRelationship.right_dataset_id == ds.id)
    ).delete()
    db.query(DataLineage).filter(
        ((DataLineage.source_type == "dataset") & (DataLineage.source_id == ds.id))
        | ((DataLineage.target_type == "dataset") & (DataLineage.target_id == ds.id))
    ).delete()
    db.query(AccessPolicy).filter(AccessPolicy.dataset_id == ds.id).delete()
    cache_manager.invalidate_dataset(ds.id)
    db.delete(ds)
    db.commit()
    return {"status": "deleted"}
