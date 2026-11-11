import { createHash } from "node:crypto";
import { existsSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { dirname, isAbsolute, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { arch, platform, release } from "node:os";

const cwd = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(cwd, "../../..");
const optimizationDir = resolve(repoRoot, "backend/surrogate_data/optimization_p13_20261027_r01");
const deliveryDir = resolve(optimizationDir, "delivery");
const recoveryRoot = resolve(optimizationDir, "resume_20261029_r01/recovery_20261102");
const cliArgs = process.argv.slice(2);
if (cliArgs.length > 0) throw new Error(`Unknown finalize arguments: ${cliArgs.join(" ")}`);

function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required; finalization refuses implicit output paths`);
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
    throw new Error(`${label} must be inside ${root}`);
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
const workDir = requiredAbsolutePath("ALN_P13_TASK_WORK");
assertInside(viewerDir, aRun, "ALN_P13_VIEWER_OUT must be inside current A_RUN");
assertInside(workDir, aRun, "ALN_P13_TASK_WORK must be inside current A_RUN");
assertRegisteredRecoveryScope(taskAttempt, aRun);
const logDir = resolve(workDir, "logs");
const receiptPath = resolve(workDir, "command_receipts.json");
assertInside(receiptPath, workDir, "Receipt path");
if (!existsSync(receiptPath)) throw new Error(`Receipt not found in current task work: ${receiptPath}`);
const receipts = JSON.parse(readFileSync(receiptPath, "utf8"));
if (receipts.schema !== "aln_optimization_p13_t16_command_receipts_v2") {
  throw new Error("Finalization requires the current-attempt receipt schema v2");
}
if (receipts.attempt_id !== taskAttempt) throw new Error("Receipt attempt mismatch");
if (receipts.a_run !== aRun) throw new Error("Receipt A_RUN mismatch");
if (receipts.task_work !== workDir) throw new Error("Receipt task_work mismatch");
if (receipts.viewer_out !== viewerDir) throw new Error("Receipt viewer_out mismatch");
if (receipts.status !== "PASS" || !Array.isArray(receipts.commands)
  || !receipts.commands.every((item) => item.exit_code === 0)) {
  throw new Error("Final verification has not passed");
}

function sha256(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

function descriptor(path, fileId) {
  const bytes = readFileSync(path);
  return { path, bytes: bytes.length, sha256: createHash("sha256").update(bytes).digest("hex"), file_id: fileId };
}

for (const command of receipts.commands) {
  if (command.cwd !== cwd) throw new Error(`Receipt command cwd mismatch: ${command.name}`);
  assertInside(command.log_path, logDir, `Receipt log path: ${command.name}`);
  assertInside(command.exit_code_file, logDir, `Receipt exit-code path: ${command.name}`);
  if (sha256(command.log_path) !== command.log_sha256
    || sha256(command.exit_code_file) !== command.exit_code_file_sha256) {
    throw new Error(`Receipt log hash mismatch: ${command.name}`);
  }
  if (Number(readFileSync(command.exit_code_file, "utf8").trim()) !== command.exit_code) {
    throw new Error(`Receipt exit-code mismatch: ${command.name}`);
  }
}

function nodeSummary(log) {
  const tests = log.match(/ℹ tests (\d+)/);
  const passed = log.match(/ℹ pass (\d+)/);
  const failed = log.match(/ℹ fail (\d+)/);
  const skipped = log.match(/ℹ skipped (\d+)/);
  if (!tests || !passed || !failed || !skipped) throw new Error("Missing Node test summary");
  return {
    tests: Number(tests[1]), passed: Number(passed[1]),
    failed: Number(failed[1]), skipped: Number(skipped[1]),
  };
}

function playwrightSummary(log) {
  const passedMatch = log.match(/(\d+) passed/);
  if (!passedMatch) throw new Error("Missing Playwright test summary");
  const passed = Number(passedMatch[1]);
  const failed = Number(log.match(/(\d+) failed/)?.[1] ?? 0);
  const skipped = Number(log.match(/(\d+) skipped/)?.[1] ?? 0);
  return { tests: passed + failed + skipped, passed, failed, skipped };
}

const nodeTests = { tests: 0, passed: 0, failed: 0, skipped: 0 };
for (const command of receipts.commands) {
  if (!command.name.startsWith("test_")) continue;
  const summary = nodeSummary(readFileSync(command.log_path, "utf8"));
  nodeTests.tests += summary.tests;
  nodeTests.passed += summary.passed;
  nodeTests.failed += summary.failed;
  nodeTests.skipped += summary.skipped;
}
const focusedCommand = receipts.commands.find((command) => command.name === "browser_focused");
if (!focusedCommand) throw new Error("Focused Playwright command receipt missing");
const focusedPlaywright = playwrightSummary(readFileSync(focusedCommand.log_path, "utf8"));
for (const key of ["tests", "passed", "failed", "skipped"]) {
  if (receipts.node_tests?.[key] !== nodeTests[key]) throw new Error(`Node ${key} count differs from logs`);
  if (receipts.focused_playwright?.[key] !== focusedPlaywright[key]) {
    throw new Error(`Playwright ${key} count differs from logs`);
  }
}
if (nodeTests.tests === 0 || nodeTests.passed !== nodeTests.tests
  || nodeTests.failed !== 0 || nodeTests.skipped !== 0) {
  throw new Error(`Node verification has not fully passed: ${nodeTests.passed}/${nodeTests.tests}`);
}
if (!receipts.playwright_executed || focusedPlaywright.tests === 0
  || focusedPlaywright.passed !== focusedPlaywright.tests
  || focusedPlaywright.failed !== 0 || focusedPlaywright.skipped !== 0) {
  throw new Error(`Focused Playwright verification has not fully passed: ${focusedPlaywright.passed}/${focusedPlaywright.tests}`);
}

function packageVersion(name) {
  const path = resolve(repoRoot, `frontend/node_modules/${name}/package.json`);
  return JSON.parse(readFileSync(path, "utf8")).version;
}

const inputPaths = [
  ["O14_GD_REVIEW", resolve(optimizationDir, "resume_20261029_r01/verification/gd_o14_review.json")],
  ["H04_CONTRACT", resolve(optimizationDir, "resume_20261029_r01/contracts/failure_delivery_contract.md")],
  ["O14_CANDIDATES", resolve(deliveryDir, "candidates.csv")],
  ["O14_METRICS", resolve(deliveryDir, "metrics.csv")],
  ["O14_DECISION", resolve(deliveryDir, "decision.json")],
  ["T15_ARTIFACT_MANIFEST", resolve(deliveryDir, "t15_artifact_manifest.json")],
];
const inputs = inputPaths.map(([id, path]) => descriptor(path, id));
const expectedInputHashes = {
  O14_GD_REVIEW: "9b0e2c9fa6937e632816044f1583dc46d29f7f69c2e3d129b8038fe40ce7b396",
  H04_CONTRACT: "f91c816789e54360472acf4961b958cbf236fb8cf61a8bfa9d0d7d7ec2a004c5",
  O14_CANDIDATES: "32eedf2acc5c54508d16b41ab79522df1d6ebcca95648580d38f627d404fc539",
  O14_METRICS: "10b281c30b530b62d8d34ba8361175031133a09d70cf4d8fc137d0f14175fbb7",
  O14_DECISION: "b93a563a354d7914db7755a9328b1367fd82547130e61b1f5afcc3f49692bf76",
  T15_ARTIFACT_MANIFEST: "5e891425e5f3799da5da8f5ef5e7d9537a21c4f9fd8dbbafc689f097684bea38",
};
for (const input of inputs) {
  if (input.sha256 !== expectedInputHashes[input.file_id]) throw new Error(`Frozen input hash changed: ${input.file_id}`);
}

const data = JSON.parse(readFileSync(resolve(viewerDir, "browser_data.json"), "utf8"));
if (data.attempt_id !== taskAttempt) throw new Error("Candidate viewer attempt mismatch");
const outputs = [
  descriptor(resolve(viewerDir, "index.html"), "index.html"),
  descriptor(resolve(viewerDir, "browser_data.json"), "browser_data.json"),
];

function walk(root) {
  const files = [];
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    if (entry.name === ".build" || entry.name === "node_modules") continue;
    const path = resolve(root, entry.name);
    if (entry.isDirectory()) files.push(...walk(path));
    else if (entry.isFile()) files.push(path);
  }
  return files.sort();
}
const sourceFiles = walk(cwd).map((path) => descriptor(path, relative(cwd, path)));
const logFiles = walk(logDir).map((path) => descriptor(path, relative(workDir, path)));
const manifest = {
  schema: "aln_optimization_p13_t16_artifact_manifest_v2",
  task_id: "T16",
  output_id: "O15",
  attempt_id: taskAttempt,
  status: "PASS",
  execution_status: "PASS",
  candidate_only: true,
  candidate_paths: {
    viewer_dir: viewerDir,
    index_html: resolve(viewerDir, "index.html"),
    browser_data_json: resolve(viewerDir, "browser_data.json"),
    command_receipts: receiptPath,
    t16_artifact_manifest: resolve(workDir, "t16_artifact_manifest.json"),
  },
  planned_promotion_paths: {
    status: "PLANNED_ONLY_NOT_PROMOTED",
    viewer_dir: resolve(optimizationDir, "viewer"),
    index_html: resolve(optimizationDir, "viewer/index.html"),
    browser_data_json: resolve(optimizationDir, "viewer/browser_data.json"),
  },
  acceptance_scope: "Isolated candidate O15 offline viewer, strict compile, reproducible build, static O14 parity/import validation, actual-count Node tests, and focused browser checks; not full T18/G6-S acceptance",
  scientific_status: "REJECTED",
  decision: data.decision.decision,
  reason: data.decision.reason,
  original_gr0: "BLOCKED",
  gr0_s_scope: "P13_SCOPED_FAILURE_DELIVERY_ONLY",
  created_at: new Date().toISOString(),
  eligible_models: [],
  recommendation_count: 0,
  grade: null,
  grade_null_reason: "no_eligible_models",
  grade_counts: { A: 0, B: 0, C: 0 },
  trusted_for_unmeasured_optimization: false,
  a_run: aRun,
  entrypoint_env: {
    ALN_P13_VIEWER_OUT: viewerDir,
    ALN_P13_TASK_WORK: workDir,
    ALN_P13_TASK_ATTEMPT: taskAttempt,
  },
  o14_freeze: {
    gd: "PASS",
    scientific_status: "REJECTED",
    review_file_id: "O14_GD_REVIEW",
    review_sha256: expectedInputHashes.O14_GD_REVIEW,
    modified_by_t16: false,
  },
  inputs,
  input_hash_validation: { status: "PASS", all_match: true, count: inputs.length },
  outputs,
  source_files: sourceFiles,
  source_sha256: Object.fromEntries(sourceFiles.map((item) => [item.file_id, item.sha256])),
  verification_receipts: descriptor(receiptPath, "command_receipts.json"),
  commands: receipts.commands,
  command: "node scripts/finalize.mjs",
  development_log_receipts: logFiles,
  environment: {
    cwd,
    node: process.version,
    node_executable: process.execPath,
    platform: `${platform()}-${arch()}`,
    os_release: release(),
    packages: {
      typescript: packageVersion("typescript"),
      esbuild: packageVersion("esbuild"),
      playwright_test: packageVersion("@playwright/test"),
      types_node: packageVersion("@types/node"),
    },
    no_color: "1",
    dependency_install_or_sync_performed: false,
  },
  validation: {
    status: "PASS",
    strict_typescript: "PASS",
    build: "PASS",
    build_reproducibility: "PASS",
    source_contract: "PASS",
    static_contract: "PASS",
    import_validation: "PASS",
    entrypoint_isolation: "PASS",
    candidate_rows: data.candidates.length,
    nonnegative_control_rows: data.candidates.filter((row) => row.control_type === "nonnegative_control").length,
    negative_control_rows: data.candidates.filter((row) => row.control_type === "negative_control").length,
    metric_rows: data.metrics.length,
    valued_metric_rows: data.metrics.filter((row) => row.value !== null).length,
    null_metric_rows: data.metrics.filter((row) => row.value === null).length,
    registered_input_descriptors_pinned: data.source_traceability.registered_inputs.length,
    node_tests: nodeTests,
    test_passed_total: nodeTests.passed,
    test_total: nodeTests.tests,
    focused_playwright: { ...receipts.focused_playwright, ...focusedPlaywright },
    counts_derived_from_current_logs: true,
    o14_full_precision_cell_parity: true,
    display_rounding_only: true,
    inline_external_json_semantic_identity: true,
    c19_structure_id: "s_c454ac7ec8a2a7f8",
    c19_measured_values_separated_from_model_predictions: true,
  },
  acceptance: {
    gd_claimed: false,
    t18_browser_acceptance: "NOT_RUN_NOT_CLAIMED",
    playwright_specs_created: true,
    playwright_config_created: true,
    playwright_executed: true,
    playwright_scope: receipts.focused_playwright.scope,
  },
  historical_evidence: "Preserved by A02 backup; old receipts and old failed-attempt files are not finalization inputs",
  not_executed: {
    neighborhood: data.decision.neighborhood,
    random_process: data.decision.random_process,
    search_status: data.decision.search_status,
    training: false,
    outer_prediction: false,
    comsol: false,
  },
  write_scope: [
    resolve(repoRoot, "frontend/optimization/p13_failure/**"),
    resolve(viewerDir, "**"),
    resolve(workDir, "**"),
  ],
  exit_code: 0,
};
if (!statSync(resolve(cwd, "tests/browser/viewer.spec.ts")).isFile()) throw new Error("Playwright spec missing");
const manifestPath = resolve(workDir, "t16_artifact_manifest.json");
assertInside(manifestPath, workDir, "Manifest path");
writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
console.log(JSON.stringify({
  status: "PASS",
  node_tests: `${nodeTests.passed}/${nodeTests.tests}`,
  focused_playwright: `${focusedPlaywright.passed}/${focusedPlaywright.tests}`,
  manifest: descriptor(manifestPath, "t16_artifact_manifest.json"),
  outputs,
}, null, 2));
