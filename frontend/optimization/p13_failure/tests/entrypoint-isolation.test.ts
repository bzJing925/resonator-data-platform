import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import type { SpawnSyncReturns } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";
import { aRun, browserPaths, ownedRoot, repoRoot, taskAttempt, taskWork, viewerDir } from "./helpers.js";

const sandbox = resolve(taskWork, "a02_isolation");
const oldViewer = resolve(repoRoot, "backend/surrogate_data/optimization_p13_20261027_r01/viewer");
const oldReceipt = resolve(oldViewer, "t16_work/attempt_002/command_receipts.json");
const playwright = resolve(ownedRoot, "../../node_modules/.bin/playwright");
const candidateViewer = resolve(sandbox, "candidate/viewer");
const candidateWork = resolve(sandbox, "candidate/work");
const baseEnv: NodeJS.ProcessEnv = {
  ...process.env,
  NO_COLOR: "1",
  ALN_P13_VIEWER_OUT: candidateViewer,
  ALN_P13_TASK_WORK: candidateWork,
  ALN_P13_TASK_ATTEMPT: taskAttempt,
};

function invoke(executable: string, args: string[], env: NodeJS.ProcessEnv): SpawnSyncReturns<string> {
  return spawnSync(executable, args, {
    cwd: ownedRoot,
    env,
    encoding: "utf8",
    maxBuffer: 16 * 1024 * 1024,
  });
}

function script(name: string, args: string[] = [], env: NodeJS.ProcessEnv = baseEnv): SpawnSyncReturns<string> {
  return invoke(process.execPath, [`scripts/${name}`, ...args], env);
}

function rejected(result: SpawnSyncReturns<string>, pattern: RegExp): void {
  assert.notEqual(result.status, 0, `${result.stdout ?? ""}\n${result.stderr ?? ""}`);
  assert.match(`${result.stdout ?? ""}\n${result.stderr ?? ""}`, pattern);
}

