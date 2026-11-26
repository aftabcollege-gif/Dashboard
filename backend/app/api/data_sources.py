import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.api.deps import enforce_permission, enforce_scoped_rate_limit, get_client_ip, get_current_user
from app.config import settings
from app.core.audit import record_audit
from app.core.security import decrypt_secret, encrypt_secret
from app.core.tenant import get_data_source_for_org
from app.database import db_session_scope, get_db
from app.engines.composite_engine import materialize_composite_dataset
from app.engines.connectors import (
    SUPPORTED_DB_TYPES,
    DataConnectorEngine,
    preview_file_from_path,
    stream_import_file_to_warehouse,
)
from app.engines.job_runner import create_job, launch_background_job
from app.engines.warehouse import sanitize_identifier, warehouse
from app.models.data import (
    CompositeDataset,
    DataLineage,
    DataSource,
    DataSourceCredential,
    Dataset,
    DatasetField,
)
from app.models.identity import User

router = APIRouter(prefix="/data-sources", tags=["Data Sources & File Ingestion"])

ALLOWED_UPLOAD_EXTS = {
    ".xlsx",
    ".xls",
    ".csv",
    ".tsv",
    ".json",
    ".jsonl",
    ".ndjson",
    ".log",
    ".txt",
    ".xml",
}


class DataSourceCreate(BaseModel):
    name: str
    source_type: str
    read_only: bool = True
    config_json: dict[str, Any] = {}
    credentials: dict[str, Any] = {}
    refresh_policy: str = "scheduled"


class DirectQueryBody(BaseModel):
    query: str
    limit: int = 200


class CompositeJoinSpec(BaseModel):
    table: str
    join_type: str = "LEFT"
    left_table: Optional[str] = None
    left_col: str
    right_col: str


class CompositeColumnSpec(BaseModel):
    table: Optional[str] = None
    column: str
    alias: Optional[str] = None


class CompositeDatasetCreate(BaseModel):
    data_source_id: str
    name: str
    description: str = ""
    base_table: str
    joins: list[CompositeJoinSpec] = []
    selected_columns: list[CompositeColumnSpec] = []
    limit_rows: int = 100000
    refresh_schedule: str = "manual"
    async_mode: bool = False


