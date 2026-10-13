/** Shared wire contracts from docs/plans/platform-optimization-luna-contracts.md. */

export interface SourceRef {
  origin_id: string;
  source_batch_id: string;
  source_device_key: string | null;
  source_revision: number | null;
}

export type ProcessingStatus =
  | 'pending'
  | 'running'
  | 'success'
  | 'failed'
  | 'cancel_requested'
  | 'cancelled';

export type ProcessingItemStatus =
  | 'pending'
  | 'running'
  | 'success'
  | 'failed'
  | 'skipped'
  | 'cancelled';

export type ProcessingStage = 'extract' | 'deembed' | 'metrics' | 'done' | 'failed';
export type QualityStatus = 'unknown' | 'complete' | 'partial' | 'invalid';

export interface ProcessingEvent {
  schema_version: 1;
  task_id: number;
  run_id: string;
  event_seq: number;
  status: ProcessingStatus;
  stage: ProcessingStage;
  progress: Record<string, unknown>;
  summary: Record<string, unknown>;
}

export interface QuerySpec {
  filters: Record<string, unknown>;
  fields: string[];
  order_by: string | null;
}

export type Aggregation = 'all' | 'mean' | 'min' | 'max' | 'p50' | 'p25' | 'p75';

export interface QualityPredicate {
  field: string;
  op: 'gte' | 'lte';
  value: number;
  unit: string;
}

export interface QualityRuleV1 {
  id: string;
  version: number;
  label: string;
  predicates: QualityPredicate[];
  combine: 'and';
}

export interface AnalysisField {
  name: string;
  aggregation: Aggregation;
}

export interface AnalysisComparisonEndpoint {
  source: SourceRef | null;
  dataset_id: string | null;
  query_spec: QuerySpec;
}

export interface AnalysisComparison {
  scope: 'source' | 'workspace_snapshot';
  a: AnalysisComparisonEndpoint;
  b: AnalysisComparisonEndpoint;
  metrics: string[];
  group_fields: string[];
  common_frequency_ghz: [number, number] | null;
  condition_policy: 'require_equal' | 'common_subset';
  wafer_metric: string | null;
  unified_color_scale: boolean;
}

export interface AnalysisSpec {
  schema_version: 1;
  query_spec: QuerySpec;
  plot_limit: number;
  chart: {
    type: 'scatter' | 'box' | 'violin' | 'swarm' | 'line' | 'wafer';
    x_fields: string[];
    y_fields: AnalysisField[];
    z_field: AnalysisField | null;
    z_mode: 'facet' | 'color';
    wafer_z: AnalysisField | null;
    wafer_facet: string | null;
    show_data_points: boolean;
  };
  units: Record<string, string>;
  quality_rule: QualityRuleV1 | null;
  comparison: AnalysisComparison | null;
}

export interface ResultManifest {
  schema_version: 1;
  query_spec: QuerySpec;
  source_refs: SourceRef[];
  matched_total: number | null;
  total_relation: 'exact' | 'lower_bound' | 'unknown';
  returned: number;
  truncated: boolean;
  sampling: { method: 'none' | 'systematic'; rate: number | null };
  local_edits: number;
  excluded_rows: number;
  generated_at: string;
  consistency: 'verified' | 'unverified';
}

export interface CapabilityFeatures {
  upload_precheck: boolean;
  processing_reports: boolean;
  projects: boolean;
  full_export_csv: boolean;
  full_export_xlsx: boolean;
  batch_compare: boolean;
  trash: boolean;
  replication_v3: boolean;
  remote_delete: boolean;
  credential_storage: boolean;
}

export interface Capabilities {
  schema_version: 1;
  mode: 'server' | 'desktop' | 'static';
  database: 'sqlite' | 'postgresql' | 'duckdb';
  protocols: string[];
  features: CapabilityFeatures;
  limits: Record<string, number | string | boolean | null>;
}

export interface SyncStatus {
  target: 'server' | 'web';
  state: 'local_only' | 'pending' | 'syncing' | 'confirmed' | 'conflict' | 'failed' | 'unknown';
  confirmed_revision: number | null;
  pending_revision: number | null;
  last_success_at: string | null;
  error_code: string | null;
  retryable: boolean;
}
