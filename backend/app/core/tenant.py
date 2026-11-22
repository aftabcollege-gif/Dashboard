"""
Centralized Multi-Tenant Isolation Helpers.

Every resource lookup MUST scope by `user.organization_id`.
Cross-organization lookups return HTTP 404 (prevents cross-tenant UUID enumeration).
"""

from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from app.models.dashboard import Dashboard, DashboardPage, DashboardTheme, Widget, Workspace
from app.models.data import DataSource, Dataset
from app.models.etl import EtlPipeline, ScheduledReport
from app.models.identity import AccessPolicy, DirectoryConnection, Group, Role, User


def _not_found(entity: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{entity} not found")


def get_workspace_for_org(db: Session, workspace_id: str, org_id: str) -> Workspace:
    ws = (
        db.query(Workspace)
        .filter(Workspace.id == workspace_id, Workspace.organization_id == org_id)
        .first()
    )
    if not ws:
        raise _not_found("Workspace")
    return ws


def get_default_workspace_for_org(db: Session, org_id: str) -> Workspace | None:
    return db.query(Workspace).filter(Workspace.organization_id == org_id).first()


def get_dashboard_for_org(db: Session, dashboard_id: str, org_id: str) -> Dashboard:
    dash = (
        db.query(Dashboard)
        .join(Workspace, Workspace.id == Dashboard.workspace_id)
        .filter(Dashboard.id == dashboard_id, Workspace.organization_id == org_id)
        .first()
    )
    if not dash:
        raise _not_found("Dashboard")
    return dash


def get_page_for_org(db: Session, page_id: str, org_id: str) -> tuple[DashboardPage, Dashboard]:
    row = (
        db.query(DashboardPage, Dashboard)
        .join(Dashboard, Dashboard.id == DashboardPage.dashboard_id)
        .join(Workspace, Workspace.id == Dashboard.workspace_id)
        .filter(DashboardPage.id == page_id, Workspace.organization_id == org_id)
        .first()
    )
    if not row:
        raise _not_found("Dashboard page")
    return row[0], row[1]


def get_widget_for_org(db: Session, widget_id: str, org_id: str) -> tuple[Widget, Dashboard]:
    row = (
        db.query(Widget, Dashboard)
        .join(Dashboard, Dashboard.id == Widget.dashboard_id)
        .join(Workspace, Workspace.id == Dashboard.workspace_id)
        .filter(Widget.id == widget_id, Workspace.organization_id == org_id)
        .first()
    )
    if not row:
        raise _not_found("Widget")
    return row[0], row[1]


def get_dataset_for_org(db: Session, dataset_id: str, org_id: str) -> Dataset:
    ds = (
        db.query(Dataset)
        .filter(Dataset.id == dataset_id, Dataset.organization_id == org_id)
        .first()
    )
    if not ds:
        raise _not_found("Dataset")
    return ds


def get_data_source_for_org(db: Session, source_id: str, org_id: str) -> DataSource:
    src = (
        db.query(DataSource)
        .filter(DataSource.id == source_id, DataSource.organization_id == org_id)
        .first()
    )
    if not src:
        raise _not_found("DataSource")
    return src


def get_pipeline_for_org(db: Session, pipeline_id: str, org_id: str) -> EtlPipeline:
    p = (
        db.query(EtlPipeline)
        .filter(EtlPipeline.id == pipeline_id, EtlPipeline.organization_id == org_id)
        .first()
    )
    if not p:
        raise _not_found("Pipeline")
    return p


def get_user_for_org(db: Session, target_user_id: str, org_id: str) -> User:
    u = db.query(User).filter(User.id == target_user_id, User.organization_id == org_id).first()
    if not u:
        raise _not_found("User")
    return u


def get_role_for_org(db: Session, role_id: str, org_id: str) -> Role:
    r = db.query(Role).filter(Role.id == role_id, Role.organization_id == org_id).first()
    if not r:
        raise _not_found("Role")
    return r


def get_group_for_org(db: Session, group_id: str, org_id: str) -> Group:
    g = db.query(Group).filter(Group.id == group_id, Group.organization_id == org_id).first()
    if not g:
        raise _not_found("Group")
    return g


def get_ad_connection_for_org(db: Session, conn_id: str, org_id: str) -> DirectoryConnection:
    conn = (
        db.query(DirectoryConnection)
        .filter(DirectoryConnection.id == conn_id, DirectoryConnection.organization_id == org_id)
        .first()
    )
    if not conn:
        raise _not_found("AD connection")
    return conn


def get_access_policy_for_org(db: Session, policy_id: str, org_id: str) -> AccessPolicy:
    pol = (
        db.query(AccessPolicy)
        .filter(AccessPolicy.id == policy_id, AccessPolicy.organization_id == org_id)
        .first()
    )
    if not pol:
        raise _not_found("Policy")
    return pol


def get_scheduled_report_for_org(db: Session, report_id: str, org_id: str) -> ScheduledReport:
    rep = (
        db.query(ScheduledReport)
        .filter(ScheduledReport.id == report_id, ScheduledReport.organization_id == org_id)
        .first()
    )
    if not rep:
        raise _not_found("Scheduled report")
    return rep


def get_theme_for_org(db: Session, theme_id: str, org_id: str) -> DashboardTheme:
    th = (
        db.query(DashboardTheme)
        .filter(
            DashboardTheme.id == theme_id,
            (DashboardTheme.is_builtin == True) | (DashboardTheme.organization_id == org_id),
        )
        .first()
    )
    if not th:
        raise _not_found("Theme")
    return th


def list_org_allowed_dataset_tables(db: Session, org_id: str) -> dict[str, Dataset]:
    """Return mapping of lowercased warehouse_table -> Dataset for the given organization."""
    rows = db.query(Dataset).filter(Dataset.organization_id == org_id).all()
    return {d.warehouse_table.lower(): d for d in rows if d.warehouse_table}
