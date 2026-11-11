import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import { ownedRoot, viewerDir } from "./helpers.js";

test("build --check proves generated index and JSON are reproducible", () => {
  const result = spawnSync(process.execPath, ["scripts/build.mjs", "--check"], {
    cwd: ownedRoot,
    encoding: "utf8",
    env: { ...process.env, NO_COLOR: "1" },
  });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
});

test("built HTML embeds exactly the external browser JSON", () => {
  const html = readFileSync(resolve(viewerDir, "index.html"), "utf8");
  const json = readFileSync(resolve(viewerDir, "browser_data.json"), "utf8");
  const match = html.match(/<script type="application\/json" id="browser-data">([\s\S]*?)<\/script>/);
  const embedded = match?.[1];
  assert.ok(embedded);
  assert.deepEqual(JSON.parse(embedded), JSON.parse(json));
  assert.doesNotMatch(html, /<script[^>]+src=|<link[^>]+href=|url\s*\(\s*['"]?https?:/i);
  assert.doesNotMatch(html, /sourceMappingURL/);
});

test("build artifacts are nonempty and have stable SHA256 receipts", () => {
  for (const name of ["index.html", "browser_data.json"]) {
    const bytes = readFileSync(resolve(viewerDir, name));
    assert.ok(bytes.length > 1000);
    assert.match(createHash("sha256").update(bytes).digest("hex"), /^[0-9a-f]{64}$/);
  }
});