async def _stream_upload_to_path(file: UploadFile, dest_path: str) -> int:
    total = 0
    try:
        with open(dest_path, "wb") as out_f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if settings.MAX_UPLOAD_BYTES > 0 and total > settings.MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Uploaded file exceeds maximum allowed size ({settings.MAX_UPLOAD_BYTES // (1024 * 1024)} MB)",
                    )
                out_f.write(chunk)
    except Exception:
        if os.path.exists(dest_path):
            try:
                os.remove(dest_path)
            except OSError:
                pass
        raise
    return total


def _org_upload_dir(org_id: str) -> str:
    safe_org = re.sub(r"[^A-Za-z0-9_-]", "", org_id) or "default"
    path = os.path.join(settings.UPLOADS_DIR, safe_org)
    os.makedirs(path, exist_ok=True)
    return path


def _check_org_storage_quota(org_dir: str, incoming_bytes: int) -> None:
    if settings.ORG_STORAGE_QUOTA_BYTES <= 0:
        return
    used = 0
    for root, _, files in os.walk(org_dir):
        for fname in files:
            try:
                used += os.path.getsize(os.path.join(root, fname))
            except OSError:
                pass
    if used + incoming_bytes > settings.ORG_STORAGE_QUOTA_BYTES:
        raise HTTPException(
            status_code=413,
            detail="Organization file storage quota exceeded; delete unused uploaded datasets first",
        )


@router.get("/supported-types")
def get_supported_types():
    return {
        "databases": SUPPORTED_DB_TYPES,
        "files": ["xlsx", "xls", "csv", "tsv", "json", "jsonl", "ndjson", "log", "txt", "xml"],
    }


@router.get("")
def list_data_sources(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "data_source", "*", "view")
    rows = db.query(DataSource).filter(DataSource.organization_id == user.organization_id).all()
    return [
        {
            "id": ds.id,
            "name": ds.name,
            "source_type": ds.source_type,
            "read_only": ds.read_only,
            "config_json": ds.config_json,
            "refresh_policy": ds.refresh_policy,
            "health_status": ds.health_status,
            "last_tested_at": ds.last_tested_at,
            "last_error": ds.last_error,
            "created_at": ds.created_at,
        }
        for ds in rows
    ]


@router.post("")
def create_data_source(
    body: DataSourceCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(db, user, "data_source", "*", "add", ip_address=get_client_ip(request))
    src = DataSource(
        organization_id=user.organization_id,
        name=body.name,
        source_type=body.source_type,
        read_only=body.read_only,
        config_json=body.config_json,
        refresh_policy=body.refresh_policy,
        created_by=user.id,
    )
    db.add(src)
    db.flush()
    db.add(
        DataSourceCredential(
            data_source_id=src.id,
            encrypted_payload=encrypt_secret(json.dumps(body.credentials)),
        )
    )
    record_audit(
        db, user.organization_id, "data_source_created", "data_source",
        resource_id=src.id, user_id=user.id, username=user.username,
        ip_address=get_client_ip(request), details={"type": body.source_type}
    )
    db.commit()
    return {"id": src.id, "name": src.name}


@router.get("/composite-datasets")
def list_composite_datasets(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "data_source", "*", "view")
    rows = (
        db.query(CompositeDataset)
        .filter(CompositeDataset.organization_id == user.organization_id)
        .order_by(CompositeDataset.created_at.desc())
        .all()
    )
    return [
        {
            "id": c.id,
            "data_source_id": c.data_source_id,
            "dataset_id": c.dataset_id,
            "name": c.name,
            "description": c.description,
            "definition_json": c.definition_json,
            "refresh_schedule": c.refresh_schedule,
            "last_refreshed_at": c.last_refreshed_at,
            "last_status": c.last_status,
            "last_error": c.last_error,
            "created_at": c.created_at,
        }
        for c in rows
    ]


@router.post("/composite-datasets")
def create_composite_dataset(
    body: CompositeDatasetCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    src = get_data_source_for_org(db, body.data_source_id, user.organization_id)
    enforce_permission(db, user, "data_source", src.id, "view", ip_address=get_client_ip(request))
    enforce_permission(db, user, "dataset", "*", "add", ip_address=get_client_ip(request))

    definition = {
        "base_table": body.base_table,
        "joins": [j.model_dump() for j in body.joins],
        "selected_columns": [c.model_dump() for c in body.selected_columns],
        "limit_rows": body.limit_rows,
    }
    comp = CompositeDataset(
        organization_id=user.organization_id,
        data_source_id=src.id,
        name=body.name,
        description=body.description,
        definition_json=definition,
        refresh_schedule=body.refresh_schedule,
        last_status="pending",
        created_by=user.id,
    )
    db.add(comp)
    db.commit()
    db.refresh(comp)

    if body.async_mode:
        job = create_job(
            db,
            organization_id=user.organization_id,
            job_type="composite_dataset",
            title=f"ساخت دیتاست ترکیبی: {body.name}",
            total_items=100,
            created_by=user.id,
        )
        comp_id = comp.id
        src_id = src.id
        uid = user.id
        uname = user.username

        def _worker(_job_id: str, progress_cb):
            with db_session_scope() as bg_db:
                c_obj = bg_db.query(CompositeDataset).filter(CompositeDataset.id == comp_id).first()
                s_obj = bg_db.query(DataSource).filter(DataSource.id == src_id).first()
                return materialize_composite_dataset(
                    bg_db,
                    comp=c_obj,
                    src=s_obj,
                    actor_user_id=uid,
                    actor_username=uname,
                    progress_callback=progress_cb,
                )

        launch_background_job(job.id, _worker)
        return {"id": comp.id, "job_id": job.id, "status": "queued"}

    try:
        res = materialize_composite_dataset(
            db, comp=comp, src=src, actor_user_id=user.id, actor_username=user.username
        )
        return res
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to materialize composite dataset: {exc}")


@router.post("/composite-datasets/{comp_id}/refresh")
def refresh_composite_dataset(
    comp_id: str,
    async_mode: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    comp = (
        db.query(CompositeDataset)
        .filter(CompositeDataset.id == comp_id, CompositeDataset.organization_id == user.organization_id)
        .first()
    )
    if not comp:
        raise HTTPException(status_code=404, detail="Composite dataset not found")
    src = get_data_source_for_org(db, comp.data_source_id, user.organization_id)
    enforce_permission(db, user, "dataset", "*", "edit")

    if async_mode:
        job = create_job(
            db,
            organization_id=user.organization_id,
            job_type="composite_refresh",
            title=f"به‌روزرسانی دیتاست ترکیبی: {comp.name}",
            total_items=100,
            created_by=user.id,
        )
        uid = user.id
        uname = user.username
        src_id = src.id

        def _worker(_job_id: str, progress_cb):
            with db_session_scope() as bg_db:
                c_obj = bg_db.query(CompositeDataset).filter(CompositeDataset.id == comp_id).first()
                s_obj = bg_db.query(DataSource).filter(DataSource.id == src_id).first()
                return materialize_composite_dataset(
                    bg_db,
                    comp=c_obj,
                    src=s_obj,
                    actor_user_id=uid,
                    actor_username=uname,
                    progress_callback=progress_cb,
                )

        launch_background_job(job.id, _worker)
        return {"id": comp.id, "job_id": job.id, "status": "queued"}

    try:
        return materialize_composite_dataset(
            db, comp=comp, src=src, actor_user_id=user.id, actor_username=user.username
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Refresh failed: {exc}")


@router.delete("/composite-datasets/{comp_id}")
def delete_composite_dataset(
    comp_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    comp = (
        db.query(CompositeDataset)
        .filter(CompositeDataset.id == comp_id, CompositeDataset.organization_id == user.organization_id)
        .first()
    )
    if not comp:
        raise HTTPException(status_code=404, detail="Composite dataset not found")
    enforce_permission(db, user, "data_source", comp.data_source_id, "view")
    db.delete(comp)
    db.commit()
    return {"status": "deleted"}


@router.delete("/{source_id}")
def delete_data_source(
    source_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    src = get_data_source_for_org(db, source_id, user.organization_id)
    enforce_permission(db, user, "data_source", src.id, "delete", ip_address=get_client_ip(request))
    fpath = (src.config_json or {}).get("file_path")
    if fpath and os.path.exists(fpath):
        org_dir = os.path.realpath(_org_upload_dir(user.organization_id))
        if os.path.realpath(fpath).startswith(org_dir + os.sep):
            try:
                os.remove(fpath)
            except OSError:
                pass
    db.delete(src)
    db.commit()
    return {"status": "deleted"}


@router.post("/{source_id}/test")
def test_data_source(source_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    src = get_data_source_for_org(db, source_id, user.organization_id)
    enforce_permission(db, user, "data_source", src.id, "view")
    cred_row = db.query(DataSourceCredential).filter(DataSourceCredential.data_source_id == src.id).first()
    creds = json.loads(decrypt_secret(cred_row.encrypted_payload)) if cred_row else {}
    result = DataConnectorEngine.test_connection(src.source_type, src.config_json, creds)
    src.last_tested_at = datetime.now(timezone.utc)
    src.health_status = "healthy" if result.get("ok") else "error"
    src.last_error = None if result.get("ok") else result.get("error")
    db.commit()
    return result


@router.get("/{source_id}/schema")
def browse_source_schema(source_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    src = get_data_source_for_org(db, source_id, user.organization_id)
    enforce_permission(db, user, "data_source", src.id, "view")
    cred_row = db.query(DataSourceCredential).filter(DataSourceCredential.data_source_id == src.id).first()
    creds = json.loads(decrypt_secret(cred_row.encrypted_payload)) if cred_row else {}
    try:
        tables = DataConnectorEngine.list_tables_and_columns(src.source_type, src.config_json, creds)
        return {"source_id": src.id, "tables": tables}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{source_id}/query")
def execute_direct_source_query(
    source_id: str,
    body: DirectQueryBody,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ip = get_client_ip(request)
    enforce_scoped_rate_limit(
        "source_sql",
        ip=ip,
        user_id=user.id,
        org_id=user.organization_id,
        ip_limit=settings.RATE_LIMIT_SQL_PER_MIN * 2,
        user_limit=settings.RATE_LIMIT_SQL_PER_MIN,
        org_limit=settings.RATE_LIMIT_SQL_PER_MIN * 5,
    )
    src = get_data_source_for_org(db, source_id, user.organization_id)
    enforce_permission(db, user, "data_source", src.id, "execute_query", ip_address=ip)
    cred_row = db.query(DataSourceCredential).filter(DataSourceCredential.data_source_id == src.id).first()
    creds = json.loads(decrypt_secret(cred_row.encrypted_payload)) if cred_row else {}
    bounded_limit = max(1, min(int(body.limit), 2000))
    try:
        df = DataConnectorEngine.extract_dataframe(
            src.source_type,
            src.config_json,
            creds,
            query=body.query,
            read_only=True,
            max_rows=bounded_limit,
        )
        df_head = df.head(bounded_limit)
        rows = json.loads(df_head.to_json(orient="records", date_format="iso"))
        return {"columns": list(df_head.columns), "rows": rows, "row_count": len(rows), "total_rows": len(df)}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=400, detail="Direct data source query execution failed")


@router.post("/files/preview")
async def preview_uploaded_file(
    file: UploadFile = File(...),
    sheet_name: Optional[str] = Form(None),
    has_header: bool = Form(True),
    null_strategy: str = Form("keep"),
    deduplicate: bool = Form(False),
    column_renames_json: str = Form("{}"),
    type_overrides_json: str = Form("{}"),
    selected_columns_json: str = Form("[]"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(db, user, "dataset", "*", "add")
    fname = file.filename or "upload.csv"
    ext = os.path.splitext(fname.lower())[1]
    if ext not in ALLOWED_UPLOAD_EXTS:
        raise HTTPException(status_code=400, detail=f"Unsupported file extension '{ext}'")

    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tf:
        tmp_path = tf.name
    try:
        await _stream_upload_to_path(file, tmp_path)
        sel_cols = json.loads(selected_columns_json or "[]")
        parsed = preview_file_from_path(
            file_path=tmp_path,
            filename=fname,
            sheet_name=sheet_name,
            has_header=has_header,
            null_strategy=null_strategy,
            deduplicate=deduplicate,
            column_renames=json.loads(column_renames_json or "{}"),
            type_overrides=json.loads(type_overrides_json or "{}"),
            selected_columns=sel_cols if isinstance(sel_cols, list) and sel_cols else None,
        )
        return {
            "filename": file.filename,
            "sheets": parsed["sheets"],
            "sheet_summaries": parsed.get("sheet_summaries", []),
            "total_rows": parsed["total_rows"],
            "row_count_estimated": parsed.get("row_count_estimated", False),
            "duplicate_count": parsed["duplicate_count"],
            "null_counts": parsed["null_counts"],
            "data_quality": parsed["data_quality"],
            "row_accounting": parsed.get("row_accounting", {}),
            "columns": parsed["columns"],
            "preview_rows": parsed["preview_rows"],
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"File parse failed: {exc}")
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _execute_file_import_job(
    db: Session,
    *,
    organization_id: str,
    user_id: str,
    username: str,
    ip_address: Optional[str],
    saved_path: str,
    fname: str,
    ext: str,
    dataset_name: str,
    sheet_name: Optional[str],
    sheet_names: list[str],
    merge_sheets: bool,
    has_header: bool,
    null_strategy: str,
    deduplicate: bool,
    column_renames: dict[str, str],
    type_overrides: dict[str, str],
    selected_columns: Optional[list[str]],
    progress_callback=None,
) -> dict[str, Any]:
    org_short = re.sub(r"[^a-z0-9]", "", organization_id.lower())[:6] or "org"
    target_sheets = sheet_names if sheet_names else [sheet_name]
    created_tables: list[str] = []
    created_datasets: list[dict[str, Any]] = []

    try:
        src = DataSource(
            organization_id=organization_id,
            name=f"File: {fname}",
            source_type="excel" if ext in ("xlsx", "xls") else ext,
            read_only=True,
            config_json={"file_path": saved_path, "sheet_name": sheet_name, "sheets": sheet_names},
            refresh_policy="manual",
            health_status="healthy",
            last_tested_at=datetime.now(timezone.utc),
            created_by=user_id,
        )
        db.add(src)
        db.commit()  # Release SQLite write lock before streaming so progress callbacks never contend
        db.refresh(src)

        if merge_sheets and len(target_sheets) > 1:
            clean_slug = re.sub(r"_+", "_", sanitize_identifier(dataset_name)).strip("_")[:40] or "excel"
            wh_table = f"wh_file_{org_short}_{clean_slug}_{uuid.uuid4().hex[:8]}"
            created_tables.append(wh_table)

            agg_total = 0
            agg_input = 0
            agg_invalid = 0
            agg_coerced = 0
            agg_warnings: list[str] = []
            last_imported: dict[str, Any] = {}

            for s_idx, sname in enumerate(target_sheets):
                imported = stream_import_file_to_warehouse(
                    file_path=saved_path,
                    filename=fname,
                    wh_table=wh_table,
                    warehouse_engine=warehouse,
                    sheet_name=sname,
                    has_header=has_header,
                    null_strategy=null_strategy,
                    deduplicate=deduplicate and s_idx == len(target_sheets) - 1,
                    column_renames=column_renames,
                    type_overrides=type_overrides,
                    selected_columns=selected_columns,
                    progress_callback=progress_callback,
                    append_mode=s_idx > 0,
                    sheet_tag_value=sname or "Sheet1",
                )
                last_imported = imported
                ra = imported.get("row_accounting") or {}
                agg_total += int(imported.get("total_rows") or 0)
                agg_input += int(ra.get("input_rows") or imported.get("total_rows") or 0)
                agg_invalid += int(ra.get("invalid_rows") or 0)
                agg_coerced += int(ra.get("coerced_invalid_cells") or 0)
                agg_warnings.extend(ra.get("warnings") or [])

            final_desc = warehouse.describe_table(wh_table)
            merged_row_accounting = {
                "input_rows": agg_input,
                "processed_rows": agg_input,
                "valid_rows": agg_total,
                "invalid_rows": agg_invalid,
                "coerced_invalid_cells": agg_coerced,
                "merged_sheets": target_sheets,
                "warnings": list(dict.fromkeys(agg_warnings)),
            }
            ds = Dataset(
                organization_id=organization_id,
                data_source_id=src.id,
                name=dataset_name,
                description=f"Imported from {fname} (merged sheets: {', '.join([str(s) for s in target_sheets])})",
                warehouse_table=wh_table,
                schema_type="flat",
                refresh_policy="manual",
                row_count=agg_total,
                last_refreshed_at=datetime.now(timezone.utc),
                freshness_status="fresh",
                profile_json={"row_accounting": merged_row_accounting, "sheets": target_sheets},
            )
            db.add(ds)
            db.flush()
            cols_meta = last_imported.get("columns", [])
            existing_names = {cm["field_name"] for cm in cols_meta}
            for dc in final_desc:
                if dc["column_name"] not in existing_names:
                    cols_meta.append(
                        {
                            "field_name": dc["column_name"],
                            "display_name": "نام شیت" if dc["column_name"] == "_sheet_name" else dc["column_name"],
                            "data_type": "string",
                            "semantic_role": "dimension",
                        }
                    )
            db.add_all(
                [
                    DatasetField(
                        dataset_id=ds.id,
                        field_name=col_meta["field_name"],
                        display_name=col_meta["display_name"],
                        data_type=col_meta["data_type"],
                        semantic_role=col_meta["semantic_role"],
                    )
                    for col_meta in cols_meta
                ]
            )
            db.add(
                DataLineage(
                    source_type="data_source",
                    source_id=src.id,
                    target_type="dataset",
                    target_id=ds.id,
                    metadata_json={
                        "filename": fname,
                        "rows": agg_total,
                        "columns_count": len(cols_meta),
                        "data_quality": last_imported.get("data_quality", {}),
                        "row_accounting": merged_row_accounting,
                    },
                )
            )
            created_datasets.append(
                {
                    "dataset_id": ds.id,
                    "data_source_id": src.id,
                    "name": ds.name,
                    "warehouse_table": wh_table,
                    "row_count": ds.row_count,
                    "total_rows": ds.row_count,
                    "columns_count": len(cols_meta),
                    "data_quality": last_imported.get("data_quality", {}),
                    "row_accounting": merged_row_accounting,
                    "columns": cols_meta,
                }
            )
        else:
            for s_idx, sname in enumerate(target_sheets):
                ds_label = dataset_name if len(target_sheets) == 1 else f"{dataset_name} ({sname})"
                clean_slug = re.sub(r"_+", "_", sanitize_identifier(ds_label)).strip("_")[:40] or "excel"
                wh_table = f"wh_file_{org_short}_{clean_slug}_{uuid.uuid4().hex[:8]}"
                created_tables.append(wh_table)

                imported = stream_import_file_to_warehouse(
                    file_path=saved_path,
                    filename=fname,
                    wh_table=wh_table,
                    warehouse_engine=warehouse,
                    sheet_name=sname,
                    has_header=has_header,
                    null_strategy=null_strategy,
                    deduplicate=deduplicate,
                    column_renames=column_renames,
                    type_overrides=type_overrides,
                    selected_columns=selected_columns,
                    progress_callback=progress_callback,
                )
                row_acc = imported.get("row_accounting") or {}
                ds = Dataset(
                    organization_id=organization_id,
                    data_source_id=src.id,
                    name=ds_label,
                    description=f"Imported from {fname}" + (f" [{sname}]" if sname else ""),
                    warehouse_table=wh_table,
                    schema_type="flat",
                    refresh_policy="manual",
                    row_count=imported["total_rows"],
                    last_refreshed_at=datetime.now(timezone.utc),
                    freshness_status="fresh",
                    profile_json={"row_accounting": row_acc, "sheet_name": sname},
                )
                db.add(ds)
                db.flush()
                db.add_all(
                    [
                        DatasetField(
                            dataset_id=ds.id,
                            field_name=col_meta["field_name"],
                            display_name=col_meta["display_name"],
                            data_type=col_meta["data_type"],
                            semantic_role=col_meta["semantic_role"],
                        )
                        for col_meta in imported["columns"]
                    ]
                )
                db.add(
                    DataLineage(
                        source_type="data_source",
                        source_id=src.id,
                        target_type="dataset",
                        target_id=ds.id,
                        metadata_json={
                            "filename": fname,
                            "sheet_name": sname,
                            "rows": imported["total_rows"],
                            "columns_count": len(imported["columns"]),
                            "data_quality": imported["data_quality"],
                            "row_accounting": row_acc,
                        },
                    )
                )
                record_audit(
                    db, organization_id, "file_dataset_imported", "dataset",
                    resource_id=ds.id, user_id=user_id, username=username,
                    ip_address=ip_address,
                    details={
                        "filename": fname,
                        "sheet": sname,
                        "rows": imported["total_rows"],
                        "columns": len(imported["columns"]),
                        "dq_score": imported["data_quality"]["dq_score"],
                    },
                    commit=False,
                )
                created_datasets.append(
                    {
                        "dataset_id": ds.id,
                        "data_source_id": src.id,
                        "name": ds.name,
                        "warehouse_table": wh_table,
                        "row_count": ds.row_count,
                        "total_rows": ds.row_count,
                        "columns_count": len(imported["columns"]),
                        "data_quality": imported["data_quality"],
                        "row_accounting": row_acc,
                        "columns": imported["columns"],
                    }
                )
        db.commit()
    except Exception:
        db.rollback()
        for t in created_tables:
            try:
                warehouse.drop_table(t)
            except Exception:
                pass
        raise

    primary = created_datasets[0]
    return {
        **primary,
        "created_datasets": created_datasets,
    }


@router.post("/files/import")
async def import_uploaded_file_dataset(
    request: Request,
    file: UploadFile = File(...),
    dataset_name: str = Form(...),
    sheet_name: Optional[str] = Form(None),
    sheet_names_json: str = Form("[]"),
    merge_sheets: bool = Form(False),
    has_header: bool = Form(True),
    null_strategy: str = Form("keep"),
    deduplicate: bool = Form(False),
    column_renames_json: str = Form("{}"),
    type_overrides_json: str = Form("{}"),
    selected_columns_json: str = Form("[]"),
    async_mode: bool = Form(False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_permission(db, user, "dataset", "*", "add", ip_address=get_client_ip(request))
    fname = file.filename or "upload.csv"
    ext_with_dot = os.path.splitext(fname.lower())[1]
    if ext_with_dot not in ALLOWED_UPLOAD_EXTS:
        raise HTTPException(status_code=400, detail=f"Unsupported file extension '{ext_with_dot}'")
    ext = ext_with_dot.lstrip(".")

    org_dir = _org_upload_dir(user.organization_id)
    safe_stem = re.sub(r"_+", "_", sanitize_identifier(os.path.splitext(fname)[0])).strip("_") or "upload"
    safe_fname = f"{int(datetime.now().timestamp())}_{uuid.uuid4().hex[:8]}_{safe_stem}.{ext}"
    saved_path = os.path.join(org_dir, safe_fname)

    await _stream_upload_to_path(file, saved_path)
    try:
        _check_org_storage_quota(org_dir, 0)
    except HTTPException:
        if os.path.exists(saved_path):
            try:
                os.remove(saved_path)
            except OSError:
                pass
        raise

    parsed_sheets = json.loads(sheet_names_json or "[]")
    sheet_names = [str(s) for s in parsed_sheets if s] if isinstance(parsed_sheets, list) else []
    parsed_sel_cols = json.loads(selected_columns_json or "[]")
    selected_columns = [str(c) for c in parsed_sel_cols if c] if isinstance(parsed_sel_cols, list) and parsed_sel_cols else None
    col_renames = json.loads(column_renames_json or "{}")
    type_overrides = json.loads(type_overrides_json or "{}")
    ip_addr = get_client_ip(request)

    if async_mode:
        job = create_job(
            db,
            organization_id=user.organization_id,
            job_type="excel_import",
            title=f"بارگذاری فایل: {fname}",
            total_items=100,
            created_by=user.id,
        )
        org_id = user.organization_id
        uid = user.id
        uname = user.username

        def _bg_import(_job_id: str, progress_cb):
            with db_session_scope() as bg_db:
                return _execute_file_import_job(
                    bg_db,
                    organization_id=org_id,
                    user_id=uid,
                    username=uname,
                    ip_address=ip_addr,
                    saved_path=saved_path,
                    fname=fname,
                    ext=ext,
                    dataset_name=dataset_name,
                    sheet_name=sheet_name,
                    sheet_names=sheet_names,
                    merge_sheets=merge_sheets,
                    has_header=has_header,
                    null_strategy=null_strategy,
                    deduplicate=deduplicate,
                    column_renames=col_renames,
                    type_overrides=type_overrides,
                    selected_columns=selected_columns,
                    progress_callback=progress_cb,
                )

        launch_background_job(job.id, _bg_import)
        return {"job_id": job.id, "status": "queued"}

    try:
        return _execute_file_import_job(
            db,
            organization_id=user.organization_id,
            user_id=user.id,
            username=user.username,
            ip_address=ip_addr,
            saved_path=saved_path,
            fname=fname,
            ext=ext,
            dataset_name=dataset_name,
            sheet_name=sheet_name,
            sheet_names=sheet_names,
            merge_sheets=merge_sheets,
            has_header=has_header,
            null_strategy=null_strategy,
            deduplicate=deduplicate,
            column_renames=col_renames,
            type_overrides=type_overrides,
            selected_columns=selected_columns,
        )
    except HTTPException:
        if os.path.exists(saved_path):
            try:
                os.remove(saved_path)
            except OSError:
                pass
        raise
    except Exception as exc:
        if os.path.exists(saved_path):
            try:
                os.remove(saved_path)
            except OSError:
                pass
        raise HTTPException(status_code=400, detail=f"Import parse error: {exc}")
