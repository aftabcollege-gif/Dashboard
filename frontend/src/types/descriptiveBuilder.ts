export interface FormulaFunctionMeta {
  name: string;
  category: "aggregate" | "math" | "conditional" | "string" | "date" | "statistical";
  signature: string;
  example: string;
  description_fa: string;
  is_aggregate: boolean;
  requires_min_args: number;
  max_args: number;
}

export interface AnalysisCatalogItem {
  id: string;
  name_fa: string;
  category: string;
  recommended_charts: string[];
  min_measures: number;
  requires_time: boolean;
}

export interface BuilderCatalogResponse {
  functions: FormulaFunctionMeta[];
  analyses: AnalysisCatalogItem[];
}

export interface AccessibleFieldMeta {
  id: string;
  field_name: string;
  display_name: string;
  data_type: string;
  semantic_role: string;
  is_primary_key: boolean;
  is_calculated: boolean;
  calculation_type: string;
  expression: string | null;
  format_type: string;
  unit: string;
  description: string;
  version: number;
  access_scope: string;
}

export interface AccessibleMetricMeta {
  id: string;
  name: string;
  slug: string;
  description: string;
  formula: string;
  metric_kind: string;
  output_data_type: string;
  format_type: string;
  unit: string;
  version: number;
  access_scope: string;
}

export interface AccessibleDatasetMeta {
  id: string;
  name: string;
  description: string;
  schema_type: string;
  row_count: number;
  freshness_status: string;
  last_refreshed_at: string | null;
  has_security_restrictions: boolean;
  fields: AccessibleFieldMeta[];
  metrics: AccessibleMetricMeta[];
}

export interface RelationshipEdgeInfo {
  id: string;
  left_dataset_id: string;
  left_dataset_name: string;
  left_field: string;
  right_dataset_id: string;
  right_dataset_name: string;
  right_field: string;
  join_type: string;
  cardinality: string;
}

export interface CandidateJoinPath {
  path_key: string;
  relationship_ids: string[];
  steps: string[];
  relationship_details: RelationshipEdgeInfo[];
}

export interface AmbiguousPathGroup {
  from_dataset_id: string;
  from_dataset_name: string;
  to_dataset_id: string;
  to_dataset_name: string;
  candidate_paths: CandidateJoinPath[];
}

export interface JoinSuggestion {
  left_dataset_id: string;
  left_dataset_name: string;
  left_field: string;
  right_dataset_id: string;
  right_dataset_name: string;
  right_field: string;
  suggested_join_type: string;
  suggested_cardinality: string;
  confidence: number;
}

export interface ExplosionCheck {
  relationship_id: string;
  left_dataset_name: string;
  right_dataset_name: string;
  join_field: string;
  cardinality: string;
  left_rows: number;
  joined_rows: number;
  expansion_factor: number;
  risk: "low" | "moderate" | "high" | "unknown";
  warning_fa: string;
}

export interface RelationshipAnalyzeResponse {
  primary_dataset_id: string | null;
  primary_dataset_name: string | null;
  ready_for_join: boolean;
  disconnected_datasets: { id: string; name: string }[];
  ambiguous_paths: AmbiguousPathGroup[];
  resolved_joins: RelationshipEdgeInfo[];
  available_relationships: RelationshipEdgeInfo[];
  suggestions: JoinSuggestion[];
  explosion_checks: ExplosionCheck[];
}

export interface FormulaValidateResponse {
  valid: boolean;
  errors: string[];
  warnings: string[];
  compiled_sql: string;
  output_data_type: string;
  calculation_type: string;
  has_aggregate: boolean;
  referenced_fields: { dataset_id: string; dataset_name: string; field_name: string; data_type: string }[];
  referenced_datasets: string[];
  referenced_calculated_fields: string[];
  referenced_metrics: string[];
}

export interface FormulaPreviewResponse extends FormulaValidateResponse {
  rows: Record<string, any>[];
  columns: string[];
  row_count: number;
  execution_ms: number;
}

export interface ImpactReport {
  has_dependencies: boolean;
  calculated_fields: { id: string; field_name: string; display_name: string; dataset_id: string }[];
  semantic_metrics: { id: string; slug: string; name: string }[];
  widgets: { id: string; title: string; dashboard_id: string }[];
}

export interface DescriptiveWidgetDraft {
  temp_id: string;
  analysis_type: string;
  title: string;
  chart_type: string;
  dataset_id: string;
  query_mode: string;
  sql_text?: string | null;
  visual_spec: Record<string, any>;
  style: Record<string, any>;
  visualization: Record<string, any>;
  drill_hierarchy?: { field: string }[];
  layout: { x: number; y: number; w: number; h: number };
  columns: string[];
  rows: Record<string, any>[];
  row_count: number;
  execution_ms?: number;
  generated_sql?: string;
  error?: string | null;
  narrative_fa: string;
  sources_used: string[];
}

export interface DescriptiveFilterDraft {
  name: string;
  field_name: string;
  dataset_id: string;
  filter_type: string;
  operator: string;
  value?: any;
}

export interface GenerateDashboardResponse {
  title: string;
  calendar_mode: "jalali" | "gregorian";
  primary_dataset_id: string;
  dataset_ids: string[];
  source_names: string[];
  relationship_analysis: RelationshipAnalyzeResponse;
  has_security_restrictions: boolean;
  executive_summary_fa: string;
  model_definition: Record<string, any>;
  filters: DescriptiveFilterDraft[];
  widgets: DescriptiveWidgetDraft[];
}

export interface DataModelRecord {
  id: string;
  name: string;
  description: string;
  model_type: string;
  definition_json: Record<string, any>;
  version: number;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}