function sha256(path: string): string {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

test("A02 entrypoints isolate all writes and reject historical paths and receipts", async (t) => {
  await t.test("missing parameters refuse build, verify and Playwright before writes", () => {
    for (const name of ["ALN_P13_VIEWER_OUT", "ALN_P13_TASK_WORK", "ALN_P13_TASK_ATTEMPT"]) {
      const env: NodeJS.ProcessEnv = {
        ...baseEnv,
        ALN_P13_VIEWER_OUT: resolve(sandbox, `missing-${name}/viewer`),
        ALN_P13_TASK_WORK: resolve(sandbox, `missing-${name}/work`),
      };
      delete env[name];
      rejected(script("build.mjs", [], env), new RegExp(name));
      assert.equal(existsSync(resolve(sandbox, `missing-${name}`)), false);
    }
    const verifyEnv = { ...baseEnv };
    delete verifyEnv.ALN_P13_TASK_WORK;
    rejected(script("verify.mjs", [], verifyEnv), /ALN_P13_TASK_WORK/);
    const browserEnv = { ...baseEnv };
    delete browserEnv.ALN_P13_VIEWER_OUT;
    rejected(
      invoke(playwright, ["test", "-c", "playwright.config.ts", "--list"], browserEnv),
      /ALN_P13_VIEWER_OUT/,
    );
  });

  await t.test("unknown script arguments refuse execution", () => {
    for (const name of ["build.mjs", "verify.mjs", "finalize.mjs"]) {
      rejected(script(name, ["--unknown-a02-option"]), /Unknown|unknown/);
    }
    assert.equal(existsSync(resolve(candidateWork, "command_receipts.json")), false);
    assert.equal(existsSync(resolve(candidateWork, "t16_artifact_manifest.json")), false);
  });

  await t.test("old-attempt and outside-A_RUN outputs are rejected without changing O15", () => {
    const protectedNames = ["index.html", "browser_data.json"];
    const before = Object.fromEntries(protectedNames.map((name) => [name, sha256(resolve(oldViewer, name))]));
    rejected(
      script("build.mjs", [], { ...baseEnv, ALN_P13_VIEWER_OUT: oldViewer }),
      /current A_RUN/,
    );
    rejected(
      script("build.mjs", [], {
        ...baseEnv,
        ALN_P13_TASK_WORK: resolve(oldViewer, "t16_work/attempt_002"),
      }),
      /current A_RUN/,
    );
    rejected(
      script("build.mjs", [], { ...baseEnv, ALN_P13_VIEWER_OUT: "relative/viewer" }),
      /absolute path/,
    );
    for (const name of protectedNames) {
      assert.equal(sha256(resolve(oldViewer, name)), before[name]);
    }
    assert.equal(aRun, resolve(repoRoot, `backend/surrogate_data/optimization_p13_20261027_r01/resume_20261029_r01/recovery_20261102/${taskAttempt}`));
  });

  await t.test("old A_RUN reselection is rejected even with internally consistent paths", () => {
    const oldAttemptRoot = resolve(aRun, "../attempt_002/A02/old-attempt-probe");
    const oldEnv: NodeJS.ProcessEnv = {
      ...baseEnv,
      ALN_P13_VIEWER_OUT: resolve(oldAttemptRoot, "viewer"),
      ALN_P13_TASK_WORK: resolve(oldAttemptRoot, "work"),
      ALN_P13_TASK_ATTEMPT: "attempt_002",
    };
    delete oldEnv.NODE_TEST_CONTEXT;
    for (const name of ["build.mjs", "finalize.mjs"]) {
      rejected(
        script(name, name === "build.mjs" ? ["--check"] : [], oldEnv),
        /recovery scope|current A00|attempt mismatch/i,
      );
    }
    rejected(
      invoke(process.execPath, [
        "--test",
        "--test-name-pattern=A02_NO_SUCH_TEST",
        resolve(ownedRoot, ".build/tests/static-contract.test.js"),
      ], oldEnv),
      /recovery scope|current A00|attempt mismatch/i,
    );
    assert.equal(existsSync(oldAttemptRoot), false);
  });

  await t.test("browser path resolver selects candidate index and import data, never formal O15", () => {
    const resolved = browserPaths();
    assert.equal(resolved.viewerDir, viewerDir);
    assert.notEqual(resolved.viewerDir, oldViewer);
    assert.equal(resolved.indexUrl, pathToFileURL(resolve(viewerDir, "index.html")).href);
    assert.equal(resolved.validText, readFileSync(resolve(viewerDir, "browser_data.json"), "utf8"));
    assert.notEqual(resolved.validText, readFileSync(resolve(oldViewer, "browser_data.json"), "utf8"));
  });

  await t.test("a valid build writes only the new candidate viewer", () => {
    const result = script("build.mjs");
    assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
    assert.ok(existsSync(resolve(candidateViewer, "index.html")));
    assert.ok(existsSync(resolve(candidateViewer, "browser_data.json")));
    assert.equal(existsSync(resolve(oldViewer, "a02_isolation")), false);
  });

  await t.test("finalize cannot fall back to an old receipt or accept a copied stale receipt", () => {
    assert.equal(existsSync(oldReceipt), true);
    const missingWork = resolve(sandbox, "missing-receipt/work");
    rejected(
      script("finalize.mjs", [], { ...baseEnv, ALN_P13_TASK_WORK: missingWork }),
      /Receipt not found/,
    );
    assert.equal(existsSync(resolve(missingWork, "t16_artifact_manifest.json")), false);

    const staleWork = resolve(sandbox, "stale-receipt/work");
    mkdirSync(staleWork, { recursive: true });
    writeFileSync(resolve(staleWork, "command_receipts.json"), `${JSON.stringify({
      schema: "aln_optimization_p13_t16_command_receipts_v1",
      task_id: "T16",
      attempt_id: "attempt_002",
      status: "PASS",
      playwright_executed: true,
      focused_playwright: { tests: 3, passed: 3, failed: 0, skipped: 0 },
      commands: [],
    }, null, 2)}\n`, "utf8");
    rejected(
      script("finalize.mjs", [], { ...baseEnv, ALN_P13_TASK_WORK: staleWork }),
      /current-attempt receipt schema|Receipt attempt mismatch|Receipt task_work mismatch/,
    );
    assert.equal(existsSync(resolve(staleWork, "t16_artifact_manifest.json")), false);
  });

  await t.test("Playwright rejects output redirected into an old attempt", () => {
    rejected(
      invoke(playwright, [
        "test",
        "-c",
        "playwright.config.ts",
        "--list",
        `--output=${resolve(oldViewer, "t18_playwright_output")}`,
      ], baseEnv),
      /current task work|current A_RUN/,
    );
  });
});
