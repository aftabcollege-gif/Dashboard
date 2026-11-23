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
from app.database import get_db
from app.engines.connectors import (
    SUPPORTED_DB_TYPES,
    DataConnectorEngine,
    preview_file_from_path,
    stream_import_file_to_warehouse,
)
from app.engines.warehouse import sanitize_identifier, warehouse
from app.models.data import (
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
        parsed = preview_file_from_path(
            file_path=tmp_path,
            filename=fname,
            sheet_name=sheet_name,
            has_header=has_header,
            null_strategy=null_strategy,
            deduplicate=deduplicate,
            column_renames=json.loads(column_renames_json or "{}"),
            type_overrides=json.loads(type_overrides_json or "{}"),
        )
        return {
            "filename": file.filename,
            "sheets": parsed["sheets"],
            "total_rows": parsed["total_rows"],
            "row_count_estimated": parsed.get("row_count_estimated", False),
            "duplicate_count": parsed["duplicate_count"],
            "null_counts": parsed["null_counts"],
            "data_quality": parsed["data_quality"],
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


@router.post("/files/import")
async def import_uploaded_file_dataset(
    request: Request,
    file: UploadFile = File(...),
    dataset_name: str = Form(...),
    sheet_name: Optional[str] = Form(None),
    has_header: bool = Form(True),
    null_strategy: str = Form("keep"),
    deduplicate: bool = Form(False),
    column_renames_json: str = Form("{}"),
    type_overrides_json: str = Form("{}"),
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
        _check_org_storage_quota(org_dir, 0)  # saved_path already exists inside org_dir
    except HTTPException:
        if os.path.exists(saved_path):
            try:
                os.remove(saved_path)
            except OSError:
                pass
        raise

    org_short = re.sub(r"[^a-z0-9]", "", user.organization_id.lower())[:6] or "org"
    clean_slug = re.sub(r"_+", "_", sanitize_identifier(dataset_name)).strip("_")[:40] or "excel"
    wh_table = f"wh_file_{org_short}_{clean_slug}_{uuid.uuid4().hex[:8]}"

    try:
        try:
            imported = stream_import_file_to_warehouse(
                file_path=saved_path,
                filename=fname,
                wh_table=wh_table,
                warehouse_engine=warehouse,
                sheet_name=sheet_name,
                has_header=has_header,
                null_strategy=null_strategy,
                deduplicate=deduplicate,
                column_renames=json.loads(column_renames_json or "{}"),
                type_overrides=json.loads(type_overrides_json or "{}"),
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Import parse error: {exc}")

        src = DataSource(
            organization_id=user.organization_id,
            name=f"File: {fname}",
            source_type="excel" if ext in ("xlsx", "xls") else ext,
            read_only=True,
            config_json={"file_path": saved_path, "sheet_name": sheet_name},
            refresh_policy="manual",
            health_status="healthy",
            last_tested_at=datetime.now(timezone.utc),
            created_by=user.id,
        )
        db.add(src)
        db.flush()

        ds = Dataset(
            organization_id=user.organization_id,
            data_source_id=src.id,
            name=dataset_name,
            description=f"Imported from {fname}",
            warehouse_table=wh_table,
            schema_type="flat",
            refresh_policy="manual",
            row_count=imported["total_rows"],
            last_refreshed_at=datetime.now(timezone.utc),
            freshness_status="fresh",
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
                    "rows": imported["total_rows"],
                    "columns_count": len(imported["columns"]),
                    "data_quality": imported["data_quality"],
                },
            )
        )
        record_audit(
            db, user.organization_id, "file_dataset_imported", "dataset",
            resource_id=ds.id, user_id=user.id, username=user.username,
            ip_address=get_client_ip(request),
            details={
                "filename": fname,
                "rows": imported["total_rows"],
                "columns": len(imported["columns"]),
                "dq_score": imported["data_quality"]["dq_score"],
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        try:
            warehouse.drop_table(wh_table)
        except Exception:
            pass
        if os.path.exists(saved_path):
            try:
                os.remove(saved_path)
            except OSError:
                pass
        raise

    return {
        "dataset_id": ds.id,
        "data_source_id": src.id,
        "name": ds.name,
        "warehouse_table": wh_table,
        "row_count": ds.row_count,
        "columns_count": len(imported["columns"]),
        "data_quality": imported["data_quality"],
        "columns": imported["columns"],
    }
