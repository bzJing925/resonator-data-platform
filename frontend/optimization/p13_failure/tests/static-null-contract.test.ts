import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import type { BrowserData } from "../src/types.js";
import { readViewerData } from "./helpers.js";
import {
  A03_DIR,
  CANDIDATE_NULL_FIELDS,
  METRIC_SOURCE_FIELDS,
} from "./null-contract-helpers.js";

const data = readViewerData() as BrowserData;

test("A03-STATIC-NULL-001 exact 20 x 5 candidate fields are JSON null", () => {
  const violations: string[] = [];
  for (const [rowIndex, candidate] of data.candidates.entries()) {
    for (const field of CANDIDATE_NULL_FIELDS) {
      const actual = Reflect.get(candidate, field) as unknown;
      if (actual !== null) violations.push(`candidates.${rowIndex}.${field}=${JSON.stringify(actual)}`);
    }
  }
  assert.equal(violations.length, 0, `legacy detector found candidate=${violations.length} (expected old count 100): ${violations.slice(0, 5).join(", ")}`);
});

test("A03-STATIC-NULL-002 exact 78 x 5 null-metric source fields are JSON null", () => {
  const missing = data.metrics.filter((row) => row.value === null);
  assert.equal(missing.length, 78);
  const violations: string[] = [];
  for (const [rowIndex, metric] of data.metrics.entries()) {
    if (metric.value !== null) continue;
    for (const field of METRIC_SOURCE_FIELDS) {
      const actual = Reflect.get(metric, field) as unknown;
      if (actual !== null) violations.push(`metrics.${rowIndex}.${field}=${JSON.stringify(actual)}`);
    }
  }
  assert.equal(violations.length, 0, `legacy detector found null_metric_source=${violations.length} (expected old count 390): ${violations.slice(0, 5).join(", ")}`);
});

test("A03-STATIC-CONTROL-001 grade and metric value null semantics retain 20 + 1 grades, 78 nulls and 82 values", () => {
  assert.equal(data.candidates.length, 20);
  assert.ok(data.candidates.every((row) => row.grade === null));
  assert.equal(data.decision.grade, null);
  assert.equal(data.metrics.filter((row) => row.value === null).length, 78);
  const valued = data.metrics.filter((row) => row.value !== null);
  assert.equal(valued.length, 82);
  assert.ok(valued.every((row) => typeof row.value === "string" && row.value.length > 0));
});

test("A03-STATIC-CONTROL-002 all 82 sourced rows retain every source field", () => {
  const valued = data.metrics.filter((row) => row.value !== null);
  assert.equal(valued.length, 82);
  for (const [rowIndex, row] of valued.entries()) {
    assert.equal(row.evidence_status, "registered_historical_reference", `valued row ${rowIndex}`);
    assert.equal(row.null_reason, "", `valued row ${rowIndex} must have no null reason`);
    for (const field of METRIC_SOURCE_FIELDS) {
      const actual = Reflect.get(row, field) as unknown;
      assert.ok(typeof actual === "string" && actual.length > 0, `valued row ${rowIndex} ${field}`);
    }
  }
});

test("A03-STATIC-CONTROL-003 all 78 missing rows retain nonempty reasons", () => {
  const missing = data.metrics.filter((row) => row.value === null);
  assert.equal(missing.length, 78);
  for (const [rowIndex, row] of missing.entries()) {
    assert.equal(row.evidence_status, "evidence_insufficient", `missing row ${rowIndex}`);
    assert.ok(typeof row.null_reason === "string" && row.null_reason.length > 0, `missing row ${rowIndex} null_reason`);
  }
});

interface Inventory {
  inputs: { a02_candidate: { path: string } };
  counts: {
    all_json_empty_strings: number;
    required_null_contract_defects: number;
    other_empty_strings_judged_individually: number;
    source_faithful_empty_exclusion_reasons: number;
    valued_row_empty_null_reasons: number;
  };
  occurrences: Array<{ json_pointer: string }>;
  global_empty_string_replacement_forbidden: boolean;
  old_o15_crosscheck: { same_json_pointers_and_judgments_as_a02: boolean };
}

test("A03-STATIC-CONTROL-004 inventory judges all 589 empty strings and does not globally replace the other 99", () => {
  const inventory = JSON.parse(readFileSync(resolve(A03_DIR, "null_field_inventory.json"), "utf8")) as Inventory;
  assert.equal(inventory.counts.all_json_empty_strings, 589);
  assert.equal(inventory.counts.required_null_contract_defects, 490);
  assert.equal(inventory.counts.other_empty_strings_judged_individually, 99);
  assert.equal(inventory.counts.source_faithful_empty_exclusion_reasons, 17);
  assert.equal(inventory.counts.valued_row_empty_null_reasons, 82);
  assert.equal(inventory.occurrences.length, 589);
  assert.equal(new Set(inventory.occurrences.map((row) => row.json_pointer)).size, 589);
  assert.equal(inventory.global_empty_string_replacement_forbidden, true);
  assert.equal(inventory.old_o15_crosscheck.same_json_pointers_and_judgments_as_a02, true);
  const actualEmptyPointers: string[] = [];
  function walk(value: unknown, parts: string[]): void {
    if (value === "") actualEmptyPointers.push(`/${parts.join("/")}`);
    else if (Array.isArray(value)) value.forEach((item, index) => walk(item, [...parts, String(index)]));
    else if (value && typeof value === "object") {
      for (const [key, item] of Object.entries(value)) walk(item, [...parts, key]);
    }
  }
  const inventoryData = JSON.parse(readFileSync(inventory.inputs.a02_candidate.path, "utf8")) as unknown;
  walk(inventoryData, []);
  assert.deepEqual(actualEmptyPointers.sort(), inventory.occurrences.map((row) => row.json_pointer).sort());
});
