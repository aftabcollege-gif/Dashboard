-- =============================================================================
-- Enterprise Intelligence Dashboard Platform — Production PostgreSQL Schema DDL
-- Layer 1: Application Metadata, Security, Dashboard Definitions, ETL & Perf
-- Includes Composite/Partial/Covering Indexes and Range Partitioning for Logs
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- =============================================================================
-- 1. IDENTITY & SECURITY
-- =============================================================================

CREATE TABLE IF NOT EXISTS organizations (
    id              VARCHAR(64) PRIMARY KEY,
    name            VARCHAR(200) NOT NULL,
    slug            VARCHAR(100) NOT NULL UNIQUE,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    settings_json   JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS users (
    id                      VARCHAR(64) PRIMARY KEY,
    organization_id         VARCHAR(64) NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    username                VARCHAR(120) NOT NULL UNIQUE,
    email                   VARCHAR(255) NOT NULL UNIQUE,
    password_hash           VARCHAR(512), -- NULL for pure external AD/SSO users (AD passwords never stored)
    auth_source             VARCHAR(32) NOT NULL DEFAULT 'local', -- local | active_directory | oidc | saml
    is_active               BOOLEAN NOT NULL DEFAULT TRUE,
    is_locked               BOOLEAN NOT NULL DEFAULT FALSE,
    failed_login_attempts   INTEGER NOT NULL DEFAULT 0,
    locked_until            TIMESTAMPTZ,
    expires_at              TIMESTAMPTZ,
    mfa_enabled             BOOLEAN NOT NULL DEFAULT FALSE,
    mfa_secret_encrypted    TEXT,
    last_login_at           TIMESTAMPTZ,
    permission_version      INTEGER NOT NULL DEFAULT 1,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_org_active ON users(organization_id, is_active);
CREATE INDEX IF NOT EXISTS idx_users_auth_source ON users(auth_source) WHERE is_active = TRUE;

CREATE TABLE IF NOT EXISTS user_profiles (
    id                  VARCHAR(64) PRIMARY KEY,
    user_id             VARCHAR(64) NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    full_name           VARCHAR(200) NOT NULL DEFAULT '',
    department          VARCHAR(120),
    org_unit            VARCHAR(120),
    region              VARCHAR(120),
    job_title           VARCHAR(120),
    locale              VARCHAR(16) NOT NULL DEFAULT 'en',
    timezone            VARCHAR(64) NOT NULL DEFAULT 'UTC',
    custom_attributes   JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_sessions (
    id              VARCHAR(64) PRIMARY KEY,
    user_id         VARCHAR(64) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    jti             VARCHAR(128) NOT NULL UNIQUE,
    ip_address      VARCHAR(64),
    user_agent      VARCHAR(300),
    is_revoked      BOOLEAN NOT NULL DEFAULT FALSE,
    expires_at      TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_sessions_user ON user_sessions(user_id, is_revoked);

CREATE TABLE IF NOT EXISTS identity_providers (
    id              VARCHAR(64) PRIMARY KEY,
    organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name            VARCHAR(150) NOT NULL,
    provider_type   VARCHAR(32) NOT NULL, -- active_directory | ldaps | oidc | saml | kerberos
    is_enabled      BOOLEAN NOT NULL DEFAULT TRUE,
    config_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS directory_connections (
    id                      VARCHAR(64) PRIMARY KEY,
    organization_id         VARCHAR(64) NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name                    VARCHAR(150) NOT NULL,
    domain_name             VARCHAR(200) NOT NULL,
    server_url              VARCHAR(300) NOT NULL,
    use_ldaps               BOOLEAN NOT NULL DEFAULT FALSE,
    base_dn                 VARCHAR(300) NOT NULL,
    bind_dn                 VARCHAR(300) NOT NULL,
    bind_password_encrypted TEXT NOT NULL,
    directory_mode          VARCHAR(32) NOT NULL DEFAULT 'ldap', -- ldap | mock_ad
    sync_policy             VARCHAR(32) NOT NULL DEFAULT 'manual', -- manual | scheduled | on_login | selected_groups
    sync_interval_minutes   INTEGER NOT NULL DEFAULT 60,
    selected_groups_json    JSONB NOT NULL DEFAULT '[]'::jsonb,
    is_active               BOOLEAN NOT NULL DEFAULT TRUE,
    last_sync_at            TIMESTAMPTZ,
    last_sync_status        VARCHAR(32),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS external_identities (
    id                      VARCHAR(64) PRIMARY KEY,
    user_id                 VARCHAR(64) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    directory_connection_id VARCHAR(64) REFERENCES directory_connections(id) ON DELETE SET NULL,
    provider_type           VARCHAR(32) NOT NULL,
    external_id             VARCHAR(255) NOT NULL,
    distinguished_name      VARCHAR(500),
    domain_name             VARCHAR(200),
    raw_attributes_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
    synced_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_ext_conn_provider_id UNIQUE (directory_connection_id, provider_type, external_id)
);

CREATE TABLE IF NOT EXISTS directory_sync_jobs (
    id                      VARCHAR(64) PRIMARY KEY,
    directory_connection_id VARCHAR(64) NOT NULL REFERENCES directory_connections(id) ON DELETE CASCADE,
    sync_type               VARCHAR(32) NOT NULL, -- full | incremental | on_login
    status                  VARCHAR(32) NOT NULL, -- running | succeeded | failed
    users_imported          INTEGER NOT NULL DEFAULT 0,
    users_updated           INTEGER NOT NULL DEFAULT 0,
    users_deactivated       INTEGER NOT NULL DEFAULT 0,
    groups_synced           INTEGER NOT NULL DEFAULT 0,
    error_message           TEXT,
    started_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at             TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS roles (
    id              VARCHAR(64) PRIMARY KEY,
    organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name            VARCHAR(120) NOT NULL,
    slug            VARCHAR(120) NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    is_system       BOOLEAN NOT NULL DEFAULT FALSE,
    is_super_admin  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (organization_id, slug)
);

CREATE TABLE IF NOT EXISTS permissions (
    id              VARCHAR(64) PRIMARY KEY,
    resource_type   VARCHAR(48) NOT NULL, -- organization|workspace|dashboard|dashboard_page|widget|drill_down|dataset|data_source|query|filter|export|admin
    action          VARCHAR(64) NOT NULL, -- view|edit|add|delete|drill_down|drill_up|drill_through|...
    description     TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (resource_type, action)
);

CREATE TABLE IF NOT EXISTS role_permissions (
    id              VARCHAR(64) PRIMARY KEY,
    role_id         VARCHAR(64) NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    permission_id   VARCHAR(64) NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
    effect          VARCHAR(16) NOT NULL DEFAULT 'allow', -- allow | deny
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (role_id, permission_id)
);

CREATE TABLE IF NOT EXISTS user_roles (
    id          VARCHAR(64) PRIMARY KEY,
    user_id     VARCHAR(64) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_id     VARCHAR(64) NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, role_id)
);

CREATE TABLE IF NOT EXISTS groups (
    id                      VARCHAR(64) PRIMARY KEY,
    organization_id         VARCHAR(64) NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name                    VARCHAR(150) NOT NULL,
    description             TEXT NOT NULL DEFAULT '',
    source                  VARCHAR(32) NOT NULL DEFAULT 'local', -- local | active_directory
    external_dn             VARCHAR(500),
    directory_connection_id VARCHAR(64) REFERENCES directory_connections(id) ON DELETE SET NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (organization_id, name)
);

CREATE TABLE IF NOT EXISTS group_members (
    id          VARCHAR(64) PRIMARY KEY,
    group_id    VARCHAR(64) NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    user_id     VARCHAR(64) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (group_id, user_id)
);

CREATE TABLE IF NOT EXISTS group_roles (
    id          VARCHAR(64) PRIMARY KEY,
    group_id    VARCHAR(64) NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    role_id     VARCHAR(64) NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (group_id, role_id)
);

CREATE TABLE IF NOT EXISTS access_policies (
    id              VARCHAR(64) PRIMARY KEY,
    organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name            VARCHAR(150) NOT NULL,
    policy_type     VARCHAR(32) NOT NULL, -- rls | cls | masking
    dataset_id      VARCHAR(64),
    target_type     VARCHAR(32) NOT NULL, -- user | group | role | all
    target_id       VARCHAR(64),
    rule_json       JSONB NOT NULL,
    priority        INTEGER NOT NULL DEFAULT 100,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS resource_permissions (
    id              VARCHAR(64) PRIMARY KEY,
    organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    subject_type    VARCHAR(24) NOT NULL, -- user | group | role
    subject_id      VARCHAR(64) NOT NULL,
    resource_type   VARCHAR(48) NOT NULL, -- organization|workspace|dashboard|dashboard_page|widget|drill_down|dataset|data_source|query|filter|export|admin
    resource_id     VARCHAR(64) NOT NULL, -- specific UUID or '*'
    action          VARCHAR(64) NOT NULL,
    effect          VARCHAR(16) NOT NULL DEFAULT 'allow', -- allow | deny
    inherit_to_children BOOLEAN NOT NULL DEFAULT TRUE,
    conditions_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_res_perm_lookup
    ON resource_permissions(subject_type, subject_id, resource_type, resource_id, action)
    INCLUDE (effect, inherit_to_children);

-- Partitioned Audit Log Table (High Volume)
CREATE TABLE IF NOT EXISTS audit_logs (
    id              VARCHAR(64) NOT NULL,
    organization_id VARCHAR(64) NOT NULL,
    user_id         VARCHAR(64),
    username        VARCHAR(120),
    action          VARCHAR(80) NOT NULL,
    resource_type   VARCHAR(48) NOT NULL,
    resource_id     VARCHAR(64),
    status          VARCHAR(24) NOT NULL, -- success | denied | error
    ip_address      VARCHAR(64),
    before_json     JSONB,
    after_json      JSONB,
    details_json    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

CREATE TABLE IF NOT EXISTS audit_logs_2026 PARTITION OF audit_logs
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE IF NOT EXISTS audit_logs_default PARTITION OF audit_logs DEFAULT;
CREATE INDEX IF NOT EXISTS idx_audit_logs_action_time ON audit_logs(action, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_user_time ON audit_logs(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS system_bootstrap_state (
    id              VARCHAR(32) PRIMARY KEY,
    completed       BOOLEAN NOT NULL DEFAULT FALSE,
    bootstrapped_by VARCHAR(64),
    completed_at    TIMESTAMPTZ
);

-- =============================================================================
-- 2. DASHBOARD MANAGEMENT
-- =============================================================================

CREATE TABLE IF NOT EXISTS workspaces (
    id              VARCHAR(64) PRIMARY KEY,
    organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name            VARCHAR(180) NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    created_by      VARCHAR(64) REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS dashboard_themes (
    id              VARCHAR(64) PRIMARY KEY,
    organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name            VARCHAR(120) NOT NULL,
    slug            VARCHAR(80) NOT NULL,
    is_builtin      BOOLEAN NOT NULL DEFAULT FALSE,
    mode            VARCHAR(16) NOT NULL DEFAULT 'light', -- light | dark
    tokens_json     JSONB NOT NULL,
    created_by      VARCHAR(64) REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (organization_id, slug)
);

CREATE TABLE IF NOT EXISTS dashboards (
    id                  VARCHAR(64) PRIMARY KEY,
    workspace_id        VARCHAR(64) NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    title               VARCHAR(220) NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    theme_id            VARCHAR(64) REFERENCES dashboard_themes(id) ON DELETE SET NULL,
    is_public           BOOLEAN NOT NULL DEFAULT FALSE,
    is_template         BOOLEAN NOT NULL DEFAULT FALSE,
    embed_token         VARCHAR(128) UNIQUE,
    refresh_interval_sec INTEGER NOT NULL DEFAULT 0,
    settings_json       JSONB NOT NULL DEFAULT '{}'::jsonb,
    allow_public_restricted BOOLEAN NOT NULL DEFAULT FALSE,
    public_restricted_reason TEXT,
    public_restricted_by VARCHAR(64),
    public_restricted_at TIMESTAMPTZ,
    created_by          VARCHAR(64) REFERENCES users(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS dashboard_pages (
    id              VARCHAR(64) PRIMARY KEY,
    dashboard_id    VARCHAR(64) NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE,
    title           VARCHAR(160) NOT NULL,
    page_order      INTEGER NOT NULL DEFAULT 0,
    is_drill_through BOOLEAN NOT NULL DEFAULT FALSE,
    settings_json   JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS dashboard_layouts (
    id              VARCHAR(64) PRIMARY KEY,
    page_id         VARCHAR(64) NOT NULL UNIQUE REFERENCES dashboard_pages(id) ON DELETE CASCADE,
    columns_count   INTEGER NOT NULL DEFAULT 12,
    row_height      INTEGER NOT NULL DEFAULT 36,
    gap_px          INTEGER NOT NULL DEFAULT 16,
    grid_enabled    BOOLEAN NOT NULL DEFAULT TRUE,
    layout_json     JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS widgets (
    id              VARCHAR(64) PRIMARY KEY,
    dashboard_id    VARCHAR(64) NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE,
    page_id         VARCHAR(64) NOT NULL REFERENCES dashboard_pages(id) ON DELETE CASCADE,
    title           VARCHAR(200) NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    chart_type      VARCHAR(48) NOT NULL DEFAULT 'bar',
    dataset_id      VARCHAR(64),
    layout_x        INTEGER NOT NULL DEFAULT 0,
    layout_y        INTEGER NOT NULL DEFAULT 0,
    layout_w        INTEGER NOT NULL DEFAULT 6,
    layout_h        INTEGER NOT NULL DEFAULT 8,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_widgets_dash_page ON widgets(dashboard_id, page_id);

CREATE TABLE IF NOT EXISTS widget_configs (
    id                  VARCHAR(64) PRIMARY KEY,
    widget_id           VARCHAR(64) NOT NULL UNIQUE REFERENCES widgets(id) ON DELETE CASCADE,
    style_json          JSONB NOT NULL DEFAULT '{}'::jsonb,
    visualization_json  JSONB NOT NULL DEFAULT '{}'::jsonb,
    interactions_json   JSONB NOT NULL DEFAULT '{}'::jsonb,
    advanced_json       JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS widget_queries (
    id                  VARCHAR(64) PRIMARY KEY,
    widget_id           VARCHAR(64) NOT NULL UNIQUE REFERENCES widgets(id) ON DELETE CASCADE,
    dataset_id          VARCHAR(64),
    saved_query_id      VARCHAR(64),
    query_mode          VARCHAR(32) NOT NULL DEFAULT 'visual', -- visual | sql | semantic
    sql_text            TEXT,
    visual_spec_json    JSONB NOT NULL DEFAULT '{}'::jsonb,
    parameters_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS widget_drill_rules (
    id                  VARCHAR(64) PRIMARY KEY,
    widget_id           VARCHAR(64) NOT NULL UNIQUE REFERENCES widgets(id) ON DELETE CASCADE,
    hierarchy_json      JSONB NOT NULL DEFAULT '[]'::jsonb,
    level_rules_json    JSONB NOT NULL DEFAULT '[]'::jsonb,
    drill_through_page_id VARCHAR(64) REFERENCES dashboard_pages(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS dashboard_filters (
    id                  VARCHAR(64) PRIMARY KEY,
    dashboard_id        VARCHAR(64) NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE,
    page_id             VARCHAR(64) REFERENCES dashboard_pages(id) ON DELETE CASCADE,
    scope               VARCHAR(24) NOT NULL DEFAULT 'dashboard', -- global | dashboard
    name                VARCHAR(140) NOT NULL,
    field_name          VARCHAR(140) NOT NULL,
    dataset_id          VARCHAR(64),
    filter_type         VARCHAR(48) NOT NULL,
    operator            VARCHAR(32) NOT NULL DEFAULT 'in',
    default_value_json  JSONB,
    target_widget_ids   JSONB NOT NULL DEFAULT '[]'::jsonb,
    excluded_widget_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    parent_filter_id    VARCHAR(64),
    is_locked           BOOLEAN NOT NULL DEFAULT FALSE,
    config_json         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS widget_filters (
    id              VARCHAR(64) PRIMARY KEY,
    widget_id       VARCHAR(64) NOT NULL REFERENCES widgets(id) ON DELETE CASCADE,
    field_name      VARCHAR(140) NOT NULL,
    filter_type     VARCHAR(48) NOT NULL,
    operator        VARCHAR(32) NOT NULL,
    value_json      JSONB,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS dashboard_versions (
    id              VARCHAR(64) PRIMARY KEY,
    dashboard_id    VARCHAR(64) NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE,
    version_number  INTEGER NOT NULL,
    label           VARCHAR(200) NOT NULL DEFAULT '',
    snapshot_json   JSONB NOT NULL,
    created_by      VARCHAR(64) REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (dashboard_id, version_number)
);

CREATE TABLE IF NOT EXISTS dashboard_comments (
    id              VARCHAR(64) PRIMARY KEY,
    dashboard_id    VARCHAR(64) NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE,
    widget_id       VARCHAR(64),
    user_id         VARCHAR(64) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    author_name     VARCHAR(150) NOT NULL,
    content         TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dashboard_comments_dash ON dashboard_comments(dashboard_id, created_at);

CREATE TABLE IF NOT EXISTS dashboard_states (
    id                      VARCHAR(64) PRIMARY KEY,
    dashboard_id            VARCHAR(64) NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE,
    user_id                 VARCHAR(64) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    active_page_id          VARCHAR(64),
    filter_state_json       JSONB NOT NULL DEFAULT '{}'::jsonb,
    drill_state_json        JSONB NOT NULL DEFAULT '{}'::jsonb,
    widget_overrides_json   JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (dashboard_id, user_id)
);

-- =============================================================================
-- 3. DATA MANAGEMENT & SEMANTIC LAYER
-- =============================================================================

CREATE TABLE IF NOT EXISTS data_sources (
    id              VARCHAR(64) PRIMARY KEY,
    organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name            VARCHAR(180) NOT NULL,
    source_type     VARCHAR(48) NOT NULL, -- postgresql|mssql|mysql|mariadb|oracle|sqlite|mongodb|rest_api|graphql|excel|csv|json|xml
    read_only       BOOLEAN NOT NULL DEFAULT TRUE,
    config_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
    refresh_policy  VARCHAR(32) NOT NULL DEFAULT 'scheduled',
    last_tested_at  TIMESTAMPTZ,
    health_status   VARCHAR(32) NOT NULL DEFAULT 'unknown', -- healthy|offline|error|unknown
    last_error      TEXT,
    created_by      VARCHAR(64) REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS data_source_credentials (
    id                      VARCHAR(64) PRIMARY KEY,
    data_source_id          VARCHAR(64) NOT NULL UNIQUE REFERENCES data_sources(id) ON DELETE CASCADE,
    encrypted_payload       TEXT NOT NULL,
    key_version             INTEGER NOT NULL DEFAULT 1,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS datasets (
    id                  VARCHAR(64) PRIMARY KEY,
    organization_id     VARCHAR(64) NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    data_source_id      VARCHAR(64) REFERENCES data_sources(id) ON DELETE SET NULL,
    name                VARCHAR(180) NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    warehouse_table     VARCHAR(180) NOT NULL UNIQUE,
    schema_type         VARCHAR(32) NOT NULL DEFAULT 'fact', -- fact|dimension|flat|materialized
    refresh_policy      VARCHAR(32) NOT NULL DEFAULT 'cached', -- live|cached|scheduled|incremental|manual
    data_version        INTEGER NOT NULL DEFAULT 1,
    row_count           BIGINT NOT NULL DEFAULT 0,
    size_bytes          BIGINT NOT NULL DEFAULT 0,
    last_refreshed_at   TIMESTAMPTZ,
    freshness_status    VARCHAR(32) NOT NULL DEFAULT 'fresh', -- fresh|stale|syncing|error
    profile_json        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS dataset_fields (
    id              VARCHAR(64) PRIMARY KEY,
    dataset_id      VARCHAR(64) NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    field_name      VARCHAR(140) NOT NULL,
    display_name    VARCHAR(180) NOT NULL,
    data_type       VARCHAR(48) NOT NULL, -- string|integer|float|decimal|boolean|date|datetime
    semantic_role   VARCHAR(32) NOT NULL DEFAULT 'dimension', -- dimension|measure|time|geo|id
    is_primary_key  BOOLEAN NOT NULL DEFAULT FALSE,
    is_calculated   BOOLEAN NOT NULL DEFAULT FALSE,
    expression      TEXT,
    format_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
    stats_json      JSONB NOT NULL DEFAULT '{}'::jsonb,
    description     TEXT NOT NULL DEFAULT '',
    calculation_type VARCHAR(32) NOT NULL DEFAULT 'physical',
    dependent_fields_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    access_scope    VARCHAR(32) NOT NULL DEFAULT 'dataset',
    version         INTEGER NOT NULL DEFAULT 1,
    semantic_class  VARCHAR(48) NOT NULL DEFAULT '',
    classification_confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    aggregation_policy VARCHAR(32) NOT NULL DEFAULT '',
    user_override_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by      VARCHAR(64) REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (dataset_id, field_name)
);

CREATE TABLE IF NOT EXISTS dataset_relationships (
    id              VARCHAR(64) PRIMARY KEY,
    left_dataset_id VARCHAR(64) NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    left_field      VARCHAR(140) NOT NULL,
    right_dataset_id VARCHAR(64) NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    right_field     VARCHAR(140) NOT NULL,
    join_type       VARCHAR(24) NOT NULL DEFAULT 'left', -- inner|left|right|full
    cardinality     VARCHAR(24) NOT NULL DEFAULT 'many_to_one',
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    metadata_json   JSONB NOT NULL DEFAULT '{}'::jsonb,
    validated_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS semantic_metrics (
    id              VARCHAR(64) PRIMARY KEY,
    organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    dataset_id      VARCHAR(64) NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    name            VARCHAR(160) NOT NULL,
    slug            VARCHAR(120) NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    formula         TEXT NOT NULL,
    format_type     VARCHAR(32) NOT NULL DEFAULT 'number',
    unit            VARCHAR(32) NOT NULL DEFAULT '',
    metric_kind     VARCHAR(32) NOT NULL DEFAULT 'analytical_metric',
    output_data_type VARCHAR(48) NOT NULL DEFAULT 'double',
    dependent_datasets_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    dependent_fields_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    dependent_metrics_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    access_scope    VARCHAR(32) NOT NULL DEFAULT 'organization',
    version         INTEGER NOT NULL DEFAULT 1,
    category        VARCHAR(48) NOT NULL DEFAULT 'analytical',
    confidence      DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    target_value    DOUBLE PRECISION,
    kpi_metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by      VARCHAR(64) REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (organization_id, slug)
);

CREATE TABLE IF NOT EXISTS queries (
    id              VARCHAR(64) PRIMARY KEY,
    organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name            VARCHAR(180) NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    dataset_id      VARCHAR(64) REFERENCES datasets(id) ON DELETE SET NULL,
    query_mode      VARCHAR(24) NOT NULL DEFAULT 'sql', -- sql|visual
    sql_text        TEXT NOT NULL,
    visual_spec_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by      VARCHAR(64) REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS query_parameters (
    id              VARCHAR(64) PRIMARY KEY,
    query_id        VARCHAR(64) NOT NULL REFERENCES queries(id) ON DELETE CASCADE,
    param_name      VARCHAR(80) NOT NULL,
    param_type      VARCHAR(32) NOT NULL DEFAULT 'string',
    default_value   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (query_id, param_name)
);

CREATE TABLE IF NOT EXISTS data_models (
    id              VARCHAR(64) PRIMARY KEY,
    organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name            VARCHAR(180) NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    model_type      VARCHAR(32) NOT NULL DEFAULT 'star_schema', -- star_schema|snowflake_schema
    definition_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    version         INTEGER NOT NULL DEFAULT 1,
    created_by      VARCHAR(64) REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS data_transformations (
    id              VARCHAR(64) PRIMARY KEY,
    dataset_id      VARCHAR(64) NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    step_order      INTEGER NOT NULL,
    transform_type  VARCHAR(48) NOT NULL, -- rename|cast|fill_null|deduplicate|filter|calculated|join|union|aggregate
    config_json     JSONB NOT NULL,
    is_enabled      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS data_lineage (
    id              VARCHAR(64) PRIMARY KEY,
    source_type     VARCHAR(48) NOT NULL, -- data_source|dataset|etl_pipeline|widget|dashboard
    source_id       VARCHAR(64) NOT NULL,
    target_type     VARCHAR(48) NOT NULL,
    target_id       VARCHAR(64) NOT NULL,
    metadata_json   JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_type, source_id, target_type, target_id)
);

-- =============================================================================
-- 4. ETL MANAGEMENT
-- =============================================================================

CREATE TABLE IF NOT EXISTS etl_pipelines (
    id                  VARCHAR(64) PRIMARY KEY,
    organization_id     VARCHAR(64) NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name                VARCHAR(180) NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    data_source_id      VARCHAR(64) REFERENCES data_sources(id) ON DELETE SET NULL,
    target_dataset_id   VARCHAR(64) REFERENCES datasets(id) ON DELETE SET NULL,
    extract_mode        VARCHAR(32) NOT NULL DEFAULT 'incremental', -- full|incremental|cdc_hash
    incremental_column  VARCHAR(120),
    primary_key_column  VARCHAR(120),
    source_query        TEXT,
    retry_policy_json   JSONB NOT NULL DEFAULT '{"max_retries":3,"initial_backoff_sec":2,"backoff_multiplier":2}'::jsonb,
    priority            INTEGER NOT NULL DEFAULT 5,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    is_running          BOOLEAN NOT NULL DEFAULT FALSE,
    fence_token         BIGINT NOT NULL DEFAULT 0,
    active_worker_id    VARCHAR(128),
    heartbeat_at        TIMESTAMPTZ,
    lease_expires_at    TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS etl_pipeline_steps (
    id          VARCHAR(64) PRIMARY KEY,
    pipeline_id VARCHAR(64) NOT NULL REFERENCES etl_pipelines(id) ON DELETE CASCADE,
    step_order  INTEGER NOT NULL,
    step_type   VARCHAR(48) NOT NULL, -- extract|clean|cast|deduplicate|calculate|aggregate|join|validate|load
    config_json JSONB NOT NULL,
    is_enabled  BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS etl_schedules (
    id                  VARCHAR(64) PRIMARY KEY,
    pipeline_id         VARCHAR(64) NOT NULL UNIQUE REFERENCES etl_pipelines(id) ON DELETE CASCADE,
    schedule_type       VARCHAR(32) NOT NULL DEFAULT 'interval', -- interval|daily|weekly|monthly|cron
    interval_minutes    INTEGER,
    cron_expression     VARCHAR(120),
    timezone            VARCHAR(64) NOT NULL DEFAULT 'UTC',
    allowed_window_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    depends_on_pipeline_id VARCHAR(64),
    is_enabled          BOOLEAN NOT NULL DEFAULT TRUE,
    next_run_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS etl_jobs (
    id              VARCHAR(64) PRIMARY KEY,
    pipeline_id     VARCHAR(64) NOT NULL REFERENCES etl_pipelines(id) ON DELETE CASCADE,
    trigger_type    VARCHAR(32) NOT NULL, -- manual|scheduled|retry|dependency
    status          VARCHAR(32) NOT NULL, -- queued|running|succeeded|failed|retrying|cancelled
    attempt_count   INTEGER NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS etl_job_runs (
    id                  VARCHAR(64) PRIMARY KEY,
    job_id              VARCHAR(64) NOT NULL REFERENCES etl_jobs(id) ON DELETE CASCADE,
    pipeline_id         VARCHAR(64) NOT NULL REFERENCES etl_pipelines(id) ON DELETE CASCADE,
    status              VARCHAR(32) NOT NULL,
    fence_token         BIGINT NOT NULL DEFAULT 0,
    worker_id           VARCHAR(128),
    heartbeat_at        TIMESTAMPTZ,
    lease_expires_at    TIMESTAMPTZ,
    rows_extracted      BIGINT NOT NULL DEFAULT 0,
    rows_inserted       BIGINT NOT NULL DEFAULT 0,
    rows_updated        BIGINT NOT NULL DEFAULT 0,
    rows_unchanged      BIGINT NOT NULL DEFAULT 0,
    duration_ms         DOUBLE PRECISION NOT NULL DEFAULT 0,
    log_text            TEXT NOT NULL DEFAULT '',
    started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS etl_checkpoints (
    id                  VARCHAR(64) PRIMARY KEY,
    pipeline_id         VARCHAR(64) NOT NULL UNIQUE REFERENCES etl_pipelines(id) ON DELETE CASCADE,
    last_cursor_value   TEXT,
    last_success_at     TIMESTAMPTZ,
    row_hashes_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS etl_errors (
    id          VARCHAR(64) PRIMARY KEY,
    pipeline_id VARCHAR(64) NOT NULL REFERENCES etl_pipelines(id) ON DELETE CASCADE,
    job_run_id  VARCHAR(64) REFERENCES etl_job_runs(id) ON DELETE SET NULL,
    error_type  VARCHAR(64) NOT NULL,
    message     TEXT NOT NULL,
    traceback   TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS etl_metrics (
    id                  VARCHAR(64) PRIMARY KEY,
    pipeline_id         VARCHAR(64) NOT NULL REFERENCES etl_pipelines(id) ON DELETE CASCADE,
    job_run_id          VARCHAR(64) REFERENCES etl_job_runs(id) ON DELETE SET NULL,
    cpu_percent         DOUBLE PRECISION NOT NULL DEFAULT 0,
    memory_mb           DOUBLE PRECISION NOT NULL DEFAULT 0,
    storage_bytes       BIGINT NOT NULL DEFAULT 0,
    throughput_rows_sec DOUBLE PRECISION NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS scheduled_reports (
    id              VARCHAR(64) PRIMARY KEY,
    organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    dashboard_id    VARCHAR(64) NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE,
    name            VARCHAR(180) NOT NULL,
    format          VARCHAR(16) NOT NULL DEFAULT 'pdf', -- pdf|xlsx|csv
    cron_expression VARCHAR(120) NOT NULL DEFAULT '0 8 * * 1',
    recipients_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    is_enabled      BOOLEAN NOT NULL DEFAULT TRUE,
    last_sent_at    TIMESTAMPTZ,
    last_file_path  TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- 5. PERFORMANCE MANAGEMENT
-- =============================================================================

CREATE TABLE IF NOT EXISTS cache_metadata (
    id                  VARCHAR(64) PRIMARY KEY,
    cache_key           VARCHAR(255) NOT NULL UNIQUE,
    organization_id     VARCHAR(64) NOT NULL,
    dashboard_id        VARCHAR(64),
    widget_id           VARCHAR(64),
    dataset_id          VARCHAR(64),
    data_version        INTEGER NOT NULL DEFAULT 1,
    permission_hash     VARCHAR(64) NOT NULL,
    filter_hash         VARCHAR(64) NOT NULL,
    hit_count           INTEGER NOT NULL DEFAULT 0,
    expires_at          TIMESTAMPTZ NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS materialized_view_metadata (
    id                  VARCHAR(64) PRIMARY KEY,
    dataset_id          VARCHAR(64) NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    view_name           VARCHAR(180) NOT NULL UNIQUE,
    granularity         VARCHAR(32) NOT NULL, -- daily|monthly|yearly|category
    definition_sql      TEXT NOT NULL,
    row_count           BIGINT NOT NULL DEFAULT 0,
    last_refreshed_at   TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Partitioned Query & Performance Logs
CREATE TABLE IF NOT EXISTS query_execution_logs (
    id              VARCHAR(64) NOT NULL,
    organization_id VARCHAR(64) NOT NULL,
    user_id         VARCHAR(64),
    dataset_id      VARCHAR(64),
    widget_id       VARCHAR(64),
    query_hash      VARCHAR(64) NOT NULL,
    execution_ms    DOUBLE PRECISION NOT NULL,
    rows_returned   INTEGER NOT NULL DEFAULT 0,
    cache_hit       BOOLEAN NOT NULL DEFAULT FALSE,
    status          VARCHAR(24) NOT NULL DEFAULT 'success',
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

CREATE TABLE IF NOT EXISTS query_execution_logs_2026 PARTITION OF query_execution_logs
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE IF NOT EXISTS query_execution_logs_default PARTITION OF query_execution_logs DEFAULT;
CREATE INDEX IF NOT EXISTS idx_qel_perf ON query_execution_logs(execution_ms DESC, created_at DESC);

CREATE TABLE IF NOT EXISTS dashboard_performance_logs (
    id              VARCHAR(64) NOT NULL,
    dashboard_id    VARCHAR(64) NOT NULL,
    user_id         VARCHAR(64),
    load_ms         DOUBLE PRECISION NOT NULL,
    widget_count    INTEGER NOT NULL DEFAULT 0,
    cache_hits      INTEGER NOT NULL DEFAULT 0,
    cache_misses    INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

CREATE TABLE IF NOT EXISTS dashboard_performance_logs_2026 PARTITION OF dashboard_performance_logs
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE IF NOT EXISTS dashboard_performance_logs_default PARTITION OF dashboard_performance_logs DEFAULT;

CREATE TABLE IF NOT EXISTS widget_performance_logs (
    id              VARCHAR(64) NOT NULL,
    widget_id       VARCHAR(64) NOT NULL,
    dashboard_id    VARCHAR(64) NOT NULL,
    user_id         VARCHAR(64),
    load_ms         DOUBLE PRECISION NOT NULL,
    cache_hit       BOOLEAN NOT NULL DEFAULT FALSE,
    rows_returned   INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

CREATE TABLE IF NOT EXISTS widget_performance_logs_2026 PARTITION OF widget_performance_logs
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE IF NOT EXISTS widget_performance_logs_default PARTITION OF widget_performance_logs DEFAULT;
