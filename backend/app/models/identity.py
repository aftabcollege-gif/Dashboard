from datetime import datetime
from typing import Optional
from sqlalchemy import (
    String, Boolean, Integer, Text, DateTime, ForeignKey, UniqueConstraint, JSON, Index
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from app.models.common import TimestampMixin, gen_uuid, utcnow


class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    settings_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    username: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    password_hash: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    auth_source: Mapped[str] = mapped_column(String(32), default="local", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mfa_secret_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    permission_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    profile: Mapped[Optional["UserProfile"]] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )


class UserProfile(Base, TimestampMixin):
    __tablename__ = "user_profiles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    full_name: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    department: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    org_unit: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    region: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    job_title: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    locale: Mapped[str] = mapped_column(String(16), default="en", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    custom_attributes: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    user: Mapped["User"] = relationship(back_populates="profile")


class UserSession(Base, TimestampMixin):
    __tablename__ = "user_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    jti: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IdentityProvider(Base, TimestampMixin):
    __tablename__ = "identity_providers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(32), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    config_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class DirectoryConnection(Base, TimestampMixin):
    __tablename__ = "directory_connections"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    domain_name: Mapped[str] = mapped_column(String(200), nullable=False)
    server_url: Mapped[str] = mapped_column(String(300), nullable=False)
    use_ldaps: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    base_dn: Mapped[str] = mapped_column(String(300), nullable=False)
    bind_dn: Mapped[str] = mapped_column(String(300), nullable=False)
    bind_password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    directory_mode: Mapped[str] = mapped_column(String(32), default="ldap", nullable=False)
    sync_policy: Mapped[str] = mapped_column(String(32), default="manual", nullable=False)
    sync_interval_minutes: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    selected_groups_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)


class ExternalIdentity(Base, TimestampMixin):
    __tablename__ = "external_identities"
    __table_args__ = (
        UniqueConstraint(
            "directory_connection_id",
            "provider_type",
            "external_id",
            name="uq_ext_conn_provider_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    directory_connection_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("directory_connections.id", ondelete="SET NULL"), nullable=True
    )
    provider_type: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    distinguished_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    domain_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    raw_attributes_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class DirectorySyncJob(Base, TimestampMixin):
    __tablename__ = "directory_sync_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    directory_connection_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("directory_connections.id", ondelete="CASCADE"), nullable=False
    )
    sync_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    users_imported: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    users_updated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    users_deactivated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    groups_synced: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class Role(Base, TimestampMixin):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("organization_id", "slug", name="uq_org_role_slug"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_super_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Permission(Base, TimestampMixin):
    __tablename__ = "permissions"
    __table_args__ = (UniqueConstraint("resource_type", "action", name="uq_perm_res_action"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    resource_type: Mapped[str] = mapped_column(String(48), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)


class RolePermission(Base, TimestampMixin):
    __tablename__ = "role_permissions"
    __table_args__ = (UniqueConstraint("role_id", "permission_id", name="uq_role_perm"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    role_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("roles.id", ondelete="CASCADE"), nullable=False
    )
    permission_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("permissions.id", ondelete="CASCADE"), nullable=False
    )
    effect: Mapped[str] = mapped_column(String(16), default="allow", nullable=False)


class UserRole(Base, TimestampMixin):
    __tablename__ = "user_roles"
    __table_args__ = (UniqueConstraint("user_id", "role_id", name="uq_user_role"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("roles.id", ondelete="CASCADE"), nullable=False, index=True
    )


class Group(Base, TimestampMixin):
    __tablename__ = "groups"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_org_group_name"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="local", nullable=False)
    external_dn: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    directory_connection_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("directory_connections.id", ondelete="SET NULL"), nullable=True
    )


class GroupMember(Base, TimestampMixin):
    __tablename__ = "group_members"
    __table_args__ = (UniqueConstraint("group_id", "user_id", name="uq_group_member"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    group_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )


class GroupRole(Base, TimestampMixin):
    __tablename__ = "group_roles"
    __table_args__ = (UniqueConstraint("group_id", "role_id", name="uq_group_role"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    group_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("roles.id", ondelete="CASCADE"), nullable=False, index=True
    )


class AccessPolicy(Base, TimestampMixin):
    __tablename__ = "access_policies"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    policy_type: Mapped[str] = mapped_column(String(32), nullable=False)  # rls | cls | masking
    dataset_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)  # user | group | role | all
    target_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    rule_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ResourcePermission(Base, TimestampMixin):
    __tablename__ = "resource_permissions"
    __table_args__ = (
        Index("idx_rp_lookup", "subject_type", "subject_id", "resource_type", "resource_id", "action"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    subject_type: Mapped[str] = mapped_column(String(24), nullable=False)  # user | group | role
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(48), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    effect: Mapped[str] = mapped_column(String(16), default="allow", nullable=False)  # allow | deny
    inherit_to_children: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    conditions_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(48), nullable=False)
    resource_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)  # success | denied | error
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    before_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    after_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    details_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
