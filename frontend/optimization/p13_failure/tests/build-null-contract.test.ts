import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import type { BrowserData } from "../src/types.js";
import { ownedRoot, readViewerData, readViewerDataText, viewerDir } from "./helpers.js";
import { collectNullViolations } from "./null-contract-helpers.js";

test("A03-BUILD-CONTROL-001 unfixed build remains reproducible through A02-safe --check", () => {
  const result = spawnSync(process.execPath, ["scripts/build.mjs", "--check"], {
    cwd: ownedRoot,
    encoding: "utf8",
    env: { ...process.env, NO_COLOR: "1" },
    maxBuffer: 16 * 1024 * 1024,
  });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
});

test("A03-BUILD-NULL-001 generated sidecar explicitly detects all 490 legacy null-contract violations", () => {
  const violations = collectNullViolations(readViewerData() as BrowserData);
  const candidate = violations.filter((row) => row.kind === "candidate").length;
  const metric = violations.filter((row) => row.kind === "null_metric_source").length;
  assert.equal(
    violations.length,
    0,
    `legacy detector found total=${violations.length}, candidate=${candidate}, null_metric_source=${metric}; H04 requires 0`,
  );
});

test("A03-BUILD-CONTROL-002 HTML embedded JSON is value-equivalent to sidecar JSON", () => {
  const html = readFileSync(resolve(viewerDir, "index.html"), "utf8");
  const match = html.match(/<script type="application\/json" id="browser-data">([\s\S]*?)<\/script>/);
  assert.ok(match?.[1], "embedded browser-data script missing");
  assert.deepEqual(JSON.parse(match[1]), JSON.parse(readViewerDataText()));
});
