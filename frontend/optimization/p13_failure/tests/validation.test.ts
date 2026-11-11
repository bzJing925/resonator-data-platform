import assert from "node:assert/strict";
import test from "node:test";
import { importBrowserDataJson } from "../src/state.js";
import type { BrowserData } from "../src/types.js";
import { parseBrowserDataJson, validateBrowserData } from "../src/validation.js";
import { readViewerData, readViewerDataText } from "./helpers.js";

function validData(): BrowserData {
  const result = validateBrowserData(readViewerData());
  assert.equal(result.ok, true, result.ok ? "" : result.errors.join("\n"));
  return structuredClone(result.data);
}

test("accepts the exact O15 browser data and preserves counts/order", () => {
  const result = validateBrowserData(readViewerData());
  assert.equal(result.ok, true, result.ok ? "" : result.errors.join("\n"));
  if (!result.ok) return;
  assert.equal(result.data.candidates.length, 20);
  assert.equal(result.data.metrics.length, 160);
  assert.equal(result.data.candidates[19]?.candidate_id, "C19_negative_control_EG0.0_FL1.5_A700_PF1");
  assert.equal(result.data.decision.recommendation_count, 0);
  assert.equal(result.data.decision.grade, null);
});

test("rejects malformed JSON", () => {
  const result = parseBrowserDataJson('{"candidates": [');
  assert.equal(result.ok, false);
});

const tamperCases: Array<{ name: string; mutate: (data: BrowserData) => void; path: RegExp }> = [
  {
    name: "candidate ID",
    mutate: (data) => { data.candidates[0]!.candidate_id = "tampered"; },
    path: /candidates\.0\.candidate_id/,
  },
  {
    name: "candidate order",
    mutate: (data) => {
      const first = data.candidates.shift();
      if (first) data.candidates.push(first);
    },
    path: /candidates\.0\.candidate_id/,
  },
  {
    name: "candidate grade",
    mutate: (data) => { Reflect.set(data.candidates[0]!, "grade", "A"); },
    path: /candidates\.0\.grade/,
  },
  {
    name: "decision grade",
    mutate: (data) => { Reflect.set(data.decision, "grade", "B"); },
    path: /decision\.grade/,
  },
  {
    name: "candidate source hash",
    mutate: (data) => { data.candidates[0]!.source_sha256 = "0".repeat(64); },
    path: /candidates\.0\.source_sha256/,
  },
  {
    name: "metric source hash",
    mutate: (data) => { data.metrics[0]!.source_sha256 = "f".repeat(64); },
    path: /metrics\.0\.source_sha256/,
  },
  {
    name: "delivery source hash",
    mutate: (data) => { data.source_traceability.o14_delivery[0]!.sha256 = "0".repeat(64); },
    path: /source_traceability\.o14_delivery\.0\.sha256/,
  },
  {
    name: "registered input source hash",
    mutate: (data) => { data.source_traceability.registered_inputs[0]!.sha256 = "0".repeat(64); },
    path: /source_traceability\.registered_inputs\.0\.sha256/,
  },
  {
    name: "registered input source ID",
    mutate: (data) => { data.source_traceability.registered_inputs[0]!.file_id = "tampered"; },
    path: /source_traceability\.registered_inputs\.0\.file_id/,
  },
];

for (const item of tamperCases) {
  test(`rejects tampered ${item.name} without replacing current valid state`, () => {
    const current = validData();
    const before = structuredClone(current);
    const tampered = structuredClone(current);
    item.mutate(tampered);
    const validation = validateBrowserData(tampered);
    assert.equal(validation.ok, false);
    if (!validation.ok) assert.match(validation.errors.join("\n"), item.path);
    const imported = importBrowserDataJson(current, JSON.stringify(tampered));
    assert.equal(imported.accepted, false);
    assert.equal(imported.data, current);
    assert.deepEqual(imported.data, before);
  });
}

test("valid JSON import replaces state only after complete validation", () => {
  const current = validData();
  const imported = importBrowserDataJson(current, readViewerDataText());
  assert.equal(imported.accepted, true);
  assert.notEqual(imported.data, current);
  assert.deepEqual(imported.data, current);
});

test("rejects missing negative controls and incomplete metric matrix", () => {
  const missingControl = validData();
  missingControl.candidates.pop();
  assert.equal(validateBrowserData(missingControl).ok, false);
  const missingMetric = validData();
  missingMetric.metrics.pop();
  assert.equal(validateBrowserData(missingMetric).ok, false);
});
