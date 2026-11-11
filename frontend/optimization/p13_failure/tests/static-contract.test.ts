import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import { EXPECTED_CANDIDATE_IDS, O14_SHA256, SOURCE_SHA256 } from "../src/o14_contract.js";
import type { BrowserData } from "../src/types.js";
import { deliveryDir, parseCsv, readViewerData, viewerDir } from "./helpers.js";
import { CANDIDATE_NULL_FIELDS, METRIC_SOURCE_FIELDS } from "./null-contract-helpers.js";

const data = readViewerData() as BrowserData;
const candidateNullFields: ReadonlySet<string> = new Set(CANDIDATE_NULL_FIELDS);
const metricSourceNullFields: ReadonlySet<string> = new Set(METRIC_SOURCE_FIELDS);

function sha256(path: string): string {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

test("browser data reproduces every O14 candidate cell and order at full precision", () => {
  const csv = parseCsv(readFileSync(resolve(deliveryDir, "candidates.csv"), "utf8"));
  assert.deepEqual(data.candidate_columns, csv.headers);
  assert.equal(data.candidates.length, 20);
  assert.deepEqual(data.candidates.map((row) => row.candidate_id), [...EXPECTED_CANDIDATE_IDS]);
  for (const [rowIndex, expected] of csv.rows.entries()) {
    const actual = data.candidates[rowIndex];
    assert.ok(actual);
    for (const column of csv.headers) {
      const raw = expected[column] ?? "";
      const expectedValue = column === "grade" || (candidateNullFields.has(column) && raw === "")
        ? null
        : raw;
      assert.deepEqual(Reflect.get(actual, column), expectedValue, `candidate ${rowIndex + 1} ${column}`);
    }
  }
  assert.equal(data.candidates.filter((row) => row.control_type === "negative_control").length, 3);
  assert.ok(data.candidates.every((row) => row.grade === null));
  assert.ok(data.candidates.every((row) => row.recommendation_eligible === "False"));
});

test("browser data reproduces all 160 O14 metric cells at full precision", () => {
  const csv = parseCsv(readFileSync(resolve(deliveryDir, "metrics.csv"), "utf8"));
  assert.deepEqual(data.metric_columns, csv.headers);
  assert.equal(data.metrics.length, 160);
  for (const [rowIndex, expected] of csv.rows.entries()) {
    const actual = data.metrics[rowIndex];
    assert.ok(actual);
    for (const column of csv.headers) {
      const raw = expected[column] ?? "";
      const expectedValue = (column === "value" && raw === "")
        || (expected.value === "" && metricSourceNullFields.has(column) && raw === "")
        ? null
        : raw;
      assert.deepEqual(Reflect.get(actual, column), expectedValue, `metric ${rowIndex + 1} ${column}`);
    }
  }
  assert.equal(data.metrics.filter((row) => row.value !== null).length, 82);
  assert.equal(data.metrics.filter((row) => row.value === null).length, 78);
});

test("decision is byte-value equivalent to O14 JSON", () => {
  assert.deepEqual(data.decision, JSON.parse(readFileSync(resolve(deliveryDir, "decision.json"), "utf8")));
  assert.equal(data.decision.scientific_status, "REJECTED");
  assert.equal(data.decision.grade, null);
  assert.equal(data.decision.recommendation_count, 0);
  assert.equal(data.decision.neighborhood.status, "not_executed");
  assert.equal(data.decision.random_process.status, "not_executed");
  assert.equal(data.decision.search_status, "not_executed");
});

test("O14 delivery and registered source hashes match actual frozen inputs", () => {
  for (const descriptor of data.source_traceability.o14_delivery) {
    assert.equal(descriptor.sha256, O14_SHA256[descriptor.file_id as keyof typeof O14_SHA256]);
    assert.equal(sha256(descriptor.path), descriptor.sha256);
  }
  assert.equal(sha256(data.source_traceability.t15_manifest.path), data.source_traceability.t15_manifest.sha256);
  const p12 = data.source_traceability.registered_inputs.find((row) => row.file_id === "P12_CANDIDATES");
  const p3 = data.source_traceability.registered_inputs.find((row) => row.file_id === "P3_STRUCTURES");
  assert.equal(p12?.sha256, SOURCE_SHA256.P12_CANDIDATES);
  assert.equal(p3?.sha256, SOURCE_SHA256.P3_STRUCTURES);
});

test("C19 separates measured geometry and same-structure values from legacy predictions", () => {
  const candidate = data.candidates.find((row) => row.candidate_id === EXPECTED_CANDIDATE_IDS[19]);
  assert.ok(candidate);
  assert.deepEqual(JSON.parse(candidate.measured_geometry_match), {
    status: "exact_match",
    match_count: 1,
    structure_ids: ["s_c454ac7ec8a2a7f8"],
  });
  assert.equal(JSON.parse(candidate.model_prediction_provenance).value_origin, "legacy_model_prediction");
  const c19 = data.metrics.filter((row) => row.candidate_id === candidate.candidate_id);
  assert.equal(c19.length, 8);
  assert.equal(c19.find((row) => row.model_id === "P3" && row.metric === "qs")?.value, "942.3123232596486");
  assert.equal(c19.find((row) => row.model_id === "P3" && row.metric === "bodeq_fitted")?.value, "926.111784408804");
  assert.equal(c19.find((row) => row.model_id === "P3" && row.metric === "qp")?.value, "446.07505044186405");
});

test("offline HTML carries required failure semantics and inline assets", () => {
  const html = readFileSync(resolve(viewerDir, "index.html"), "utf8");
  for (const text of [
    "当前证据不支持 k² 提升 1 个百分点",
    "历史模型参考",
    "evidence_insufficient",
    "not_executed",
    "no_eligible_models",
    "无当前推荐",
    "grade: null",
  ]) assert.ok(html.includes(text), text);
  assert.match(html, /@media\s*\(max-width:/);
  assert.match(html, /CSP_OFFLINE_PLACEHOLDER|<meta http-equiv="Content-Security-Policy"/);
});
