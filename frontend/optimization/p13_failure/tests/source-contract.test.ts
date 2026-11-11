import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import { ownedRoot } from "./helpers.js";

const sourceFiles = readdirSync(resolve(ownedRoot, "src"))
  .filter((name) => name.endsWith(".ts") || name.endsWith(".html") || name.endsWith(".css"))
  .map((name) => resolve(ownedRoot, "src", name));

test("owned browser source contains no network or dynamic-code escape hatch", () => {
  for (const path of sourceFiles) {
    const source = readFileSync(path, "utf8");
    assert.doesNotMatch(source, /\bfetch\s*\(|XMLHttpRequest|WebSocket\s*\(|EventSource\s*\(|sendBeacon\s*\(/, path);
    assert.doesNotMatch(source, /\beval\s*\(|new\s+Function\b|innerHTML|outerHTML|insertAdjacentHTML/, path);
    assert.doesNotMatch(source, /https?:\/\//, path);
  }
});

test("source includes responsive and accessible offline shell", () => {
  const template = readFileSync(resolve(ownedRoot, "src/index.template.html"), "utf8");
  const css = readFileSync(resolve(ownedRoot, "src/style.css"), "utf8");
  assert.match(template, /当前证据不支持 k² 提升 1 个百分点/);
  assert.match(template, /<main\b/);
  assert.match(template, /<h1\b/);
  assert.match(template, /aria-live="polite"/);
  assert.match(template, /<noscript>/);
  assert.match(css, /@media\s*\(max-width:/);
  assert.match(css, /:focus-visible/);
  assert.match(css, /prefers-reduced-motion/);
});

test("source pins O14 identity and does not implement model execution", () => {
  const contract = readFileSync(resolve(ownedRoot, "src/o14_contract.ts"), "utf8");
  assert.match(contract, /C19_negative_control_EG0\.0_FL1\.5_A700_PF1/);
  assert.match(contract, /s_c454ac7ec8a2a7f8/);
  const allSource = sourceFiles.map((path) => readFileSync(path, "utf8")).join("\n");
  assert.doesNotMatch(allSource, /tensorflow|torch\.|sklearn|comsol|bootstrap\s*\(|predict\s*\(/i);
});
