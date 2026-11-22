from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.api.deps import enforce_permission, get_current_user
from app.core.rls_cls import apply_cls_and_masking, build_data_security_context
from app.database import get_db
from app.engines.data_engine import DataProfiler
from app.engines.filter_engine import get_cascading_filter_options
from app.engines.query_engine import execute_dataset_sql
from app.engines.warehouse import warehouse
from app.models.data import (
    DataLineage,
    Dataset,
    DatasetField,
    DatasetRelationship,
    DataTransformation,
    SemanticMetric,
)
from app.models.identity import User

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
        fields = db.query(DatasetField).filter(DatasetField.dataset_id == d.id).all()
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
            "last_refreshed_at": d.last_refreshed_at,
            "freshness_status": d.freshness_status,
            "is_uploaded_excel": bool(d.warehouse_table and d.warehouse_table.startswith("wh_file_")),
            "fields": [
                {
                    "id": f.id, "field_name": f.field_name, "display_name": f.display_name,
                    "data_type": f.data_type, "semantic_role": f.semantic_role,
                    "is_primary_key": f.is_primary_key, "is_calculated": f.is_calculated, "expression": f.expression
                }
                for f in fields
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
    enforce_permission(db, user, "dataset", body.dataset_id, "edit")
    sm = SemanticMetric(
        organization_id=user.organization_id,
        dataset_id=body.dataset_id,
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
    rows = db.query(DatasetRelationship).all()
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
    enforce_permission(db, user, "dataset", body.left_dataset_id, "edit")
    rel = DatasetRelationship(
        left_dataset_id=body.left_dataset_id,
        left_field=body.left_field,
        right_dataset_id=body.right_dataset_id,
        right_field=body.right_field,
        join_type=body.join_type,
        cardinality=body.cardinality,
    )
    db.add(rel)
    db.commit()
    return {"id": rel.id}


@router.get("/{dataset_id}/profile")
def profile_dataset(dataset_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "dataset", dataset_id, "view")
    ds = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not ds:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return DataProfiler.profile_table(ds.warehouse_table)


@router.get("/{dataset_id}/preview")
def preview_dataset(
    dataset_id: str, limit: int = 50, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    enforce_permission(db, user, "dataset", dataset_id, "view")
    ds = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not ds:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return execute_dataset_sql(
        db, user, f'SELECT * FROM "{ds.warehouse_table}" LIMIT {min(limit, 500)}', dataset=ds, use_cache=False
    )


@router.post("/{dataset_id}/filter-options")
def cascading_filter_options(
    dataset_id: str,
    body: FilterOptionsRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(db, user, "dataset", dataset_id, "view")
    ds = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not ds:
        raise HTTPException(status_code=404, detail="Dataset not found")
    sec_ctx = build_data_security_context(db, user, ds.id)
    if body.field_name in sec_ctx.hidden_columns:
        return {"values": []}
    vals = get_cascading_filter_options(
        ds.warehouse_table,
        body.field_name,
        body.parent_filters,
        sec_ctx.rls_where_clauses,
        sec_ctx.rls_params,
        search=body.search,
        limit=body.limit,
    )
    return {"field_name": body.field_name, "values": vals}


@router.post("/{dataset_id}/calculated-fields")
def add_calculated_field(
    dataset_id: str,
    body: CalculatedFieldCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(db, user, "dataset", dataset_id, "edit")
    ds = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not ds:
        raise HTTPException(status_code=404, detail="Dataset not found")
    f = DatasetField(
        dataset_id=ds.id,
        field_name=body.field_name,
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
    enforce_permission(db, user, "dataset", dataset_id, "view")
    ds = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not ds:
        raise HTTPException(status_code=404, detail="Dataset not found")
    upstream = db.query(DataLineage).filter(DataLineage.target_type == "dataset", DataLineage.target_id == dataset_id).all()
    downstream = db.query(DataLineage).filter(DataLineage.source_type == "dataset", DataLineage.source_id == dataset_id).all()
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
    enforce_permission(db, user, "dataset", dataset_id, "edit")
    cnt = db.query(DataTransformation).filter(DataTransformation.dataset_id == dataset_id).count()
    tr = DataTransformation(
        dataset_id=dataset_id,
        step_order=cnt + 1,
        transform_type=body.transform_type,
        config_json=body.config_json,
    )
    db.add(tr)
    db.commit()
    return {"id": tr.id, "step_order": tr.step_order}


@router.delete("/{dataset_id}")
def delete_dataset(dataset_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "dataset", dataset_id, "edit")
    ds = db.query(Dataset).filter(Dataset.id == dataset_id, Dataset.organization_id == user.organization_id).first()
    if not ds:
        raise HTTPException(status_code=404, detail="Dataset not found")
    db.query(DatasetField).filter(DatasetField.dataset_id == ds.id).delete()
    db.query(DataTransformation).filter(DataTransformation.dataset_id == ds.id).delete()
    db.delete(ds)
    db.commit()
    return {"status": "deleted"}

