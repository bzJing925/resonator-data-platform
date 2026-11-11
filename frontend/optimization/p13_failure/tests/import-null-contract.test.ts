import assert from "node:assert/strict";
import test from "node:test";
import { isDeepStrictEqual } from "node:util";
import { importBrowserDataJson } from "../src/state.js";
import type { BrowserData } from "../src/types.js";
import { validateBrowserData } from "../src/validation.js";
import {
  correctNullData,
  currentCandidateData,
  deletePath,
  firstValidationErrors,
  getPath,
  nonJsonImportText,
  nullMatrixContexts,
  setPath,
} from "./null-contract-helpers.js";

/**
 * Execute direct validation and import preservation even when either one exposes a defect.
 * The isolated-mutation pass prevents unrelated legacy null errors from hiding the named case.
 */
function rejectedCaseProblems(
  current: BrowserData,
  direct: BrowserData,
  path: string,
  missing: boolean,
  checkPath: boolean,
  importText: string,
): string[] {
  const problems: string[] = [];
  const validation = validateBrowserData(direct);
  if (validation.ok) {
    problems.push(`invalid ${path} was accepted by direct validation`);
  } else if (checkPath) {
    const expectedPath = missing ? path.split(".").slice(0, -1).join(".") : path;
    if (!validation.errors.some((error) => error.includes(expectedPath))) {
      problems.push(`${expectedPath} missing from ${validation.errors.join(" | ")}`);
    }
  }

  const before = structuredClone(current);
  const imported = importBrowserDataJson(current, importText);
  if (imported.accepted) problems.push(`invalid import ${path} was accepted`);
  if (imported.data !== current) problems.push(`failed import ${path} replaced the current object`);
  if (!isDeepStrictEqual(imported.data, before)) problems.push(`failed import ${path} mutated current state`);
  return problems;
}

function exerciseRejectedCase(
  path: string,
  apply: (data: BrowserData) => void,
  missing = false,
  token?: string,
): void {
  const problems: string[] = [];

  // This is the corrected contract fixture. It is never gated on old-validator acceptance.
  const nullCurrent = correctNullData();
  const nullDirect = structuredClone(nullCurrent);
  apply(nullDirect);
  const nullText = token
    ? nonJsonImportText(nullCurrent, path, token)
    : JSON.stringify(nullDirect);
  problems.push(...rejectedCaseProblems(
    nullCurrent,
    nullDirect,
    path,
    missing,
    false,
    nullText,
  ).map((problem) => `correct-null fixture: ${problem}`));

  // Also isolate the named mutation from the candidate's otherwise-current valid report.
  const current = currentCandidateData();
  const direct = structuredClone(current);
  apply(direct);
  const text = token ? nonJsonImportText(current, path, token) : JSON.stringify(direct);
  problems.push(...rejectedCaseProblems(current, direct, path, missing, true, text)
    .map((problem) => `isolated mutation: ${problem}`));

  assert.deepEqual(problems, [], problems.join("\n"));
}

test("A03-IMPORT-NULL-001 correct-null synthetic data is accepted as a positive contract control", () => {
  const data = correctNullData();
  const result = validateBrowserData(data);
  assert.equal(result.ok, true, firstValidationErrors(result));
  if (!result.ok) return;
  assert.equal(result.data.candidates.filter((row) => row.conservative_value === null).length, 20);
  assert.equal(result.data.metrics.filter((row) => row.value === null).length, 78);
  assert.equal(result.data.metrics.filter((row) => row.value !== null && row.source_file_id).length, 82);
});

interface Variant {
  id: string;
  apply: (data: BrowserData, path: string) => void;
  missing?: boolean;
  token?: string;
}

