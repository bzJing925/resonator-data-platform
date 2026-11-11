export interface CandidateRow {
  candidate_id: string;
  control_type: string;
  eg_um: string;
  fl_um: string;
  area_um2: string;
  has_pf: string;
  branch_id: string;
  support_label: string;
  source_file_id: string;
  source_sha256: string;
  source_row_key: string;
  legacy_recommendation_text: string;
  legacy_nearest_scaled_distance: string;
  recommendation_eligible: string;
  grade: null;
  grade_null_reason: string;
  evidence_status: string;
  trusted_for_unmeasured_optimization: string;
  new_candidate: string;
  model_prediction_provenance: string;
  measured_geometry_match: string;
  exclusion_reason: string;
  conservative_value: null;
  conservative_value_null_reason: string;
  error_margin: null;
  error_margin_null_reason: string;
  extrapolation_distance: null;
  extrapolation_distance_null_reason: string;
  testable_max_distance: null;
  testable_max_distance_null_reason: string;
  neighborhood_result: null;
  neighborhood_status: string;
  neighborhood_null_reason: string;
}

export interface MetricRow {
  candidate_id: string;
  model_id: string;
  metric: string;
  value: string | null;
  unit: string;
  source_file_id: string | null;
  source_sha256: string | null;
  source_row_key: string | null;
  scope: string | null;
  support_label: string | null;
  evidence_status: string;
  null_reason: string;
}

export interface DecisionValue {
  value: string;
  unit: string;
}

export interface Decision {
  scientific_status: string;
  decision: string;
  reason: string;
  eligible_models: string[];
  recommendation_eligible: boolean;
  recommendation_count: number;
  grade: null;
  grade_null_reason: string;
  grade_counts: { A: number; B: number; C: number };
  baseline: DecisionValue;
  absolute_target: DecisionValue;
  required_uplift: DecisionValue;
  conservative_evidence: {
    conservative_value: null;
    conservative_value_null_reasons: string[];
    error_margin: null;
    error_margin_null_reasons: string[];
    extrapolation_distance: null;
    extrapolation_distance_null_reasons: string[];
    testable_max_distance: null;
    testable_max_distance_null_reasons: string[];
  };
  neighborhood: {
    status: string;
    null_reason: string;
    nine_point_prediction_executed: boolean;
  };
  random_process: {
    status: string;
    reason: string;
    seed: number;
    seed_used: boolean;
    random_draw_count: number;
    bootstrap_prediction_count: number;
    refit_count: number;
  };
  search_status: string;
  trusted_for_unmeasured_optimization: boolean;
}

export interface FileDescriptor {
  path: string;
  bytes: number;
  sha256: string;
  file_id: string;
}

export interface SourceTraceability {
  o14_delivery: FileDescriptor[];
  t15_manifest: FileDescriptor;
  registered_inputs: FileDescriptor[];
}

export interface BrowserData {
  schema: string;
  task_id: string;
  output_id: string;
  attempt_id: string;
  source_output_id: string;
  candidate_columns: string[];
  metric_columns: string[];
  candidates: CandidateRow[];
  metrics: MetricRow[];
  decision: Decision;
  source_traceability: SourceTraceability;
}

export type ValidationResult =
  | { ok: true; data: BrowserData; errors: [] }
  | { ok: false; errors: string[] };

export interface ImportResult {
  accepted: boolean;
  data: BrowserData;
  validation: ValidationResult;
}
