from datetime import datetime, timezone
import re
import secrets
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.api.dashboards import (
    ALL_30_CHART_TYPES,
    _apply_public_override_change,
    _assert_dashboard_public_safe,
    _enforce_widget_dataset_permissions,
)
from app.api.deps import enforce_permission, get_client_ip, get_current_user
from app.core.audit import record_audit
from app.core.cache import cache_manager
from app.core.permissions import permission_resolver
from app.core.rls_cls import apply_cls_and_masking, build_data_security_context
from app.core.tenant import (
    get_dataset_for_org,
    get_default_workspace_for_org,
    get_theme_for_org,
    get_workspace_for_org,
)
from app.database import get_db
from app.engines.descriptive_builder import (
    ANALYSIS_CATALOG,
    _build_factual_narrative_fa,
    analyze_dataset_relationships,
    generate_descriptive_dashboard_plan,
    list_accessible_datasets_metadata,
    preview_formula_execution,
)
from app.engines.formula_engine import SQL_FUNCTION_CATALOG, validate_and_compile_formula
from app.engines.query_engine import build_visual_query_sql, execute_dataset_sql
from app.engines.warehouse import sanitize_identifier, warehouse
from app.models.dashboard import (
    Dashboard,
    DashboardFilter,
    DashboardLayout,
    DashboardPage,
    DashboardVersion,
    Widget,
    WidgetConfig,
    WidgetDrillRule,
    WidgetQuery,
)
from app.models.data import DataLineage, DataModel, Dataset, DatasetField, DatasetRelationship, SemanticMetric
from app.models.identity import User

router = APIRouter(prefix="/descriptive-builder", tags=["Descriptive Dashboard Builder"])
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class DataModelUpsertRequest(BaseModel):
    name: str
    description: str = ""
    model_type: str = "descriptive_model"
    definition_json: dict[str, Any] = Field(default_factory=dict)


class RelationshipAnalyzeRequest(BaseModel):
    dataset_ids: list[str]
    primary_dataset_id: Optional[str] = None
    chosen_relationship_ids: list[str] = Field(default_factory=list)
    ad_hoc_joins: list[dict[str, Any]] = Field(default_factory=list)


class RelationshipCreateRequest(BaseModel):
    left_dataset_id: str
    left_field: str
    right_dataset_id: str
    right_field: str
    join_type: str = "left"
    cardinality: str = "many_to_one"


class FormulaValidateRequest(BaseModel):
    dataset_ids: list[str]
    formula: str
    calculation_type: str = "calculated_column"  # calculated_column | calculated_measure | analytical_metric
    self_field_name: Optional[str] = None
    chosen_relationship_ids: list[str] = Field(default_factory=list)
    ad_hoc_joins: list[dict[str, Any]] = Field(default_factory=list)


class FormulaPreviewRequest(FormulaValidateRequest):
    field_name: str = "calc_preview"
    filters: list[dict[str, Any]] = Field(default_factory=list)
    limit: int = 50


class CalculatedFieldUpsertRequest(BaseModel):
    dataset_id: str
    dataset_ids: list[str] = Field(default_factory=list)
    field_name: str
    display_name: str
    description: str = ""
    formula: str
    calculation_type: str = "calculated_column"  # calculated_column | calculated_measure
    format_type: str = "number"
    unit: str = ""
    access_scope: str = "dataset"
    force: bool = False


class MeasureUpsertRequest(BaseModel):
    dataset_id: str
    dataset_ids: list[str] = Field(default_factory=list)
    name: str
    slug: str
    description: str = ""
    formula: str
    metric_kind: str = "analytical_metric"  # calculated_measure | analytical_metric
    format_type: str = "number"
    unit: str = ""
    access_scope: str = "organization"
    force: bool = False


class GenerateDashboardRequest(BaseModel):
    dataset_ids: list[str]
    title: str = "داشبورد تحلیل توصیفی"
    selected_fields: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    measures: list[Any] = Field(default_factory=list)
    analysis_types: list[str] = Field(default_factory=list)
    filters: list[dict[str, Any]] = Field(default_factory=list)
    calendar_mode: str = "jalali"
    date_range: Optional[dict[str, Any]] = None
    chosen_relationship_ids: list[str] = Field(default_factory=list)
    ad_hoc_joins: list[dict[str, Any]] = Field(default_factory=list)
    target_values: dict[str, float] = Field(default_factory=dict)


class PreviewWidgetRequest(BaseModel):
    dataset_id: str
    title: str = "پیش‌نمایش ویجت"
    chart_type: str = "bar"
    analysis_type: str = "org_unit_comparison"
    query_mode: str = "visual"
    sql_text: Optional[str] = None
    visual_spec: dict[str, Any] = Field(default_factory=dict)
    filters: list[dict[str, Any]] = Field(default_factory=list)
    calendar_mode: str = "jalali"


