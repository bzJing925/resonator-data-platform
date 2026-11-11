import { C19_STRUCTURE_ID, EXPECTED_CANDIDATE_IDS, EXPECTED_METRICS, EXPECTED_MODELS, EXPECTED_REGISTERED_INPUTS, O14_SHA256, SOURCE_SHA256 } from "./o14_contract.js";
import type { BrowserData, Decision, ValidationResult } from "./types.js";

const CANDIDATE_COLUMNS = [
  "candidate_id", "control_type", "eg_um", "fl_um", "area_um2", "has_pf", "branch_id",
  "support_label", "source_file_id", "source_sha256", "source_row_key",
  "legacy_recommendation_text", "legacy_nearest_scaled_distance", "recommendation_eligible",
  "grade", "grade_null_reason", "evidence_status", "trusted_for_unmeasured_optimization",
  "new_candidate", "model_prediction_provenance", "measured_geometry_match", "exclusion_reason",
  "conservative_value", "conservative_value_null_reason", "error_margin", "error_margin_null_reason",
  "extrapolation_distance", "extrapolation_distance_null_reason", "testable_max_distance",
  "testable_max_distance_null_reason", "neighborhood_result", "neighborhood_status",
  "neighborhood_null_reason",
] as const;

const METRIC_COLUMNS = [
  "candidate_id", "model_id", "metric", "value", "unit", "source_file_id", "source_sha256",
  "source_row_key", "scope", "support_label", "evidence_status", "null_reason",
] as const;

const CANDIDATE_NULL_FIELDS = new Set<string>([
  "conservative_value",
  "error_margin",
  "extrapolation_distance",
  "testable_max_distance",
  "neighborhood_result",
]);
const METRIC_SOURCE_NULL_FIELDS = new Set<string>([
  "source_file_id",
  "source_sha256",
  "source_row_key",
  "scope",
  "support_label",
]);
const EXPECTED_CANDIDATE_SUPPORT_LABELS = [
  "within_hull_new_level",
  "outside_training_bounds",
  "within_hull_new_level",
  "outside_training_bounds",
  "outside_training_bounds",
  "outside_training_bounds",
  "outside_training_bounds",
  "outside_training_bounds",
  "outside_training_bounds",
  "outside_training_bounds",
  "outside_training_bounds",
  "within_hull_new_level",
  "within_hull_new_level",
  "within_hull_new_level",
  "within_hull_new_level",
  "within_hull_new_level",
  "outside_training_bounds",
  "outside_training_bounds",
  "outside_training_bounds",
  "exact_training_structure",
] as const;

const EXPECTED_DECISION: Decision = {
  scientific_status: "REJECTED",
  decision: "evidence_insufficient",
  reason: "no_eligible_models",
  eligible_models: [],
  recommendation_eligible: false,
  recommendation_count: 0,
  grade: null,
  grade_null_reason: "no_eligible_models",
  grade_counts: { A: 0, B: 0, C: 0 },
  baseline: { value: "14.674470284168734", unit: "percent_number" },
  absolute_target: { value: "15.674470284168734", unit: "percent_number" },
  required_uplift: { value: "1", unit: "percentage_points" },
  conservative_evidence: {
    conservative_value: null,
    conservative_value_null_reasons: ["no_eligible_models", "P3_AGGREGATION_ESTIMAND_MISMATCH", "E1_COORDINATE_OBSERVATIONS_MISSING", "HARD_CAPACITY_INSUFFICIENT_13_KEYS"],
    error_margin: null,
    error_margin_null_reasons: ["no_eligible_models", "HARD_CAPACITY_INSUFFICIENT_13_KEYS"],
    extrapolation_distance: null,
    extrapolation_distance_null_reasons: ["no_eligible_models"],
    testable_max_distance: null,
    testable_max_distance_null_reasons: ["no_eligible_models"],
  },
  neighborhood: { status: "not_executed", null_reason: "no_eligible_models", nine_point_prediction_executed: false },
  random_process: {
    status: "not_executed", reason: "no_eligible_models", seed: 20261027, seed_used: false,
    random_draw_count: 0, bootstrap_prediction_count: 0, refit_count: 0,
  },
  search_status: "not_executed",
  trusted_for_unmeasured_optimization: false,
};