const variants: Variant[] = [
  { id: "missing-key", apply: (data, path) => deletePath(data, path), missing: true },
  { id: "empty-string", apply: (data, path) => setPath(data, path, "") },
  { id: "string-null", apply: (data, path) => setPath(data, path, "null") },
  { id: "string-NaN", apply: (data, path) => setPath(data, path, "NaN") },
  { id: "number-NaN", apply: (data, path) => setPath(data, path, Number.NaN), token: "NaN" },
  { id: "number-Infinity", apply: (data, path) => setPath(data, path, Number.POSITIVE_INFINITY), token: "Infinity" },
  { id: "number-negative-Infinity", apply: (data, path) => setPath(data, path, Number.NEGATIVE_INFINITY), token: "-Infinity" },
];

for (const context of nullMatrixContexts()) {
  for (const variant of variants) {
    test(`A03-IMPORT-NULL-${context.id}-${variant.id} rejected per field with failed-import state preservation`, () => {
      assert.equal(getPath(correctNullData(), context.path), null, `${context.path} must start as JSON null`);
      exerciseRejectedCase(
        context.path,
        (data) => variant.apply(data, context.path),
        variant.missing === true,
        variant.token,
      );
    });
  }
}

function forgeryCase(id: string, path: string, mutate: (data: BrowserData) => void): void {
  test(`A03-IMPORT-FORGE-${id} rejected with failed-import state preservation`, () => {
    exerciseRejectedCase(path, mutate);
  });
}

forgeryCase("candidate-grade-A", "candidates.0.grade", (data) => { Reflect.set(data.candidates[0]!, "grade", "A"); });
forgeryCase("decision-grade-B", "decision.grade", (data) => { Reflect.set(data.decision, "grade", "B"); });
forgeryCase("valued-metric-null", "metrics.0", (data) => { data.metrics[0]!.value = null; });
forgeryCase("candidate-source-file-id", "candidates.0.source_file_id", (data) => { data.candidates[0]!.source_file_id = "FORGED"; });
forgeryCase("candidate-source-sha256", "candidates.0.source_sha256", (data) => { data.candidates[0]!.source_sha256 = "0".repeat(64); });
forgeryCase("candidate-source-row-key", "candidates.0.source_row_key", (data) => { data.candidates[0]!.source_row_key = "source_row_number=20"; });
forgeryCase("candidate-support-label", "candidates.0.support_label", (data) => { data.candidates[0]!.support_label = "forged_support"; });
forgeryCase("metric-source-file-id", "metrics.0.source_file_id", (data) => { data.metrics[0]!.source_file_id = "FORGED"; });
forgeryCase("metric-source-sha256", "metrics.0.source_sha256", (data) => { data.metrics[0]!.source_sha256 = "f".repeat(64); });
forgeryCase("metric-source-row-key", "metrics.0.source_row_key", (data) => { data.metrics[0]!.source_row_key = "source_row_number=20"; });
forgeryCase("metric-scope", "metrics.0.scope", (data) => { data.metrics[0]!.scope = "forged_scope"; });
forgeryCase("metric-support-label", "metrics.0.support_label", (data) => { data.metrics[0]!.support_label = "forged_support"; });
forgeryCase("candidate-recommendation-eligible", "candidates.0.recommendation_eligible", (data) => { data.candidates[0]!.recommendation_eligible = "True"; });
forgeryCase("decision-recommendation-eligible", "decision", (data) => { data.decision.recommendation_eligible = true; });
forgeryCase("decision-recommendation-count", "decision", (data) => { data.decision.recommendation_count = 1; });
forgeryCase("candidate-new-recommendation", "candidates.0.new_candidate", (data) => { data.candidates[0]!.new_candidate = "True"; });
forgeryCase("current-recommendation-key", "candidates.0", (data) => { Reflect.set(data.candidates[0]!, "recommendation", "forge current recommendation"); });
forgeryCase("candidate-order", "candidates.0.candidate_id", (data) => {
  [data.candidates[0], data.candidates[1]] = [data.candidates[1]!, data.candidates[0]!];
});
forgeryCase("metric-order", "metrics.0.metric", (data) => {
  [data.metrics[0], data.metrics[1]] = [data.metrics[1]!, data.metrics[0]!];
});