class SaveDashboardRequest(BaseModel):
    workspace_id: Optional[str] = None
    title: str
    description: str = ""
    theme_id: Optional[str] = None
    is_public: bool = False
    allow_public_restricted: Optional[bool] = None
    public_restricted_reason: Optional[str] = None
    dataset_ids: list[str] = Field(default_factory=list)
    model_id: Optional[str] = None
    model_definition: dict[str, Any] = Field(default_factory=dict)
    filters: list[dict[str, Any]] = Field(default_factory=list)
    widgets: list[dict[str, Any]] = Field(default_factory=list)


def _compute_field_impact(db: Session, org_id: str, ds_id: str, field_name: str) -> dict[str, Any]:
    """Finds downstream calculated fields, semantic metrics, and dashboard widgets referencing field_name."""
    low = field_name.lower()
    ds_ids = [d.id for d in db.query(Dataset).filter(Dataset.organization_id == org_id).all()]
    dep_calc_fields = []
    for f in db.query(DatasetField).filter(DatasetField.dataset_id.in_(ds_ids), DatasetField.is_calculated == True).all():
        if f.dataset_id == ds_id and f.field_name.lower() == low:
            continue
        if re.search(rf"\b{re.escape(low)}\b", str(f.expression or ""), flags=re.IGNORECASE):
            dep_calc_fields.append({"id": f.id, "field_name": f.field_name, "display_name": f.display_name, "dataset_id": f.dataset_id})

    dep_metrics = []
    for m in db.query(SemanticMetric).filter(SemanticMetric.organization_id == org_id).all():
        if m.slug.lower() == low:
            continue
        if re.search(rf"\b{re.escape(low)}\b", str(m.formula or ""), flags=re.IGNORECASE):
            dep_metrics.append({"id": m.id, "slug": m.slug, "name": m.name})

    dep_widgets = []
    for w, wq in (
        db.query(Widget, WidgetQuery)
        .join(WidgetQuery, WidgetQuery.widget_id == Widget.id)
        .filter(Widget.dataset_id.in_(ds_ids))
        .all()
    ):
        spec_txt = str(wq.visual_spec_json or "") + " " + str(wq.sql_text or "")
        if re.search(rf"\b{re.escape(low)}\b", spec_txt, flags=re.IGNORECASE):
            dep_widgets.append({"id": w.id, "title": w.title, "dashboard_id": w.dashboard_id})

    return {
        "has_dependencies": bool(dep_calc_fields or dep_metrics or dep_widgets),
        "calculated_fields": dep_calc_fields,
        "semantic_metrics": dep_metrics,
        "widgets": dep_widgets,
    }


@router.get("/catalog")
def get_builder_catalog(user: User = Depends(get_current_user)):
    return {
        "functions": SQL_FUNCTION_CATALOG,
        "analyses": ANALYSIS_CATALOG,
        "join_types": [
            {"id": "INNER", "label_fa": "اشتراک داخلی (INNER JOIN)", "description_fa": "فقط ردیف‌هایی که در هر دو منبع مقدار متناظر دارند."},
            {"id": "LEFT", "label_fa": "حفظ منبع اصلی (LEFT JOIN)", "description_fa": "تمام ردیف‌های منبع اصلی حفظ و مقادیر متناظر از منبع دوم الحاق می‌شوند."},
            {"id": "RIGHT", "label_fa": "حفظ منبع دوم (RIGHT JOIN)", "description_fa": "تمام ردیف‌های منبع دوم حفظ می‌شوند."},
            {"id": "FULL", "label_fa": "اتصال کامل (FULL OUTER JOIN)", "description_fa": "تمام ردیف‌های هر دو منبع با پر کردن NULL در بخش‌های ناموجود."},
        ],
        "calendars": [
            {"id": "jalali", "label_fa": "تقویم شمسی (هجری شمسی)"},
            {"id": "gregorian", "label_fa": "تقویم میلادی (Gregorian)"},
        ],
    }


