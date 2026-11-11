import { defineConfig } from "@playwright/test";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { isAbsolute, relative, resolve } from "node:path";

const ownedRoot = process.cwd();
const repoRoot = resolve(ownedRoot, "../../..");
const recoveryRoot = resolve(
  repoRoot,
  "backend/surrogate_data/optimization_p13_20261027_r01/resume_20261029_r01/recovery_20261102",
);

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required; browser tests refuse implicit output paths`);
  return value;
}

function requiredAbsolutePath(name: string): string {
  const value = required(name);
  if (!isAbsolute(value)) throw new Error(`${name} must be an absolute path`);
  return resolve(value);
}

function assertInside(path: string, root: string, label: string): void {
  const rel = relative(root, path);
  if (!rel || rel.startsWith("..") || isAbsolute(rel)) {
    throw new Error(`${label} must be inside ${root}`);
  }
}

const registeredRecoveryScopePath = resolve(recoveryRoot, "attempt_001/A02/recovery_scope.json");
const registeredRecoveryScopeSha256 = "c05014a55235cc9852eaf710e4c79feb96431d4c82008137c2dcc34756cc3613";

type RecoveryScope = {
  schema: string;
  task_id: string;
  attempt_id: string;
  recovery_attempt_id: string;
  a_run: string;
  a00_write_scope: { path: string; bytes: number; sha256: string };
};

function assertRegisteredRecoveryScope(taskAttempt: string, aRun: string): void {
  let scopeBytes: Buffer;
  try {
    scopeBytes = readFileSync(registeredRecoveryScopePath);
  } catch {
    throw new Error(`A02-registered recovery scope missing or unreadable: ${registeredRecoveryScopePath}`);
  }
  if (createHash("sha256").update(scopeBytes).digest("hex") !== registeredRecoveryScopeSha256) {
    throw new Error("A02-registered recovery scope hash mismatch");
  }
  const scope = JSON.parse(scopeBytes.toString("utf8")) as RecoveryScope;
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
  const a00 = JSON.parse(a00Bytes.toString("utf8")) as { schema: string; attempt_id: string; a_run: string };
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
const viewerOut = requiredAbsolutePath("ALN_P13_VIEWER_OUT");
const taskWork = requiredAbsolutePath("ALN_P13_TASK_WORK");
assertInside(viewerOut, aRun, "ALN_P13_VIEWER_OUT must be inside current A_RUN");
assertInside(taskWork, aRun, "ALN_P13_TASK_WORK must be inside current A_RUN");
assertRegisteredRecoveryScope(taskAttempt, aRun);

const args = process.argv.slice(2);
for (const [index, arg] of args.entries()) {
  let output: string | undefined;
  if (arg === "--output") output = args[index + 1];
  if (arg.startsWith("--output=")) output = arg.slice("--output=".length);
  if (output !== undefined) {
    if (!output || !isAbsolute(output)) throw new Error("Playwright --output must be an absolute path");
    assertInside(resolve(output), taskWork, "Playwright --output must be inside current task work");
  }
}

export default defineConfig({
  testDir: "./tests/browser",
  outputDir: resolve(taskWork, "playwright_output"),
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [
    ["list"],
    ["json", { outputFile: resolve(taskWork, "receipts/playwright_report.json") }],
  ],
  metadata: {
    task_attempt: taskAttempt,
    a_run: aRun,
    viewer_out: viewerOut,
    task_work: taskWork,
  },
  use: {
    browserName: "chromium",
    headless: true,
    offline: true,
    viewport: { width: 1440, height: 1000 },
    actionTimeout: 10_000,
    navigationTimeout: 10_000,
    trace: "retain-on-failure",
  },
});
