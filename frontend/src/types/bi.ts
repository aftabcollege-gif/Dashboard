export interface ThemeTokens {
  primary: string;
  secondary: string;
  background: string;
  surface: string;
  card_bg: string;
  text: string;
  muted_text: string;
  border: string;
  radius: number;
  shadow: string;
  blur: number;
  font_family: string;
  chart_palette: string[];
}

export interface BiTheme {
  id: string;
  name: string;
  slug: string;
  is_builtin: boolean;
  mode: "light" | "dark";
  tokens: ThemeTokens;
}

export interface WidgetDataResult {
  widget_id: string;
  effective_chart_type: string;
  columns: string[];
  rows: Record<string, any>[];
  row_count: number;
  execution_ms: number;
  cache_hit: boolean;
  generated_sql?: string;
  error?: string;
  drill?: {
    level: number;
    breadcrumbs: { level: number; label: string; field: string | null; value: any }[];
    can_drill_down: boolean;
    effective_dimension: string | null;
    drill_through_page_id: string | null;
  };
  freshness?: {
    status: string;
    last_refreshed_at: string | null;
    data_version: number;
  };
}

export interface DashboardWidget {
  id: string;
  dashboard_id: string;
  page_id: string;
  title: string;
  description: string;
  chart_type: string;
  dataset_id: string | null;
  dataset_freshness?: {
    status: string;
    last_refreshed_at: string | null;
    row_count: number;
    data_version: number;
  };
  layout: { x: number; y: number; w: number; h: number };
  config: {
    style: Record<string, any>;
    visualization: Record<string, any>;
    interactions: Record<string, any>;
    advanced: Record<string, any>;
  };
  query: {
    query_mode: string;
    sql_text: string | null;
    visual_spec: Record<string, any>;
    parameters: Record<string, any>;
  };
  drill_rule: {
    hierarchy: { field: string }[];
    level_rules: { level: number; chart_type: string; field?: string }[];
    drill_through_page_id: string | null;
  };
  widget_filters: {
    id: string;
    field_name: string;
    filter_type: string;
    operator: string;
    value: any;
    is_active: boolean;
  }[];
  permissions: {
    view: boolean;
    edit: boolean;
    delete: boolean;
    drill_down: boolean;
  };
}

export interface DashboardFilterDef {
  id: string;
  page_id: string | null;
  scope: "global" | "dashboard";
  name: string;
  field_name: string;
  dataset_id: string | null;
  filter_type: string;
  operator: string;
  default_value: any;
  target_widget_ids: string[];
  excluded_widget_ids: string[];
  parent_filter_id: string | null;
  is_locked: boolean;
}

export interface DashboardDetail {
  id: string;
  workspace_id: string;
  title: string;
  description: string;
  theme_id: string | null;
  theme: BiTheme | null;
  settings?: Record<string, any>;
  is_public: boolean;
  is_template: boolean;
  embed_token: string | null;
  refresh_interval_sec: number;
  pages: {
    id: string;
    title: string;
    page_order: number;
    is_drill_through: boolean;
    layout_settings: {
      columns_count: number;
      row_height: number;
      gap_px: number;
      grid_enabled: boolean;
    };
  }[];
  widgets: DashboardWidget[];
  filters: DashboardFilterDef[];
  permissions: {
    view: boolean;
    edit: boolean;
    delete: boolean;
    add_widget: boolean;
    change_theme: boolean;
    export: boolean;
  };
  updated_at: string;
}

export interface DatasetInfo {
  id: string;
  name: string;
  description: string;
  data_source_id: string | null;
  warehouse_table: string;
  schema_type: string;
  refresh_policy: string;
  data_version: number;
  row_count: number;
  last_refreshed_at: string | null;
  freshness_status: string;
  fields: {
    id: string;
    field_name: string;
    display_name: string;
    data_type: string;
    semantic_role: string;
    is_primary_key: boolean;
    is_calculated: boolean;
    expression: string | null;
  }[];
}

export type KpiComputabilityStatus =
  | "computable"
  | "computable_with_warning"
  | "needs_data"
  | "uncomputable";

export interface KpiDefinition {
  id: string;
  slug?: string;
  name: string;
  description?: string;
  is_standard?: boolean;
  category?: string;
  formula: string;
  compiled_sql?: string;
  base_fields?: string[];
  required_fields?: string[];
  missing_fields?: string[];
  missing_fields_fa?: string[];
  source_datasets?: string[];
  source?: string;
  format_type?: string;
  display_format?: string;
  unit?: string;
  priority?: number;
  comparison_mode?: "none" | "previous_period" | "target" | string;
  target_value?: number | null;
  value?: number | null;
  formatted_value?: string;
  previous_value?: number | null;
  computability_status?: KpiComputabilityStatus;
  computability_status_fa?: string;
  warnings_fa?: string[];
  errors_fa?: string[];
  rejection_reason_fa?: string;
  eligible_definition_fa?: string;
  delayed_definition_fa?: string;
  comparison_basis?: string;
  score?: number;
  confidence?: number;
  is_favorite?: boolean;
  limitations?: string[];
  multi_source_agg?: Record<string, any> | null;
  pre_agg_grain?: Record<string, any> | null;
  time_field?: string | null;
}

export interface WidgetDrillDetailsResponse {
  widget_id: string;
  title: string;
  formula?: string | null;
  drill_through_page_id?: string | null;
  available_dimensions: string[];
  breakdown_dimension: string | null;
  breakdown_columns: string[];
  breakdown_rows: Record<string, any>[];
  columns: string[];
  rows: Record<string, any>[];
  row_count: number;
}