@router.get("/smart-kpis/{dataset_id}")
def get_smart_kpis_for_builder(
    dataset_id: str,
    related_dataset_ids: Optional[str] = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Exposes Intelligent KPI Engine suggestions directly inside Descriptive Dashboard Builder."""
    from app.engines.kpi_engine import IntelligentKpiEngine

    ds = get_dataset_for_org(db, dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", ds.id, "view")
    rel_list = [x.strip() for x in (related_dataset_ids or "").split(",") if x.strip()]
    plan = IntelligentKpiEngine.build_intelligent_dashboard_plan(
        db, ds, user=user, related_dataset_ids=rel_list or None
    )
    return {
        "dataset_id": ds.id,
        "dataset_name": ds.name,
        "domain_detection": plan["domain_detection"],
        "field_classifications": plan["field_classifications"],
        "kpis": plan["kpis"],
    }


@router.get("/datasets")
def list_builder_datasets(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return {"datasets": list_accessible_datasets_metadata(db, user)}


@router.get("/datasets/{dataset_id}/fields")
def list_builder_dataset_fields(
    dataset_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    ds = get_dataset_for_org(db, dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", ds.id, "view")
    all_meta = list_accessible_datasets_metadata(db, user)
    match = next((d for d in all_meta if d["id"] == ds.id), None)
    if not match:
        raise HTTPException(status_code=403, detail="Dataset not accessible")
    return match


# ---------------- VERSIONED DATA MODELS (BUILDER WORKSPACE STATE) ----------------
@router.get("/models")
def list_data_models(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "dataset", "*", "view")
    rows = (
        db.query(DataModel)
        .filter(DataModel.organization_id == user.organization_id)
        .order_by(DataModel.updated_at.desc())
        .all()
    )
    out = []
    for m in rows:
        ds_ids = (m.definition_json or {}).get("dataset_ids") or []
        if isinstance(ds_ids, list) and ds_ids:
            if any(
                not permission_resolver.resolve(db, user, "dataset", str(did), "view").allowed
                for did in ds_ids
            ):
                continue
        out.append({
            "id": m.id,
            "name": m.name,
            "description": getattr(m, "description", ""),
            "model_type": m.model_type,
            "definition_json": m.definition_json,
            "version": getattr(m, "version", 1),
            "created_by": getattr(m, "created_by", None),
            "created_at": m.created_at,
            "updated_at": m.updated_at,
        })
    return out


@router.post("/models")
def create_data_model(
    body: DataModelUpsertRequest, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    enforce_permission(db, user, "dataset", "*", "view", ip_address=ip)
    for did in (body.definition_json or {}).get("dataset_ids") or []:
        ds = get_dataset_for_org(db, str(did), user.organization_id)
        enforce_permission(db, user, "dataset", ds.id, "view", ip_address=ip)
    dm = DataModel(
        organization_id=user.organization_id,
        name=body.name.strip() or "مدل تحلیل توصیفی",
        description=body.description,
        model_type=body.model_type,
        definition_json=body.definition_json,
        version=1,
        created_by=user.id,
    )
    db.add(dm)
    db.commit()
    db.refresh(dm)
    return {"id": dm.id, "name": dm.name, "version": dm.version}


@router.put("/models/{model_id}")
def update_data_model(
    model_id: str,
    body: DataModelUpsertRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ip = get_client_ip(request)
    enforce_permission(db, user, "dataset", "*", "view", ip_address=ip)
    dm = db.query(DataModel).filter(DataModel.id == model_id, DataModel.organization_id == user.organization_id).first()
    if not dm:
        raise HTTPException(status_code=404, detail="Data model not found")
    for did in (body.definition_json or {}).get("dataset_ids") or []:
        ds = get_dataset_for_org(db, str(did), user.organization_id)
        enforce_permission(db, user, "dataset", ds.id, "view", ip_address=ip)
    dm.name = body.name.strip() or dm.name
    dm.description = body.description
    dm.model_type = body.model_type
    dm.definition_json = body.definition_json
    dm.version = int(getattr(dm, "version", 1) or 1) + 1
    db.commit()
    return {"id": dm.id, "name": dm.name, "version": dm.version}


# ---------------- RELATIONSHIPS & MULTI-DATASET GRAPH ----------------
@router.post("/relationships/analyze")
def analyze_relationships_endpoint(
    body: RelationshipAnalyzeRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return analyze_dataset_relationships(
            db,
            user,
            body.dataset_ids,
            primary_dataset_id=body.primary_dataset_id,
            chosen_relationship_ids=body.chosen_relationship_ids,
            ad_hoc_joins=body.ad_hoc_joins,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/relationships")
def create_builder_relationship(
    body: RelationshipCreateRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ip = get_client_ip(request)
    lds = get_dataset_for_org(db, body.left_dataset_id, user.organization_id)
    rds = get_dataset_for_org(db, body.right_dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", lds.id, "edit", ip_address=ip)
    enforce_permission(db, user, "dataset", rds.id, "edit", ip_address=ip)

    if lds.id == rds.id:
        raise HTTPException(status_code=400, detail="اتصال یک منبع به خودش مجاز نیست")

    l_sc = build_data_security_context(db, user, lds.id)
    r_sc = build_data_security_context(db, user, rds.id)
    if body.left_field in l_sc.hidden_columns or body.right_field in r_sc.hidden_columns:
        raise HTTPException(status_code=403, detail="فیلدهای مخفی‌شده توسط CLS نمی‌توانند کلید ارتباط باشند")

    l_field = db.query(DatasetField).filter(DatasetField.dataset_id == lds.id, DatasetField.field_name == body.left_field).first()
    r_field = db.query(DatasetField).filter(DatasetField.dataset_id == rds.id, DatasetField.field_name == body.right_field).first()
    if not l_field or not r_field:
        raise HTTPException(status_code=400, detail="یکی از ستون‌های کلید در دیتاست یافت نشد")

    jtype = body.join_type.lower()
    if jtype == "full outer":
        jtype = "full"
    if jtype not in ("inner", "left", "right", "full"):
        raise HTTPException(status_code=400, detail="نوع Join باید یکی از inner/left/right/full باشد")

    rel = DatasetRelationship(
        left_dataset_id=lds.id,
        left_field=body.left_field,
        right_dataset_id=rds.id,
        right_field=body.right_field,
        join_type=jtype,
        cardinality=body.cardinality,
        is_active=True,
        validated_at=datetime.now(timezone.utc),
    )
    db.add(rel)
    if not db.query(DataLineage).filter(
        DataLineage.source_type == "dataset",
        DataLineage.source_id == lds.id,
        DataLineage.target_type == "dataset",
        DataLineage.target_id == rds.id,
    ).first():
        db.add(DataLineage(source_type="dataset", source_id=lds.id, target_type="dataset", target_id=rds.id, metadata_json={"join_keys": f"{body.left_field}={body.right_field}"}))
    record_audit(
        db,
        user.organization_id,
        "dataset_relationship_created",
        "dataset",
        resource_id=lds.id,
        user_id=user.id,
        username=user.username,
        ip_address=ip,
        details={"right_dataset_id": rds.id, "left_field": body.left_field, "right_field": body.right_field, "join_type": jtype},
        commit=False,
    )
    db.commit()
    db.refresh(rel)
    return {"id": rel.id, "join_type": rel.join_type, "cardinality": rel.cardinality}


# ---------------- FORMULA VALIDATE & PREVIEW ----------------
@router.post("/formulas/validate")
def validate_formula_endpoint(
    body: FormulaValidateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        res = validate_and_compile_formula(
            db,
            user,
            dataset_ids=body.dataset_ids,
            formula=body.formula,
            calculation_type=body.calculation_type,
            qualify_with_alias=False,
            auto_safe_divide=True,
            self_field_name=body.self_field_name,
        )
        # If multiple datasets are referenced by this formula, verify valid join path exists
        if len(res["referenced_datasets"]) > 1:
            rel_plan = analyze_dataset_relationships(
                db,
                user,
                body.dataset_ids,
                primary_dataset_id=body.dataset_ids[0],
                chosen_relationship_ids=body.chosen_relationship_ids,
                ad_hoc_joins=body.ad_hoc_joins,
            )
            if not rel_plan["ready_for_join"]:
                res["warnings"].append("فرمول به چند منبع داده ارجاع می‌دهد اما مسیر Join بدون ابهام بین آن‌ها کامل نیست.")
        return {
            "valid": True,
            "compiled_sql": res["compiled_sql"],
            "output_data_type": res["output_data_type"],
            "is_aggregate": res["is_aggregate"],
            "warnings": res["warnings"],
            "errors": [],
            "referenced_fields": [
                {"dataset_id": r["dataset_id"], "dataset_name": r["dataset_name"], "field_name": r["field_name"], "display_name": r["display_name"]}
                for r in res["referenced_fields"]
            ],
            "referenced_datasets": res["referenced_datasets"],
        }
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        return {"valid": False, "compiled_sql": None, "output_data_type": "unknown", "warnings": [], "errors": [str(exc)], "referenced_fields": []}


@router.post("/formulas/preview")
def preview_formula_endpoint(
    body: FormulaPreviewRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return preview_formula_execution(
            db,
            user,
            dataset_ids=body.dataset_ids,
            formula=body.formula,
            calculation_type=body.calculation_type,
            field_name=body.field_name,
            chosen_relationship_ids=body.chosen_relationship_ids,
            ad_hoc_joins=body.ad_hoc_joins,
            filters=body.filters,
            limit=body.limit,
            self_field_name=body.self_field_name,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------------- CALCULATED FIELDS & ANALYTICAL MEASURES ----------------
@router.get("/calculated-fields")
def list_calculated_fields(
    dataset_id: Optional[str] = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ds_q = db.query(Dataset).filter(Dataset.organization_id == user.organization_id)
    if dataset_id:
        ds_q = ds_q.filter(Dataset.id == dataset_id)
    allowed_ds = {
        d.id: d
        for d in ds_q.all()
        if permission_resolver.resolve(db, user, "dataset", d.id, "view").allowed
    }
    if not allowed_ds:
        return []
    fields = (
        db.query(DatasetField)
        .filter(DatasetField.dataset_id.in_(list(allowed_ds.keys())), DatasetField.is_calculated == True)
        .order_by(DatasetField.updated_at.desc())
        .all()
    )
    out = []
    for f in fields:
        out.append({
            "id": f.id,
            "dataset_id": f.dataset_id,
            "dataset_name": allowed_ds[f.dataset_id].name,
            "field_name": f.field_name,
            "display_name": f.display_name,
            "description": getattr(f, "description", ""),
            "data_type": f.data_type,
            "semantic_role": f.semantic_role,
            "calculation_type": getattr(f, "calculation_type", "calculated_column"),
            "formula": f.expression,
            "format_json": f.format_json,
            "dependent_fields": getattr(f, "dependent_fields_json", []),
            "access_scope": getattr(f, "access_scope", "dataset"),
            "version": getattr(f, "version", 1),
            "created_by": getattr(f, "created_by", None),
            "created_at": f.created_at,
            "updated_at": f.updated_at,
        })
    return out


@router.post("/calculated-fields")
def create_calculated_field(
    body: CalculatedFieldUpsertRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ip = get_client_ip(request)
    ds = get_dataset_for_org(db, body.dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", ds.id, "edit", ip_address=ip)

    tech_name = sanitize_identifier(body.field_name)
    if not IDENT_RE.match(tech_name):
        raise HTTPException(status_code=400, detail="نام فنی فیلد باید با حروف انگلیسی و زیرخط باشد")
    if db.query(DatasetField).filter(DatasetField.dataset_id == ds.id, DatasetField.field_name == tech_name).first():
        raise HTTPException(status_code=409, detail=f"فیلدی با نام فنی '{tech_name}' قبلاً وجود دارد")

    ds_scope = body.dataset_ids if body.dataset_ids else [ds.id]
    if ds.id not in ds_scope:
        ds_scope = [ds.id] + ds_scope

    try:
        val = validate_and_compile_formula(
            db,
            user,
            dataset_ids=ds_scope,
            formula=body.formula,
            calculation_type=body.calculation_type,
            qualify_with_alias=False,
            auto_safe_divide=True,
            self_field_name=tech_name,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    deps = [{"dataset_id": r["dataset_id"], "field_name": r["field_name"]} for r in val["referenced_fields"]]
    role = "measure" if (body.calculation_type == "calculated_measure" or val["output_data_type"] in ("integer", "double")) else "dimension"
    df = DatasetField(
        dataset_id=ds.id,
        field_name=tech_name,
        display_name=body.display_name.strip() or tech_name,
        data_type=val["output_data_type"],
        semantic_role=role,
        is_primary_key=False,
        is_calculated=True,
        expression=body.formula.strip(),
        description=body.description,
        calculation_type=body.calculation_type,
        dependent_fields_json=deps,
        access_scope=body.access_scope,
        format_json={"format_type": body.format_type, "unit": body.unit},
        version=1,
        created_by=user.id,
    )
    db.add(df)
    db.flush()
    for dep_ds_id in {dep["dataset_id"] for dep in deps}:
        if not db.query(DataLineage).filter(
            DataLineage.source_type == "dataset", DataLineage.source_id == dep_ds_id,
            DataLineage.target_type == "calculated_field", DataLineage.target_id == df.id,
        ).first():
            db.add(DataLineage(source_type="dataset", source_id=dep_ds_id, target_type="calculated_field", target_id=df.id, metadata_json={"field_name": tech_name}))
    cache_manager.invalidate_dataset(ds.id)
    record_audit(
        db,
        user.organization_id,
        "calculated_field_created",
        "dataset",
        resource_id=ds.id,
        user_id=user.id,
        username=user.username,
        ip_address=ip,
        details={"field_name": tech_name, "calculation_type": body.calculation_type},
        commit=False,
    )
    db.commit()
    db.refresh(df)
    return {
        "id": df.id,
        "field_name": df.field_name,
        "display_name": df.display_name,
        "calculation_type": df.calculation_type,
        "output_data_type": df.data_type,
        "compiled_sql": val["compiled_sql"],
        "warnings": val["warnings"],
        "version": df.version,
    }


@router.get("/calculated-fields/{field_id}/impact")
def get_calculated_field_impact(field_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    df = db.query(DatasetField).filter(DatasetField.id == field_id, DatasetField.is_calculated == True).first()
    if not df:
        raise HTTPException(status_code=404, detail="Calculated field not found")
    ds = get_dataset_for_org(db, df.dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", ds.id, "view")
    return _compute_field_impact(db, user.organization_id, ds.id, df.field_name)


@router.put("/calculated-fields/{field_id}")
def update_calculated_field(
    field_id: str,
    body: CalculatedFieldUpsertRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ip = get_client_ip(request)
    df = db.query(DatasetField).filter(DatasetField.id == field_id, DatasetField.is_calculated == True).first()
    if not df:
        raise HTTPException(status_code=404, detail="Calculated field not found")
    ds = get_dataset_for_org(db, df.dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", ds.id, "edit", ip_address=ip)

    impact = _compute_field_impact(db, user.organization_id, ds.id, df.field_name)
    if impact["has_dependencies"] and not body.force:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "فیلدهای محاسباتی یا ویجت‌های دیگر به این فیلد وابسته هستند؛ برای اعمال ویرایش force=true ارسال کنید.",
                "impact": impact,
            },
        )

    ds_scope = body.dataset_ids if body.dataset_ids else [ds.id]
    if ds.id not in ds_scope:
        ds_scope = [ds.id] + ds_scope
    try:
        val = validate_and_compile_formula(
            db,
            user,
            dataset_ids=ds_scope,
            formula=body.formula,
            calculation_type=body.calculation_type,
            qualify_with_alias=False,
            auto_safe_divide=True,
            self_field_name=df.field_name,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    df.display_name = body.display_name.strip() or df.display_name
    df.description = body.description
    df.expression = body.formula.strip()
    df.calculation_type = body.calculation_type
    df.data_type = val["output_data_type"]
    df.dependent_fields_json = [{"dataset_id": r["dataset_id"], "field_name": r["field_name"]} for r in val["referenced_fields"]]
    df.access_scope = body.access_scope
    df.format_json = {"format_type": body.format_type, "unit": body.unit}
    df.version = int(getattr(df, "version", 1) or 1) + 1
    cache_manager.invalidate_dataset(ds.id)
    db.commit()
    return {"id": df.id, "version": df.version, "compiled_sql": val["compiled_sql"], "warnings": val["warnings"], "impact": impact}


@router.delete("/calculated-fields/{field_id}")
def delete_calculated_field(
    field_id: str,
    force: bool = Query(default=False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    df = db.query(DatasetField).filter(DatasetField.id == field_id, DatasetField.is_calculated == True).first()
    if not df:
        raise HTTPException(status_code=404, detail="Calculated field not found")
    ds = get_dataset_for_org(db, df.dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", ds.id, "edit")
    impact = _compute_field_impact(db, user.organization_id, ds.id, df.field_name)
    if impact["has_dependencies"] and not force:
        raise HTTPException(
            status_code=409,
            detail={"message": "امکان حذف مستقیم وجود ندارد چون وابستگی فعال دارد.", "impact": impact},
        )
    db.delete(df)
    cache_manager.invalidate_dataset(ds.id)
    db.commit()
    return {"status": "deleted", "impact": impact}


@router.get("/measures")
def list_analytical_measures(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    metrics = (
        db.query(SemanticMetric)
        .filter(SemanticMetric.organization_id == user.organization_id)
        .order_by(SemanticMetric.updated_at.desc())
        .all()
    )
    out = []
    for m in metrics:
        if not permission_resolver.resolve(db, user, "dataset", m.dataset_id, "view").allowed:
            continue
        out.append({
            "id": m.id,
            "dataset_id": m.dataset_id,
            "name": m.name,
            "slug": m.slug,
            "description": m.description,
            "formula": m.formula,
            "metric_kind": getattr(m, "metric_kind", "analytical_metric"),
            "output_data_type": getattr(m, "output_data_type", "double"),
            "format_type": m.format_type,
            "unit": m.unit,
            "dependent_datasets": getattr(m, "dependent_datasets_json", []),
            "dependent_fields": getattr(m, "dependent_fields_json", []),
            "access_scope": getattr(m, "access_scope", "organization"),
            "version": getattr(m, "version", 1),
            "created_by": getattr(m, "created_by", None),
            "created_at": m.created_at,
            "updated_at": m.updated_at,
        })
    return out


@router.post("/measures")
def create_analytical_measure(
    body: MeasureUpsertRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ip = get_client_ip(request)
    ds = get_dataset_for_org(db, body.dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", ds.id, "edit", ip_address=ip)

    slug = sanitize_identifier(body.slug or body.name)
    if db.query(SemanticMetric).filter(SemanticMetric.organization_id == user.organization_id, SemanticMetric.slug == slug).first():
        raise HTTPException(status_code=409, detail=f"شاخصی با شناسه '{slug}' قبلاً ثبت شده است")

    ds_scope = body.dataset_ids if body.dataset_ids else [ds.id]
    if ds.id not in ds_scope:
        ds_scope = [ds.id] + ds_scope
    try:
        val = validate_and_compile_formula(
            db,
            user,
            dataset_ids=ds_scope,
            formula=body.formula,
            calculation_type=body.metric_kind,
            qualify_with_alias=False,
            auto_safe_divide=True,
            self_field_name=slug,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    sm = SemanticMetric(
        organization_id=user.organization_id,
        dataset_id=ds.id,
        name=body.name.strip() or slug,
        slug=slug,
        description=body.description,
        formula=body.formula.strip(),
        format_type=body.format_type,
        unit=body.unit,
        metric_kind=body.metric_kind,
        output_data_type=val["output_data_type"],
        dependent_datasets_json=val["referenced_datasets"],
        dependent_fields_json=[{"dataset_id": r["dataset_id"], "field_name": r["field_name"]} for r in val["referenced_fields"]],
        dependent_metrics_json=[],
        access_scope=body.access_scope,
        version=1,
        created_by=user.id,
    )
    db.add(sm)
    cache_manager.invalidate_dataset(ds.id)
    record_audit(
        db,
        user.organization_id,
        "analytical_metric_created",
        "dataset",
        resource_id=ds.id,
        user_id=user.id,
        username=user.username,
        ip_address=ip,
        details={"slug": slug, "metric_kind": body.metric_kind},
        commit=False,
    )
    db.commit()
    db.refresh(sm)
    return {"id": sm.id, "slug": sm.slug, "name": sm.name, "version": sm.version, "compiled_sql": val["compiled_sql"], "warnings": val["warnings"]}


@router.delete("/measures/{metric_id}")
def delete_analytical_measure(
    metric_id: str, force: bool = Query(default=False), user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    sm = db.query(SemanticMetric).filter(SemanticMetric.id == metric_id, SemanticMetric.organization_id == user.organization_id).first()
    if not sm:
        raise HTTPException(status_code=404, detail="Metric not found")
    enforce_permission(db, user, "dataset", sm.dataset_id, "edit")
    impact = _compute_field_impact(db, user.organization_id, sm.dataset_id, sm.slug)
    if impact["has_dependencies"] and not force:
        raise HTTPException(status_code=409, detail={"message": "این شاخص دارای وابستگی فعال در ویجت‌ها یا فرمول‌های دیگر است.", "impact": impact})
    db.delete(sm)
    cache_manager.invalidate_dataset(sm.dataset_id)
    db.commit()
    return {"status": "deleted", "impact": impact}


# ---------------- GENERATE / PREVIEW / SAVE DESCRIPTIVE DASHBOARD ----------------
@router.post("/generate")
def generate_descriptive_dashboard(
    body: GenerateDashboardRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return generate_descriptive_dashboard_plan(
            db,
            user,
            dataset_ids=body.dataset_ids,
            title=body.title,
            selected_fields=body.selected_fields,
            dimensions=body.dimensions,
            measures=body.measures,
            analysis_types=body.analysis_types,
            filters=body.filters,
            calendar_mode=body.calendar_mode,
            date_range=body.date_range,
            chosen_relationship_ids=body.chosen_relationship_ids,
            ad_hoc_joins=body.ad_hoc_joins,
            target_values=body.target_values,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/preview")
def preview_descriptive_widget(
    body: PreviewWidgetRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ds = get_dataset_for_org(db, body.dataset_id, user.organization_id)
    enforce_permission(db, user, "dataset", ds.id, "view")
    if body.chart_type not in ALL_30_CHART_TYPES:
        raise HTTPException(status_code=400, detail=f"نوع نمودار نامعتبر است: {body.chart_type}")

    try:
        if body.query_mode == "sql" and body.sql_text:
            res = execute_dataset_sql(
                db, user, body.sql_text, dataset=ds, filters=body.filters, use_cache=False, enforce_dataset_rbac=True
            )
            sql = body.sql_text
        else:
            sec_bundle: dict[str, Any] = {}
            sql, params, _ = build_visual_query_sql(
                db,
                ds,
                body.visual_spec,
                body.filters,
                [],
                [],
                user=user,
                enforce_join_rbac=True,
                security_bundle_out=sec_bundle,
            )
            raw = warehouse.execute_query(sql, params)
            vis_cols, masked_rows = apply_cls_and_masking(raw["columns"], raw["rows"], sec_bundle["merged_sec_ctx"])
            res = {
                "columns": vis_cols,
                "rows": masked_rows,
                "row_count": len(masked_rows),
                "execution_ms": raw["execution_ms"],
                "generated_sql": sql,
            }

        x_field = body.visual_spec.get("x_field")
        y_label = body.visual_spec.get("y_field") or "شاخص"
        narrative = _build_factual_narrative_fa(
            analysis_type=body.analysis_type,
            title=body.title,
            source_names=[ds.name],
            x_field=x_field,
            y_label=y_label,
            rows=res["rows"],
            total_records=int(ds.row_count or res["row_count"]),
            calendar_mode=body.calendar_mode,
        )
        return {**res, "narrative_fa": narrative}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/save")
def save_descriptive_dashboard(
    body: SaveDashboardRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ip = get_client_ip(request)
    enforce_permission(db, user, "dashboard", "*", "add", ip_address=ip)
    ws = (
        get_workspace_for_org(db, body.workspace_id, user.organization_id)
        if body.workspace_id
        else get_default_workspace_for_org(db, user.organization_id)
    )
    theme_id = body.theme_id
    if theme_id:
        get_theme_for_org(db, theme_id, user.organization_id)

    # Validate RBAC on ALL referenced datasets first
    for dsid in body.dataset_ids:
        ds = get_dataset_for_org(db, dsid, user.organization_id)
        enforce_permission(db, user, "dataset", ds.id, "view", ip_address=ip)

    # Create or update analytic DataModel record
    model_id = body.model_id
    if model_id:
        dm = db.query(DataModel).filter(DataModel.id == model_id, DataModel.organization_id == user.organization_id).first()
        if dm:
            dm.definition_json = body.model_definition
            dm.version = int(getattr(dm, "version", 1) or 1) + 1
    else:
        dm = DataModel(
            organization_id=user.organization_id,
            name=f"مدل: {body.title}",
            description=body.description,
            model_type="descriptive_model",
            definition_json=body.model_definition,
            version=1,
            created_by=user.id,
        )
        db.add(dm)
        db.flush()
        model_id = dm.id

    dash = Dashboard(
        workspace_id=ws.id,
        title=body.title.strip() or "داشبورد تحلیل توصیفی",
        description=body.description,
        theme_id=theme_id,
        is_public=False,
        is_template=False,
        created_by=user.id,
        settings_json={"descriptive_model_id": model_id, "source": "descriptive_builder"},
    )
    db.add(dash)
    db.flush()

    page = DashboardPage(dashboard_id=dash.id, title="تحلیل توصیفی", page_order=0, is_drill_through=False)
    db.add(page)
    db.flush()

    layout_items: list[dict[str, Any]] = []
    for idx, w_in in enumerate(body.widgets):
        ctype = str(w_in.get("chart_type") or "bar")
        if ctype not in ALL_30_CHART_TYPES:
            raise HTTPException(status_code=400, detail=f"نوع نمودار نامعتبر است: {ctype}")
        ds_id = w_in.get("dataset_id") or (body.dataset_ids[0] if body.dataset_ids else None)
        qmode = str(w_in.get("query_mode") or "visual")
        sql_txt = w_in.get("sql_text")
        vspec = w_in.get("visual_spec") or {}
        _enforce_widget_dataset_permissions(db, user, ds_id, qmode, sql_txt, vspec, ip_address=ip)

        lay = w_in.get("layout") or {}
        lx = int(lay.get("x", (idx % 2) * 6))
        ly = int(lay.get("y", (idx // 2) * 7))
        lw = int(lay.get("w", 6))
        lh = int(lay.get("h", 7))
        w = Widget(
            dashboard_id=dash.id,
            page_id=page.id,
            title=str(w_in.get("title") or f"ویجت {idx+1}"),
            description=str(w_in.get("narrative_fa") or w_in.get("description") or ""),
            chart_type=ctype,
            dataset_id=ds_id,
            layout_x=lx,
            layout_y=ly,
            layout_w=lw,
            layout_h=lh,
        )
        db.add(w)
        db.flush()
        db.add(WidgetConfig(
            widget_id=w.id,
            style_json=w_in.get("style") or {},
            visualization_json=w_in.get("visualization") or {"show_legend": True, "show_tooltip": True},
            interactions_json=w_in.get("interactions") or {"cross_filter": True},
            advanced_json={
                "narrative_fa": w_in.get("narrative_fa") or "",
                "sources_used": w_in.get("sources_used") or [],
                "analysis_type": w_in.get("analysis_type") or "",
            },
        ))
        db.add(WidgetQuery(
            widget_id=w.id,
            dataset_id=ds_id,
            query_mode=qmode,
            sql_text=sql_txt,
            visual_spec_json=vspec,
        ))
        if w_in.get("drill_hierarchy"):
            db.add(WidgetDrillRule(widget_id=w.id, hierarchy_json=w_in["drill_hierarchy"], level_rules_json=[]))
        layout_items.append({"i": w.id, "x": lx, "y": ly, "w": lw, "h": lh})

    db.add(DashboardLayout(page_id=page.id, columns_count=12, layout_json=layout_items))

    # Save Dashboard Filters
    for flt in body.filters:
        fname = str(flt.get("field_name") or "").strip()
        if not fname:
            continue
        db.add(DashboardFilter(
            dashboard_id=dash.id,
            page_id=page.id,
            scope="dashboard",
            name=str(flt.get("name") or fname),
            field_name=fname,
            dataset_id=flt.get("dataset_id") or (body.dataset_ids[0] if body.dataset_ids else None),
            filter_type=str(flt.get("filter_type") or "multi_select"),
            operator=str(flt.get("operator") or "in"),
            default_value_json={"value": flt.get("value")} if flt.get("value") is not None else None,
            target_widget_ids=[],
            excluded_widget_ids=[],
            is_locked=False,
        ))

    # Track lineage for every source dataset
    for dsid in set(body.dataset_ids):
        if not db.query(DataLineage).filter(
            DataLineage.source_type == "dataset",
            DataLineage.source_id == dsid,
            DataLineage.target_type == "dashboard",
            DataLineage.target_id == dash.id,
        ).first():
            db.add(DataLineage(source_type="dataset", source_id=dsid, target_type="dashboard", target_id=dash.id))

    if body.allow_public_restricted is not None:
        _apply_public_override_change(
            db, user, dash, enable=bool(body.allow_public_restricted), reason=body.public_restricted_reason, ip_address=ip, operation="descriptive_save"
        )
    if body.is_public:
        enforce_permission(db, user, "dashboard", "*", "share", ip_address=ip)
        _assert_dashboard_public_safe(db, user.organization_id, dash)
        dash.embed_token = secrets.token_urlsafe(24)
        dash.is_public = True

    db.add(DashboardVersion(
        dashboard_id=dash.id,
        version_number=1,
        label="نسخه اولیه (تولید داشبورد توصیفی)",
        snapshot_json={"title": dash.title, "model_id": model_id, "widget_count": len(body.widgets)},
        created_by=user.id,
    ))
    record_audit(
        db,
        user.organization_id,
        "descriptive_dashboard_saved",
        "dashboard",
        resource_id=dash.id,
        user_id=user.id,
        username=user.username,
        ip_address=ip,
        details={"dataset_ids": body.dataset_ids, "widget_count": len(body.widgets)},
        commit=False,
    )
    db.commit()
    return {"id": dash.id, "title": dash.title, "model_id": model_id, "default_page_id": page.id, "widget_count": len(body.widgets)}