type UnknownRecord = Record<string, unknown>;

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function exactKeys(record: UnknownRecord, keys: readonly string[], path: string, errors: string[]): void {
  const actual = Object.keys(record).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    errors.push(`${path}: 字段集合不匹配`);
  }
}

function exact(value: unknown, expected: unknown, path: string, errors: string[]): void {
  if (value !== expected) errors.push(`${path}: 期望 ${String(expected)}，实际 ${String(value)}`);
}

function stringValue(value: unknown, path: string, errors: string[]): value is string {
  if (typeof value !== "string") {
    errors.push(`${path}: 必须为字符串`);
    return false;
  }
  return true;
}

function descriptor(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) {
    errors.push(`${path}: 必须为对象`);
    return;
  }
  exactKeys(value, ["path", "bytes", "sha256", "file_id"], path, errors);
  stringValue(value.path, `${path}.path`, errors);
  stringValue(value.file_id, `${path}.file_id`, errors);
  if (typeof value.bytes !== "number" || !Number.isSafeInteger(value.bytes) || value.bytes < 0) {
    errors.push(`${path}.bytes: 必须为非负安全整数`);
  }
  if (typeof value.sha256 !== "string" || !/^[0-9a-f]{64}$/.test(value.sha256)) {
    errors.push(`${path}.sha256: 必须为 64 位小写 SHA256`);
  }
}

function deepEqual(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left) && Array.isArray(right) && left.length === right.length
      && left.every((item, index) => deepEqual(item, right[index]));
  }
  if (isRecord(left) && isRecord(right)) {
    const leftKeys = Object.keys(left).sort();
    const rightKeys = Object.keys(right).sort();
    return leftKeys.length === rightKeys.length
      && leftKeys.every((key, index) => key === rightKeys[index] && deepEqual(left[key], right[key]));
  }
  return false;
}

function parseNestedJson(value: unknown, path: string, errors: string[]): unknown {
  if (!stringValue(value, path, errors)) return null;
  try {
    return JSON.parse(value) as unknown;
  } catch {
    errors.push(`${path}: 内嵌 JSON 无效`);
    return null;
  }
}

function validateCandidate(value: unknown, index: number, errors: string[]): void {
  const path = `candidates.${index}`;
  if (!isRecord(value)) {
    errors.push(`${path}: 必须为对象`);
    return;
  }
  exactKeys(value, CANDIDATE_COLUMNS, path, errors);
  for (const column of CANDIDATE_COLUMNS) {
    if (column === "grade" || CANDIDATE_NULL_FIELDS.has(column)) {
      exact(value[column], null, `${path}.${column}`, errors);
    } else {
      stringValue(value[column], `${path}.${column}`, errors);
    }
  }
  const expectedId = EXPECTED_CANDIDATE_IDS[index];
  exact(value.candidate_id, expectedId, `${path}.candidate_id`, errors);
  exact(value.control_type, index < 17 ? "nonnegative_control" : "negative_control", `${path}.control_type`, errors);
  exact(value.source_file_id, "P12_CANDIDATES", `${path}.source_file_id`, errors);
  exact(value.source_sha256, SOURCE_SHA256.P12_CANDIDATES, `${path}.source_sha256`, errors);
  exact(value.source_row_key, `source_row_number=${index + 1}`, `${path}.source_row_key`, errors);
  exact(value.support_label, EXPECTED_CANDIDATE_SUPPORT_LABELS[index], `${path}.support_label`, errors);
  for (const [field, expected] of [
    ["recommendation_eligible", "False"], ["grade_null_reason", "no_eligible_models"],
    ["evidence_status", "evidence_insufficient"], ["trusted_for_unmeasured_optimization", "False"],
    ["new_candidate", "False"], ["neighborhood_status", "not_executed"],
    ["neighborhood_null_reason", "no_eligible_models"],
  ] as const) exact(value[field], expected, `${path}.${field}`, errors);

  const prediction = parseNestedJson(value.model_prediction_provenance, `${path}.model_prediction_provenance`, errors);
  const expectedPrediction = {
    value_origin: "legacy_model_prediction", new_prediction: false, prediction_recomputed: false,
    trusted_for_unmeasured_optimization: false,
  };
  if (!deepEqual(prediction, expectedPrediction)) errors.push(`${path}.model_prediction_provenance: 与 O14 历史预测语义不匹配`);
  const measured = parseNestedJson(value.measured_geometry_match, `${path}.measured_geometry_match`, errors);
  const expectedMeasured = index === 19
    ? { status: "exact_match", match_count: 1, structure_ids: [C19_STRUCTURE_ID] }
    : { status: "no_exact_match", match_count: 0, structure_ids: [] };
  if (!deepEqual(measured, expectedMeasured)) errors.push(`${path}.measured_geometry_match: 与 O14 几何匹配不匹配`);
  if (index === 19) {
    exact(value.support_label, "exact_training_structure", `${path}.support_label`, errors);
    if (value.exclusion_reason === "") errors.push(`${path}.exclusion_reason: C19 不得为空`);
  }
}

