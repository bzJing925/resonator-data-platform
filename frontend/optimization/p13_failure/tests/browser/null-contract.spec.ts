import { expect, test } from "@playwright/test";
import type { BrowserData } from "../../src/types.js";
import { browserPaths } from "../helpers.js";
import { correctNullData } from "../null-contract-helpers.js";

const { indexUrl } = browserPaths();

async function importPayload(page: import("@playwright/test").Page, text: string): Promise<void> {
  await page.getByLabel("导入 browser_data.json").setInputFiles({
    name: "browser_data.json",
    mimeType: "application/json",
    buffer: Buffer.from(text),
  });
}

test.beforeEach(async ({ page }) => {
  await page.goto(indexUrl);
  await expect(page.locator("[data-candidate-row]")).toHaveCount(20);
});

test("A03-BROWSER-NULL-001 correct-null synthetic import is accepted as a positive browser control", async ({ page }) => {
  const payload = correctNullData();
  await importPayload(page, JSON.stringify(payload));
  await expect(page.locator("#import-message")).toContainText("导入校验通过");
  await expect(page.locator("[data-candidate-row]")).toHaveCount(20);
  await expect(page.locator("#metric-rows [data-metric-row]")).toHaveCount(160);
});

test("A03-BROWSER-NULL-002 empty-string required-null import is rejected and current report is preserved", async ({ page }) => {
  const embedded = await page.locator("#browser-data").textContent();
  assertEmbedded(embedded);
  const payload = JSON.parse(embedded) as BrowserData;
  // Red run: the old candidate already contains this empty string. Green run: inject it into valid null data.
  Reflect.set(payload.candidates[0]!, "conservative_value", "");
  const before = await page.locator("[data-candidate-row]").first().textContent();
  await importPayload(page, JSON.stringify(payload));
  await expect(page.locator("#import-message")).toHaveText(/已拒绝导入|导入校验通过/);
  const message = await page.locator("#import-message").textContent();
  expect(message).toContain("已拒绝导入");
  expect(message).toContain("candidates.0.conservative_value");
  await expect(page.locator("[data-candidate-row]").first()).toHaveText(before ?? "");
  await expect(page.locator("[data-candidate-row]")).toHaveCount(20);
  await expect(page.locator("#metric-rows [data-metric-row]")).toHaveCount(160);
});

function assertEmbedded(value: string | null): asserts value is string {
  if (value === null) throw new Error("embedded browser-data script missing");
}
