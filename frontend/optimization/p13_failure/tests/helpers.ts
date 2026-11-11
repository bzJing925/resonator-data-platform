import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { isAbsolute, relative, resolve } from "node:path";
import { pathToFileURL } from "node:url";

export const ownedRoot = process.cwd();
export const repoRoot = resolve(ownedRoot, "../../..");
export const deliveryDir = resolve(
  repoRoot,
  "backend/surrogate_data/optimization_p13_20261027_r01/delivery",
);
const recoveryRoot = resolve(
  repoRoot,
  "backend/surrogate_data/optimization_p13_20261027_r01/resume_20261029_r01/recovery_20261102",
);

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required; tests refuse implicit output paths`);
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
    throw new Error(`${label} must be a descendant of current A_RUN: ${root}`);
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

export const taskAttempt = required("ALN_P13_TASK_ATTEMPT");
if (!/^attempt_[A-Za-z0-9][A-Za-z0-9._-]*$/.test(taskAttempt) || taskAttempt.includes("..")) {
  throw new Error(`Invalid ALN_P13_TASK_ATTEMPT: ${taskAttempt}`);
}
export const aRun = resolve(recoveryRoot, taskAttempt);
export const viewerDir = requiredAbsolutePath("ALN_P13_VIEWER_OUT");
export const taskWork = requiredAbsolutePath("ALN_P13_TASK_WORK");
assertInside(viewerDir, aRun, "ALN_P13_VIEWER_OUT");
assertInside(taskWork, aRun, "ALN_P13_TASK_WORK");
assertRegisteredRecoveryScope(taskAttempt, aRun);

export function browserPaths(): { viewerDir: string; indexUrl: string; validText: string } {
  return {
    viewerDir,
    indexUrl: pathToFileURL(resolve(viewerDir, "index.html")).href,
    validText: readViewerDataText(),
  };
}

export function readViewerDataText(): string {
  return readFileSync(resolve(viewerDir, "browser_data.json"), "utf8");
}

export function readViewerData(): unknown {
  return JSON.parse(readViewerDataText()) as unknown;
}

export function parseCsv(text: string): { headers: string[]; rows: Array<Record<string, string>> } {
  const records: string[][] = [];
  let record: string[] = [];
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
    } else if (char === '"') {
      quoted = true;
    } else if (char === ",") {
      record.push(field);
      field = "";
    } else if (char === "\n") {
      record.push(field.replace(/\r$/, ""));
      records.push(record);
      record = [];
      field = "";
    } else {
      field += char;
    }
  }
  if (field.length > 0 || record.length > 0) {
    record.push(field.replace(/\r$/, ""));
    records.push(record);
  }
  const headers = records.shift();
  if (!headers || headers.length === 0) throw new Error("CSV header missing");
  return {
    headers,
    rows: records.map((cells) => {
      if (cells.length !== headers.length) throw new Error("CSV column count mismatch");
      return Object.fromEntries(headers.map((header, index) => [header, cells[index] ?? ""]));
    }),
  };
}