function validateMetric(value: unknown, index: number, errors: string[]): void {
  const path = `metrics.${index}`;
  if (!isRecord(value)) {
    errors.push(`${path}: 必须为对象`);
    return;
  }
  exactKeys(value, METRIC_COLUMNS, path, errors);
  for (const column of METRIC_COLUMNS) {
    if (column === "value" || (value.value === null && METRIC_SOURCE_NULL_FIELDS.has(column))) continue;
    stringValue(value[column], `${path}.${column}`, errors);
  }
  const candidateIndex = Math.floor(index / 8);
  const model = EXPECTED_MODELS[Math.floor((index % 8) / 4)];
  const metric = EXPECTED_METRICS[index % 4];
  exact(value.candidate_id, EXPECTED_CANDIDATE_IDS[candidateIndex], `${path}.candidate_id`, errors);
  exact(value.model_id, model, `${path}.model_id`, errors);
  exact(value.metric, metric, `${path}.metric`, errors);
  exact(value.unit, metric === "k2eff_pct" ? "percent_number" : "dimensionless", `${path}.unit`, errors);
  if (value.value === null) {
    exact(value.evidence_status, "evidence_insufficient", `${path}.evidence_status`, errors);
    if (typeof value.null_reason !== "string" || value.null_reason.length === 0) {
      errors.push(`${path}.null_reason: null 指标必须给出原因`);
    }
    for (const field of ["source_file_id", "source_sha256", "source_row_key", "scope", "support_label"] as const) {
      exact(value[field], null, `${path}.${field}`, errors);
    }
    return;
  }
  if (!stringValue(value.value, `${path}.value`, errors)) return;
  if (!/^-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(value.value) || !Number.isFinite(Number(value.value))) {
    errors.push(`${path}.value: 必须为有限十进制完整精度字符串`);
  }
  exact(value.evidence_status, "registered_historical_reference", `${path}.evidence_status`, errors);
  exact(value.null_reason, "", `${path}.null_reason`, errors);
  exact(value.support_label, EXPECTED_CANDIDATE_SUPPORT_LABELS[candidateIndex], `${path}.support_label`, errors);
  const sourceId = value.source_file_id;
  if (sourceId !== "P12_CANDIDATES" && sourceId !== "P3_STRUCTURES") {
    errors.push(`${path}.source_file_id: 未登记来源 ${String(sourceId)}`);
    return;
  }
  exact(value.source_sha256, SOURCE_SHA256[sourceId], `${path}.source_sha256`, errors);
  if (sourceId === "P12_CANDIDATES") {
    exact(value.source_row_key, `source_row_number=${candidateIndex + 1}`, `${path}.source_row_key`, errors);
    exact(value.scope, "p12_failure_control", `${path}.scope`, errors);
  } else {
    const allowed = candidateIndex === 19 && model === "P3" && (metric === "qs" || metric === "bodeq_fitted");
    if (!allowed) errors.push(`${path}: P3_STRUCTURES 仅允许 C19/P3/Qs/BodeQ 同结构值`);
    exact(value.source_row_key, `structure_id=${C19_STRUCTURE_ID}`, `${path}.source_row_key`, errors);
    exact(value.scope, "O01_P3_deembedded_structure_aggregates", `${path}.scope`, errors);
  }
}

