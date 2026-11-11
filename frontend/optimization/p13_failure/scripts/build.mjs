import { build } from "esbuild";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, isAbsolute, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ownedDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(ownedDir, "../../..");
const deliveryDir = resolve(repoRoot, "backend/surrogate_data/optimization_p13_20261027_r01/delivery");
const recoveryRoot = resolve(repoRoot, "backend/surrogate_data/optimization_p13_20261027_r01/resume_20261029_r01/recovery_20261102");
const args = process.argv.slice(2);
if (args.length > 1 || (args.length === 1 && args[0] !== "--check")) {
  throw new Error(`Unknown build arguments: ${args.join(" ")}; only --check is supported`);
}

function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required; recovery builds refuse implicit output paths`);
  return value;
}

function requiredAbsolutePath(name) {
  const value = required(name);
  if (!isAbsolute(value)) throw new Error(`${name} must be an absolute path`);
  return resolve(value);
}

function assertInside(path, root, label) {
  const rel = relative(root, path);
  if (!rel || rel.startsWith("..") || isAbsolute(rel)) {
    throw new Error(`${label} must be a descendant of current A_RUN: ${root}`);
  }
}

const registeredRecoveryScopePath = resolve(recoveryRoot, "attempt_001/A02/recovery_scope.json");
const registeredRecoveryScopeSha256 = "c05014a55235cc9852eaf710e4c79feb96431d4c82008137c2dcc34756cc3613";

function assertRegisteredRecoveryScope(taskAttempt, aRun) {
  let scopeBytes;
  try {
    scopeBytes = readFileSync(registeredRecoveryScopePath);
  } catch {
    throw new Error(`A02-registered recovery scope missing or unreadable: ${registeredRecoveryScopePath}`);
  }
  if (createHash("sha256").update(scopeBytes).digest("hex") !== registeredRecoveryScopeSha256) {
    throw new Error("A02-registered recovery scope hash mismatch");
  }
  const scope = JSON.parse(scopeBytes.toString("utf8"));
  const a00Path = resolve(aRun, "A00/write_scope.json");
  if (scope.schema !== "aln_p13_recovery_a02_runtime_scope_v1"
    || scope.task_id !== "A02"
    || scope.attempt_id !== "attempt_002"
    || scope.recovery_attempt_id !== taskAttempt
    || scope.a_run !== aRun
    || scope.a00_write_scope?.path !== a00Path) {
    throw new Error("Recovery scope mismatch: selected A_RUN is not the current A00/A02 scope");
  }
  const a00Bytes = readFileSync(a00Path);
  const a00 = JSON.parse(a00Bytes.toString("utf8"));
  if (a00Bytes.length !== scope.a00_write_scope.bytes
    || createHash("sha256").update(a00Bytes).digest("hex") !== scope.a00_write_scope.sha256
    || a00.schema !== "aln_p13_recovery_write_scope_v1"
    || a00.attempt_id !== taskAttempt
    || a00.a_run !== aRun) {
    throw new Error("Current A00 recovery identity mismatch");
  }
}

const taskAttempt = required("ALN_P13_TASK_ATTEMPT");
if (!/^attempt_[A-Za-z0-9][A-Za-z0-9._-]*$/.test(taskAttempt) || taskAttempt.includes("..")) {
  throw new Error(`Invalid ALN_P13_TASK_ATTEMPT: ${taskAttempt}`);
}
const aRun = resolve(recoveryRoot, taskAttempt);
const viewerDir = requiredAbsolutePath("ALN_P13_VIEWER_OUT");
const taskWork = requiredAbsolutePath("ALN_P13_TASK_WORK");
assertInside(viewerDir, aRun, "ALN_P13_VIEWER_OUT");
assertInside(taskWork, aRun, "ALN_P13_TASK_WORK");
assertRegisteredRecoveryScope(taskAttempt, aRun);
const expectedHashes = {
  "candidates.csv": "32eedf2acc5c54508d16b41ab79522df1d6ebcca95648580d38f627d404fc539",
  "metrics.csv": "10b281c30b530b62d8d34ba8361175031133a09d70cf4d8fc137d0f14175fbb7",
  "decision.json": "b93a563a354d7914db7755a9328b1367fd82547130e61b1f5afcc3f49692bf76",
};

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function descriptor(path, fileId) {
  const bytes = readFileSync(path);
  return { path, bytes: bytes.length, sha256: sha256(bytes), file_id: fileId };
}

function parseCsv(text) {
  const records = [];
  let record = [];
  let field = "";
  let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (quoted) {
      if (char === '"' && text[index + 1] === '"') {
        field += '"';
        index += 1;
      } else if (char === '"') {
        quoted = false;
      } else {
        field += char;
      }
    } else if (char === '"' && field.length === 0) {
      quoted = true;
    } else if (char === ",") {
      record.push(field);
      field = "";
    } else if (char === "\n") {
      record.push(field.endsWith("\r") ? field.slice(0, -1) : field);
      records.push(record);
      record = [];
      field = "";
    } else {
      field += char;
    }
  }
  if (quoted) throw new Error("Unclosed CSV quote");
  if (field.length > 0 || record.length > 0) {
    record.push(field.endsWith("\r") ? field.slice(0, -1) : field);
    records.push(record);
  }
  const headers = records.shift();
  if (!headers) throw new Error("CSV header missing");
  const rows = records.map((cells) => {
    if (cells.length !== headers.length) throw new Error("CSV column count mismatch");
    return Object.fromEntries(headers.map((header, index) => [header, cells[index]]));
  });
  return { headers, rows };
}

for (const [name, expected] of Object.entries(expectedHashes)) {
  const actual = descriptor(resolve(deliveryDir, name), name);
  if (actual.sha256 !== expected) throw new Error(`O14 hash mismatch: ${name}`);
}

const manifestPath = resolve(deliveryDir, "t15_artifact_manifest.json");
const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
for (const output of manifest.outputs) {
  if (expectedHashes[output.file_id] !== output.sha256) throw new Error(`T15 manifest mismatch: ${output.file_id}`);
}
const candidatesCsv = parseCsv(readFileSync(resolve(deliveryDir, "candidates.csv"), "utf8"));
const metricsCsv = parseCsv(readFileSync(resolve(deliveryDir, "metrics.csv"), "utf8"));
if (candidatesCsv.rows.length !== 20 || metricsCsv.rows.length !== 160) throw new Error("O14 row counts changed");

const CANDIDATE_NULL_FIELDS = new Set([
  "conservative_value",
  "error_margin",
  "extrapolation_distance",
  "testable_max_distance",
  "neighborhood_result",
]);
const METRIC_SOURCE_NULL_FIELDS = new Set([
  "source_file_id",
  "source_sha256",
  "source_row_key",
  "scope",
  "support_label",
]);

const candidates = candidatesCsv.rows.map((row) => Object.fromEntries(
  Object.entries(row).map(([field, value]) => [
    field,
    field === "grade" || (CANDIDATE_NULL_FIELDS.has(field) && value === "") ? null : value,
  ]),
));
const metrics = metricsCsv.rows.map((row) => Object.fromEntries(
  Object.entries(row).map(([field, value]) => [
    field,
    (field === "value" && value === "")
      || (row.value === "" && METRIC_SOURCE_NULL_FIELDS.has(field) && value === "")
      ? null
      : value,
  ]),
));
const data = {
  schema: "aln_optimization_p13_o15_browser_data_v1",
  task_id: "T16",
  output_id: "O15",
  attempt_id: taskAttempt,
  source_output_id: "O14",
  candidate_columns: candidatesCsv.headers,
  metric_columns: metricsCsv.headers,
  candidates,
  metrics,
  decision: JSON.parse(readFileSync(resolve(deliveryDir, "decision.json"), "utf8")),
  source_traceability: {
    o14_delivery: ["candidates.csv", "metrics.csv", "decision.json"].map((name) => descriptor(resolve(deliveryDir, name), name)),
    t15_manifest: descriptor(manifestPath, "t15_artifact_manifest.json"),
    registered_inputs: manifest.inputs,
  },
};
const jsonText = `${JSON.stringify(data, null, 2)}\n`;

const bundle = await build({
  entryPoints: [resolve(ownedDir, "src/app.ts")],
  bundle: true,
  write: false,
  format: "iife",
  platform: "browser",
  target: ["es2020"],
  minify: false,
  sourcemap: false,
  legalComments: "none",
});
const js = bundle.outputFiles[0].text.replace(/<\/script/gi, "<\\/script");
const css = readFileSync(resolve(ownedDir, "src/style.css"), "utf8");
const embeddedJson = JSON.stringify(data).replace(/</g, "\\u003c").replace(/\u2028|\u2029/g, (char) => char === "\u2028" ? "\\u2028" : "\\u2029");
const template = readFileSync(resolve(ownedDir, "src/index.template.html"), "utf8");
for (const token of ["<!-- INLINE_CSS -->", "<!-- BROWSER_DATA -->", "<!-- INLINE_JS -->"]) {
  if (!template.includes(token)) throw new Error(`Missing template token: ${token}`);
}
const html = template
  .replace("<!-- INLINE_CSS -->", () => css)
  .replace("<!-- BROWSER_DATA -->", () => embeddedJson)
  .replace("<!-- INLINE_JS -->", () => js);
const outputs = new Map([
  [resolve(viewerDir, "browser_data.json"), jsonText],
  [resolve(viewerDir, "index.html"), html],
]);

if (process.argv.includes("--check")) {
  for (const [path, expected] of outputs) {
    if (!existsSync(path) || readFileSync(path, "utf8") !== expected) throw new Error(`Build output stale: ${path}`);
  }
  console.log("O15 build check PASS: index.html and browser_data.json are reproducible");
} else {
  mkdirSync(viewerDir, { recursive: true });
  for (const [path, content] of outputs) writeFileSync(path, content, "utf8");
  console.log(JSON.stringify({
    status: "PASS",
    candidates: candidates.length,
    metrics: metrics.length,
    outputs: [...outputs.keys()].map((path) => descriptor(path, path.endsWith(".json") ? "browser_data.json" : "index.html")),
    attempt_id: taskAttempt,
    a_run: aRun,
    task_work: taskWork,
  }, null, 2));
}
