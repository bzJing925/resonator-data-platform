import assert from "node:assert/strict";
import { resolve } from "node:path";
import type { BrowserData, ValidationResult } from "../src/types.js";
import { aRun, readViewerData } from "./helpers.js";

export const A03_DIR = resolve(aRun, "A03");

export const CANDIDATE_NULL_FIELDS = [
  "conservative_value",
  "error_margin",
  "extrapolation_distance",
  "testable_max_distance",
  "neighborhood_result",
] as const;

export const METRIC_SOURCE_FIELDS = [
  "source_file_id",
  "source_sha256",
  "source_row_key",
  "scope",
  "support_label",
] as const;

export interface NullViolation {
  kind: "candidate" | "null_metric_source";
  path: string;
  actual: unknown;
}

/** Construct the contract fixture without asking the implementation under test to accept it. */
export function correctNullData(input = readViewerData() as BrowserData): BrowserData {
  const data = structuredClone(input);
  assert.equal(data.candidates.length, 20);
  assert.equal(data.metrics.length, 160);
  for (const candidate of data.candidates) {
    for (const field of CANDIDATE_NULL_FIELDS) {
      const actual = Reflect.get(candidate, field) as unknown;
      assert.ok(actual === "" || actual === null, `unexpected ${field} seed value`);
      Reflect.set(candidate, field, null);
    }
  }
  for (const metric of data.metrics) {
    if (metric.value === null) {
      for (const field of METRIC_SOURCE_FIELDS) {
        const actual = Reflect.get(metric, field) as unknown;
        assert.ok(actual === "" || actual === null, `unexpected null-metric ${field} seed value`);
        Reflect.set(metric, field, null);
      }
    }
  }
  return data;
}

export function currentCandidateData(): BrowserData {
  return structuredClone(readViewerData() as BrowserData);
}

export function collectNullViolations(data: BrowserData): NullViolation[] {
  const violations: NullViolation[] = [];
  for (const [rowIndex, candidate] of data.candidates.entries()) {
    for (const field of CANDIDATE_NULL_FIELDS) {
      const actual = Reflect.get(candidate, field) as unknown;
      if (actual !== null) {
        violations.push({ kind: "candidate", path: `candidates.${rowIndex}.${field}`, actual });
      }
    }
  }
  for (const [rowIndex, metric] of data.metrics.entries()) {
    if (metric.value !== null) continue;
    for (const field of METRIC_SOURCE_FIELDS) {
      const actual = Reflect.get(metric, field) as unknown;
      if (actual !== null) {
        violations.push({ kind: "null_metric_source", path: `metrics.${rowIndex}.${field}`, actual });
      }
    }
  }
  return violations;
}

export function nullMatrixContexts(data = currentCandidateData()): Array<{ id: string; path: string }> {
  const missingMetricIndex = data.metrics.findIndex((row) => row.value === null);
  assert.ok(missingMetricIndex >= 0);
  const contexts = CANDIDATE_NULL_FIELDS.map((field) => ({
    id: `candidate-${field}`,
    path: `candidates.0.${field}`,
  }));
  contexts.push(
    { id: "candidate-grade", path: "candidates.0.grade" },
    { id: "decision-grade", path: "decision.grade" },
    { id: "null-metric-value", path: `metrics.${missingMetricIndex}.value` },
  );
  for (const field of METRIC_SOURCE_FIELDS) {
    contexts.push({ id: `null-metric-${field}`, path: `metrics.${missingMetricIndex}.${field}` });
  }
  return contexts;
}

export function getPath(value: unknown, path: string): unknown {
  let current = value;
  for (const key of path.split(".")) {
    if (typeof current !== "object" || current === null) throw new Error(`Missing parent for ${path}`);
    current = Reflect.get(current, key) as unknown;
  }
  return current;
}

function parentAtPath(value: unknown, path: string): { parent: object; leaf: string } {
  const keys = path.split(".");
  const leaf = keys.pop();
  assert.ok(leaf);
  const parent = keys.length === 0 ? value : getPath(value, keys.join("."));
  if (typeof parent !== "object" || parent === null) throw new Error(`Missing parent for ${path}`);
  return { parent, leaf };
}

export function setPath(value: unknown, path: string, next: unknown): void {
  const { parent, leaf } = parentAtPath(value, path);
  Reflect.set(parent, leaf, next);
}

export function deletePath(value: unknown, path: string): void {
  const { parent, leaf } = parentAtPath(value, path);
  Reflect.deleteProperty(parent, leaf);
}

export function nonJsonImportText(data: BrowserData, path: string, token: string): string {
  const copy = structuredClone(data);
  const placeholder = "__A03_NON_JSON_NUMBER__";
  setPath(copy, path, placeholder);
  const text = JSON.stringify(copy);
  const quoted = JSON.stringify(placeholder);
  assert.ok(text.includes(quoted), `placeholder missing for ${path}`);
  return text.replace(quoted, token);
}

export function firstValidationErrors(result: ValidationResult): string {
  return result.ok ? "none" : result.errors.slice(0, 3).join(" | ");
}