function validateTraceability(value: unknown, errors: string[]): void {
  if (!isRecord(value)) {
    errors.push("source_traceability: 必须为对象");
    return;
  }
  exactKeys(value, ["o14_delivery", "t15_manifest", "registered_inputs"], "source_traceability", errors);
  if (!Array.isArray(value.o14_delivery) || value.o14_delivery.length !== 3) {
    errors.push("source_traceability.o14_delivery: 必须恰有 3 项");
  } else {
    value.o14_delivery.forEach((item, index) => {
      const path = `source_traceability.o14_delivery.${index}`;
      descriptor(item, path, errors);
      if (!isRecord(item)) return;
      const id = ["candidates.csv", "metrics.csv", "decision.json"][index] as keyof typeof O14_SHA256;
      exact(item.file_id, id, `${path}.file_id`, errors);
      exact(item.sha256, O14_SHA256[id], `${path}.sha256`, errors);
    });
  }
  descriptor(value.t15_manifest, "source_traceability.t15_manifest", errors);
  if (isRecord(value.t15_manifest)) {
    exact(value.t15_manifest.file_id, "t15_artifact_manifest.json", "source_traceability.t15_manifest.file_id", errors);
    exact(value.t15_manifest.sha256, "5e891425e5f3799da5da8f5ef5e7d9537a21c4f9fd8dbbafc689f097684bea38", "source_traceability.t15_manifest.sha256", errors);
  }
  if (!Array.isArray(value.registered_inputs) || value.registered_inputs.length !== 25) {
    errors.push("source_traceability.registered_inputs: 必须保留 25 项");
    return;
  }
  value.registered_inputs.forEach((item, index) => {
    const path = `source_traceability.registered_inputs.${index}`;
    descriptor(item, path, errors);
    const expected = EXPECTED_REGISTERED_INPUTS[index];
    if (!isRecord(item) || !expected) return;
    exact(item.file_id, expected[0], `${path}.file_id`, errors);
    exact(item.path, expected[1], `${path}.path`, errors);
    exact(item.bytes, expected[2], `${path}.bytes`, errors);
    exact(item.sha256, expected[3], `${path}.sha256`, errors);
  });
}

export function validateBrowserData(input: unknown): ValidationResult {
  const errors: string[] = [];
  if (!isRecord(input)) return { ok: false, errors: ["根值必须为 JSON 对象"] };
  exactKeys(input, ["schema", "task_id", "output_id", "attempt_id", "source_output_id", "candidate_columns", "metric_columns", "candidates", "metrics", "decision", "source_traceability"], "root", errors);
  exact(input.schema, "aln_optimization_p13_o15_browser_data_v1", "schema", errors);
  exact(input.task_id, "T16", "task_id", errors);
  exact(input.output_id, "O15", "output_id", errors);
  if (typeof input.attempt_id !== "string"
    || !/^attempt_[A-Za-z0-9][A-Za-z0-9._-]*$/.test(input.attempt_id)
    || input.attempt_id.includes("..")) {
    errors.push("attempt_id: 必须为有效的隔离 attempt 标识");
  }
  exact(input.source_output_id, "O14", "source_output_id", errors);
  if (!deepEqual(input.candidate_columns, [...CANDIDATE_COLUMNS])) errors.push("candidate_columns: 与 O14 表头或顺序不一致");
  if (!deepEqual(input.metric_columns, [...METRIC_COLUMNS])) errors.push("metric_columns: 与 O14 表头或顺序不一致");
  if (!Array.isArray(input.candidates) || input.candidates.length !== 20) {
    errors.push("candidates: 必须按原顺序恰有 20 行");
  } else {
    input.candidates.forEach((row, index) => validateCandidate(row, index, errors));
  }
  if (!Array.isArray(input.metrics) || input.metrics.length !== 160) {
    errors.push("metrics: 必须按 candidate/model/metric 顺序恰有 160 行");
  } else {
    input.metrics.forEach((row, index) => validateMetric(row, index, errors));
  }
  if (isRecord(input.decision) && input.decision.grade !== null) exact(input.decision.grade, null, "decision.grade", errors);
  if (!deepEqual(input.decision, EXPECTED_DECISION)) errors.push("decision: 必须逐值复现 O14 decision.json");
  validateTraceability(input.source_traceability, errors);
  if (errors.length > 0) return { ok: false, errors: errors.slice(0, 100) };
  return { ok: true, data: input as unknown as BrowserData, errors: [] };
}

export function parseBrowserDataJson(text: string): ValidationResult {
  try {
    return validateBrowserData(JSON.parse(text) as unknown);
  } catch {
    return { ok: false, errors: ["JSON 语法无效"] };
  }
}

export function isBrowserData(input: unknown): input is BrowserData {
  return validateBrowserData(input).ok;
}
