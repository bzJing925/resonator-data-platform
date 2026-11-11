import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, isAbsolute, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const cwd = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(cwd, "../../..");
const recoveryRoot = resolve(
  repoRoot,
  "backend/surrogate_data/optimization_p13_20261027_r01/resume_20261029_r01/recovery_20261102",
);
const cliArgs = process.argv.slice(2);
if (cliArgs.length > 0) throw new Error(`Unknown verify arguments: ${cliArgs.join(" ")}`);

function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required; verification refuses implicit output paths`);
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
const workDir = requiredAbsolutePath("ALN_P13_TASK_WORK");
assertInside(viewerDir, aRun, "ALN_P13_VIEWER_OUT");
assertInside(workDir, aRun, "ALN_P13_TASK_WORK");
assertRegisteredRecoveryScope(taskAttempt, aRun);
const logDir = resolve(workDir, "logs");
const tsbuildDir = resolve(workDir, "tsbuild");
const node = process.execPath;
const tsc = resolve(cwd, "../../node_modules/.bin/tsc");
const playwright = resolve(cwd, "../../node_modules/.bin/playwright");
mkdirSync(logDir, { recursive: true });

const checks = [
  { name: "strict_compile", executable: tsc, args: ["-p", "tsconfig.json", "--outDir", tsbuildDir] },
  { name: "build", executable: node, args: ["scripts/build.mjs"] },
  { name: "build_check", executable: node, args: ["scripts/build.mjs", "--check"] },
  { name: "test_source", executable: node, args: ["--test", resolve(tsbuildDir, "tests/source-contract.test.js")] },
  { name: "test_build", executable: node, args: ["--test", resolve(tsbuildDir, "tests/build-contract.test.js")] },
  { name: "test_static", executable: node, args: ["--test", resolve(tsbuildDir, "tests/static-contract.test.js")] },
  { name: "test_validation", executable: node, args: ["--test", resolve(tsbuildDir, "tests/validation.test.js")] },
  { name: "test_entrypoints", executable: node, args: ["--test", resolve(tsbuildDir, "tests/entrypoint-isolation.test.js")] },
  {
    name: "browser_focused",
    executable: playwright,
    args: [
      "test",
      "-c",
      "playwright.config.ts",
      `--output=${resolve(workDir, "playwright_output/final")}`,
      "--grep",
      "offline report shows|desktop and narrow layouts|valid JSON import",
    ],
  },
];

function sha256(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

function quote(value) {
  return /^[A-Za-z0-9_./=:-]+$/.test(value) ? value : `'${value.replaceAll("'", `'"'"'`)}'`;
}

function nodeSummary(log) {
  const tests = log.match(/ℹ tests (\d+)/);
  const passed = log.match(/ℹ pass (\d+)/);
  const failed = log.match(/ℹ fail (\d+)/);
  const skipped = log.match(/ℹ skipped (\d+)/);
  return {
    summary_found: Boolean(tests && passed && failed && skipped),
    tests: Number(tests?.[1] ?? 0),
    passed: Number(passed?.[1] ?? 0),
    failed: Number(failed?.[1] ?? 0),
    skipped: Number(skipped?.[1] ?? 0),
  };
}

function playwrightSummary(log) {
  const passed = Number(log.match(/(\d+) passed/)?.[1] ?? 0);
  const failed = Number(log.match(/(\d+) failed/)?.[1] ?? 0);
  const skipped = Number(log.match(/(\d+) skipped/)?.[1] ?? 0);
  return {
    scope: "main-metric selector, valid import, C19 separation, and 390px narrow overflow/local scrolling",
    summary_found: /(\d+) passed/.test(log),
    tests: passed + failed + skipped,
    passed,
    failed,
    skipped,
  };
}

const receipts = [];
for (const check of checks) {
  const logPath = resolve(logDir, `final_${check.name}.log`);
  const exitPath = resolve(logDir, `final_${check.name}.exit_code`);
  const result = spawnSync(check.executable, check.args, {
    cwd,
    env: { ...process.env, NO_COLOR: "1" },
    encoding: "utf8",
    maxBuffer: 16 * 1024 * 1024,
  });
  const exitCode = result.status ?? 1;
  writeFileSync(logPath, `${result.stdout ?? ""}${result.stderr ?? ""}`, "utf8");
  writeFileSync(exitPath, `${exitCode}\n`, "utf8");
  receipts.push({
    name: check.name,
    command: [check.executable, ...check.args].map(quote).join(" "),
    executable: check.executable,
    args: check.args,
    cwd,
    exit_code: exitCode,
    log_path: logPath,
    log_sha256: sha256(logPath),
    exit_code_file: exitPath,
    exit_code_file_sha256: sha256(exitPath),
  });
  console.log(`${check.name}: exit ${exitCode}`);
}

const nodeTests = { summary_found: true, tests: 0, passed: 0, failed: 0, skipped: 0 };
for (const receipt of receipts.filter((item) => item.name.startsWith("test_"))) {
  const summary = nodeSummary(readFileSync(receipt.log_path, "utf8"));
  nodeTests.summary_found &&= summary.summary_found;
  nodeTests.tests += summary.tests;
  nodeTests.passed += summary.passed;
  nodeTests.failed += summary.failed;
  nodeTests.skipped += summary.skipped;
}
const focusedPlaywright = playwrightSummary(readFileSync(resolve(logDir, "final_browser_focused.log"), "utf8"));
const passed = receipts.every((receipt) => receipt.exit_code === 0)
  && nodeTests.summary_found
  && nodeTests.tests > 0
  && nodeTests.passed === nodeTests.tests
  && nodeTests.failed === 0
  && nodeTests.skipped === 0
  && focusedPlaywright.summary_found
  && focusedPlaywright.tests > 0
  && focusedPlaywright.passed === focusedPlaywright.tests
  && focusedPlaywright.failed === 0
  && focusedPlaywright.skipped === 0;
const payload = {
  schema: "aln_optimization_p13_t16_command_receipts_v2",
  task_id: "T16",
  attempt_id: taskAttempt,
  status: passed ? "PASS" : "FAIL",
  a_run: aRun,
  viewer_out: viewerDir,
  task_work: workDir,
  entrypoint_env: {
    ALN_P13_VIEWER_OUT: viewerDir,
    ALN_P13_TASK_WORK: workDir,
    ALN_P13_TASK_ATTEMPT: taskAttempt,
  },
  playwright_executed: true,
  node_tests: nodeTests,
  focused_playwright: focusedPlaywright,
  commands: receipts,
};
const receiptPath = resolve(workDir, "command_receipts.json");
writeFileSync(receiptPath, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
console.log(`${payload.status}: ${receiptPath}`);
if (payload.status !== "PASS") process.exitCode = 1;
