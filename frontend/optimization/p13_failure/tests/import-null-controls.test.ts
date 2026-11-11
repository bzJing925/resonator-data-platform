import assert from "node:assert/strict";
import test from "node:test";
import { importBrowserDataJson } from "../src/state.js";
import type { BrowserData } from "../src/types.js";
import { validateBrowserData } from "../src/validation.js";
import { readViewerData, readViewerDataText } from "./helpers.js";
import { nonJsonImportText } from "./null-contract-helpers.js";

function currentData(): BrowserData {
  const data = readViewerData() as BrowserData;
  const result = validateBrowserData(data);
  assert.equal(result.ok, true, result.ok ? "" : result.errors.slice(0, 3).join(" | "));
  return structuredClone(data);
}

function assertFailedImportPreservesState(current: BrowserData, text: string): void {
  const before = structuredClone(current);
  const imported = importBrowserDataJson(current, text);
  assert.equal(imported.accepted, false);
  assert.equal(imported.data, current);
  assert.deepEqual(imported.data, before);
}

test("A03-IMPORT-CONTROL-001 current candidate valid JSON import is accepted without data changes", () => {
  const current = currentData();
  const imported = importBrowserDataJson(current, readViewerDataText());
  assert.equal(imported.accepted, true, imported.validation.ok ? "" : imported.validation.errors.slice(0, 3).join(" | "));
  assert.notEqual(imported.data, current);
  assert.deepEqual(imported.data, current);
});

test("A03-IMPORT-CONTROL-002 valid numeric 0 counters remain numeric zero, not null or strings", () => {
  const current = currentData();
  const imported = importBrowserDataJson(current, JSON.stringify(current));
  assert.equal(imported.accepted, true);
  assert.equal(imported.data.decision.recommendation_count, 0);
  assert.equal(imported.data.decision.random_process.random_draw_count, 0);
  assert.equal(imported.data.decision.random_process.bootstrap_prediction_count, 0);
  assert.equal(imported.data.decision.random_process.refit_count, 0);
  assert.deepEqual(imported.data.decision.grade_counts, { A: 0, B: 0, C: 0 });
});

test("A03-IMPORT-CONTROL-003 full-precision string 0 is preserved on a sourced valued metric", () => {
  const current = currentData();
  const row = current.metrics.find((metric) => metric.value !== null);
  assert.ok(row);
  row.value = "0";
  const validation = validateBrowserData(current);
  assert.equal(validation.ok, true, validation.ok ? "" : validation.errors.slice(0, 3).join(" | "));
  const imported = importBrowserDataJson(current, JSON.stringify(current));
  assert.equal(imported.accepted, true);
  assert.equal(imported.data.metrics[current.metrics.indexOf(row)]!.value, "0");
  assert.notEqual(imported.data.metrics[current.metrics.indexOf(row)]!.value, null);
});

test("A03-IMPORT-CONTROL-004 malformed JSON is rejected without replacing current state", () => {
  const current = currentData();
  assertFailedImportPreservesState(current, '{"candidates":[');
});

test("A03-IMPORT-CONTROL-005 forged candidate grade is rejected without replacing current state", () => {
  const current = currentData();
  Reflect.set(current.candidates[0]!, "grade", "A");
  assertFailedImportPreservesState(current, JSON.stringify(current));
});

test("A03-IMPORT-CONTROL-006 forged candidate source hash is rejected without replacing current state", () => {
  const current = currentData();
  current.candidates[0]!.source_sha256 = "0".repeat(64);
  assertFailedImportPreservesState(current, JSON.stringify(current));
});

test("A03-IMPORT-CONTROL-007 forged recommendation count is rejected without replacing current state", () => {
  const current = currentData();
  current.decision.recommendation_count = 1;
  assertFailedImportPreservesState(current, JSON.stringify(current));
});

test("A03-IMPORT-CONTROL-008 forged candidate order is rejected without replacing current state", () => {
  const current = currentData();
  [current.candidates[0], current.candidates[1]] = [current.candidates[1]!, current.candidates[0]!];
  assertFailedImportPreservesState(current, JSON.stringify(current));
});

test("A03-IMPORT-CONTROL-009 forged metric order is rejected without replacing current state", () => {
  const current = currentData();
  [current.metrics[0], current.metrics[1]] = [current.metrics[1]!, current.metrics[0]!];
  assertFailedImportPreservesState(current, JSON.stringify(current));
});

test("A03-IMPORT-CONTROL-010 empty-string value on a missing metric is rejected without replacing current state", () => {
  const current = currentData();
  const row = current.metrics.find((metric) => metric.value === null);
  assert.ok(row);
  row.value = "";
  assertFailedImportPreservesState(current, JSON.stringify(current));
});

test("A03-IMPORT-CONTROL-011 raw non-JSON NaN is rejected without replacing current state", () => {
  const current = currentData();
  const rowIndex = current.metrics.findIndex((metric) => metric.value === null);
  assert.ok(rowIndex >= 0);
  assertFailedImportPreservesState(current, nonJsonImportText(current, `metrics.${rowIndex}.value`, "NaN"));
});
