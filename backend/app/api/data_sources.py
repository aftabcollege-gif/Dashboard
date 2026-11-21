import json
import os
from datetime import datetime, timezone
from typing import Any, Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.api.deps import enforce_permission, get_client_ip, get_current_user
from app.config import settings
from app.core.audit import record_audit
from app.core.security import decrypt_secret, encrypt_secret
from app.database import get_db
from app.engines.connectors import (
    SUPPORTED_DB_TYPES,
    DataConnectorEngine,
    parse_file_to_preview,
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


@router.get("/supported-types")
def get_supported_types():
    return {
        "databases": SUPPORTED_DB_TYPES,
        "files": ["xlsx", "xls", "csv", "json", "xml"],
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
    enforce_permission(db, user, "data_source", source_id, "delete", ip_address=get_client_ip(request))
    src = db.query(DataSource).filter(DataSource.id == source_id).first()
    if not src:
        raise HTTPException(status_code=404, detail="Data source not found")
    db.delete(src)
    db.commit()
    return {"status": "deleted"}


@router.post("/{source_id}/test")
def test_data_source(source_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_permission(db, user, "data_source", source_id, "view")
    src = db.query(DataSource).filter(DataSource.id == source_id).first()
    if not src:
        raise HTTPException(status_code=404, detail="Data source not found")
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
    enforce_permission(db, user, "data_source", source_id, "view")
    src = db.query(DataSource).filter(DataSource.id == source_id).first()
    if not src:
        raise HTTPException(status_code=404, detail="Data source not found")
    cred_row = db.query(DataSourceCredential).filter(DataSourceCredential.data_source_id == src.id).first()
    creds = json.loads(decrypt_secret(cred_row.encrypted_payload)) if cred_row else {}
    try:
        tables = DataConnectorEngine.list_tables_and_columns(src.source_type, src.config_json, creds)
        return {"source_id": src.id, "tables": tables}
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
    enforce_permission(db, user, "data_source", source_id, "execute_query", ip_address=get_client_ip(request))
    src = db.query(DataSource).filter(DataSource.id == source_id).first()
    if not src:
        raise HTTPException(status_code=404, detail="Data source not found")
    cred_row = db.query(DataSourceCredential).filter(DataSourceCredential.data_source_id == src.id).first()
    creds = json.loads(decrypt_secret(cred_row.encrypted_payload)) if cred_row else {}
    try:
        df = DataConnectorEngine.extract_dataframe(
            src.source_type, src.config_json, creds, query=body.query, read_only=src.read_only
        )
        df_head = df.head(min(body.limit, 2000))
        rows = json.loads(df_head.to_json(orient="records", date_format="iso"))
        return {"columns": list(df_head.columns), "rows": rows, "row_count": len(rows), "total_rows": len(df)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


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
    content = await file.read()
    try:
        parsed = parse_file_to_preview(
            file_bytes=content,
            filename=file.filename or "upload.csv",
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
            "duplicate_count": parsed["duplicate_count"],
            "null_counts": parsed["null_counts"],
            "columns": parsed["columns"],
            "preview_rows": parsed["preview_rows"],
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"File parse failed: {exc}")


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
    content = await file.read()
    fname = file.filename or "upload.csv"
    ext = os.path.splitext(fname.lower())[1].lstrip(".")

    # Persist raw uploaded file in workspace uploads dir
    safe_fname = f"{int(datetime.now().timestamp())}_{sanitize_identifier(fname)}.{ext}"
    saved_path = os.path.join(settings.UPLOADS_DIR, safe_fname)
    with open(saved_path, "wb") as f:
        f.write(content)

    try:
        parsed = parse_file_to_preview(
            file_bytes=content,
            filename=fname,
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

    wh_table = f"wh_file_{sanitize_identifier(dataset_name)}_{int(datetime.now().timestamp()) % 100000}"
    load_stats = warehouse.load_dataframe(wh_table, parsed["dataframe"], mode="replace")

    ds = Dataset(
        organization_id=user.organization_id,
        data_source_id=src.id,
        name=dataset_name,
        description=f"Imported from {fname}",
        warehouse_table=wh_table,
        schema_type="flat",
        refresh_policy="manual",
        row_count=load_stats["total_rows"],
        last_refreshed_at=datetime.now(timezone.utc),
        freshness_status="fresh",
    )
    db.add(ds)
    db.flush()

    for col_meta in parsed["columns"]:
        db.add(
            DatasetField(
                dataset_id=ds.id,
                field_name=col_meta["field_name"],
                display_name=col_meta["display_name"],
                data_type=col_meta["data_type"],
                semantic_role=col_meta["semantic_role"],
            )
        )

    db.add(
        DataLineage(
            source_type="data_source",
            source_id=src.id,
            target_type="dataset",
            target_id=ds.id,
            metadata_json={"filename": fname, "rows": load_stats["total_rows"]},
        )
    )
    record_audit(
        db, user.organization_id, "file_dataset_imported", "dataset",
        resource_id=ds.id, user_id=user.id, username=user.username,
        ip_address=get_client_ip(request), details={"filename": fname, "rows": load_stats["total_rows"]}
    )
    db.commit()

    return {
        "dataset_id": ds.id,
        "data_source_id": src.id,
        "name": ds.name,
        "warehouse_table": wh_table,
        "row_count": ds.row_count,
        "columns": parsed["columns"],
    }
